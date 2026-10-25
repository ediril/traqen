"""Saving the page you're on from the browser (bookmarklet popup).

The server decides everything the save window shows. For each project the
picker offers (keyed by its option's value: "" for no project, else its id),
it renders the recent traqs and, where a traq in that project already has the
page, an "Already in" notice. The window's script only shows the parts for
the picked project.
"""

from dataclasses import dataclass

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request

from . import db as dbm
from .auth import CSRF, current_user, get_db
from .bookmarklet import MAX_NOTE
from .live.links import classify, normalize_url, page_key
from .shell import projects
from .templating import templates
from .traqs import (
    append_line,
    create,
    find_traqs,
    link_in,
    name_link,
    recent_traqs,
    set_text,
)
from .web import check_links_later, chosen_project, get_traq, remember_timezone

router = APIRouter()

RECENT = 3  # recent traqs offered above the search box
MATCHES = 8  # search results shown at most


@dataclass(frozen=True)
class Page:
    """The page being saved, as the bookmarklet sent it."""

    url: str
    title: str
    note: str
    popup: bool

    @classmethod
    def sent(cls, url: str, title: str, note: str, popup: bool) -> "Page":
        """Raises ValueError for anything but a web page."""
        return cls(
            normalize_url(url),
            " ".join(title.split())[:300],
            note.strip()[:MAX_NOTE],
            popup,
        )


def project_key(project_id: int | None) -> str:
    return "" if project_id is None else str(project_id)


def holders(db, user_id: int, url: str) -> list:
    """The user's open traqs that already link to this page."""
    return db.execute(
        """
        SELECT traqs.* FROM links JOIN traqs ON traqs.id = links.traq_id
        WHERE traqs.user_id = ? AND traqs.status = 'open' AND links.page = ?
        """,
        (user_id, page_key(url)),
    ).fetchall()


def choices(traqs, url: str, held: set[int]) -> list[dict]:
    """Traqs to offer in the window. One that already has the page is shown with
    a check and can't be picked; one that's nothing but the page isn't shown."""
    return [
        {
            "id": traq["id"],
            "title": traq["title"],
            "project": traq["project_name"],
            "has_page": traq["id"] in held,
        }
        for traq in traqs
        if not (traq["id"] in held and just_the_page(traq, url))
    ]


def just_the_page(traq, url: str) -> bool:
    lines = [line for line in traq["text"].splitlines() if line.strip()]
    page = page_key(url)
    return all((link := link_in(line)) and page_key(link) == page for line in lines)


def capture_form(
    request, db, user, page: Page, project=None, already_there=False, status=200
):
    """The save window, with `project` picked. `already_there` says the traq
    picked last time turned out to have the page already."""
    held = holders(db, user["id"], page.url)
    held_ids = {traq["id"] for traq in held}

    def recent(project_id):
        # Enough extra to make up for traqs that are just the page (left out).
        found = recent_traqs(db, user["id"], RECENT + len(held), project_id)
        return choices(found, page.url, held_ids)[:RECENT]

    user_projects = projects(db, user["id"])
    # With no project picked, recent traqs come from every project.
    groups = [{"key": "", "name": "Everything", "recent": recent(None)}] + [
        {"key": str(p["id"]), "name": p["name"], "recent": recent(p["id"])}
        for p in user_projects
    ]
    return templates.TemplateResponse(
        request,
        "capture.html",
        {
            "page": page,
            "source": classify(page.url).source,
            "projects": user_projects,
            "groups": groups,
            "project": project,
            "selected": project_key(project),
            "held_in": {project_key(traq["project_id"]) for traq in held},
            "already_there": already_there,
        },
        status_code=status,
    )


@router.get("/capture")
def capture_page(
    request: Request,
    url: str = "",
    title: str = "",
    note: str = "",
    popup: bool = False,
    user=Depends(current_user),
    db=Depends(get_db),
):
    try:
        page = Page.sent(url, title, note, popup)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "capture.html",
            {"error": "This page can’t be saved — only web pages can."},
            status_code=400,
        )
    return capture_form(request, db, user, page)


@router.get("/capture/traqs")
def capture_traq_search(
    request: Request,
    q: str = "",
    url: str = "",
    user=Depends(current_user),
    db=Depends(get_db),
):
    """Open traqs to add the page to, found as you type, as rows for the window."""
    held = {traq["id"] for traq in holders(db, user["id"], url)}
    found = find_traqs(db, user["id"], q, open_only=True, limit=MATCHES + len(held))
    return templates.TemplateResponse(
        request, "_capture_rows.html", {"rows": choices(found, url, held)[:MATCHES]}
    )


@router.post("/capture", dependencies=CSRF)
def capture_save(
    request: Request,
    background: BackgroundTasks,
    url: str = Form(...),
    traq: str = Form(...),
    title: str = Form(""),
    project_id: str = Form(""),
    new_project: str = Form(""),
    note: str = Form(""),
    tz: str = Form(""),
    popup: bool = Form(False),
    user=Depends(current_user),
    db=Depends(get_db),
):
    try:
        page = Page.sent(url, title, note, popup)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # The page goes into the traq's text as a link line, with the selected
    # text (if any) after it. A page is never added where it's already linked:
    # not to a traq that has it, nor as a new traq in a project (or among traqs
    # without one) where an open traq has it. Checking and saving happen
    # together, so a save can't slip in between them.
    with dbm.transaction(db):
        remember_timezone(db, user, tz)
        held = holders(db, user["id"], page.url)
        if traq == "new":
            # A project named here is only created when it's new, so it holds
            # nothing yet: a refused save never leaves a project behind.
            project = chosen_project(db, user, project_id, new_project)
            if any(t["project_id"] == project for t in held):
                return capture_form(request, db, user, page, project, status=409)
            traq_id, new_links = create(
                db, user["id"], project, append_line(page.url, page.note)
            )
        else:
            try:
                target = get_traq(db, user, int(traq))
            except ValueError as exc:
                raise HTTPException(400, "Pick a traq") from exc
            if any(t["id"] == target["id"] for t in held):
                return capture_form(
                    request, db, user, page, already_there=True, status=409
                )
            text = append_line(target["text"], page.url)
            if page.note:
                text = append_line(text, page.note)
            traq_id, new_links = target["id"], set_text(db, target["id"], text)
        name_link(db, traq_id, page.url, page.title)

    # Respond (and close the window) right away; check new links after.
    background.add_task(
        check_links_later, request.app, [link["id"] for link in new_links]
    )
    return templates.TemplateResponse(
        request,
        "capture_done.html",
        {"traq": get_traq(db, user, traq_id), "url": page.url, "popup": popup},
    )
