import sqlite3
from datetime import timedelta

import pytest

from traqen import db as dbm
from traqen import document
from traqen.traqs import blocks, first_line, split_link


def rows(page):
    """Ids of the traqs a list page shows, in order."""
    return [
        int(row["href"].rsplit("/", 1)[1]) for row in page.select("[data-traq-row]")
    ]


def project_id(db, name):
    return db.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()[0]


def test_note_traq_is_its_text(session, db):
    session.create_traq("Ask Alex about the SSO timeline\nCS says CSV is OK")
    traq = db.execute("SELECT * FROM traqs").fetchone()
    assert traq["title"] == "Ask Alex about the SSO timeline"
    # Typed as lines, kept as paragraphs.
    assert traq["text"] == "Ask Alex about the SSO timeline\n\nCS says CSV is OK"


def test_link_traq_is_named_after_the_page(session, db):
    session.mock_http.get(
        host="api.github.com", path="/repos/acme/api/issues/9"
    ).respond(
        json={
            "title": "Export times out",
            "state": "open",
            "assignees": [],
            "comments": 2,
        }
    )
    session.create_traq("https://github.com/acme/api/issues/9 nightly export")
    traq = db.execute("SELECT title, text FROM traqs").fetchone()
    # "link + note" typed on one line keeps the link on its own line.
    assert tuple(traq) == (
        "Export times out",
        "https://github.com/acme/api/issues/9\n\nnightly export",
    )
    assert db.execute("SELECT url FROM links").fetchone()[0] == (
        "https://github.com/acme/api/issues/9"
    )


def test_unreadable_link_traq_is_named_after_the_url(session, db):
    session.create_traq("https://acme.slack.com/archives/C1/p1")
    title = db.execute("SELECT title FROM traqs").fetchone()[0]
    assert title == "https://acme.slack.com/archives/C1/p1"


def test_titles_and_links_are_recognized():
    assert split_link("https://x.com/a the fix") == ("https://x.com/a", "the fix")
    assert split_link("github.com/acme/api/pull/1") == (
        "github.com/acme/api/pull/1",
        "",
    )
    assert split_link("node.js is slow") == (None, "node.js is slow")
    assert first_line("\n\n  Hello  \nworld") == "Hello"
    assert first_line("# **Hello** world") == "Hello world"
    long = "Follow up with the platform team about why the nightly export job " * 2
    assert first_line(long).endswith("…") and len(first_line(long)) <= 81


def test_list_is_open_traqs_newest_first(session):
    older = session.create_traq("Older")
    newer = session.create_traq("Newer")
    finished = session.create_traq("Finished")
    session.post(f"/traqs/{finished}/done")

    assert rows(session.page("/")) == [newer, older]
    assert rows(session.page("/archive")) == [finished]

    session.post(f"/traqs/{finished}/done")  # reopen
    assert rows(session.page("/")) == [finished, newer, older]


def test_projects_are_created_from_the_new_traq_dialog(session, db):
    home = session.page("/")
    # No projects yet: the dialog offers "+ New project" but no picker.
    assert home.select_one("[data-new-project]")
    assert not home.select_one("select[name=project_id]")

    in_project = session.create_traq("Renewal risk", new_project="Acme")
    acme = project_id(db, "Acme")
    session.create_traq("Unrelated")

    home = session.page("/")
    assert home.select_one(f'a[href="/projects/{acme}"] [data-nav-name]').text == "Acme"
    assert home.select_one("select[name=project_id]")  # once a project exists

    project_page = session.page(f"/projects/{acme}")
    assert rows(project_page) == [in_project]
    # While viewing a project, it's the default for a new traq.
    assert project_page.select_one(f'#new-traq option[value="{acme}"][selected]')

    # Picking an existing project, and naming an existing one, both reuse it.
    session.create_traq("Second", project_id=str(acme))
    session.create_traq("Third", new_project="  Acme ")
    assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    in_acme = "SELECT COUNT(*) FROM traqs WHERE project_id = ?"
    assert db.execute(in_acme, (acme,)).fetchone()[0] == 3


