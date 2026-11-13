from datetime import timedelta

from traqen import db as dbm

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def test_text_saved_from_the_editor_is_shown_formatted(session, db):
    traq_id = session.create_traq("Draft")
    slack = "https://acme.slack.com/archives/C1/p1"
    session.post(
        f"/traqs/{traq_id}/edit",
        text=f"## SSO **rollout**\n\nBlocked on:\n\n{slack}\n\n- [ ] call Acme\n"
        "- [x] send the doc\n\n| Who | When |\n| --- | --- |\n| Ana | Fri |",
    )
    page = session.page(f"/traqs/{traq_id}")
    assert page.select_one("[data-traq-title]").text == "SSO rollout"
    assert page.select_one("[data-link-card] .link-title")["href"] == slack
    text = page.select("[data-traq-text]")
    assert [p.text for p in text[0].select("p")] == ["Blocked on:"]  # not the title
    boxes = text[1].select("input[data-task-line]")
    assert [(b.parent.text.strip(), b.has_attr("checked")) for b in boxes] == [
        ("call Acme", False),
        ("send the doc", True),
    ]
    assert [td.text for td in text[1].select("td")] == ["Ana", "Fri"]


def test_ticking_a_checklist_item_saves_it(session, db):
    traq_id = session.create_traq("Launch")
    session.post(f"/traqs/{traq_id}/edit", text="Launch\n\n- [ ] DNS\n- [ ] Docs")

    def ticked():
        page = session.page(f"/traqs/{traq_id}")
        return {
            box.parent.text.strip(): box.has_attr("checked")
            for box in page.select("input[data-task-line]")
        }

    def tick(name, done):
        page = session.page(f"/traqs/{traq_id}")
        box = next(
            b
            for b in page.select("input[data-task-line]")
            if b.parent.text.strip() == name
        )
        shown = page.select_one("[data-tasks]")["data-text-shown"]
        fields = {"line": box["data-task-line"], "shown": shown}
        return session.post(f"/traqs/{traq_id}/tasks", done=str(done).lower(), **fields)

    assert tick("Docs", True).headers["location"] == f"/traqs/{traq_id}"
    assert ticked() == {"DNS": False, "Docs": True}
    assert db.execute("SELECT text FROM traqs").fetchone()[0] == (
        "Launch\n\n- [ ] DNS\n- [x] Docs"
    )
    tick("Docs", False)
    assert ticked() == {"DNS": False, "Docs": False}


def test_a_checklist_item_isnt_ticked_in_text_that_changed_since(session, db):
    traq_id = session.create_traq("Launch")
    session.post(f"/traqs/{traq_id}/edit", text="Launch\n\n- [ ] DNS")
    page = session.page(f"/traqs/{traq_id}")
    line = page.select_one("input[data-task-line]")["data-task-line"]
    shown = page.select_one("[data-tasks]")["data-text-shown"]
    # Edited elsewhere: line 2 is now a different item.
    session.post(f"/traqs/{traq_id}/edit", text="Launch\n\n- [ ] Ship it")
    session.post(f"/traqs/{traq_id}/tasks", line=line, done="true", shown=shown)
    assert db.execute("SELECT text FROM traqs").fetchone()[0].endswith("- [ ] Ship it")
    # Nor is a line that isn't a checklist item, even one that looks like it.
    session.post(f"/traqs/{traq_id}/edit", text="Launch\n\n```\n- [ ] not a task\n```")
    shown = session.page(f"/traqs/{traq_id}").select_one("[data-tasks]")[
        "data-text-shown"
    ]
    session.post(f"/traqs/{traq_id}/tasks", line="3", done="true", shown=shown)
    assert "- [ ] not a task" in db.execute("SELECT text FROM traqs").fetchone()[0]
    shown = session.page(f"/traqs/{traq_id}").select_one("[data-tasks]")[
        "data-text-shown"
    ]
    session.post(f"/traqs/{traq_id}/tasks", line="0", done="true", shown=shown)
    assert db.execute("SELECT text FROM traqs").fetchone()[0].startswith("Launch\n")


def upload(session, traq_id, data, name="shot.png"):
    return session.client.post(
        f"/traqs/{traq_id}/images",
        data={"csrf": session.csrf},
        files={"image": (name, data, "image/png")},
    )


def test_images_are_added_and_shown_only_to_their_owner(session, sign_in_as, db):
    traq_id = session.create_traq("Screens")
    added = upload(session, traq_id, PNG)
    url = added.json()["url"]
    shown = session.get(url)
    assert (shown.status_code, shown.content) == (200, PNG)
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"

    ben = sign_in_as("ben")
    assert ben.get(url).status_code == 404
    assert upload(ben, traq_id, PNG).status_code == 404  # not ben's traq


def test_only_images_up_to_5_mb_are_added(session):
    traq_id = session.create_traq("Screens")
    # What the file is, not what the browser says it is.
    assert upload(session, traq_id, b"<svg onload=alert(1)>").status_code == 415
    too_big = PNG + b"\0" * (5 * 1024 * 1024)
    assert upload(session, traq_id, too_big).status_code == 413


def test_images_no_traq_shows_are_dropped_after_a_day(session, db):
    traq_id = session.create_traq("Screens")
    kept, dropped, new = (upload(session, traq_id, PNG).json()["url"] for _ in range(3))
    db.execute("UPDATE images SET created_at = ?", (dbm.ago(timedelta(days=2)),))
    db.execute(
        "UPDATE images SET created_at = ? WHERE id = ?",
        (dbm.stamp(), new.rsplit("/", 1)[1]),
    )
    session.post(f"/traqs/{traq_id}/edit", text=f"Screens\n\n![]({kept})")
    assert session.get(kept).status_code == 200
    assert session.get(dropped).status_code == 404
    assert session.get(new).status_code == 200  # may be in an editor not saved yet

    db.execute("UPDATE images SET created_at = ?", (dbm.ago(timedelta(days=2)),))
    session.post(f"/traqs/{traq_id}/delete")
    assert session.get(kept).status_code == 404
