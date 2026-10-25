import json
import shutil
import subprocess
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi.testclient import TestClient

from traqen.bookmarklet import MAX_NOTE, bookmarklet_code

from .conftest import html

PAGE = "https://acme.slack.com/archives/C1/p1"


def capture_url(url=PAGE, title="Acme thread", note=""):
    return f"/capture?url={quote(url, safe='')}&title={quote(title)}&note={quote(note)}"


def run_bookmarklet(base_url, *, href, title, selection="", popup_allowed=True):
    """Run the generated bookmarklet in Node against a stubbed page; returns the
    URL it opened as a popup, or navigated to when the popup was blocked."""
    if shutil.which("node") is None:
        pytest.skip("Node is needed to run the bookmarklet")
    script = f"""
        let opened = null;
        globalThis.location = {{ href: {json.dumps(href)} }};
        globalThis.document = {{ title: {json.dumps(title)} }};
        globalThis.getSelection = () => ({json.dumps(selection)});
        const allowed = {json.dumps(popup_allowed)};
        globalThis.open = (url) => {{ opened = url; return allowed ? {{}} : null; }};
        eval({json.dumps(bookmarklet_code(base_url).removeprefix("javascript:"))});
        console.log(JSON.stringify({{ opened, href: location.href }}));
    """
    result = json.loads(
        subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True
        ).stdout
    )
    return result["opened"] if popup_allowed else result["href"]


def capture_params(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}", parse_qs(parts.query)


def test_bookmarklet_sends_the_page_and_selection_to_capture():
    page = "https://github.com/acme/api/pull/7?tab=files&x=1#diff"
    opened = run_bookmarklet(
        "http://localhost:8000", href=page, title="Fix & ship", selection="  key bit  "
    )
    target, params = capture_params(opened)
    assert target == "http://localhost:8000/capture"
    assert params == {
        "url": [page],
        "title": ["Fix & ship"],
        "note": ["key bit"],
        "popup": ["1"],  # so Traqen closes the window after saving
    }


def test_bookmarklet_without_selection_sends_no_note_and_caps_long_ones():
    _, params = capture_params(
        run_bookmarklet("http://localhost:8000", href="https://a.example/x", title="A")
    )
    assert "note" not in params
    _, params = capture_params(
        run_bookmarklet(
            "http://localhost:8000",
            href="https://a.example/x",
            title="A",
            selection="y" * (MAX_NOTE + 50),
        )
    )
    assert params["note"] == ["y" * MAX_NOTE]


def test_bookmarklet_falls_back_to_the_tab_when_popups_are_blocked():
    href = run_bookmarklet(
        "http://localhost:8000",
        href="https://a.example/x",
        title="A",
        popup_allowed=False,
    )
    target, params = capture_params(href)
    assert target == "http://localhost:8000/capture"
    assert "popup" not in params  # a tab goes back to the page instead


def test_bookmarklet_base_url_cannot_break_out_of_its_string():
    # A quote in the configured address must stay data, not become code.
    opened = run_bookmarklet(
        "http://host.example/a'b\"c", href="https://a.example/x", title="A"
    )
    assert opened.startswith("http://host.example/a'b\"c/capture?")


def test_bookmark_is_named_save_to_traqen(session):
    # Dragging the link names the bookmark after its text; the logo letter is
    # drawn by CSS so it doesn't end up in the name.
    link = session.page("/").select_one("[data-bookmarklet]")
    assert link["href"].startswith("javascript:")
    assert link.get_text(strip=True) == "Save to Traqen"


def test_capture_into_a_new_traq(session, db):
    done = session.post(
        "/capture", url=PAGE, traq="new", note="selected text", new_project="Acme"
    )
    assert done.status_code == 200
    traq = db.execute("SELECT * FROM traqs").fetchone()
    link = db.execute("SELECT * FROM links").fetchone()
    # The link is the first line, the selection follows; Slack can't be read,
    # so the traq is named after the link.
    assert (traq["title"], traq["text"]) == (PAGE, f"{PAGE}\nselected text")
    assert link["url"] == PAGE
    # Checked after the response went out, but checked all the same.
    assert link["live"] == "unavailable" and link["checked_at"] is not None
    project = db.execute("SELECT id, name FROM projects").fetchone()
    assert project["name"] == "Acme" and traq["project_id"] == project["id"]


def offered(page):
    """Ids of the existing traqs the capture window offers to pick (those of
    other projects are there too, but can't be picked)."""
    return [
        int(radio["value"])
        for radio in page.select("input[name=traq]")
        if radio["value"] != "new" and not radio.find_parent("fieldset", disabled=True)
    ]


def found(session, q, url=""):
    """The traqs a search in the capture window lists: (id, has the page)."""
    rows = html(session.get(f"/capture/traqs?q={quote(q)}&url={quote(url)}").text)
    return [
        (int(row["data-traq"]), row.has_attr("data-has-page"))
        for row in rows.select("[data-traq]")
    ]


