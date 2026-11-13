"""Traqs: the list, creating one, its page, editing, archive, and search."""

import hashlib
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db as dbm
from . import document, images, traqs
from .auth import CSRF, current_user, get_db, safe_next
from .live import engine
from .live.links import classify
from .shell import render
from .templating import templates
from .traqs import SORTS, blocks, find_traqs, link_changes, list_traqs, tidy_new_text

router = APIRouter()


def back(path: str) -> RedirectResponse:
    return RedirectResponse(path, 303)


def get_traq(db: sqlite3.Connection, user, traq_id: int) -> sqlite3.Row:
    traq = db.execute(
        "SELECT * FROM traqs WHERE id = ? AND user_id = ?", (traq_id, user["id"])
    ).fetchone()
    if traq is None:
        raise HTTPException(404, "Traq not found")
    return traq


def get_project(db: sqlite3.Connection, user, project_id: int) -> sqlite3.Row:
    project = db.execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user["id"])
    ).fetchone()
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def chosen_project(db, user, project_id: str, new_project: str) -> int | None:
    """The project picked in a form: a new one by name, an existing one, or none."""
    name = " ".join(new_project.split())
    if name:
        db.execute(
            "INSERT INTO projects (user_id, name, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, name) DO NOTHING",
            (user["id"], name, dbm.stamp()),
        )
        return db.execute(
            "SELECT id FROM projects WHERE user_id = ? AND name = ?", (user["id"], name)
        ).fetchone()["id"]
    if project_id:
        try:
            return get_project(db, user, int(project_id))["id"]
        except ValueError as exc:
            raise HTTPException(400, "Unknown project") from exc
    return None


def remember_timezone(db, user, tz: str) -> None:
    """Times are shown in the browser's timezone, which the forms send along."""
    try:
        ZoneInfo(tz)
    except (ValueError, ZoneInfoNotFoundError):
        return
    db.execute("UPDATE users SET timezone = ? WHERE id = ?", (tz, user["id"]))


def app_deps(request: Request):
    app = request.app
    return app.state.settings, app.state.cipher


async def check_links_later(app, link_ids: list[int]) -> None:
    """Check just-saved links after the response has gone out, so saving
    doesn't wait on the network. Uses its own connection: the request's is
    closed by then."""
    db = dbm.connect(app.state.settings.database_path)
    try:
        links = [
            db.execute(
                "SELECT links.*, traqs.user_id FROM links "
                "JOIN traqs ON traqs.id = links.traq_id WHERE links.id = ?",
                (link_id,),
            ).fetchone()
            for link_id in link_ids
        ]
        await engine.refresh(
            db,
            app.state.settings,
            app.state.cipher,
            [link for link in links if link is not None],
        )
    finally:
        db.close()


# Lists -----------------------------------------------------------------------


def list_sort(request: Request, sort: str = "") -> str:
    """How lists are sorted: as picked (a column heading), else as picked
    last time, else most recently updated first."""
    if sort in SORTS:
        return sort
    remembered = request.cookies.get("sort")
    return remembered if remembered in SORTS else "updated"


def sorted_list(request, db, user, name: str, sort: str, **context):
    """A list page, remembering how it's sorted for the next list you open."""
    response = render(request, db, user, name, sort=sort, **context)
    response.set_cookie(
        "sort", sort, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax"
    )
    return response


def traq_list(request, db, user, sort: str, project=None):
    traqs = list_traqs(
        db,
        user["id"],
        status="open",
        sort=sort,
        project_id=project["id"] if project else None,
    )
    return sorted_list(
        request,
        db,
        user,
        "home.html",
        sort,
        traqs=traqs,
        project=project,
        current_project=project["id"] if project else None,
        section=None if project else "all",
    )


@router.get("/")
def home(
    request: Request,
    sort: str = Depends(list_sort),
    user=Depends(current_user),
    db=Depends(get_db),
):
    return traq_list(request, db, user, sort)


@router.get("/projects/{project_id}")
def project_page(
    request: Request,
    project_id: int,
    sort: str = Depends(list_sort),
    user=Depends(current_user),
    db=Depends(get_db),
):
    return traq_list(request, db, user, sort, get_project(db, user, project_id))