def test_edit_changes_text_title_and_project(session, db):
    traq_id = session.create_traq("First draft")
    session.post(
        f"/traqs/{traq_id}/edit", text="Better\nwith details", new_project="Q4"
    )
    traq = db.execute("SELECT * FROM traqs").fetchone()
    assert (traq["title"], traq["text"]) == ("Better", "Better\nwith details")
    assert traq["project_id"] == project_id(db, "Q4")

    session.post(f"/traqs/{traq_id}/edit", text="Better", project_id="")
    assert db.execute("SELECT project_id FROM traqs").fetchone()[0] is None


def test_delete(session, db):
    traq_id = session.create_traq("Throwaway")
    assert session.post(f"/traqs/{traq_id}/delete").headers["location"] == "/"
    assert db.execute("SELECT COUNT(*) FROM traqs").fetchone()[0] == 0


def test_search_finds_done_traqs_and_treats_wildcards_literally(session):
    traq_id = session.create_traq("Defer SAML group mapping\nto v2")
    session.post(f"/traqs/{traq_id}/done")
    assert rows(session.page("/search?q=v2")) == [traq_id]
    assert rows(session.page("/search?q=%25")) == []


def test_created_time_is_shown_in_the_browsers_timezone(session, db):
    session.create_traq("Timed", tz="America/Los_Angeles")
    assert (
        db.execute("SELECT timezone FROM users").fetchone()[0] == "America/Los_Angeles"
    )
    db.execute("UPDATE traqs SET created_at = '2020-01-10T17:30:00+00:00'")
    assert "Fri Jan 10, 9:30 AM" in session.page("/").select_one("[data-traq-row]").text


def test_users_cannot_see_each_others_traqs(session, sign_in_as, db):
    traq_id = session.create_traq("Ana's private traq", new_project="Ana's")
    anas = project_id(db, "Ana's")

    ben = sign_in_as("ben")
    assert ben.get(f"/traqs/{traq_id}").status_code == 404
    assert ben.get(f"/projects/{anas}").status_code == 404
    assert ben.post(f"/traqs/{traq_id}/delete").status_code == 404
    # Someone else's project can't be used either.
    assert ben.post("/traqs", text="x", project_id=str(anas)).status_code == 404
    assert rows(ben.page("/search?q=private")) == []
    assert session.get(f"/traqs/{traq_id}").status_code == 200