def test_capture_offers_a_few_recent_traqs_not_all_of_them(session):
    ids = [session.create_traq(f"Traq {n}") for n in range(12)]
    assert offered(session.page(capture_url())) == ids[::-1][:3]


def test_capture_search_finds_older_open_traqs(session, sign_in_as):
    old = session.create_traq("Acme SSO rollout\nwith the identity team")
    for n in range(10):
        session.create_traq(f"Unrelated {n}")
    finished = session.create_traq("Acme SSO retro")
    session.post(f"/traqs/{finished}/done")

    assert found(session, "identity") == [(old, False)]
    # Only open traqs, at most 8, and nothing until you type.
    assert found(session, "SSO") == [(old, False)]
    assert len(found(session, "Unrelated")) == 8
    assert found(session, "") == []

    # Someone else's traqs never show up.
    assert found(sign_in_as("ben"), "Acme") == []


def banner(response):
    """Why saving is refused: ("text", message) when the traq picked turned out
    to have the page, ("where", pill) when a new traq in the picked project
    would duplicate it; else None."""
    page = html(response.text)
    there = page.select_one("[data-already-there]")
    if there:
        return ("text", there.text)
    alert = page.select_one("[data-dup-alert]")
    if alert.has_attr("hidden"):
        return None
    shown = [n for n in alert.select("[data-held-in]") if not n.has_attr("hidden")]
    return ("where", shown[0].select_one("[data-dup-where]").text)


def save_disabled(response):
    return html(response.text).select_one("[data-save]").has_attr("disabled")


def test_capture_into_an_existing_traq_appends_the_link(session, db):
    traq_id = session.create_traq("Existing traq\nsome notes")
    assert offered(session.page(capture_url())) == [traq_id]
    session.post("/capture", url=PAGE, traq=str(traq_id), note="why")
    text = db.execute("SELECT text FROM traqs WHERE id = ?", (traq_id,)).fetchone()[0]
    assert text == f"Existing traq\nsome notes\n{PAGE}\nwhy"


def test_duplicates_are_refused_and_nothing_is_saved(session, db):
    def snapshot():
        return [tuple(r) for r in db.execute("SELECT id, text FROM traqs ORDER BY id")]

    in_acme = session.create_traq(f"{PAGE}\nAcme thread", new_project="Acme")
    acme = db.execute("SELECT id FROM projects").fetchone()[0]
    before = snapshot()

    # Into a traq that already has it.
    again = session.post("/capture", url=PAGE, traq=str(in_acme), note="kept")
    assert again.status_code == 409
    assert banner(again) == ("text", "That traq already has this page.")
    # As a new traq in the same project, by picking it or by naming it.
    for fields in ({"project_id": str(acme)}, {"new_project": "Acme"}):
        again = session.post("/capture", url=PAGE, traq="new", note="kept", **fields)
        assert again.status_code == 409 and banner(again) == ("where", "Acme")
    assert snapshot() == before
    # The window stays open with the note and project kept, to pick again.
    form = html(again.text)
    assert form.select_one("textarea[name=note]").text == "kept"
    assert form.select_one(f'select[name=project_id] option[value="{acme}"][selected]')

    # Another project, a brand-new project, or no project (Everything) is fine...
    for fields in ({"new_project": "Pricing"}, {}):
        assert (
            session.post("/capture", url=PAGE, traq="new", **fields).status_code == 200
        )
    # ...but only once in Everything.
    again = session.post("/capture", url=PAGE, traq="new")
    assert again.status_code == 409 and banner(again) == ("where", "Everything")


def test_links_in_done_traqs_dont_count_as_duplicates(session):
    done = session.create_traq(f"{PAGE}\nold work")
    session.post(f"/traqs/{done}/done")
    assert session.post("/capture", url=PAGE, traq="new").status_code == 200


def test_the_same_page_by_another_url_is_a_duplicate(session):
    pull = "https://github.com/acme/api/pull/7"
    session.create_traq(f"{pull}\nthe fix")
    page = session.create_traq("https://status.example.com/incidents/1")
    for same in (f"{pull}#discussion_r1", f"{pull}/files"):
        again = session.post("/capture", url=same, traq="new")
        assert again.status_code == 409 and banner(again) == ("where", "Everything")
    # Into a traq that has the page under another address.
    again = session.post(
        "/capture", url="https://Status.example.com/incidents/1#update", traq=str(page)
    )
    assert banner(again) == ("text", "That traq already has this page.")
    # The same pull request on two lines of one traq is one card.
    both = session.create_traq(f"Review\n{pull}\n{pull}/files")
    assert len(session.page(f"/traqs/{both}").select("[data-link-card]")) == 1