@router.post("/projects/{project_id}/rename", dependencies=CSRF)
def rename_project(
    project_id: int,
    name: str = Form(...),
    next: str = Form("/"),
    user=Depends(current_user),
    db=Depends(get_db),
):
    name = " ".join(name.split())
    if not name:
        raise HTTPException(400, "A project needs a name")
    with dbm.transaction(db):
        project = get_project(db, user, project_id)
        taken = db.execute(
            "SELECT 1 FROM projects WHERE user_id = ? AND name = ? AND id != ?",
            (user["id"], name, project["id"]),
        ).fetchone()
        if taken:
            raise HTTPException(400, f"You already have a project named “{name}”")
        db.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project["id"]))
    return back(safe_next(next))


@router.post("/projects/{project_id}/delete", dependencies=CSRF)
def delete_project(
    project_id: int,
    delete_traqs: bool = Form(False),
    next: str = Form("/"),
    user=Depends(current_user),
    db=Depends(get_db),
):
    with dbm.transaction(db):
        project = get_project(db, user, project_id)
        if delete_traqs:
            db.execute("DELETE FROM traqs WHERE project_id = ?", (project["id"],))
            images.drop_unused(db, user["id"])
        # Otherwise its traqs are kept without a project (ON DELETE SET NULL).
        db.execute("DELETE FROM projects WHERE id = ?", (project["id"],))
    # The page you were on may be gone (the project, or a traq deleted with it).
    next = safe_next(next)
    if next.startswith(("/projects/", "/traqs/")):
        next = "/"
    return back(next)


@router.get("/archive")
def archive(
    request: Request,
    sort: str = Depends(list_sort),
    user=Depends(current_user),
    db=Depends(get_db),
):
    traqs = list_traqs(db, user["id"], status="done", sort=sort)
    return sorted_list(
        request, db, user, "archive.html", sort, traqs=traqs, section="archive"
    )


@router.get("/search")
def search(
    request: Request,
    q: str = "",
    sort: str = Depends(list_sort),
    user=Depends(current_user),
    db=Depends(get_db),
):
    query = q.strip()
    results = find_traqs(db, user["id"], query, sort=sort)
    return sorted_list(request, db, user, "search.html", sort, q=query, results=results)


# A traq ----------------------------------------------------------------------


@router.post("/traqs", dependencies=CSRF)
async def create_traq(
    request: Request,
    text: str = Form(""),
    project_id: str = Form(""),
    new_project: str = Form(""),
    tz: str = Form(""),
    user=Depends(current_user),
    db=Depends(get_db),
):
    """Start a traq from what you have: a link (and a note), or just a note."""
    text = tidy_new_text(text)
    if not text:
        return back("/")
    with dbm.transaction(db):
        remember_timezone(db, user, tz)
        project = chosen_project(db, user, project_id, new_project)
        _, new_links = traqs.create(db, user["id"], project, text)
    # Check its links now, so the list shows the page's title, not its URL.
    settings, cipher = app_deps(request)
    await engine.refresh(db, settings, cipher, new_links)
    return back(f"/projects/{project}" if project else "/")


def traq_view(db: sqlite3.Connection, user, traq_id: int) -> dict:
    """What the traq's content shows: its title and text, with a card for each
    link line, each with what changed since you last acknowledged it. Only
    you clear a change (see `acknowledge`); looking doesn't."""
    traq = get_traq(db, user, traq_id)
    rows = db.execute("SELECT * FROM links WHERE traq_id = ?", (traq_id,)).fetchall()
    links = {
        link["url"]: {
            "link": link,
            "source": classify(link["url"]).source,
            "changes": link_changes(link),
            "shown": fingerprint(link["state"]),
        }
        for link in rows
    }
    view = {
        "traq": traq,
        "text_shown": fingerprint(traq["text"]),
        "blocks": blocks(traq["text"], links),
        # Whether to offer "Connect" or "Reconnect" where a link asks for one.
        "connected": {
            row["provider"]: row["status"]
            for row in db.execute(
                "SELECT provider, status FROM connections WHERE user_id = ?",
                (user["id"],),
            )
        },
    }
    return view


def fingerprint(value: str | None) -> str:
    """Identifies what the page showed (a card's state, the traq's text), so
    acting on it never touches a change that arrived after the page was shown."""
    return hashlib.sha256((value or "").encode()).hexdigest()[:16]


