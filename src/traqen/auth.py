"""Signing in to Traqen with Google, sessions, and CSRF protection."""

import base64
import json
import secrets
import sqlite3
from collections.abc import Iterator
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db as dbm
from .templating import csrf_token, templates

GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

router = APIRouter()


class LoginRequired(Exception):
    def __init__(self, next_url: str):
        self.next_url = next_url


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    db = dbm.connect(request.app.state.settings.database_path)
    try:
        yield db
    finally:
        db.close()


def current_user(
    request: Request, db: sqlite3.Connection = Depends(get_db)
) -> sqlite3.Row:
    if not request.app.state.settings.sign_in_enabled:
        return _local_user(request, db)
    user_id = request.session.get("user_id")
    user = (
        db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_id
        else None
    )
    if user is None:
        request.session.clear()
        target = request.url.path + (
            f"?{request.url.query}" if request.url.query else ""
        )
        raise LoginRequired(target)
    return user


LOCAL_SUB = "local"


def _local_user(request: Request, db: sqlite3.Connection) -> sqlite3.Row:
    """With sign-in off, everyone is the same local user."""
    db.execute(
        "INSERT INTO users (google_sub, email, name, created_at) "
        "VALUES (?, 'local@localhost', 'Local user', ?) "
        "ON CONFLICT (google_sub) DO NOTHING",
        (LOCAL_SUB, dbm.stamp()),
    )
    user = db.execute(
        "SELECT * FROM users WHERE google_sub = ?", (LOCAL_SUB,)
    ).fetchone()
    request.session["user_id"] = user["id"]
    return user


def _google(request: Request):
    settings = request.app.state.settings
    if not settings.sign_in_enabled:
        raise HTTPException(404, "Sign-in is turned off on this server")
    return settings


async def require_csrf(request: Request) -> None:
    form = await request.form()
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(str(form.get("csrf", "")), expected):
        raise HTTPException(403, "This form expired. Go back, reload, and try again.")


CSRF = [Depends(require_csrf)]


def safe_next(value: str | None, default: str = "/") -> str:
    """Only allow redirects to paths on this site."""
    if value and value.startswith("/") and not value.startswith(("//", "/\\")):
        return value
    return default


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if request.session.get("user_id") or not request.app.state.settings.sign_in_enabled:
        return RedirectResponse(safe_next(next), 303)
    return templates.TemplateResponse(
        request, "login.html", {"next": safe_next(next), "error": None}
    )


@router.get("/auth/google")
def google_start(request: Request, next: str = "/"):
    settings = _google(request)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    request.session["google_oauth"] = {
        "state": state,
        "nonce": nonce,
        "next": safe_next(next),
    }
    query = {
        "client_id": settings.google.client_id,
        "redirect_uri": f"{settings.base_url}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTHORIZE}?{urlencode(query)}", 303)


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: sqlite3.Connection = Depends(get_db),
):
    settings = _google(request)
    pending = request.session.pop("google_oauth", None)
    if (
        error
        or not pending
        or not code
        or not secrets.compare_digest(state, pending["state"])
    ):
        return _login_error(request, "Sign-in was cancelled or expired. Try again.")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": settings.google.client_id,
                "client_secret": settings.google.client_secret,
                "redirect_uri": f"{settings.base_url}/auth/google/callback",
                "grant_type": "authorization_code",
            },
        )
    if response.status_code != 200 or "id_token" not in response.json():
        return _login_error(request, "Google sign-in failed. Try again.")

    # The ID token came straight from Google's token endpoint over TLS, so its
    # signature need not be re-verified (OpenID Connect Core 3.1.3.7); the
    # claims below still must match this sign-in.
    claims = _jwt_claims(response.json()["id_token"])
    valid = (
        claims.get("iss") in GOOGLE_ISSUERS
        and claims.get("aud") == settings.google.client_id
        and claims.get("exp", 0) > dbm.now().timestamp()
        and secrets.compare_digest(str(claims.get("nonce", "")), pending["nonce"])
        and claims.get("email_verified") is True
    )
    if not valid:
        return _login_error(request, "Google sign-in could not be verified.")

    email = claims["email"]
    name = claims.get("name") or email
    db.execute(
        """
        INSERT INTO users (google_sub, email, name, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT (google_sub) DO UPDATE SET email = excluded.email,
            name = excluded.name
        """,
        (claims["sub"], email, name, dbm.stamp()),
    )
    user_id = db.execute(
        "SELECT id FROM users WHERE google_sub = ?", (claims["sub"],)
    ).fetchone()["id"]

    request.session.clear()
    request.session["user_id"] = user_id
    csrf_token(request)
    return RedirectResponse(pending["next"], 303)


@router.post("/logout", dependencies=CSRF)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


def _login_error(request: Request, message: str):
    return templates.TemplateResponse(
        request, "login.html", {"next": "/", "error": message}, status_code=400
    )


def _jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))
