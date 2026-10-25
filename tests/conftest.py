import base64
import json
import time
from contextlib import ExitStack
from urllib.parse import parse_qs, urlsplit

import pytest
import respx
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from traqen import db as dbm
from traqen.app import create_app
from traqen.config import OAuthClient, Settings

GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        base_url="http://testserver",
        database_path=tmp_path / "traqen.db",
        session_secret="test-session-secret-" + "x" * 32,
        encryption_key=Fernet.generate_key().decode(),
        refresh_minutes=15,
        google=OAuthClient("google-client", "google-secret"),
        github=OAuthClient("github-client", "github-secret"),
        atlassian=OAuthClient("atlassian-client", "atlassian-secret"),
    )


@pytest.fixture
def app(settings):
    return create_app(settings, background=False)


@pytest.fixture
def mock_http():
    # Only outbound calls made by the app go through respx; TestClient requests
    # use their own in-process transport.
    with respx.mock(assert_all_called=False) as router:
        yield router


def id_token(claims: dict) -> str:
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


def sign_in(
    client: TestClient,
    mock_http,
    sub="user-1",
    email="ana@example.com",
    nonce=None,
    next="/",
):
    start = client.get("/auth/google", params={"next": next}, follow_redirects=False)
    query = parse_qs(urlsplit(start.headers["location"]).query)
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "google-client",
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0].title(),
        "exp": int(time.time()) + 600,
        "nonce": nonce or query["nonce"][0],
    }
    mock_http.post(GOOGLE_TOKEN).respond(json={"id_token": id_token(claims)})
    return client.get(
        f"/auth/google/callback?code=abc&state={query['state'][0]}",
        follow_redirects=False,
    )


def html(text: str) -> BeautifulSoup:
    return BeautifulSoup(text, "html.parser")


def csrf_of(client: TestClient) -> str:
    return html(client.get("/").text).select_one("input[name=csrf]")["value"]


@pytest.fixture
def db(settings):
    connection = dbm.connect(settings.database_path)
    yield connection
    connection.close()


class Session:
    """A signed-in browser session with helpers for form posts."""

    def __init__(self, client: TestClient, mock_http, db, **user):
        self.client = client
        self.mock_http = mock_http
        self.db = db
        assert sign_in(client, mock_http, **user).status_code == 303
        self.csrf = csrf_of(client)

    def post(self, path, **data):
        return self.client.post(
            path, data={"csrf": self.csrf, **data}, follow_redirects=False
        )

    def get(self, path):
        return self.client.get(path, follow_redirects=False)

    def page(self, path) -> BeautifulSoup:
        return html(self.get(path).text)

    def create_traq(self, text="Acme blocked by SCIM support", **fields) -> int:
        """Create a traq like the New traq dialog does; returns its id."""
        response = self.post("/traqs", text=text, **fields)
        assert response.status_code == 303, response.text
        return self.db.execute("SELECT max(id) FROM traqs").fetchone()[0]

    def recheck(self, traq_id: int) -> str:
        """Make the traq's links due for a check and run it, as the traq page
        does after loading; returns the refreshed content."""
        self.db.execute(
            "UPDATE links SET checked_at = NULL WHERE traq_id = ?", (traq_id,)
        )
        return self.get(f"/traqs/{traq_id}/live").text


@pytest.fixture
def sign_in_as(app, mock_http, db):
    """Signs in another browser: `sign_in_as("ben")` is ben@example.com."""
    with ExitStack() as stack:

        def browser(name: str) -> Session:
            client = stack.enter_context(TestClient(app))
            return Session(
                client, mock_http, db, sub=f"user-{name}", email=f"{name}@example.com"
            )

        yield browser


@pytest.fixture
def session(sign_in_as):
    return sign_in_as("ana")
