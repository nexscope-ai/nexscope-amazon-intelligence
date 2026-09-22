"""System credential-store access shared by every NexScope skill script."""

from __future__ import annotations

import json
import os
import platform
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, build_opener, install_opener

PRODUCT_CODE = "nexscope-amazon-intelligence"
SERVICE_NAME = "nexscope.codex.nexscope-amazon-intelligence"
DEFAULT_API_BASE = "https://api.nexscope.ai/"
ALLOWED_API_HOSTS = {"api.nexscope.ai"}


class CredentialError(RuntimeError):
    """A safe, non-secret credential error."""


class AuthenticatedSameOriginRedirects(HTTPRedirectHandler):
    """Never forward an Authorization header to another origin."""

    @staticmethod
    def _origin(value: str) -> tuple[str, str | None, int | None]:
        parsed = urlparse(value)
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        target = urljoin(request.full_url, new_url)
        if request.get_header("Authorization") and self._origin(target) != self._origin(request.full_url):
            raise CredentialError("Authenticated request redirect left its origin")
        return super().redirect_request(request, file_pointer, code, message, headers, target)


def data_dir() -> Path:
    override = os.environ.get("NEXSCOPE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "NexScope" / "Codex"
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA", "").strip()
        if not root:
            raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: LOCALAPPDATA is not set")
        return Path(root) / "NexScope" / "Codex"
    raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: only macOS and Windows are supported")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise CredentialError(f"Invalid local configuration: {path.name}") from error
    if not isinstance(value, dict):
        raise CredentialError(f"Invalid local configuration: {path.name}")
    return value


def read_config() -> dict:
    return _read_json(data_dir() / "config.json")


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def validate_api_base(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_API_HOSTS or parsed.username or parsed.password:
        raise CredentialError("Configured API base is not an allowed NexScope HTTPS endpoint")
    if parsed.query or parsed.fragment:
        raise CredentialError("Configured API base must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path == "/api":
        path = ""
    return parsed._replace(path=path + "/", params="", query="", fragment="").geturl()


def ensure_config(api_base: str = DEFAULT_API_BASE) -> dict:
    path = data_dir() / "config.json"
    current = _read_json(path)
    installation_id = str(current.get("installationId", ""))
    try:
        parsed = uuid.UUID(installation_id)
        valid_id = parsed.version == 4 and installation_id == str(parsed)
    except ValueError:
        valid_id = False
    if not valid_id:
        installation_id = str(uuid.uuid4())
    config = {
        "schemaVersion": 1,
        "installationId": installation_id,
        "productCode": PRODUCT_CODE,
        "apiBase": validate_api_base(str(current.get("apiBase") or api_base)),
        "currentVersion": current.get("currentVersion"),
        "credentialMode": "keyring",
    }
    write_json_atomic(path, config)
    return config


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError, NoKeyringError
    except ImportError as error:
        raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: install the bundled keyring dependency") from error
    try:
        backend = keyring.get_keyring()
        priority = getattr(backend, "priority", 0)
        if not isinstance(priority, (int, float)) or priority <= 0:
            raise NoKeyringError("no secure keyring backend")
    except (KeyringError, NoKeyringError, RuntimeError) as error:
        raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: unlock or configure the system credential store") from error
    backend_name = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    if "plaintext" in backend_name or "file" in backend_name or backend_name.startswith("keyrings.alt"):
        raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: plaintext credential backends are forbidden")
    return keyring


def store(installation_token: str, credential_revision: int, installation_id: str) -> None:
    if not installation_token.startswith("npi_") or credential_revision < 1:
        raise CredentialError("Invalid credential response")
    secret = json.dumps({
        "installationToken": installation_token,
        "credentialRevision": credential_revision,
    }, separators=(",", ":"))
    try:
        _keyring().set_password(SERVICE_NAME, installation_id, secret)
    except Exception as error:
        raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: could not save credentials") from error


def load(config: dict | None = None) -> dict:
    config = config or read_config()
    installation_id = str(config.get("installationId", ""))
    if not installation_id:
        raise CredentialError("No connected NexScope installation")
    try:
        keyring = _keyring()
        raw = keyring.get_password(SERVICE_NAME, installation_id)
        value = json.loads(raw) if raw else None
    except Exception as error:
        raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: could not read credentials") from error
    if not isinstance(value, dict) or not str(value.get("installationToken", "")).startswith("npi_"):
        raise CredentialError("No connected NexScope credentials")
    try:
        credential_revision = int(value.get("credentialRevision", 0))
    except (TypeError, ValueError) as error:
        raise CredentialError("No connected NexScope credentials") from error
    if credential_revision < 1:
        raise CredentialError("No connected NexScope credentials")
    secret = {
        "installationToken": value["installationToken"],
        "credentialRevision": credential_revision,
    }
    if "apiKey" in value:
        try:
            keyring.set_password(SERVICE_NAME, installation_id, json.dumps(secret, separators=(",", ":")))
        except Exception as error:
            raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: could not remove legacy credentials") from error
    return secret


def delete(config: dict | None = None) -> None:
    config = config or read_config()
    installation_id = str(config.get("installationId", ""))
    if not installation_id:
        return
    try:
        _keyring().delete_password(SERVICE_NAME, installation_id)
    except Exception as error:
        if error.__class__.__name__ != "PasswordDeleteError":
            raise CredentialError("CREDENTIAL_STORE_UNAVAILABLE: could not remove credentials") from error


def activate() -> None:
    """Load managed credentials into this process before legacy scripts read env."""
    install_opener(build_opener(AuthenticatedSameOriginRedirects()))
    override = os.environ.get("NEXSCOPE_AUTH_MODE", "").strip().lower()
    if override == "environment":
        return
    config = read_config()
    mode = override or str(config.get("credentialMode", "keyring")).lower()
    if mode != "keyring":
        raise CredentialError("NEXSCOPE_AUTH_MODE must be 'environment' or 'keyring'")
    secret = load(config)
    os.environ["NEXSCOPE_API_KEY"] = str(secret["installationToken"])
    os.environ["NEXSCOPE_PROXY_BASE"] = validate_api_base(str(config.get("apiBase") or DEFAULT_API_BASE))


def installation_token() -> str:
    return str(load().get("installationToken", ""))


def ensure_available() -> None:
    _keyring()
