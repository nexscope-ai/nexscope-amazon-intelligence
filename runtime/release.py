"""Verify, decrypt, and install a licensed plugin release."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .credentials import PRODUCT_CODE, data_dir, read_config, write_json_atomic

MAGIC = b"NSP1"
PAYLOAD_ENTRY = "payload/nexscope-plugin.nsp1"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
MAX_ENCRYPTED_PAYLOAD_BYTES = MAX_PAYLOAD_BYTES + 32
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_FILES = 20_000
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
ARTIFACT_FIELDS = {
    "platform", "arch", "artifactUrl", "artifactSha256", "artifactBytes",
    "encryptedPayloadSha256", "encryptedPayloadBytes", "payloadSha256", "payloadBytes",
}
LICENSE_FIELDS = {
    "schemaVersion", "licenseId", "productCode", "installationId", "entitlementId",
    "entitlementRevision", "releaseId", "version", "platform", "arch", "payloadSha256",
    "issuedAt", "updatesUntil", "usagePolicy",
}


class ReleaseError(RuntimeError):
    pass


def _valid_artifact(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != ARTIFACT_FIELDS:
        return False
    hashes = (value["artifactSha256"], value["encryptedPayloadSha256"], value["payloadSha256"])
    sizes = (value["artifactBytes"], value["encryptedPayloadBytes"], value["payloadBytes"])
    return (
        (value["platform"], value["arch"]) in {("macos", "arm64"), ("macos", "x64"), ("windows", "x64")}
        and isinstance(value["artifactUrl"], str)
        and all(isinstance(item, int) and not isinstance(item, bool) for item in sizes)
        and 1 <= sizes[0] <= MAX_ARCHIVE_BYTES
        and 1 <= sizes[2] <= MAX_PAYLOAD_BYTES
        and sizes[1] == sizes[2] + 32 <= MAX_ENCRYPTED_PAYLOAD_BYTES
        and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in hashes)
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _b64url(value: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ReleaseError("SIGNATURE_INVALID: malformed base64url")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as error:
        raise ReleaseError("SIGNATURE_INVALID: malformed compact JWS") from error


def verify_jws(compact: str, keys: dict[str, str], expected_type: str) -> tuple[dict, str]:
    try:
        protected_raw, payload_raw, signature_raw = compact.split(".")
        protected = json.loads(_b64url(protected_raw))
        payload = json.loads(_b64url(payload_raw))
        if not isinstance(protected, dict) or not isinstance(payload, dict):
            raise ValueError("JWS objects required")
        kid = protected["kid"]
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise ReleaseError("SIGNATURE_INVALID: malformed compact JWS") from error
    if protected.get("alg") != "EdDSA" or protected.get("typ") != expected_type or protected.get("crit"):
        raise ReleaseError("SIGNATURE_INVALID: unsupported JWS header")
    if kid not in keys:
        raise ReleaseError("SIGNATURE_INVALID: unknown signing key")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(_b64url(keys[kid]))
        public_key.verify(_b64url(signature_raw), f"{protected_raw}.{payload_raw}".encode("ascii"))
    except Exception as error:
        raise ReleaseError("SIGNATURE_INVALID: signature verification failed") from error
    return payload, kid


def decrypt_payload(source: Path, destination: Path, key_b64: str, version: str) -> None:
    encrypted = source.read_bytes()
    if len(encrypted) < 4 + 12 + 16 or encrypted[:4] != MAGIC:
        raise ReleaseError("PACKAGE_HASH_MISMATCH: invalid encrypted payload")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = _b64url(key_b64)
        if len(key) != 32:
            raise ValueError("invalid AES key length")
        clear = AESGCM(key).decrypt(
            encrypted[4:16], encrypted[16:], f"{PRODUCT_CODE}\n{version}".encode("utf-8")
        )
    except Exception as error:
        raise ReleaseError("PACKAGE_HASH_MISMATCH: payload authentication failed") from error
    destination.write_bytes(clear)


def safe_extract(source: Path, destination: Path) -> None:
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ReleaseError("Archive exceeds the 512 MiB limit")
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES:
            raise ReleaseError("Archive contains too many files")
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            path = PurePosixPath(name)
            mode = entry.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if (
                not name
                or path.is_absolute()
                or ".." in path.parts
                or (path.parts and ":" in path.parts[0])
                or stat.S_ISLNK(mode)
                or (file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))
            ):
                raise ReleaseError(f"Unsafe archive entry: {name!r}")
            normalized = str(path).rstrip("/").casefold()
            if normalized in seen:
                raise ReleaseError(f"Duplicate archive target: {name!r}")
            seen.add(normalized)
            total += entry.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise ReleaseError("Expanded archive exceeds the 500 MiB limit")
        for entry in entries:
            target = destination.joinpath(*PurePosixPath(entry.filename.replace("\\", "/")).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source_handle, target.open("xb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)


def extract_payload_entry(source: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            matches = [entry for entry in archive.infolist() if entry.filename == PAYLOAD_ENTRY and not entry.is_dir()]
            if len(matches) != 1:
                raise ReleaseError(f"Release archive must contain exactly one {PAYLOAD_ENTRY}")
            with archive.open(matches[0]) as source_handle, destination.open("xb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
    except (OSError, zipfile.BadZipFile) as error:
        raise ReleaseError("Release archive is not a valid ZIP") from error


def load_trusted_keys() -> dict[str, str]:
    path = Path(__file__).with_name("trusted_keys.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError("SIGNATURE_INVALID: trusted key set is unavailable") from error
    if not isinstance(value, dict) or not value:
        raise ReleaseError("SIGNATURE_INVALID: no release signing key is configured")
    return {str(key): str(item) for key, item in value.items()}


def install_encrypted_payload(
    payload_path: Path,
    payload_key: str,
    license_jws: str,
    manifest_jws: str,
    grant: dict,
) -> Path:
    config = read_config()
    keys = load_trusted_keys()
    manifest, _ = verify_jws(manifest_jws, keys, "NEXSCOPE-RELEASE")
    license_claims, _ = verify_jws(license_jws, keys, "NEXSCOPE-LICENSE")
    version = str(manifest.get("version", ""))
    manifest_fields = {"schemaVersion", "productCode", "version", "minInstallerVersion", "artifacts"}
    artifacts = manifest.get("artifacts")
    pairs = [(item.get("platform"), item.get("arch")) for item in artifacts] if isinstance(artifacts, list) else []
    platform_name = license_claims.get("platform")
    arch = license_claims.get("arch")
    matches = [item for item in artifacts or [] if isinstance(item, dict)
               and item.get("platform") == platform_name and item.get("arch") == arch]
    if (
        set(manifest) != manifest_fields
        or not VERSION_RE.fullmatch(version)
        or not VERSION_RE.fullmatch(str(manifest.get("minInstallerVersion", "")))
        or not isinstance(artifacts, list) or not artifacts or any(not _valid_artifact(item) for item in artifacts)
        or pairs != sorted(set(pairs))
        or any(pair not in {("macos", "arm64"), ("macos", "x64"), ("windows", "x64")} for pair in pairs)
        or manifest.get("schemaVersion") != 1
        or set(license_claims) != LICENSE_FIELDS
        or license_claims.get("schemaVersion") != 1
        or manifest.get("productCode") != PRODUCT_CODE
        or license_claims.get("productCode") != PRODUCT_CODE
        or license_claims.get("installationId") != config.get("installationId")
        or license_claims.get("version") != version
        or license_claims.get("usagePolicy") != "INSTALLED_VERSION_SURVIVES_EXPIRY"
        or str(license_claims.get("releaseId")) != str(grant.get("releaseId"))
        or str(grant.get("version")) != version
        or len(matches) != 1
    ):
        raise ReleaseError("SIGNATURE_INVALID: signed claims do not match this installation")
    artifact = matches[0]
    if (
        license_claims.get("payloadSha256") != artifact.get("payloadSha256")
        or str(grant.get("platform")) != platform_name
        or str(grant.get("arch")) != arch
        or any(grant.get(field) != artifact.get(field) for field in ARTIFACT_FIELDS - {"platform", "arch"})
    ):
        raise ReleaseError("SIGNATURE_INVALID: release artifact does not match the grant")
    if payload_path.stat().st_size != artifact.get("encryptedPayloadBytes") or sha256_file(payload_path) != artifact.get("encryptedPayloadSha256"):
        raise ReleaseError("PACKAGE_HASH_MISMATCH: encrypted payload digest differs")
    root = data_dir()
    versions = root / "releases"
    target = versions / version
    if target.exists():
        raise ReleaseError("Release is already installed")
    versions.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="install-", dir=versions))
    clear_zip = work / "payload.zip"
    extracted = work / "plugin"
    state_path = root / "state.json"
    license_path = root / "licenses" / f"{version}.json"
    try:
        decrypt_payload(payload_path, clear_zip, payload_key, version)
        expected_clear = str(artifact.get("payloadSha256", ""))
        if clear_zip.stat().st_size != artifact.get("payloadBytes") or len(expected_clear) != 64 or sha256_file(clear_zip) != expected_clear:
            raise ReleaseError("PACKAGE_HASH_MISMATCH: clear payload digest differs")
        safe_extract(clear_zip, extracted)
        clear_zip.unlink()
        os.replace(extracted, target)
        shutil.rmtree(work, ignore_errors=True)
        write_json_atomic(state_path, {
            "schemaVersion": 1,
            "status": "extracted",
            "pendingVersion": version,
            "previousVersion": config.get("currentVersion"),
        })
        (root / "licenses").mkdir(parents=True, exist_ok=True)
        write_json_atomic(license_path, {"licenseJws": license_jws})
        return target
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if config.get("currentVersion"):
            write_json_atomic(state_path, {
                "schemaVersion": 1,
                "currentVersion": config["currentVersion"],
                "status": "installed",
            })
        else:
            state_path.unlink(missing_ok=True)
        license_path.unlink(missing_ok=True)
        write_json_atomic(root / "config.json", config)
        raise
