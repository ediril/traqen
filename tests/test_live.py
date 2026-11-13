import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from traqen import db as dbm
from traqen import integrations
from traqen.app import create_app, refresh_due
from traqen.crypto import TokenCipher
from traqen.live import engine, jira, net

from .conftest import Session, html

PR_URL = "https://github.com/acme/api/pull/42"
GH = "api.github.com"
JIRA_URL = "https://acme.atlassian.net/browse/SCIM-12"
JIRA_API = "https://api.atlassian.com/ex/jira/cloud-1/rest/api/3/issue/SCIM-12"
PAGE_IP = "93.184.216.34"


def the_link(db):
    return db.execute("SELECT * FROM links").fetchone()


def connect(db, settings, provider, tokens, meta=None):
    user_id = db.execute("SELECT id FROM users").fetchone()["id"]
    cipher = TokenCipher(settings.encryption_key)
    integrations.save_connection(db, cipher, user_id, provider, tokens, meta)


def mock_pull(mock_http, *, reviews, comments, status=200):
    mock_http.get(host=GH, path="/repos/acme/api/issues/42").respond(
        status,
        json={
            "title": "Add SCIM endpoint",
            "state": "open",
            "assignees": [{"login": "ana"}],
            "comments": comments,
            "pull_request": {},
            "updated_at": "2026-10-01T10:00:00Z",
        },
    )
    mock_http.get(host=GH, path="/repos/acme/api/pulls/42").respond(
        json={
            "state": "open",
            "draft": False,
            "merged_at": None,
            "requested_reviewers": [{"login": "ben"}],
        }
    )
    mock_http.get(host=GH, path="/repos/acme/api/pulls/42/reviews").respond(
        json=reviews
    )


# GitHub ----------------------------------------------------------------------


def test_github_pull_request_is_live_and_changes_are_surfaced(session, db):
    mock_pull(session.mock_http, reviews=[], comments=1)
    traq_id = session.create_traq(PR_URL + " the fix")

    link = the_link(db)
    assert link["live"] == "live"
    assert link["title"] == "Add SCIM endpoint"
    state = dbm.load_json(link["state"])
    assert state["fields"] == {
        "State": "Open",
        "Review": "Review requested",
        "Assignee": "ana",
        "Comments": 1,
    }
    assert link["seen_state"] == link["state"]  # nothing new on first sight
    assert link["updated_at"] == "2026-10-01T10:00:00+00:00"  # GitHub's own time

    page = session.get(f"/traqs/{traq_id}").text
    assert "Add SCIM endpoint" in page and "Review requested" in page

    session.mock_http.routes.clear()
    mock_pull(
        session.mock_http,
        reviews=[{"state": "APPROVED", "user": {"login": "ben"}}],
        comments=3,
    )
    page = session.recheck(traq_id)
    assert "Review: Review requested → Approved" in page
    assert "2 new comments" in page
    # Looking again doesn't clear it; "Mark as seen" does.
    assert "Review requested → Approved" in session.get(f"/traqs/{traq_id}").text
    mark_seen(session, traq_id)
    assert "Review requested → Approved" not in session.get(f"/traqs/{traq_id}").text


def test_renamed_repository_is_followed(session, db):
    moved = "/repositories/123/issues/7"
    session.mock_http.get(host=GH, path="/repos/acme/old-api/issues/7").respond(
        301, headers={"location": f"https://{GH}{moved}"}
    )
    session.mock_http.get(host=GH, path=moved).respond(
        json={"title": "Fix login", "state": "open", "comments": 0}
    )
    session.create_traq("https://github.com/acme/old-api/issues/7")
    link = the_link(db)
    assert (link["live"], link["title"]) == ("live", "Fix login")


def test_private_github_link_without_connection_offers_to_connect(session, db):
    session.mock_http.get(host=GH, path="/repos/acme/secret/issues/1").respond(404)
    traq_id = session.create_traq("https://github.com/acme/secret/issues/1")
    link = the_link(db)
    assert link["live"] == "unavailable"
    assert "connect GitHub" in link["live_reason"]
    page = session.get(f"/traqs/{traq_id}").text
    assert f"/connections/github/start?next=/traqs/{traq_id}" in page


