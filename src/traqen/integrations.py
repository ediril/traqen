"""Per-user OAuth connections to work tools (GitHub, Jira Cloud).

Each user authorizes their own account; Traqen only reads what that user can
already see. Tokens are stored encrypted and refreshed here.
"""

import asyncio
import sqlite3
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from . import db as dbm
from .config import OAuthClient, Settings
from .crypto import TokenCipher
from .live.state import ConnectionRevoked

GITHUB_SCOPE = "repo"  # GitHub has no read-only scope that covers private repos
JIRA_SCOPE = "read:jira-work offline_access"

PROVIDERS = {"github": "GitHub", "jira": "Jira"}


class OAuthError(Exception):
    pass


def client_for(settings: Settings, provider: str) -> OAuthClient | None:
    return settings.github if provider == "github" else settings.atlassian


def redirect_uri(settings: Settings, provider: str) -> str:
    return f"{settings.base_url}/connections/{provider}/callback"


def authorize_url(settings: Settings, provider: str, state: str) -> str:
    oauth = client_for(settings, provider)
    if provider == "github":
        query = {
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri(settings, provider),
            "scope": GITHUB_SCOPE,
            "state": state,
            "allow_signup": "false",
        }
        return "https://github.com/login/oauth/authorize?" + urlencode(query)
    query = {
        "audience": "api.atlassian.com",
        "client_id": oauth.client_id,
        "scope": JIRA_SCOPE,
        "redirect_uri": redirect_uri(settings, provider),
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return "https://auth.atlassian.com/authorize?" + urlencode(query)


async def exchange_code(
    settings: Settings, provider: str, code: str, client: httpx.AsyncClient
) -> dict:
    oauth = client_for(settings, provider)
    if provider == "github":
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": oauth.client_id,
                "client_secret": oauth.client_secret,
                "code": code,
                "redirect_uri": redirect_uri(settings, provider),
            },
            headers={"Accept": "application/json"},
        )
    else:
        response = await client.post(
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": oauth.client_id,
                "client_secret": oauth.client_secret,
                "code": code,
                "redirect_uri": redirect_uri(settings, provider),
            },
        )
    tokens = response.json() if response.content else {}
    if response.status_code != 200 or "access_token" not in tokens:
        detail = (
            tokens.get("error_description")
            or tokens.get("error")
            or response.status_code
        )
        raise OAuthError(f"{PROVIDERS[provider]} sign-in failed: {detail}")
    return tokens


def save_connection(
    db: sqlite3.Connection,
    cipher: TokenCipher,
    user_id: int,
    provider: str,
    tokens: dict,
    meta: dict | None = None,
) -> None:
    """Save a (re)connection. Its metadata starts over, so e.g. Jira's sites are
    discovered again, in case different ones were picked this time."""
    refresh = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in")
    db.execute(
        """
        INSERT INTO connections (user_id, provider, access_token,
            refresh_token, expires_at, meta, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?)
        ON CONFLICT (user_id, provider) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at,
            meta = excluded.meta,
            status = 'ok',
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            provider,
            cipher.encrypt(tokens["access_token"]),
            cipher.encrypt(refresh) if refresh else None,
            dbm.stamp(dbm.now() + timedelta(seconds=int(expires_in)))
            if expires_in
            else None,
            dbm.dump_json(meta or {}),
            dbm.stamp(),
            dbm.stamp(),
        ),
    )


# Token refreshes are serialized per user and provider across the whole process:
# Atlassian rotates refresh tokens, so two concurrent refreshes (a page load and
# the background refresher) would invalidate each other.
_refresh_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _refresh_lock(user_id: int, provider: str) -> asyncio.Lock:
    return _refresh_locks.setdefault((user_id, provider), asyncio.Lock())


class Credentials:
    """Valid access tokens for one user's connections during a refresh run."""

    def __init__(
        self,
        db: sqlite3.Connection,
        cipher: TokenCipher,
        settings: Settings,
        client: httpx.AsyncClient,
        user_id: int,
    ):
        self.db, self.cipher, self.settings = db, cipher, settings
        self.client, self.user_id = client, user_id

    def _row(self, provider: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM connections WHERE user_id = ? AND provider = ?",
            (self.user_id, provider),
        ).fetchone()

    def status(self, provider: str) -> str | None:
        row = self._row(provider)
        return row["status"] if row else None

    def meta(self, provider: str) -> dict:
        row = self._row(provider)
        return dbm.load_json(row["meta"]) if row else {}

    def save_meta(self, provider: str, meta: dict) -> None:
        self.db.execute(
            "UPDATE connections SET meta = ?, updated_at = ? "
            "WHERE user_id = ? AND provider = ?",
            (dbm.dump_json(meta), dbm.stamp(), self.user_id, provider),
        )

    async def token(self, provider: str) -> str | None:
        """Current access token, refreshed if expired; None if not usable."""
        async with _refresh_lock(self.user_id, provider):
            row = self._row(provider)
            if not row or row["status"] != "ok":
                return None
            expires = dbm.parse_time(row["expires_at"])
            if expires and expires - timedelta(seconds=60) <= dbm.now():
                return await self._refresh(row)
            return self.cipher.decrypt(row["access_token"])

    async def refreshed_token(self, provider: str, rejected: str) -> str:
        """Called after the API rejected `rejected`; refresh or give up."""
        async with _refresh_lock(self.user_id, provider):
            row = self._row(provider)
            if row and row["status"] == "ok":
                current = self.cipher.decrypt(row["access_token"])
                if current != rejected:
                    return current  # another check already refreshed it
                if row["refresh_token"]:
                    return await self._refresh(row)
            self.revoke(provider)
            raise ConnectionRevoked(provider)

    async def _refresh(self, row: sqlite3.Row) -> str:
        provider = row["provider"]
        if provider != "jira" or not row["refresh_token"]:
            self.revoke(provider)
            raise ConnectionRevoked(provider)
        oauth = self.settings.atlassian
        response = await self.client.post(
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": oauth.client_id,
                "client_secret": oauth.client_secret,
                "refresh_token": self.cipher.decrypt(row["refresh_token"]),
            },
        )
        if response.status_code in (400, 401, 403):
            self.revoke(provider)
            raise ConnectionRevoked(provider)
        response.raise_for_status()
        tokens = response.json()
        # Atlassian rotates refresh tokens: the old one stops working once used.
        self.db.execute(
            """
            UPDATE connections SET access_token = ?, refresh_token = ?,
                expires_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                self.cipher.encrypt(tokens["access_token"]),
                self.cipher.encrypt(tokens.get("refresh_token"))
                if tokens.get("refresh_token")
                else row["refresh_token"],
                dbm.stamp(dbm.now() + timedelta(seconds=int(tokens["expires_in"]))),
                dbm.stamp(),
                row["id"],
            ),
        )
        return tokens["access_token"]

    def revoke(self, provider: str) -> None:
        self.db.execute(
            "UPDATE connections SET status = 'reconnect', updated_at = ? "
            "WHERE user_id = ? AND provider = ?",
            (dbm.stamp(), self.user_id, provider),
        )
