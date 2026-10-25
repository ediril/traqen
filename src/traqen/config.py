import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class Settings:
    base_url: str
    database_path: Path
    session_secret: str
    encryption_key: str
    refresh_minutes: int
    # None means sign-in is off: everyone is one local user (localhost only).
    google: OAuthClient | None
    github: OAuthClient | None
    atlassian: OAuthClient | None

    @property
    def sign_in_enabled(self) -> bool:
        return self.google is not None

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set (see .env.example)")
    return value


def _optional_client(prefix: str) -> OAuthClient | None:
    client_id = os.environ.get(f"{prefix}_CLIENT_ID", "").strip()
    client_secret = os.environ.get(f"{prefix}_CLIENT_SECRET", "").strip()
    if not client_id and not client_secret:
        return None
    if not client_id or not client_secret:
        raise RuntimeError(
            f"{prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET must be set together"
        )
    return OAuthClient(client_id, client_secret)


def _sign_in_client(base_url: str) -> OAuthClient | None:
    mode = os.environ.get("TRAQEN_SIGN_IN", "google").strip()
    if mode == "google":
        return OAuthClient(
            _required("GOOGLE_CLIENT_ID"), _required("GOOGLE_CLIENT_SECRET")
        )
    if mode != "none":
        raise RuntimeError("TRAQEN_SIGN_IN must be 'google' or 'none'")
    if urlsplit(base_url).hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError(
            "TRAQEN_SIGN_IN=none is only allowed when TRAQEN_BASE_URL is localhost"
        )
    return None


def load_settings() -> Settings:
    load_dotenv()
    base_url = os.environ.get("TRAQEN_BASE_URL", "http://localhost:8000").rstrip("/")
    return Settings(
        base_url=base_url,
        database_path=Path(os.environ.get("TRAQEN_DATABASE", "traqen.db")),
        session_secret=_required("TRAQEN_SESSION_SECRET"),
        encryption_key=_required("TRAQEN_ENCRYPTION_KEY"),
        refresh_minutes=int(os.environ.get("TRAQEN_REFRESH_MINUTES", "15")),
        google=_sign_in_client(base_url),
        github=_optional_client("GITHUB"),
        atlassian=_optional_client("ATLASSIAN"),
    )
