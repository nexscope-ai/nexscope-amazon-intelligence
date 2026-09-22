#!/usr/bin/env python3
"""NexScope browser activation and managed release CLI."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import credentials, release

INSTALLER_VERSION = "1.0.0"
VERIFICATION_HOSTS = {"nexscope.ai", "www.nexscope.ai"}
RELEASE_REPOSITORY = "/nexscope-ai/nexscope-amazon-intelligence/releases/download/"
DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MARKETPLACE_NAME = "nexscope-managed"


class InstallerError(RuntimeError):
    pass


class RetryableInstallerError(InstallerError):
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


class _ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        parsed = urlparse(new_url)
        if parsed.scheme != "https" or parsed.hostname not in DOWNLOAD_HOSTS:
            raise InstallerError("Release redirect left the approved HTTPS hosts")
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def _platform() -> tuple[str, str]:
    system = platform.system()
    machine = platform.machine().lower()
    system_name = {"Darwin": "macos", "Windows": "windows"}.get(system)
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"x86_64", "amd64"} else None
    if not system_name or not arch or (system_name == "windows" and arch != "x64"):
        raise InstallerError(f"Unsupported platform: {system or 'unknown'} {machine or 'unknown'}")
    return system_name, arch


def _request(
    path: str, *, body: dict | None = None, token: str | None = None,
    query: dict | None = None, api_base: str | None = None,
) -> dict:
    config = credentials.read_config() or credentials.ensure_config()
    base = credentials.validate_api_base(str(api_base or config.get("apiBase") or credentials.DEFAULT_API_BASE))
    route = "/" + path.lstrip("/")
    if urlparse(base).path.rstrip("/").endswith("/api") and route.startswith("/api/"):
        route = route[4:]
    url = base.rstrip("/") + route
    if query:
        url += "?" + urlencode(query)
    headers = {"Accept": "application/json", "Cache-Control": "no-store"}
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with build_opener(credentials.AuthenticatedSameOriginRedirects()).open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise InstallerError("NexScope response exceeds 1 MiB")
    except HTTPError as error:
        retry = error.headers.get("Retry-After")
        if error.code == 429 or error.code >= 500:
            raise RetryableInstallerError(f"NexScope HTTP {error.code}", int(retry) if retry and retry.isdigit() else 0) from error
        raise InstallerError(f"NexScope HTTP {error.code}") from error
    except URLError as error:
        raise RetryableInstallerError("Could not reach NexScope") from error
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallerError("NexScope returned an invalid response") from error
    if not isinstance(value, dict) or value.get("code") != 0:
        reason = value.get("data", {}).get("reason") if isinstance(value.get("data"), dict) else None
        raise InstallerError(str(reason or value.get("msg") or "NexScope request failed"))
    result = value.get("data")
    if not isinstance(result, dict):
        raise InstallerError("NexScope returned an invalid data object")
    return result


def _device_name() -> str:
    name = socket.gethostname().strip()
    if not name or len(name) > 80 or any(ord(character) < 32 for character in name):
        return "This device"
    return name


def _save_connection(config: dict, result: dict) -> None:
    token = str(result.get("installationToken", ""))
    updated = dict(config)
    updated["apiBase"] = credentials.validate_api_base(str(result.get("apiBase") or config["apiBase"]))
    save_started = False
    try:
        current = _request(
            "/api/plugins/installations/current", token=token, api_base=updated["apiBase"],
        )
        installation = current.get("installation")
        if (
            not isinstance(installation, dict)
            or installation.get("installationId") != config["installationId"]
            or not isinstance(current.get("entitlement"), dict)
            or current.get("eligible") is not True
        ):
            raise InstallerError("NexScope could not verify the activated installation")
        save_started = True
        credentials.store(token, int(result.get("credentialRevision", 0)), config["installationId"])
        credentials.write_json_atomic(credentials.data_dir() / "config.json", updated)
    except Exception:
        try:
            _request(
                "/api/plugins/installations/current/revoke", body={}, token=token,
                api_base=updated["apiBase"],
            )
        except Exception:
            pass
        if save_started:
            try:
                credentials.delete(updated)
            except credentials.CredentialError:
                pass
        raise


def connect() -> None:
    credentials.ensure_available()
    config = credentials.ensure_config()
    system, arch = _platform()
    authorization = _request("/api/plugins/authorizations", body={
        "productCode": credentials.PRODUCT_CODE,
        "installationId": config["installationId"],
        "deviceName": _device_name(),
        "platform": system,
        "arch": arch,
        "installerVersion": INSTALLER_VERSION,
    })
    verification_uri = str(authorization.get("verificationUri", ""))
    verification_complete = str(authorization.get("verificationUriComplete") or verification_uri)
    parsed = urlparse(verification_complete)
    if parsed.scheme != "https" or parsed.hostname not in VERIFICATION_HOSTS:
        raise InstallerError("NexScope returned an unsafe verification URL")
    user_code = str(authorization.get("userCode", ""))
    device_code = str(authorization.get("deviceCode", ""))
    interval = max(5, int(authorization.get("interval", 5)))
    deadline = time.monotonic() + min(600, int(authorization.get("expiresIn", 600)))
    print("Opening the NexScope authorization page...")
    print(f"Confirm this code: {user_code}")
    if not webbrowser.open(verification_complete):
        print(f"Open this URL manually: {verification_uri}")
    print("Waiting for browser confirmation (valid for up to 10 minutes). Press Ctrl+C to cancel.")
    backoff = interval
    while time.monotonic() < deadline:
        try:
            result = _request("/api/plugins/authorizations/redeem", body={"deviceCode": device_code})
            backoff = interval
        except RetryableInstallerError as error:
            delay = min(30, max(error.retry_after, backoff))
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
            backoff = min(30, backoff * 2)
            continue
        status = str(result.get("status", ""))
        if status == "PENDING":
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
            continue
        if status != "ACTIVATED":
            raise InstallerError(f"Authorization ended with status {status or 'unknown'}")
        _save_connection(config, result)
        print("Account connected and credentials stored securely.")
        return
    raise InstallerError("Authorization expired; start again")


def status() -> None:
    result = _request("/api/plugins/installations/current", token=credentials.installation_token())
    installation = result.get("installation")
    entitlement = result.get("entitlement")
    if not isinstance(installation, dict) or not isinstance(entitlement, dict):
        raise InstallerError("NexScope returned an invalid installation status")
    safe = {key: installation.get(key) for key in (
        "installationId", "productCode", "deviceName", "status", "installedVersion"
    )}
    safe.update({"eligible": result.get("eligible"), "reason": result.get("reason")})
    print(json.dumps(safe, ensure_ascii=False, indent=2))


def disconnect(local_only: bool = False) -> None:
    if not local_only:
        _request("/api/plugins/installations/current/revoke", body={}, token=credentials.installation_token())
    credentials.delete()
    print("Local credentials removed. " + ("The remote device seat remains active; remove it on the account page." if local_only else "The remote device was disconnected."))


class _InstallLock:
    def __enter__(self):
        self.path = credentials.data_dir() / ".install.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            # ponytail: one-hour stale lock; replace with PID/process probing if false positives appear.
            if time.time() - self.path.stat().st_mtime <= 3600:
                raise InstallerError("Another install or update is already running") from error
            self.path.unlink()
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(self.fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, *_):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


def _download(url: str, destination: Path, expected_bytes: int, expected_sha256: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in DOWNLOAD_HOSTS:
        raise InstallerError("Release URL is not an allowed HTTPS host")
    if parsed.hostname == "github.com" and not parsed.path.startswith(RELEASE_REPOSITORY):
        raise InstallerError("Release URL is outside the approved repository")
    if expected_bytes < 1 or expected_bytes > release.MAX_ARCHIVE_BYTES:
        raise InstallerError("Invalid release size")
    if shutil.disk_usage(destination.parent).free < expected_bytes + release.MAX_EXTRACTED_BYTES:
        raise InstallerError("Not enough free disk space for a safe install")
    try:
        opener = build_opener(_ReleaseRedirects())
        with opener.open(Request(url, headers={"Accept": "application/octet-stream"}), timeout=60) as response, destination.open("xb") as handle:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in DOWNLOAD_HOSTS:
                raise InstallerError("Release redirect left the approved HTTPS hosts")
            remaining = expected_bytes + 1
            while remaining:
                chunk = response.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)
    except (HTTPError, URLError, OSError) as error:
        raise InstallerError("Release download failed") from error
    if destination.stat().st_size != expected_bytes or release.sha256_file(destination) != expected_sha256:
        raise InstallerError("PACKAGE_HASH_MISMATCH: release archive differs")


def _codex(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("codex")
    if not executable:
        raise InstallerError("Codex CLI is required to register the managed plugin")
    return subprocess.run(
        [executable, "plugin", *arguments], text=True, capture_output=True, timeout=120, check=False
    )


def register_codex(plugin_source: Path) -> None:
    root = credentials.data_dir() / "marketplace"
    current = root / "plugins" / credentials.PRODUCT_CODE
    staging = current.with_name(current.name + ".new")
    backup = current.with_name(current.name + ".old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plugin_source, staging)
    marketplace = {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": "NexScope Managed"},
        "plugins": [{
            "name": credentials.PRODUCT_CODE,
            "source": {"source": "local", "path": f"./plugins/{credentials.PRODUCT_CODE}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }],
    }
    credentials.write_json_atomic(root / ".agents/plugins/marketplace.json", marketplace)
    if current.exists():
        os.replace(current, backup)
    os.replace(staging, current)
    added_marketplace = False
    try:
        listed = _codex("marketplace", "list")
        if listed.returncode != 0:
            raise InstallerError("Could not inspect Codex plugin marketplaces")
        matching = [line.split(maxsplit=1) for line in listed.stdout.splitlines()[1:] if line.strip()]
        roots = {parts[0]: parts[1].strip() for parts in matching if len(parts) == 2}
        expected_root = str(root.resolve())
        if MARKETPLACE_NAME in roots and str(Path(roots[MARKETPLACE_NAME]).resolve()) != expected_root:
            raise InstallerError(f"Codex marketplace name {MARKETPLACE_NAME!r} is already used by another path")
        if MARKETPLACE_NAME not in roots:
            added = _codex("marketplace", "add", expected_root, "--json")
            if added.returncode != 0:
                raise InstallerError("Could not add the NexScope managed marketplace")
            added_marketplace = True
        installed = _codex("add", f"{credentials.PRODUCT_CODE}@{MARKETPLACE_NAME}", "--json")
        if installed.returncode != 0:
            raise InstallerError("Could not install the managed plugin in Codex")
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(current, ignore_errors=True)
        if backup.exists():
            os.replace(backup, current)
        if added_marketplace:
            _codex("marketplace", "remove", MARKETPLACE_NAME, "--json")
        raise


def _finalize_install(version: str) -> None:
    config = credentials.read_config()
    config["currentVersion"] = version
    credentials.write_json_atomic(credentials.data_dir() / "config.json", config)
    credentials.write_json_atomic(credentials.data_dir() / "state.json", {
        "schemaVersion": 1, "currentVersion": version, "status": "installed"
    })


def _recover_incomplete_install() -> bool:
    state_path = credentials.data_dir() / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as error:
        raise InstallerError("Invalid local install state") from error
    if not isinstance(state, dict) or state.get("status") != "extracted":
        return False
    version = str(state.get("pendingVersion", ""))
    target = credentials.data_dir() / "releases" / version
    if not release.VERSION_RE.fullmatch(version) or not target.is_dir():
        raise InstallerError("Incomplete install cannot be recovered safely")
    register_codex(target)
    _finalize_install(version)
    return True


def update(channel: str = "stable") -> None:
    token = credentials.installation_token()
    if _recover_incomplete_install():
        version = credentials.read_config()["currentVersion"]
        _request("/api/plugins/installations/current/report", body={
            "installedVersion": version, "installerVersion": INSTALLER_VERSION,
        }, token=token)
        print("Recovered the interrupted installation. Restart Codex and confirm the plugin version in a new task.")
        return
    config = credentials.read_config()
    latest_query = {
        "productCode": credentials.PRODUCT_CODE,
        "channel": channel,
        "installerVersion": INSTALLER_VERSION,
    }
    if config.get("currentVersion"):
        latest_query["currentVersion"] = config["currentVersion"]
    latest = _request("/api/plugins/releases/latest", token=token, query=latest_query)
    if not latest.get("updateAvailable"):
        print("The latest available version is already installed.")
        return
    release_id = str((latest.get("release") or {}).get("id", ""))
    if not release_id:
        raise InstallerError("NexScope returned an update without a release id")
    grant = _request(f"/api/plugins/releases/{release_id}/grant", body={
        "installerVersion": INSTALLER_VERSION,
    }, token=token)
    with _InstallLock(), tempfile.TemporaryDirectory(prefix="nexscope-update-") as temp:
        archive = Path(temp) / "release.zip"
        _download(str(grant["artifactUrl"]), archive, int(grant["artifactBytes"]), str(grant["artifactSha256"]))
        payload = Path(temp) / "nexscope-plugin.nsp1"
        release.extract_payload_entry(archive, payload)
        installed_path = release.install_encrypted_payload(
            payload, str(grant["payloadKey"]), str(grant["licenseJws"]), str(grant["manifestJws"]), grant
        )
        try:
            register_codex(installed_path)
            _finalize_install(installed_path.name)
        except Exception:
            shutil.rmtree(installed_path, ignore_errors=True)
            (credentials.data_dir() / "licenses" / f"{installed_path.name}.json").unlink(missing_ok=True)
            state_path = credentials.data_dir() / "state.json"
            previous_version = config.get("currentVersion")
            if previous_version:
                credentials.write_json_atomic(state_path, {
                    "schemaVersion": 1, "currentVersion": previous_version, "status": "installed"
                })
            else:
                state_path.unlink(missing_ok=True)
            raise
    _request("/api/plugins/installations/current/report", body={
        "installedVersion": credentials.read_config()["currentVersion"],
        "installerVersion": INSTALLER_VERSION,
    }, token=token)
    print("Installation complete. Restart Codex and confirm the plugin version in a new task.")


def doctor() -> None:
    system, arch = _platform()
    config = credentials.read_config()
    result = {
        "platform": system,
        "arch": arch,
        "installerVersion": INSTALLER_VERSION,
        "dataPath": str(credentials.data_dir()),
        "configPresent": bool(config),
        "credentialPresent": False,
        "currentVersion": config.get("currentVersion"),
    }
    try:
        credentials.load(config)
        result["credentialPresent"] = True
    except credentials.CredentialError:
        pass
    print(json.dumps(result, ensure_ascii=False, indent=2))


def rollback(version: str) -> None:
    if not release.VERSION_RE.fullmatch(version):
        raise InstallerError("Rollback version must be strict semantic versioning")
    target = credentials.data_dir() / "releases" / version
    license_path = credentials.data_dir() / "licenses" / f"{version}.json"
    if not target.is_dir() or not license_path.is_file():
        raise InstallerError("The requested licensed version is not retained locally")
    token = credentials.installation_token()
    with _InstallLock():
        register_codex(target)
        _finalize_install(version)
    _request("/api/plugins/installations/current/report", body={
        "installedVersion": version, "installerVersion": INSTALLER_VERSION,
    }, token=token)
    print("Rolled back to the retained local version. Restart Codex and confirm the plugin version in a new task.")


def uninstall(revoke: bool = False) -> None:
    config = credentials.read_config()
    if revoke and config:
        disconnect(False)
    elif config:
        try:
            credentials.delete(config)
        except credentials.CredentialError:
            pass
    root = credentials.data_dir()
    _codex("remove", f"{credentials.PRODUCT_CODE}@{MARKETPLACE_NAME}", "--json")
    marketplaces = _codex("marketplace", "list")
    if marketplaces.returncode == 0 and any(line.split(maxsplit=1)[0] == MARKETPLACE_NAME for line in marketplaces.stdout.splitlines()[1:] if line.strip()):
        _codex("marketplace", "remove", MARKETPLACE_NAME)
    shutil.rmtree(root / "marketplace", ignore_errors=True)
    for name in ("releases", "licenses"):
        shutil.rmtree(root / name, ignore_errors=True)
    for name in ("config.json", "state.json", ".install.lock"):
        (root / name).unlink(missing_ok=True)
    print("Managed plugin files removed; user reports and other plugins were not changed.")
    if not revoke:
        print("The remote device seat remains active; remove it on the NexScope account page.")


def menu() -> None:
    print("NexScope Amazon Intelligence")
    print("1. Install or connect account")
    print("2. Check for updates")
    print("3. Check installation status")
    print("4. Disconnect account")
    print("5. Uninstall plugin")
    choice = input("Choose [1-5]: ").strip()
    if choice == "1":
        connect()
        update()
    elif choice == "2": update()
    elif choice == "3": status()
    elif choice == "4": disconnect()
    elif choice == "5": uninstall(input("Also disconnect the remote device? [y/N]: ").strip().lower() == "y")
    else: raise InstallerError("Invalid choice")


def main() -> None:
    parser = argparse.ArgumentParser(prog="nexscope-installer")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("connect")
    commands.add_parser("status")
    disconnect_parser = commands.add_parser("disconnect")
    disconnect_parser.add_argument("--local-only", action="store_true")
    update_parser = commands.add_parser("update")
    update_parser.add_argument("--channel", choices=("stable", "preview"), default="stable")
    commands.add_parser("doctor")
    rollback_parser = commands.add_parser("rollback")
    rollback_parser.add_argument("version")
    uninstall_parser = commands.add_parser("uninstall")
    uninstall_parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    if args.command is None: menu()
    elif args.command == "connect": connect()
    elif args.command == "status": status()
    elif args.command == "disconnect": disconnect(args.local_only)
    elif args.command == "update": update(args.channel)
    elif args.command == "doctor": doctor()
    elif args.command == "rollback": rollback(args.version)
    elif args.command == "uninstall": uninstall(args.revoke)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
    except (credentials.CredentialError, release.ReleaseError, InstallerError, KeyError, ValueError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)
