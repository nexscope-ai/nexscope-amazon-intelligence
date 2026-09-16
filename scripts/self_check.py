#!/usr/bin/env python3
"""Small runnable checks for credential routing and release safety."""

from __future__ import annotations

import base64
import ast
import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.request import Request as DefaultRequest, urlopen as default_urlopen

ROOT = Path(__file__).resolve().parents[1]
os.sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from runtime import credentials, installer, release


def repository_manifest() -> list[dict[str, str]]:
    entries = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or ".git" in relative.parts or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        entries.append({"path": relative.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return entries


def http_construction_points() -> list[dict[str, object]]:
    points = []
    roots = (ROOT / "runtime", ROOT / "scripts", ROOT / "skills")
    for path in sorted(candidate for root in roots for candidate in root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.function = "<module>"

            def visit_FunctionDef(self, node):
                previous, self.function = self.function, node.name
                self.generic_visit(node)
                self.function = previous

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                if name == "Request":
                    segment = ast.get_source_segment(source, node) or ""
                    first_arg = node.args[0].id if node.args and isinstance(node.args[0], ast.Name) else ""
                    function = next((item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == self.function), None)
                    function_source = ast.get_source_segment(source, function) if function is not None else source
                    points.append({
                        "path": path.relative_to(ROOT).as_posix(),
                        "line": node.lineno,
                        "function": self.function,
                        "externalPresigned": first_arg == "put_url",
                        "authorization": "Authorization" in (function_source or segment),
                    })
                self.generic_visit(node)

        Visitor().visit(tree)
    return points


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def sign(key: Ed25519PrivateKey, kid: str, typ: str, payload: dict) -> str:
    header = b64(json.dumps({"alg": "EdDSA", "kid": kid, "typ": typ}, separators=(",", ":")).encode())
    body = b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{header}.{body}.{b64(key.sign(f'{header}.{body}'.encode()))}"


def zip_file(path: Path, name: str = "skills/demo/SKILL.md", content: bytes = b"ok") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, content)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        os.environ["NEXSCOPE_DATA_DIR"] = str(temp / "data")
        os.environ["NEXSCOPE_AUTH_MODE"] = "environment"
        os.environ["NEXSCOPE_API_KEY"] = "canary-api-key"
        os.environ["NEXSCOPE_PROXY_BASE"] = credentials.DEFAULT_API_BASE
        credentials.activate()
        assert os.environ["NEXSCOPE_API_KEY"] == "canary-api-key"

        class MemoryKeyring:
            priority = 1
            value = None

            def set_password(self, _service, _account, value): self.value = value
            def get_password(self, _service, _account): return self.value
            def delete_password(self, _service, _account): self.value = None

        memory_keyring = MemoryKeyring()
        original_keyring = credentials._keyring
        credentials._keyring = lambda: memory_keyring
        try:
            credentials.store("npi_installation", 3, "installation")
            assert "apiKey" not in json.loads(memory_keyring.value)
            assert credentials.load({"installationId": "installation"}) == {
                "installationToken": "npi_installation", "credentialRevision": 3,
            }
            memory_keyring.value = json.dumps({
                "apiKey": "legacy-general-key", "installationToken": "npi_installation",
                "credentialRevision": 3,
            })
            credentials.load({"installationId": "installation"})
            assert "apiKey" not in json.loads(memory_keyring.value)
            os.environ["NEXSCOPE_AUTH_MODE"] = "keyring"
            token_config = {**credentials.ensure_config(), "installationId": "installation"}
            credentials.write_json_atomic(credentials.data_dir() / "config.json", token_config)
            credentials.activate()
            assert os.environ["NEXSCOPE_API_KEY"] == "npi_installation"
        finally:
            credentials._keyring = original_keyring
            os.environ["NEXSCOPE_AUTH_MODE"] = "environment"

        good = temp / "good.zip"
        zip_file(good)
        release.safe_extract(good, temp / "good")
        assert (temp / "good/skills/demo/SKILL.md").read_text() == "ok"

        bad = temp / "bad.zip"
        zip_file(bad, "../outside")
        try:
            release.safe_extract(bad, temp / "bad")
            raise AssertionError("Zip Slip was accepted")
        except release.ReleaseError:
            pass

        link = temp / "link.zip"
        with zipfile.ZipFile(link, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
        try:
            release.safe_extract(link, temp / "link")
            raise AssertionError("symlink was accepted")
        except release.ReleaseError:
            pass

        collision = temp / "collision.zip"
        with zipfile.ZipFile(collision, "w") as archive:
            archive.writestr("Skill/File.txt", "one")
            archive.writestr("skill/file.TXT", "two")
        try:
            release.safe_extract(collision, temp / "collision")
            raise AssertionError("case-insensitive collision was accepted")
        except release.ReleaseError:
            pass

        config = credentials.ensure_config()
        clear = temp / "payload.zip"
        zip_file(clear)
        version = "1.1.0"
        aes_key = os.urandom(32)
        nonce = os.urandom(12)
        encrypted = temp / "payload.enc"
        encrypted.write_bytes(release.MAGIC + nonce + AESGCM(aes_key).encrypt(
            nonce, clear.read_bytes(), f"{credentials.PRODUCT_CODE}\n{version}".encode()
        ))
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes_raw()
        release.load_trusted_keys = lambda: {"check": b64(public)}
        common = {"schemaVersion": 1, "productCode": credentials.PRODUCT_CODE, "version": version}
        artifact = {
            "platform": "macos",
            "arch": "arm64",
            "artifactUrl": "https://github.com/lambbell/nexscope-amazon-intelligence/releases/download/v1.1.0/test.zip",
            "artifactSha256": "0" * 64,
            "artifactBytes": 1,
            "encryptedPayloadSha256": release.sha256_file(encrypted),
            "encryptedPayloadBytes": encrypted.stat().st_size,
            "payloadSha256": release.sha256_file(clear),
            "payloadBytes": clear.stat().st_size,
        }
        manifest = sign(private, "check", "NEXSCOPE-RELEASE", {
            **common,
            "minInstallerVersion": "1.0.0",
            "artifacts": [artifact],
        })
        license_jws = sign(private, "check", "NEXSCOPE-LICENSE", {
            **common,
            "licenseId": "7",
            "installationId": config["installationId"],
            "entitlementId": "8",
            "entitlementRevision": 1,
            "releaseId": "42",
            "platform": "macos",
            "arch": "arm64",
            "payloadSha256": release.sha256_file(clear),
            "issuedAt": 1,
            "updatesUntil": 2,
            "usagePolicy": "INSTALLED_VERSION_SURVIVES_EXPIRY",
        })
        parts = manifest.split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        try:
            release.verify_jws(".".join(parts), {"check": b64(public)}, "NEXSCOPE-RELEASE")
            raise AssertionError("tampered signature was accepted")
        except release.ReleaseError:
            pass
        grant = {**artifact, "releaseId": "42", "version": version}
        installed = release.install_encrypted_payload(encrypted, b64(aes_key), license_jws, manifest, grant)
        assert (installed / "skills/demo/SKILL.md").read_text() == "ok"
        calls = []
        def fake_codex(*args):
            calls.append(args)
            output = "MARKETPLACE ROOT\n" if args == ("marketplace", "list") else "{}"
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        installer._codex = fake_codex
        installer.register_codex(installed)
        assert (credentials.data_dir() / "marketplace/plugins" / credentials.PRODUCT_CODE / "skills/demo/SKILL.md").is_file()
        assert any(call[:2] == ("marketplace", "add") for call in calls)
        assert any(call[0] == "add" for call in calls)

        outer = temp / "outer.zip"
        with zipfile.ZipFile(outer, "w") as archive:
            archive.writestr(release.PAYLOAD_ENTRY, encrypted.read_bytes())
        extracted_frame = temp / "extracted.nsp1"
        release.extract_payload_entry(outer, extracted_frame)
        assert extracted_frame.read_bytes() == encrypted.read_bytes()

        config["apiBase"] = "https://api.nexscope.ai/api/"
        credentials.write_json_atomic(credentials.data_dir() / "config.json", config)
        requested = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self, _): return b'{"code":0,"data":{"ok":true}}'
        class ApiOpener:
            def open(self, request, timeout):
                requested.append((request.full_url, request.get_header("Authorization")))
                return Response()
        original_build_opener = installer.build_opener
        installer.build_opener = lambda *_handlers: ApiOpener()
        try:
            installer._request("/api/plugins/installations/current", token="npi_test")
        finally:
            installer.build_opener = original_build_opener
        assert requested == [("https://api.nexscope.ai/api/plugins/installations/current", "Bearer npi_test")]

        redirect_request = installer.Request(
            "https://api.nexscope.ai/api/plugins/installations/current",
            headers={"Authorization": "Bearer npi_test"},
        )
        redirects = credentials.AuthenticatedSameOriginRedirects()
        same_origin = redirects.redirect_request(
            redirect_request, None, 302, "Found", {},
            "https://api.nexscope.ai/api/plugins/installations/current/",
        )
        assert same_origin.get_header("Authorization") == "Bearer npi_test"
        try:
            redirects.redirect_request(
                redirect_request, None, 302, "Found", {},
                "https://example.com/credential-capture",
            )
            raise AssertionError("cross-origin authenticated redirect was accepted")
        except credentials.CredentialError:
            pass

        received = []

        class RedirectTarget(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(("target", self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_): pass

        class RedirectOrigin(BaseHTTPRequestHandler):
            target_url = ""

            def do_GET(self):
                if self.path == "/same":
                    self.send_response(302)
                    self.send_header("Location", "/ok")
                    self.end_headers()
                elif self.path == "/cross":
                    self.send_response(302)
                    self.send_header("Location", self.target_url)
                    self.end_headers()
                else:
                    received.append(("same", self.headers.get("Authorization")))
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

            def log_message(self, *_): pass

        target_server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectTarget)
        origin_server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectOrigin)
        RedirectOrigin.target_url = f"http://127.0.0.1:{target_server.server_port}/capture"
        threads = [Thread(target=server.serve_forever, daemon=True) for server in (target_server, origin_server)]
        for thread in threads: thread.start()
        try:
            origin = f"http://127.0.0.1:{origin_server.server_port}"
            with default_urlopen(DefaultRequest(origin + "/same", headers={"Authorization": "Bearer npi_test"})) as response:
                assert response.read() == b"ok"
            try:
                default_urlopen(DefaultRequest(origin + "/cross", headers={"Authorization": "Bearer npi_test"}))
                raise AssertionError("skill-style urlopen leaked Authorization across origins")
            except credentials.CredentialError:
                pass
            with default_urlopen(origin + "/cross") as response:
                assert response.read() == b"ok"
        finally:
            for server in (origin_server, target_server): server.shutdown(); server.server_close()
            for thread in threads: thread.join()
        assert received == [("same", "Bearer npi_test"), ("target", None)]

        original_store, original_delete, original_request = credentials.store, credentials.delete, installer._request
        cleanup_calls = []
        def failing_store(*_):
            raise credentials.CredentialError("test failure")
        credentials.store = failing_store
        credentials.delete = lambda saved=None: cleanup_calls.append(("delete", saved["installationId"]))
        def connection_request(path, **kwargs):
            cleanup_calls.append((path, kwargs["token"]))
            if path.endswith("/current"):
                return {
                    "installation": {"installationId": config["installationId"]},
                    "entitlement": {}, "eligible": True,
                }
            return {}
        installer._request = connection_request
        try:
            try:
                installer._save_connection(config, {
                    "apiBase": credentials.DEFAULT_API_BASE,
                    "installationToken": "npi_test", "credentialRevision": 1,
                })
                raise AssertionError("failed credential save was accepted")
            except credentials.CredentialError:
                pass
        finally:
            credentials.store, credentials.delete, installer._request = original_store, original_delete, original_request
        assert cleanup_calls == [
            ("/api/plugins/installations/current", "npi_test"),
            ("/api/plugins/installations/current/revoke", "npi_test"),
            ("delete", config["installationId"]),
        ]

        original_token, original_request = credentials.installation_token, installer._request
        credentials.installation_token = lambda: "npi_test"
        installer._request = lambda *_args, **_kwargs: {
            "installation": {"installationId": "device", "productCode": credentials.PRODUCT_CODE,
                             "deviceName": "Mac", "status": "ACTIVE", "installedVersion": "1.1.0"},
            "entitlement": {"updatesUntil": 123}, "eligible": True, "reason": None,
        }
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                installer.status()
        finally:
            credentials.installation_token, installer._request = original_token, original_request
        assert json.loads(output.getvalue()) == {
            "installationId": "device", "productCode": credentials.PRODUCT_CODE, "deviceName": "Mac",
            "status": "ACTIVE", "installedVersion": "1.1.0", "eligible": True, "reason": None,
        }

        wheels = temp / "wheels"
        wheels.mkdir()
        (wheels / "cryptography-1.0-py3-none-any.whl").write_bytes(b"wheel")
        (wheels / "keyring-1.0-py3-none-any.whl").write_bytes(b"wheel")
        trusted = temp / "trusted.json"
        trusted.write_text(json.dumps({"check": b64(public)}), encoding="utf-8")
        build_output = temp / "build"
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/build_release.py"), "1.1.0",
            "--wheel-dir", str(wheels), "--trusted-keys", str(trusted),
            "--artifact-base-url", "https://github.com/lambbell/nexscope-amazon-intelligence/releases/download/v1.1.0",
            "--output", str(build_output),
        ], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        metadata = json.loads((build_output / "release-metadata.json").read_text())
        assert [(item["platform"], item["arch"]) for item in metadata["artifacts"]] == [
            ("macos", "arm64"), ("macos", "x64"), ("windows", "x64")]
        for item in metadata["artifacts"]:
            with zipfile.ZipFile(build_output / Path(item["artifactUrl"]).name) as archive:
                assert archive.namelist().count(release.PAYLOAD_ENTRY) == 1

    offenders = []
    for path in (ROOT / "skills").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "NEXSCOPE_API_KEY" in text and "_nexscope_activate()" not in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"credential bootstrap missing: {offenders}"
    assert not list((ROOT / "skills").rglob("onboarding.py"))
    points = http_construction_points()
    nexscope_points = [point for point in points if point["path"].startswith("skills/") and not point["externalPresigned"]]
    nexscope_points += [
        point for point in points
        if point["path"] == "runtime/installer.py" and point["function"] == "_request"
    ]
    assert nexscope_points and all(point["authorization"] for point in nexscope_points), nexscope_points
    presigned_points = [point for point in points if point["externalPresigned"]]
    assert presigned_points and not any(point["authorization"] for point in presigned_points), presigned_points

    manifest = repository_manifest()
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(sys.argv) == 3 and sys.argv[1] == "--manifest":
        Path(sys.argv[2]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif len(sys.argv) != 1:
        raise SystemExit("usage: self_check.py [--manifest PATH]")
    print(
        f"self-check passed: {len(nexscope_points)} NexScope HTTP points, "
        f"{len(presigned_points)} external presigned points, "
        f"manifest sha256={hashlib.sha256(manifest_bytes).hexdigest()} ({len(manifest)} files)"
    )


if __name__ == "__main__":
    main()