def test_a_page_traqen_cant_read_is_named_by_its_title(session, db):
    session.post("/capture", url=PAGE, traq="new", title="  Acme   incident thread ")
    assert db.execute("SELECT title FROM traqs").fetchone()[0] == "Acme incident thread"
    assert db.execute("SELECT title FROM links").fetchone()[0] == "Acme incident thread"


def test_capture_rejects_other_users_traqs_and_bad_urls(session):
    assert session.post("/capture", url=PAGE, traq="999").status_code == 404
    assert session.get(capture_url(url="chrome://settings")).status_code == 400
    response = session.post("/capture", url=PAGE, traq="new", project_id="999")
    assert response.status_code == 404


def test_capture_requires_sign_in(app):
    with TestClient(app) as client:
        response = client.get(capture_url(), follow_redirects=False)
        assert response.headers["location"].startswith("/login?next=/capture")


def test_after_saving_a_popup_closes_and_a_tab_goes_back(session):
    # Decided by how the bookmarklet opened Traqen, not by guessing from
    # window.opener (which some pages and browsers cut).
    form = session.page(capture_url() + "&popup=1")
    assert form.select_one("input[name=popup]")["value"] == "1"
    popup = session.post("/capture", url=PAGE + "?a", traq="new", popup="1").text
    assert "window.close()" in popup and "location.replace" not in popup

    assert not session.page(capture_url()).select_one("input[name=popup]")
    tab = session.post("/capture", url=PAGE + "?b", traq="new").text
    assert f"location.replace({json.dumps(PAGE + '?b')})" in tab
    assert "window.close()" not in tab


def test_window_warns_up_front_when_the_default_choice_is_taken(session, db):
    # Not in any traq yet: nothing to warn about.
    form = session.get(capture_url())
    assert (banner(form), save_disabled(form)) == (None, False)

    # In a traq with no project: a new traq in Everything would duplicate it.
    session.create_traq(f"{PAGE}\nfirst save")
    form = session.get(capture_url())
    assert (banner(form), save_disabled(form)) == (("where", "Everything"), True)

    # In a project only: the default (no project) is fine; the window still
    # has the notice for that project, to show if it's picked.
    other = "https://acme.slack.com/archives/C1/p9"
    session.create_traq(f"{other}\nsaved", new_project="Acme")
    form = session.get(capture_url(url=other))
    assert (banner(form), save_disabled(form)) == (None, False)
    acme = db.execute("SELECT id FROM projects").fetchone()[0]
    notices = html(form.text).select("[data-held-in]")
    assert [(n["data-held-in"], n.has_attr("hidden")) for n in notices] == [
        (str(acme), True)
    ]


def test_recent_traqs_treat_every_project_the_same(session, db):
    # In the "SDK" project: one traq has the page among other things, one is
    # just the page's link, one doesn't have it. Plus a traq with no project.
    among = session.create_traq(f"SDK notes\n{PAGE}\nmore", new_project="SDK")
    session.create_traq(PAGE, new_project="SDK")
    other = session.create_traq("SDK plan", new_project="SDK")
    loose = session.create_traq("No project traq")
    sdk = db.execute("SELECT id FROM projects WHERE name = 'SDK'").fetchone()[0]

    def offered_in(form, key):
        rows = form.select_one(f'[data-recent-for="{key}"]').select("[data-traq]")
        return [(int(row["data-traq"]), row.has_attr("data-has-page")) for row in rows]

    form = session.page(capture_url())
    # Same rule in Everything and in the project: the traq that has the page
    # among other things is listed with a check; the one that's just the page
    # isn't listed at all.
    assert offered_in(form, "") == [(loose, False), (other, False), (among, True)]
    assert offered_in(form, sdk) == [(other, False), (among, True)]

    # Rendered the same way with SDK picked (after a refusal): banner, and the
    # traq with the page still listed, marked.
    again = session.post("/capture", url=PAGE, traq="new", project_id=str(sdk))
    assert banner(again) == ("where", "SDK")
    assert offered_in(html(again.text), sdk) == [(other, False), (among, True)]
    assert offered(html(again.text)) == [other]


def test_search_marks_traqs_that_have_the_page(session):
    has_it = session.create_traq(f"{PAGE}\nalready here")
    session.create_traq(PAGE)  # just the page: not offered
    without = session.create_traq("Other traq")
    marks = dict(
        row for q in ("traq", "already", "slack") for row in found(session, q, PAGE)
    )
    assert marks == {without: False, has_it: True}


def test_search_isnt_crowded_out_by_traqs_that_are_just_the_page(session):
    older = session.create_traq("Notes on the slack thread")
    for _ in range(12):
        session.create_traq(PAGE)  # newer, but just the page: never offered
    assert found(session, "slack", PAGE) == [(older, False)]