def test_github_uses_the_users_token(session, settings, db):
    connect(db, settings, "github", {"access_token": "gho_secret"})
    route = session.mock_http.get(host=GH, path="/repos/acme/api/issues/7").respond(
        json={
            "title": "Bug",
            "state": "closed",
            "state_reason": "completed",
            "assignees": [],
            "comments": 0,
        }
    )
    session.create_traq("https://github.com/acme/api/issues/7")
    assert route.calls.last.request.headers["authorization"] == "Bearer gho_secret"
    fields = dbm.load_json(the_link(db)["state"])["fields"]
    assert fields == {"State": "Closed", "Assignee": "Unassigned", "Comments": 0}


@pytest.mark.parametrize(
    ("sso_url", "shown"),
    [
        ("https://github.com/orgs/acme/sso?authorization_request=x", True),
        # Only GitHub's own pages are offered as the place to fix access.
        ("https://evil.example/sso", False),
    ],
)
def test_github_org_sso_requirement_links_to_authorization(
    session, settings, db, sso_url, shown
):
    connect(db, settings, "github", {"access_token": "gho_secret"})
    session.mock_http.get(host=GH, path="/repos/acme/api/issues/7").respond(
        403, headers={"X-GitHub-SSO": f"required; url={sso_url}"}
    )
    traq_id = session.create_traq("https://github.com/acme/api/issues/7")
    fix = session.page(f"/traqs/{traq_id}").find("a", string="Fix access")
    assert (fix["href"] if fix else None) == (sso_url if shown else None)


def test_revoked_github_token_asks_to_reconnect(session, settings, db):
    connect(db, settings, "github", {"access_token": "gho_revoked"})
    session.mock_http.get(host=GH, path="/repos/acme/api/issues/7").respond(401)
    session.create_traq("https://github.com/acme/api/issues/7")
    link = the_link(db)
    assert link["live"] == "unavailable"
    assert link["live_reason"] == "Reconnect GitHub to keep this live"
    status = db.execute("SELECT status FROM connections").fetchone()[0]
    assert status == "reconnect"


def test_rate_limit_keeps_last_known_state(session, db):
    mock_pull(session.mock_http, reviews=[], comments=1)
    traq_id = session.create_traq(PR_URL)
    link = the_link(db)

    session.mock_http.routes.clear()
    session.mock_http.get(host=GH).respond(403, headers={"x-ratelimit-remaining": "0"})
    page = session.recheck(traq_id)

    after = the_link(db)
    assert after["live"] == "error"
    assert "rate limit" in after["live_reason"]
    assert after["state"] == link["state"]
    assert "showing last known state" in page


# Jira ------------------------------------------------------------------------

JIRA_ISSUE = {
    "fields": {
        "summary": "SCIM provisioning",
        "status": {"name": "In Progress"},
        "assignee": {"displayName": "Ana Diaz"},
        "priority": {"name": "High"},
        "updated": "2026-10-01T10:00:00.000+0000",
        "comment": {"total": 4},
    }
}
ACME_SITE = {"id": "cloud-1", "url": "https://acme.atlassian.net", "name": "acme"}


def test_jira_site_the_connection_doesnt_cover_offers_to_reconnect(
    session, settings, db
):
    connect(
        db,
        settings,
        "jira",
        {"access_token": "jira-a", "refresh_token": "jira-r", "expires_in": 3600},
    )
    other = {"id": "cloud-2", "url": "https://other.atlassian.net"}
    session.mock_http.get(jira.RESOURCES).respond(json=[other])
    traq_id = session.create_traq(JIRA_URL)
    assert "doesn’t include acme.atlassian.net" in the_link(db)["live_reason"]
    button = session.page(f"/traqs/{traq_id}").find("a", string="Reconnect Jira")
    assert button["href"] == f"/connections/jira/start?next=/traqs/{traq_id}"


def test_jira_link_without_connection_offers_to_connect(session, db):
    traq_id = session.create_traq(JIRA_URL)
    link = the_link(db)
    assert (link["live"], link["live_reason"]) == (
        "unavailable",
        "Connect Jira to keep this live",
    )
    assert "Connect Jira" in session.get(f"/traqs/{traq_id}").text


def test_jira_issue_is_live(session, settings, db):
    connect(
        db,
        settings,
        "jira",
        {"access_token": "jira-a", "refresh_token": "jira-r", "expires_in": 3600},
        meta={"resources": [ACME_SITE]},
    )
    route = session.mock_http.get(url__startswith=JIRA_API).respond(json=JIRA_ISSUE)
    session.create_traq(JIRA_URL)

    assert route.calls.last.request.headers["authorization"] == "Bearer jira-a"
    link = the_link(db)
    assert link["title"] == "SCIM-12: SCIM provisioning"
    assert link["updated_at"] == "2026-10-01T10:00:00+00:00"  # Jira's own time
    assert dbm.load_json(link["state"])["fields"] == {
        "Status": "In Progress",
        "Assignee": "Ana Diaz",
        "Priority": "High",
        "Comments": 4,
    }


