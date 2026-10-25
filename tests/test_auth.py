from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from traqen.app import create_app
from traqen.config import load_settings

from .conftest import csrf_of, sign_in


def test_pages_require_sign_in(app):
    with TestClient(app) as client:
        response = client.get("/traqs/1", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/traqs/1"


def test_google_sign_in_creates_user_and_returns_to_next(app, mock_http):
    with TestClient(app) as client:
        response = sign_in(client, mock_http, next="/archive")
        assert response.status_code == 303
        assert response.headers["location"] == "/archive"
        assert client.get("/").status_code == 200


def test_sign_in_rejects_mismatched_nonce(app, mock_http):
    with TestClient(app) as client:
        response = sign_in(client, mock_http, nonce="not-the-one-we-sent")
        assert response.status_code == 400
        assert client.get("/", follow_redirects=False).status_code == 303


def test_callback_without_started_sign_in_is_rejected(app):
    with TestClient(app) as client:
        response = client.get("/auth/google/callback?code=x&state=y")
        assert response.status_code == 400


def test_login_next_cannot_leave_the_site(app, mock_http):
    with TestClient(app) as client:
        response = sign_in(client, mock_http, next="//evil.example")
        assert response.headers["location"] == "/"


def test_forms_require_csrf_token(session):
    response = session.client.post("/traqs", data={"text": "x"}, follow_redirects=False)
    assert response.status_code == 403
    response = session.client.post(
        "/traqs", data={"text": "x", "csrf": "wrong"}, follow_redirects=False
    )
    assert response.status_code == 403


def test_session_for_deleted_user_signs_out_instead_of_looping(session, db):
    db.execute("DELETE FROM users")
    response = session.get("/")
    assert response.headers["location"] == "/login?next=/"
    assert session.get("/login").status_code == 200


def signed_out_app(settings):
    """The app with sign-in off, as run on your own computer."""
    local = replace(settings, google=None, base_url="http://localhost:8000")
    return create_app(local, background=False)


def browser(app, host="localhost:8000", client="127.0.0.1"):
    return TestClient(app, base_url=f"http://{host}", client=(client, 50000))


def test_sign_in_off_uses_one_local_user(settings):
    app = signed_out_app(settings)
    with browser(app) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 200
        assert "Sign out" not in home.text
        assert client.get("/login", follow_redirects=False).headers["location"] == "/"
        assert client.get("/auth/google").status_code == 404

        created = client.post(
            "/traqs",
            data={"csrf": csrf_of(client), "text": "Local traq"},
            follow_redirects=False,
        )
        assert created.status_code == 303
    with browser(app) as other_browser:
        assert "Local traq" in other_browser.get("/").text


def test_sign_in_off_is_refused_off_localhost(monkeypatch):
    monkeypatch.setenv("TRAQEN_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("TRAQEN_ENCRYPTION_KEY", "k")
    monkeypatch.setenv("TRAQEN_SIGN_IN", "none")
    monkeypatch.setenv("TRAQEN_BASE_URL", "https://traqen.example.com")
    with pytest.raises(
        RuntimeError, match="only allowed when TRAQEN_BASE_URL is localhost"
    ):
        load_settings()
    monkeypatch.setenv("TRAQEN_BASE_URL", "http://localhost:8000")
    assert load_settings().sign_in_enabled is False


@pytest.mark.parametrize(
    ("host", "client", "allowed"),
    [
        ("localhost:8000", "127.0.0.1", True),
        ("127.0.0.1:8000", "127.0.0.1", True),
        # A page that points its own domain at 127.0.0.1 (DNS rebinding).
        ("attacker.example:8000", "127.0.0.1", False),
        # Another machine on the network.
        ("localhost:8000", "192.168.1.20", False),
    ],
)
def test_with_sign_in_off_only_this_computer_gets_in(settings, host, client, allowed):
    with browser(signed_out_app(settings), host, client) as visitor:
        response = visitor.get("/")
    assert (response.status_code == 200) == allowed
    assert ("Everything" in response.text) == allowed