def test_old_database_is_refused(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE traqs (id INTEGER PRIMARY KEY, title TEXT)")
    old.close()
    with pytest.raises(RuntimeError, match="older version"):
        dbm.init(path)
    # A database made by this version opens again on the next start.
    current = tmp_path / "current.db"
    dbm.init(current)
    dbm.init(current)


def test_rename_project(session, db):
    session.create_traq("A", new_project="Acme")
    session.create_traq("B", new_project="Pricing")
    acme = project_id(db, "Acme")

    renamed = session.post(
        f"/projects/{acme}/rename", name=" Acme  Corp ", next="/archive"
    )
    assert renamed.headers["location"] == "/archive"  # back where you were
    assert project_id(db, "Acme Corp") == acme
    taken = session.post(f"/projects/{acme}/rename", name="Pricing")
    assert taken.status_code == 400 and "already have a project" in taken.text


def test_delete_project_keeps_its_traqs_by_default(session, db):
    traq_id = session.create_traq("Keep me", new_project="Acme")
    acme = project_id(db, "Acme")
    # Deleting the project you're looking at sends you to Everything.
    deleted = session.post(f"/projects/{acme}/delete", next=f"/projects/{acme}")
    assert deleted.headers["location"] == "/"
    assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    traq = db.execute("SELECT id, project_id FROM traqs").fetchone()
    assert (traq["id"], traq["project_id"]) == (traq_id, None)


def test_delete_project_with_its_traqs(session, db):
    session.create_traq("Goes", new_project="Acme")
    session.create_traq("Stays")
    session.post(f"/projects/{project_id(db, 'Acme')}/delete", delete_traqs="1")
    titles = [row[0] for row in db.execute("SELECT title FROM traqs")]
    assert titles == ["Stays"]


def test_cannot_rename_or_delete_someone_elses_project(session, sign_in_as, db):
    session.create_traq("x", new_project="Ana's")
    anas = project_id(db, "Ana's")
    ben = sign_in_as("ben")
    assert ben.post(f"/projects/{anas}/rename", name="Mine").status_code == 404
    assert ben.post(f"/projects/{anas}/delete").status_code == 404
    assert db.execute("SELECT name FROM projects").fetchone()[0] == "Ana's"


def test_traq_page_splits_title_from_body_and_follows_status(session, db):
    def shown(traq_id):
        page = session.page(f"/traqs/{traq_id}")
        body = page.select_one("[data-traq-text]")
        return (
            page.select_one("[data-traq-title]").text,
            body.text.strip() if body else None,
            page.select_one("[data-crumb]")["href"],
        )

    # A note: its first line is the title and only the rest is the body.
    note = session.create_traq(
        "Ask Alex about SSO\nCS says CSV is OK", new_project="Acme"
    )
    acme = f"/projects/{project_id(db, 'Acme')}"
    assert shown(note) == ("Ask Alex about SSO", "CS says CSV is OK", acme)

    # A link: titled after the link, so all of the text is the body.
    link = session.create_traq("https://acme.slack.com/archives/C1/p1 the thread")
    assert shown(link) == ("https://acme.slack.com/archives/C1/p1", "the thread", "/")

    # Done traqs live in the Archive, and the menu offers Reopen instead.
    def done_action(traq_id):
        page = session.page(f"/traqs/{traq_id}")
        form = page.select_one(f'form[action="/traqs/{traq_id}/done"]')
        return form.button.get_text(" ", strip=True)

    assert done_action(note) == "Mark done"
    session.post(f"/traqs/{note}/done")
    assert shown(note)[2] == "/archive"
    assert done_action(note) == "Reopen"


def test_sidebar_highlights_where_you_are(session, db):
    def highlighted(path):
        return [a["href"] for a in session.page(path).select("#sidebar a.active")]

    loose = session.create_traq("No project")
    in_project = session.create_traq("In Acme", new_project="Acme")
    acme = project_id(db, "Acme")

    assert highlighted("/") == ["/"]
    assert highlighted(f"/traqs/{loose}") == ["/"]
    assert highlighted(f"/projects/{acme}") == [f"/projects/{acme}"]
    assert highlighted(f"/traqs/{in_project}") == [f"/projects/{acme}"]
    assert highlighted("/archive") == ["/archive"]
    session.post(f"/traqs/{in_project}/done")
    assert highlighted(f"/traqs/{in_project}") == ["/archive"]
    assert highlighted("/search?q=x") == []


def test_editing_keeps_links_in_step_with_the_text(session, db):
    a, b, c = (f"https://acme.slack.com/archives/C1/p{n}" for n in (1, 2, 3))
    traq_id = session.create_traq(f"Plan\n{a}\nnotes\n{b}")

    def tracked():
        return [r[0] for r in db.execute("SELECT url FROM links ORDER BY url")]

    assert tracked() == [a, b]
    # Remember a's state to show it survives an edit that keeps a.
    db.execute("UPDATE links SET seen_state = 'kept' WHERE url = ?", (a,))

    # Drop b, add c, and mention a link mid-sentence (not tracked).
    session.post(
        f"/traqs/{traq_id}/edit",
        text=f"Plan\n{a}\nsee {b} for context\n{c}\n{c}",
    )
    assert tracked() == [a, c]
    kept = db.execute("SELECT seen_state FROM links WHERE url = ?", (a,)).fetchone()
    assert kept[0] == "kept"

    session.post(f"/traqs/{traq_id}/edit", text="Plan only")
    assert tracked() == []


def test_a_link_rewritten_to_the_same_page_keeps_its_state(session, db):
    traq_id = session.create_traq("Plan\nhttps://status.example.com/incidents/1")
    db.execute("UPDATE links SET seen_state = 'kept'")
    moved = "https://Status.example.com/incidents/1#update"
    session.post(f"/traqs/{traq_id}/edit", text=f"Plan\n{moved}")
    link = db.execute("SELECT url, seen_state FROM links").fetchall()
    assert [tuple(row) for row in link] == [(moved, "kept")]


def test_title_follows_the_first_line(session, db):
    slack = "https://acme.slack.com/archives/C1/p1"
    traq_id = session.create_traq(f"{slack}\nwhy it matters")

    def title():
        return db.execute("SELECT title FROM traqs").fetchone()[0]

    assert title() == slack  # a link line names it (the URL until it's read)
    db.execute("UPDATE links SET title = 'Acme thread'")
    session.post(f"/traqs/{traq_id}/edit", text=f"{slack}\nwhy it matters")
    assert title() == "Acme thread"
    session.post(f"/traqs/{traq_id}/edit", text=f"Acme follow-up\n{slack}")
    assert title() == "Acme follow-up"


def test_page_shows_cards_where_the_links_are():
    a, b = "https://a.example/x", "https://b.example/y"
    cards = {a: {"link": "A"}, b: {"link": "B"}}

    def shown(text):
        return [
            {"kind": "text", "html": str(block["html"]).strip()}
            if block["kind"] == "text"
            else block
            for block in blocks(text, cards)
        ]

    assert shown(f"# Title\n\nintro\n\n{a}\n\n*middle*\n\n<{b}>") == [
        {"kind": "text", "html": "<p>intro</p>"},
        {"kind": "link", "link": "A"},
        {"kind": "text", "html": "<p><em>middle</em></p>"},
        {"kind": "link", "link": "B"},
    ]
    # A link as the first line is the title and still gets its card.
    assert shown(f"{a}\n\nnote") == [
        {"kind": "link", "link": "A"},
        {"kind": "text", "html": "<p>note</p>"},
    ]
    # A first line too long to be the title in full stays in the text.
    long = "word " * 30
    assert shown(long) == [{"kind": "text", "html": f"<p>{long.strip()}</p>"}]


def test_only_a_link_on_its_own_in_a_paragraph_is_a_card():
    text = "\n".join(
        [
            "Title",
            "https://a.example/1",  # 1: a paragraph's line
            "",
            "- https://a.example/2",  # in a list
            "",
            "> https://a.example/3",  # in a quote
            "",
            "```",
            "https://a.example/4",  # code
            "```",
            "",
            "see https://a.example/5",  # in a sentence
            "",
            "[named](https://a.example/6)",  # a named link stays a link
            "",
            "<https://a.example/7>",  # 15: an autolink
            "",
            "- an item",
            "  https://a.example/8",  # the item's next line
        ]
    )
    assert document.link_lines(text) == {
        1: "https://a.example/1",
        15: "https://a.example/7",
    }


def test_text_is_shown_formatted_and_never_runs():
    html = str(
        document.render(
            "see https://a.example/x?y=1&z=2, then <b>bold</b> "
            "[x](javascript:alert(1)) ![i](javascript:alert(1))\n\n"
            "| A | B |\n| - | - |\n| ~~1~~ | `2` |"
        )
    )
    assert (
        '<a href="https://a.example/x?y=1&amp;z=2" target="_blank" rel="noopener">'
        in html
    )
    assert "&lt;b&gt;bold&lt;/b&gt;" in html  # raw HTML is shown, not run
    assert "javascript" not in html.replace("[x](javascript:alert(1))", "").replace(
        "![i](javascript:alert(1))", ""
    )
    assert "<td><s>1</s></td>" in html and "<td><code>2</code></td>" in html


def test_title_is_the_first_lines_text():
    assert document.plain("## **Q4** plan ##") == "Q4 plan"
    assert document.plain("- [ ] Call `Acme` back") == "Call Acme back"
    assert document.plain("> [Spec](https://x.example) review") == "Spec review"


def test_each_link_shows_its_status_and_whether_it_changed(session, db):
    urls = [f"https://acme.slack.com/archives/C1/p{n}" for n in range(5)]
    traq_id = session.create_traq("Links\n" + "\n".join(urls))
    seen = '{"title": "t", "fields": {"State": "Open"}}'
    moved = '{"title": "t", "fields": {"State": "Closed"}}'
    for url, live, state, seen_state in [
        (urls[0], "live", seen, seen),  # live, nothing new
        (urls[1], "live", moved, seen),  # live, changed since last look
        (urls[2], "unavailable", None, None),  # e.g. page gone
        (urls[3], "error", seen, seen),  # check failed, last state kept
        (urls[4], "pending", None, None),  # not checked yet
    ]:
        db.execute(
            "UPDATE links SET live = ?, state = ?, seen_state = ?, live_reason = 'r' "
            "WHERE url = ?",
            (live, state, seen_state, url),
        )

    def shown(card):
        lit = "changed" in card["class"]
        assert (
            lit
            == bool(card.select("[data-change]"))
            == bool(card.select("[data-acknowledge]"))
        )
        return card.select_one("[data-status]")["data-status"], lit

    cards = session.page(f"/traqs/{traq_id}").select("[data-link-card]")
    assert [shown(card) for card in cards] == [
        ("live", False),
        ("live", True),
        ("dead", False),
        ("failed", False),
        ("pending", False),
    ]


def test_sidebar_counts_open_traqs_per_project(session):
    session.create_traq("a", new_project="Acme")
    session.create_traq("b", new_project="Acme")
    done = session.create_traq("c", new_project="Acme")
    session.post(f"/traqs/{done}/done")
    session.create_traq("d", new_project="Empty one")
    session.post(f"/traqs/{session.create_traq('e', new_project='Empty one')}/delete")
    session.create_traq("no project")

    counts = {
        row.select_one("[data-nav-name]").text: row.select_one("[data-nav-count]").text
        for row in session.page("/").select("#sidebar .project-row")
    }
    # Done traqs don't count; Everything counts every open traq.
    assert counts == {"Everything": "4", "Acme": "2", "Empty one": "1"}


def test_everything_shows_each_traqs_project(session, db):
    in_acme = session.create_traq("Renewal", new_project="Acme")
    loose = session.create_traq("No project")

    def pills(path):
        return {
            int(row["href"].rsplit("/", 1)[1]): (
                pill.text if (pill := row.select_one("[data-project-pill]")) else None
            )
            for row in session.page(path).select("[data-traq-row]")
        }

    assert pills("/") == {in_acme: "Acme", loose: None}
    # In a project, every traq is that project's: no pills.
    assert pills(f"/projects/{project_id(db, 'Acme')}") == {in_acme: None}


def test_lists_sort_by_title_created_or_updated_and_remember_it(session, db):
    banana = session.create_traq("Banana")
    apple = session.create_traq("apple")
    cherry = session.create_traq("cherry", new_project="Fruit")
    for traq_id, days in ((banana, 3), (apple, 2), (cherry, 1)):
        moment = dbm.ago(timedelta(days=days))
        db.execute(
            "UPDATE traqs SET created_at = ?, updated_at = ? WHERE id = ?",
            (moment, moment, traq_id),
        )
    # Editing the oldest makes it the last updated; moving one to another
    # project doesn't change what it says.
    session.post(f"/traqs/{banana}/edit", text="Banana\nripe", project_id="")
    session.post(f"/traqs/{apple}/edit", text="apple", new_project="Fruit")

    def headings(page):
        return [a["data-sort"] for a in page.select("[data-sort].is-sorted")]

    assert rows(session.page("/")) == [cherry, apple, banana]  # newest first
    assert rows(session.page("/?sort=updated")) == [banana, cherry, apple]
    page = session.page("/?sort=title")
    assert rows(page) == [apple, banana, cherry]  # ignoring case
    assert headings(page) == ["title"]

    # The order picked sticks: on this list, other lists, and search.
    assert rows(session.page("/")) == [apple, banana, cherry]
    assert rows(session.page(f"/projects/{project_id(db, 'Fruit')}")) == [apple, cherry]
    assert rows(session.page("/search?q=an")) == [banana]
    page = session.page("/search?q=a&sort=created")
    assert rows(page) == [apple, banana]
    sort_link = page.select_one("[data-sort=title]")["href"]
    assert "q=a" in sort_link and "sort=title" in sort_link  # keeps the search
    assert rows(session.page("/")) == [cherry, apple, banana]