def test_expired_jira_token_is_refreshed_and_rotated(session, settings, db):
    connect(
        db,
        settings,
        "jira",
        {"access_token": "old-a", "refresh_token": "old-r", "expires_in": -10},
        meta={"resources": [ACME_SITE]},
    )
    token_route = session.mock_http.post(
        "https://auth.atlassian.com/oauth/token"
    ).respond(
        json={"access_token": "new-a", "refresh_token": "new-r", "expires_in": 3600}
    )
    issue_route = session.mock_http.get(url__startswith=JIRA_API).respond(
        json=JIRA_ISSUE
    )
    session.create_traq(JIRA_URL)

    assert (
        json.loads(token_route.calls.last.request.content)["refresh_token"] == "old-r"
    )
    assert issue_route.calls.last.request.headers["authorization"] == "Bearer new-a"
    row = db.execute("SELECT * FROM connections").fetchone()
    cipher = TokenCipher(settings.encryption_key)
    assert cipher.decrypt(row["refresh_token"]) == "new-r"
    assert the_link(db)["live"] == "live"


def test_jira_site_added_after_connecting_is_discovered(session, settings, db):
    connect(
        db,
        settings,
        "jira",
        {"access_token": "jira-a", "refresh_token": "jira-r", "expires_in": 3600},
        meta={"resources": []},
    )
    session.mock_http.get(jira.RESOURCES).respond(json=[ACME_SITE])
    session.mock_http.get(url__startswith=JIRA_API).respond(json=JIRA_ISSUE)
    session.create_traq(JIRA_URL)
    assert the_link(db)["live"] == "live"


