"""Rendering pages inside the app shell (sidebar + content)."""

import sqlite3

from fastapi import Request

from .bookmarklet import bookmarklet_code
from .templating import templates


def projects(db: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """The user's projects with how many open traqs each has."""
    return db.execute(
        """
        SELECT projects.id, projects.name, COUNT(traqs.id) AS open_traqs
        FROM projects
        LEFT JOIN traqs ON traqs.project_id = projects.id AND traqs.status = 'open'
        WHERE projects.user_id = ?
        GROUP BY projects.id ORDER BY projects.name COLLATE NOCASE
        """,
        (user_id,),
    ).fetchall()


def nav_rows(db, user_id, user_projects, current_project, section) -> list[dict]:
    """The sidebar's list: Everything, then each project, all alike (name,
    open traq count, highlight). Only projects can be renamed or deleted."""
    everything = db.execute(
        "SELECT COUNT(*) FROM traqs WHERE user_id = ? AND status = 'open'", (user_id,)
    ).fetchone()[0]
    return [
        {
            "href": "/",
            "name": "Everything",
            "open_traqs": everything,
            "current": section == "all",
            "project_id": None,
        }
    ] + [
        {
            "href": f"/projects/{p['id']}",
            "name": p["name"],
            "open_traqs": p["open_traqs"],
            "current": current_project == p["id"],
            "project_id": p["id"],
        }
        for p in user_projects
    ]


def render(
    request: Request,
    db: sqlite3.Connection,
    user,
    name: str,
    status_code: int = 200,
    current_project: int | None = None,
    section: str | None = None,
    **context,
):
    """`section` ("all" or "archive") or `current_project` says which sidebar
    item the page belongs to, so it's highlighted."""
    user_projects = projects(db, user["id"])
    return templates.TemplateResponse(
        request,
        name,
        {
            "user": user,
            "shell": True,
            "projects": user_projects,
            "nav": nav_rows(db, user["id"], user_projects, current_project, section),
            # The project being viewed: the default for a new traq.
            "current_project": current_project,
            "section": section,
            "bookmarklet": bookmarklet_code(request.app.state.settings.base_url),
            **context,
        },
        status_code=status_code,
    )