@router.get("/traqs/{traq_id}")
def show_traq(
    request: Request, traq_id: int, user=Depends(current_user), db=Depends(get_db)
):
    """Shows the last known state right away; links due a check are checked
    after the page loads (see `traq_live`), so opening never waits on them."""
    traq = get_traq(db, user, traq_id)
    project = get_project(db, user, traq["project_id"]) if traq["project_id"] else None
    done = traq["status"] == "done"
    return render(
        request,
        db,
        user,
        "traq.html",
        **traq_view(db, user, traq_id),
        check_links=bool(engine.due_links(db, engine.ON_OPEN_MAX_AGE, traq_id=traq_id)),
        project=project,
        # Highlight where this traq lives, as its breadcrumb does: Archive when
        # done, else its project, else Everything.
        section="archive" if done else (None if project else "all"),
        current_project=None if done else traq["project_id"],
    )


@router.get("/traqs/{traq_id}/live")
async def traq_live(
    request: Request, traq_id: int, user=Depends(current_user), db=Depends(get_db)
):
    """Check the traq's due links, then send its content back to swap in."""
    get_traq(db, user, traq_id)
    settings, cipher = app_deps(request)
    await engine.refresh(
        db,
        settings,
        cipher,
        engine.due_links(db, engine.ON_OPEN_MAX_AGE, traq_id=traq_id),
    )
    return templates.TemplateResponse(
        request,
        "_traq_view.html",
        {"user": user, **traq_view(db, user, traq_id)},
    )


@router.post("/traqs/{traq_id}/links/{link_id}/acknowledge", dependencies=CSRF)
def acknowledge(
    traq_id: int,
    link_id: int,
    shown: str = Form(...),
    user=Depends(current_user),
    db=Depends(get_db),
):
    """You've seen what changed on this card: it dims until something else does."""
    get_traq(db, user, traq_id)
    link = db.execute(
        "SELECT state FROM links WHERE id = ? AND traq_id = ?", (link_id, traq_id)
    ).fetchone()
    if link is None:
        raise HTTPException(404, "Link not found")
    if fingerprint(link["state"]) == shown:
        db.execute("UPDATE links SET seen_state = state WHERE id = ?", (link_id,))
    return back(f"/traqs/{traq_id}")


@router.post("/traqs/{traq_id}/edit", dependencies=CSRF)
def edit_traq(
    traq_id: int,
    text: str = Form(""),
    project_id: str = Form(""),
    new_project: str = Form(""),
    user=Depends(current_user),
    db=Depends(get_db),
):
    with dbm.transaction(db):
        get_traq(db, user, traq_id)
        db.execute(
            "UPDATE traqs SET project_id = ? WHERE id = ?",
            (chosen_project(db, user, project_id, new_project), traq_id),
        )
        traqs.set_text(db, traq_id, text.strip())
        images.drop_unused(db, user["id"])
    # Links added while editing are checked by the traq's page, which opens next.
    return back(f"/traqs/{traq_id}")


@router.post("/traqs/{traq_id}/tasks", dependencies=CSRF)
def tick_task(
    traq_id: int,
    line: int = Form(...),
    done: bool = Form(False),
    shown: str = Form(...),
    user=Depends(current_user),
    db=Depends(get_db),
):
    """Tick (or untick) a checklist item from the traq's page. Only in the text
    the page showed: if it's changed since, nothing is ticked, and the page
    shows the text as it is now."""
    with dbm.transaction(db):
        traq = get_traq(db, user, traq_id)
        ticked = document.tick(traq["text"], line, done)
        if fingerprint(traq["text"]) == shown and ticked is not None:
            traqs.set_text(db, traq_id, ticked)
    return back(f"/traqs/{traq_id}")


@router.post("/traqs/{traq_id}/done", dependencies=CSRF)
def toggle_done(traq_id: int, user=Depends(current_user), db=Depends(get_db)):
    traq = get_traq(db, user, traq_id)
    db.execute(
        "UPDATE traqs SET status = ? WHERE id = ?",
        ("open" if traq["status"] == "done" else "done", traq_id),
    )
    return back(f"/traqs/{traq_id}")


@router.post("/traqs/{traq_id}/delete", dependencies=CSRF)
def delete_traq(traq_id: int, user=Depends(current_user), db=Depends(get_db)):
    traq = get_traq(db, user, traq_id)
    db.execute("DELETE FROM traqs WHERE id = ?", (traq_id,))
    images.drop_unused(db, user["id"])
    return back(f"/projects/{traq['project_id']}" if traq["project_id"] else "/")