# Public pages ----------------------------------------------------------------


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve example hosts to a public address; everything else for real."""
    real = net._public_address

    async def resolve(host, port):
        if host.endswith("example.com"):
            return PAGE_IP
        return await real(host, port)

    monkeypatch.setattr(net, "_public_address", resolve)


def page(title, body):
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><nav>Menu</nav><main>{body}</main></body></html>"
    )


def test_public_page_is_fetched_by_address_with_original_host(session, public_dns, db):
    route = session.mock_http.get(f"https://{PAGE_IP}/incidents/1").respond(
        html=page("Incident 1", "Investigating elevated errors")
    )
    traq_id = session.create_traq("https://status.example.com/incidents/1")
    request = route.calls.last.request
    assert request.headers["host"] == "status.example.com"
    assert request.extensions["sni_hostname"] == "status.example.com"

    link = the_link(db)
    assert link["title"] == "Incident 1"
    assert dbm.load_json(link["state"])["preview"] == "Investigating elevated errors"

    session.mock_http.routes.clear()
    session.mock_http.get(f"https://{PAGE_IP}/incidents/1").respond(
        html=page("Incident 1", "Resolved")
    )
    card = html(session.recheck(traq_id))
    assert [chip.text for chip in card.select("[data-change]")] == ["Updated"]


def test_text_a_visitor_cant_see_is_not_the_page(session, public_dns, db):
    def body(error):
        hidden = (
            f"<div hidden><span>{error}</span></div>"
            f'<p style="display: none">{error}</p>'
        )
        return page("Kagi", f"Search without ads{hidden}")

    session.mock_http.get(f"https://{PAGE_IP}/").respond(
        html=body("Verification failed")
    )
    traq_id = session.create_traq("https://kagi.example.com/")
    assert dbm.load_json(the_link(db)["state"])["preview"] == "Search without ads"
    # Hidden text changing isn't the page changing.
    session.mock_http.routes.clear()
    session.mock_http.get(f"https://{PAGE_IP}/").respond(html=body("Try again later"))
    assert html(session.recheck(traq_id)).select("[data-change]") == []


def mark_seen(session, traq_id, page=None):
    """Click "Mark as seen" on the (first) lit card, as shown on `page`."""
    page = page or session.page(f"/traqs/{traq_id}")
    form = page.select_one("[data-acknowledge]")
    return session.post(form["action"], shown=form.select_one("[name=shown]")["value"])


def test_a_change_stays_lit_until_you_clear_it(session, db, public_dns):
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "one"))
    traq_id = session.create_traq("https://status.example.com/x")

    def card():
        return session.page(f"/traqs/{traq_id}").select_one("[data-link-card]")

    assert "settled" in card()["class"]  # nothing new on first sight
    session.mock_http.routes.clear()
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "two"))
    session.recheck(traq_id)
    # Lit up, on every visit, until you say you've seen it.
    for _ in range(2):
        assert "changed" in card()["class"]
        assert [c.text for c in card().select("[data-change]")] == ["Updated"]
    assert mark_seen(session, traq_id).headers["location"] == f"/traqs/{traq_id}"
    assert "settled" in card()["class"] and not card().select("[data-change]")


def test_mark_as_seen_never_clears_a_change_you_havent_seen(session, db, public_dns):
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "one"))
    traq_id = session.create_traq("https://status.example.com/x")
    session.mock_http.routes.clear()
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "two"))
    session.recheck(traq_id)
    shown = session.page(f"/traqs/{traq_id}")
    # The page changes again after you opened it, then you click "Mark as seen".
    session.mock_http.routes.clear()
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "three"))
    session.recheck(traq_id)
    mark_seen(session, traq_id, shown)
    card = session.page(f"/traqs/{traq_id}").select_one("[data-link-card]")
    assert "changed" in card["class"]


def test_opening_a_traq_checks_links_not_checked_in_the_last_minute(
    session, db, public_dns
):
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "one"))
    traq_id = session.create_traq("https://status.example.com/x")
    assert not session.page(f"/traqs/{traq_id}").select_one("[data-live-url]")
    db.execute("UPDATE links SET checked_at = ?", (dbm.ago(timedelta(minutes=2)),))
    assert session.page(f"/traqs/{traq_id}").select_one("[data-live-url]")


def test_sign_in_pages_are_not_tracked(session, public_dns, db):
    session.mock_http.get(f"https://{PAGE_IP}/doc").respond(
        html=page("Sign in", '<form><input type="password"></form>')
    )
    session.create_traq("https://wiki.example.com/doc")
    link = the_link(db)
    assert (link["live"], link["live_reason"]) == (
        "unavailable",
        "This page requires signing in",
    )


def test_internal_addresses_are_never_fetched(session, public_dns, db):
    session.create_traq("http://127.0.0.1:8000/admin")
    link = the_link(db)
    assert link["live"] == "unavailable"
    assert "non-public" in link["live_reason"]


def test_redirects_into_internal_addresses_are_blocked(session, public_dns, db):
    session.mock_http.get(f"https://{PAGE_IP}/go").respond(
        302, headers={"location": "http://169.254.169.254/latest/meta-data"}
    )
    session.create_traq("https://short.example.com/go")
    assert "non-public" in the_link(db)["live_reason"]


def test_redirects_are_checked_even_if_the_client_follows_them(mock_http, public_dns):
    mock_http.get(f"https://{PAGE_IP}/go").respond(
        302, headers={"location": "http://169.254.169.254/latest/meta-data"}
    )
    secret = mock_http.get("http://169.254.169.254/latest/meta-data").respond(
        text="SECRET"
    )

    async def fetch():
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await net.safe_get(client, "https://short.example.com/go")

    with pytest.raises(net.BlockedURL):
        asyncio.run(fetch())
    assert not secret.called


def test_signed_in_tools_without_integrations_stay_plain_links(session, db):
    session.mock_http.reset()
    session.create_traq("https://acme.slack.com/archives/C1/p1")
    link = the_link(db)
    assert link["live"] == "unavailable"
    assert link["live_reason"] == "Live state for Slack isn’t available yet"
    assert not session.mock_http.calls


def test_link_added_by_editing_gets_its_state_when_the_page_opens(session, db):
    traq_id = session.create_traq("Acme incident")
    session.post(
        f"/traqs/{traq_id}/edit",
        text="Acme incident\nhttps://acme.slack.com/archives/C1/p1",
    )
    assert (
        f'data-live-url="/traqs/{traq_id}/live"'
        in session.get(f"/traqs/{traq_id}").text
    )

    session.mock_http.reset()
    fresh = session.get(f"/traqs/{traq_id}/live").text
    assert 'data-status="dead"' in fresh and "Live state for Slack" in fresh
    assert not session.mock_http.calls
    # There's nothing live to check on it, so it isn't checked again.
    db.execute("UPDATE links SET checked_at = '2020-01-01T00:00:00+00:00'")
    assert "data-live-url" not in session.get(f"/traqs/{traq_id}").text


# Background refresh ----------------------------------------------------------


def test_background_refresh_skips_done_traqs(session, settings, db):
    mock_pull(session.mock_http, reviews=[], comments=1)
    open_traq = session.create_traq(PR_URL)
    done_traq = session.create_traq(PR_URL)
    session.post(f"/traqs/{done_traq}/done")
    db.execute("UPDATE links SET checked_at = '2020-01-01T00:00:00+00:00'")

    session.mock_http.reset()
    asyncio.run(refresh_due(settings, TokenCipher(settings.encryption_key)))

    checked = {
        row["traq_id"]: row["checked_at"]
        for row in db.execute("SELECT traq_id, checked_at FROM links")
    }
    assert checked[open_traq] > "2020-01-01T00:00:00+00:00"
    assert checked[done_traq] == "2020-01-01T00:00:00+00:00"
    assert session.mock_http.calls.call_count == 3  # issue, pull, reviews


def due(db):
    return engine.due_links(db, engine.ON_OPEN_MAX_AGE)


def run_refresh(db, settings, links):
    asyncio.run(
        engine.refresh(db, settings, TokenCipher(settings.encryption_key), links)
    )


def test_deleting_a_traq_while_its_links_are_checked(session, settings, db):
    mock_pull(session.mock_http, reviews=[], comments=1)
    kept = session.create_traq(PR_URL)
    deleted = session.create_traq(PR_URL)
    db.execute("UPDATE links SET checked_at = NULL")
    being_checked = due(db)

    session.post(f"/traqs/{deleted}/delete")
    run_refresh(db, settings, being_checked)

    link = the_link(db)
    assert link["traq_id"] == kept and link["checked_at"] is not None


def test_a_check_that_takes_too_long_is_cut_off(session, settings, monkeypatch, db):
    mock_pull(session.mock_http, reviews=[], comments=1)
    session.create_traq(PR_URL)
    session.create_traq("https://slow.example.com/page")
    db.execute("UPDATE links SET checked_at = NULL, live = 'pending'")

    real_check_url = engine.check_url

    async def check_url(client, url, credentials):
        if "slow.example.com" in url:
            await asyncio.sleep(60)  # e.g. a page that trickles its bytes
        return await real_check_url(client, url, credentials)

    monkeypatch.setattr(engine, "check_url", check_url)
    monkeypatch.setattr(engine, "CHECK_TIMEOUT_SECONDS", 0.2)
    run_refresh(db, settings, due(db))

    live = {
        row["url"]: (row["live"], row["live_reason"])
        for row in db.execute("SELECT * FROM links")
    }
    assert live["https://slow.example.com/page"] == ("error", "Check took too long")
    assert live[PR_URL] == ("live", "")


# Connecting accounts ---------------------------------------------------------


def test_connecting_github_makes_private_links_live(session, settings, db):
    issue = "https://github.com/acme/secret/issues/1"
    route = session.mock_http.get(host=GH, path="/repos/acme/secret/issues/1")
    route.respond(404)
    traq_id = session.create_traq(issue)
    assert "Connect GitHub" in session.get(f"/traqs/{traq_id}").text

    start = session.get(f"/connections/github/start?next=/traqs/{traq_id}")
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert query["scope"] == ["repo"]
    session.mock_http.post("https://github.com/login/oauth/access_token").respond(
        json={"access_token": "gho_new", "scope": "repo"}
    )
    done = session.get(f"/connections/github/callback?code=c&state={query['state'][0]}")
    assert done.headers["location"] == f"/traqs/{traq_id}"

    # Connecting is the token exchange alone: nothing else to fail on the way.
    assert [call.request.url.host for call in session.mock_http.calls[-1:]] == [
        "github.com"
    ]
    row = db.execute("SELECT * FROM connections").fetchone()
    assert row["status"] == "ok"
    assert row["access_token"] != "gho_new"
    cipher = TokenCipher(settings.encryption_key)
    assert cipher.decrypt(row["access_token"]) == "gho_new"

    route.respond(
        json={"title": "Secret bug", "state": "open", "assignees": [], "comments": 0}
    )
    # Connecting marks GitHub links due, so the page checks them after loading.
    assert "data-live-url" in session.get(f"/traqs/{traq_id}").text
    page = session.get(f"/traqs/{traq_id}/live").text
    assert "Secret bug" in page and "Connect GitHub" not in page


def test_denied_connection_explains_admin_approval(session):
    start = session.get("/connections/jira/start")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = session.get(
        f"/connections/jira/callback?error=access_denied&state={state}"
    )
    assert response.status_code == 400
    assert "an admin may need to approve Traqen" in response.text


def test_unreachable_page_is_a_transient_error(session, public_dns, db):
    session.mock_http.get(f"https://{PAGE_IP}/x").mock(
        side_effect=httpx.ConnectTimeout("timeout")
    )
    session.create_traq("https://slow.example.com/x")
    link = the_link(db)
    assert link["live"] == "error"
    assert "Couldn’t reach the page" in link["live_reason"]


def test_jira_not_set_up_on_server_does_not_offer_connecting(settings, mock_http, db):
    app = create_app(replace(settings, atlassian=None), background=False)
    with TestClient(app) as client:
        session = Session(client, mock_http, db)
        traq_id = session.create_traq(JIRA_URL)
        link = the_link(db)
        assert link["live_reason"] == "Jira live state isn’t set up on this server"
        assert "Connect Jira" not in session.get(f"/traqs/{traq_id}").text


def test_malformed_response_marks_only_that_link(session, db):
    session.mock_http.get(host=GH, path="/repos/acme/api/issues/7").respond(
        200, text="not json"
    )
    traq_id = session.create_traq("https://github.com/acme/api/issues/7")
    link = the_link(db)
    assert (link["live"], link["live_reason"]) == ("error", "Check failed unexpectedly")
    assert session.get(f"/traqs/{traq_id}").status_code == 200


def test_concurrent_jira_refreshes_use_the_refresh_token_once(session, settings, db):
    connect(
        db,
        settings,
        "jira",
        {"access_token": "old-a", "refresh_token": "old-r", "expires_in": -10},
    )
    route = session.mock_http.post("https://auth.atlassian.com/oauth/token").respond(
        json={"access_token": "new-a", "refresh_token": "new-r", "expires_in": 3600}
    )
    cipher = TokenCipher(settings.encryption_key)
    user_id = db.execute("SELECT id FROM users").fetchone()[0]

    async def main():
        async with httpx.AsyncClient() as client:
            # Two independent runs, like a page load and the background refresher.
            first, second = (
                integrations.Credentials(db, cipher, settings, client, user_id)
                for _ in range(2)
            )
            return await asyncio.gather(first.token("jira"), second.token("jira"))

    assert asyncio.run(main()) == ["new-a", "new-a"]
    assert route.call_count == 1


def test_opening_a_traq_never_waits_on_link_checks(session, db):
    mock_pull(session.mock_http, reviews=[], comments=1)
    traq_id = session.create_traq(PR_URL)
    db.execute("UPDATE links SET checked_at = NULL")  # due a check

    session.mock_http.reset()
    page = session.get(f"/traqs/{traq_id}").text
    assert session.mock_http.calls.call_count == 0  # shown from what's stored
    assert f'data-live-url="/traqs/{traq_id}/live"' in page

    # The page then asks for the check, which finds the change.
    session.mock_http.routes.clear()
    mock_pull(
        session.mock_http,
        reviews=[{"state": "APPROVED", "user": {"login": "ben"}}],
        comments=1,
    )
    fresh = session.get(f"/traqs/{traq_id}/live").text
    assert "Review: Review requested → Approved" in fresh
    # Checked just now: the next visit has nothing to check.
    assert "data-live-url" not in session.get(f"/traqs/{traq_id}").text


def test_live_check_is_only_for_your_own_traqs(session, sign_in_as):
    traq_id = session.create_traq("https://acme.slack.com/archives/C1/p1")
    assert sign_in_as("ben").get(f"/traqs/{traq_id}/live").status_code == 404


def test_a_page_changing_makes_its_traq_the_last_updated(session, db, public_dns):
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "one"))
    watched = session.create_traq("https://status.example.com/x")
    note = session.create_traq("Notes")
    # The page traq was written first, the note a day later.
    for traq_id, days in ((watched, 2), (note, 1)):
        db.execute(
            "UPDATE traqs SET created_at = ?, updated_at = ? WHERE id = ?",
            (dbm.ago(timedelta(days=days)), dbm.ago(timedelta(days=days)), traq_id),
        )

    def by_updated():
        return [
            int(row["href"].rsplit("/", 1)[1])
            for row in session.page("/?sort=updated").select("[data-traq-row]")
        ]

    assert by_updated() == [note, watched]
    # Checked again with nothing new: still not updated.
    session.recheck(watched)
    assert by_updated() == [note, watched]

    session.mock_http.routes.clear()
    session.mock_http.get(f"https://{PAGE_IP}/x").respond(html=page("A", "two"))
    session.recheck(watched)
    assert by_updated() == [watched, note]
