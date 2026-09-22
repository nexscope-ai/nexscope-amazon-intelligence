#!/usr/bin/env python3
"""Build one encrypted payload and thin Windows/macOS release archives."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.credentials import PRODUCT_CODE
from runtime.release import MAGIC, MAX_ARCHIVE_BYTES, MAX_PAYLOAD_BYTES, PAYLOAD_ENTRY

INCLUDE_ROOTS = (".codex-plugin", "assets", "runtime", "skills")
EXCLUDED_NAMES = {"__pycache__", ".DS_Store"}
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_for_payload(version: str, trusted_keys: bytes) -> list[tuple[Path | bytes, str]]:
    result: list[tuple[Path | bytes, str]] = []
    for root_name in INCLUDE_ROOTS:
        root = ROOT / root_name
        for path in root.rglob("*"):
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                raise SystemExit(f"payload source may not contain symlinks: {relative}")
            if path.is_file() and not any(part in EXCLUDED_NAMES for part in path.parts):
                if relative == ".codex-plugin/plugin.json":
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                    manifest["version"] = version
                    result.append((json.dumps(manifest, indent=2, ensure_ascii=False).encode() + b"\n", relative))
                elif relative == "runtime/trusted_keys.json":
                    result.append((trusted_keys, relative))
                else:
                    result.append((path, relative))
    return sorted(result, key=lambda item: item[1])


def write_zip(path: Path, entries: list[tuple[Path | bytes, str, int]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, name, mode in sorted(entries, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            value = source if isinstance(source, bytes) else source.read_bytes()
            archive.writestr(info, value)


def hashed_requirements(wheels: list[Path]) -> bytes:
    packages: dict[tuple[str, str], list[str]] = {}
    for wheel in wheels:
        parts = wheel.name.split("-", 2)
        if len(parts) < 3:
            raise SystemExit(f"invalid wheel filename: {wheel.name}")
        package = parts[0].replace("_", "-")
        packages.setdefault((package, parts[1]), []).append(digest(wheel))
    lines = []
    for (package, version), hashes in sorted(packages.items()):
        lines.append(f"{package}=={version} " + " ".join(f"--hash=sha256:{value}" for value in sorted(hashes)))
    return ("\n".join(lines) + "\n").encode()


def encrypt(clear_zip: Path, destination: Path, version: str, key: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(nonce, clear_zip.read_bytes(), f"{PRODUCT_CODE}\n{version}".encode())
    destination.write_bytes(MAGIC + nonce + encrypted)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("--output", type=Path, help="Fresh output directory (defaults to dist/<version>)")
    parser.add_argument("--wheel-dir", type=Path, required=True, help="Directory of reviewed offline wheels to include")
    parser.add_argument("--trusted-keys", type=Path, required=True, help="Reviewed Ed25519 public-key JSON")
    parser.add_argument("--artifact-base-url", required=True, help="Official GitHub release asset directory")
    parser.add_argument("--min-installer-version", default="1.0.0")
    args = parser.parse_args()
    if not VERSION_RE.fullmatch(args.version) or not VERSION_RE.fullmatch(args.min_installer_version):
        raise SystemExit("version values must use strict semantic versioning")
    trusted_keys = json.loads(args.trusted_keys.read_text(encoding="utf-8"))
    if not isinstance(trusted_keys, dict) or not trusted_keys:
        raise SystemExit("--trusted-keys must contain the reviewed production Ed25519 public key")
    try:
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
               or len(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))) != 32 or "=" in value
               for value in trusted_keys.values()):
            raise ValueError
    except (TypeError, ValueError):
        raise SystemExit("--trusted-keys values must be unpadded 32-byte Ed25519 public keys") from None
    trusted_keys_bytes = (json.dumps(trusted_keys, indent=2, sort_keys=True) + "\n").encode()
    base_url = args.artifact_base_url.rstrip("/")
    parsed_url = urlparse(base_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "github.com" or not parsed_url.path.startswith("/nexscope-ai/nexscope-amazon-intelligence/releases/download/") or parsed_url.query or parsed_url.fragment:
        raise SystemExit("--artifact-base-url must be an official GitHub release asset directory")
    output = args.output.resolve() if args.output else ROOT / "dist" / args.version
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise SystemExit("output directory must be empty to avoid mixing release artifacts")
    clear_zip = output / f"{PRODUCT_CODE}-{args.version}-payload.zip"
    write_zip(clear_zip, [(path, name, 0o644) for path, name in files_for_payload(args.version, trusted_keys_bytes)])
    clear_digest = digest(clear_zip)
    clear_bytes = clear_zip.stat().st_size
    if clear_bytes > MAX_PAYLOAD_BYTES:
        raise SystemExit("plaintext payload exceeds the 256 MiB contract limit")
    payload_key_bytes = secrets.token_bytes(32)
    payload_key = base64.urlsafe_b64encode(payload_key_bytes).rstrip(b"=").decode("ascii")

    wheels = sorted(args.wheel_dir.glob("*.whl"))
    wheel_names = {wheel.name.split("-")[0].replace("_", "-").lower() for wheel in wheels}
    if not {"cryptography", "keyring"}.issubset(wheel_names):
        raise SystemExit("--wheel-dir must include reviewed cryptography and keyring wheels")
    requirements = hashed_requirements(wheels)

    targets = (
        ("macos", "arm64", ROOT / "install.command", 0o755),
        ("macos", "x64", ROOT / "install.command", 0o755),
        ("windows", "x64", ROOT / "install.bat", 0o644),
    )
    artifacts = []
    for platform_name, arch, launcher, mode in targets:
        target = f"{platform_name}-{arch}"
        encrypted = output / f".{target}.nsp1"
        encrypt(clear_zip, encrypted, args.version, payload_key_bytes)
        entries: list[tuple[Path | bytes, str, int]] = [
            (encrypted, PAYLOAD_ENTRY, 0o600),
            (launcher, launcher.name, mode),
            (ROOT / "OFFLINE_INSTALL.md", "OFFLINE_INSTALL.md", 0o644),
            (requirements, "runtime/requirements.lock", 0o644),
        ]
        for path in (ROOT / "runtime").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.name != "requirements.lock":
                name = "runtime/" + path.relative_to(ROOT / "runtime").as_posix()
                entries.append((trusted_keys_bytes if name == "runtime/trusted_keys.json" else path, name, 0o644))
        entries.extend((wheel, "wheels/" + wheel.name, 0o644) for wheel in wheels)
        artifact = output / f"{PRODUCT_CODE}-{args.version}-{target}.zip"
        write_zip(artifact, entries)
        if artifact.stat().st_size > MAX_ARCHIVE_BYTES:
            raise SystemExit(f"{artifact.name} exceeds the 512 MiB contract limit")
        artifacts.append({
            "platform": platform_name,
            "arch": arch,
            "artifactUrl": f"{base_url}/{artifact.name}",
            "artifactSha256": digest(artifact),
            "artifactBytes": artifact.stat().st_size,
            "encryptedPayloadSha256": digest(encrypted),
            "encryptedPayloadBytes": encrypted.stat().st_size,
            "payloadSha256": clear_digest,
            "payloadBytes": clear_bytes,
        })
        encrypted.unlink()

    metadata = {
        "schemaVersion": 1,
        "productCode": PRODUCT_CODE,
        "version": args.version,
        "minInstallerVersion": args.min_installer_version,
        "artifacts": artifacts,
    }
    (output / "release-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    key_path = output / "payload-key.private.json"
    key_path.write_text(json.dumps({"version": args.version, "payloadKey": payload_key}, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(key_path, 0o600)
    clear_zip.unlink()
    print(json.dumps({"metadata": str(output / "release-metadata.json"), "privateKeyFile": str(key_path)}, indent=2))


if __name__ == "__main__":
    main()
