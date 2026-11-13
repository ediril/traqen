"""A traq's text, the links in it, and what it's called.

A traq is what the user wrote (Markdown, see `document`). Each line that is
just a link is tracked (with live state) in `links`; a link inside a sentence
stays part of the text. A traq's text is only ever written through `create`
and `set_text`, which keep its links and title in step with it.
"""

import sqlite3

from . import db as dbm
from . import document
from .document import link_in
from .live.links import page_key
from .live.state import describe_changes

TITLE_LENGTH = 80


def split_link(text: str) -> tuple[str | None, str]:
    """`"https://… the fix we're waiting on"` -> (url, note); else (None, text)."""
    parts = text.strip().split(None, 1)
    if parts and link_in(parts[0]):
        return parts[0], parts[1].strip() if len(parts) > 1 else ""
    return None, text.strip()


def tidy_new_text(text: str) -> str:
    """A new traq as typed in the New traq box (plain text), as Markdown: each
    line a paragraph, and "link + note" on one line split so the link has a
    line of its own, so it's tracked."""
    first, _, rest = document.normalized(text).strip().partition("\n")
    url, note = split_link(first)
    if url and note:
        first = f"{url}\n{note}"
    return document.paragraphs(f"{first}\n{rest}")


def link_pages(text: str) -> dict[str, str]:
    """The pages a traq's text links to (see `page_key`), in order, each with
    the URL it's first written as; a second link to the same page stays a
    plain line."""
    pages: dict[str, str] = {}
    for url in document.link_lines(text).values():
        pages.setdefault(page_key(url), url)
    return pages


def has_page(text: str, url: str) -> bool:
    """Whether a traq's text already links to the page `url` points to."""
    return page_key(url) in link_pages(text)


def name_link(db: sqlite3.Connection, traq_id: int, url: str, title: str) -> None:
    """Give a link a title before it's been read (e.g. the page's title, sent by
    the browser), so a page Traqen can't read isn't known by its URL."""
    if title:
        db.execute(
            "UPDATE links SET title = ? "
            "WHERE traq_id = ? AND page = ? AND title IS NULL",
            (title, traq_id, page_key(url)),
        )
        update_title(db, traq_id)


def first_line(text: str) -> str:
    """The first non-empty line's text, shortened at a word boundary if it's
    long."""
    number = document.first_line_number(text)
    line = "" if number is None else document.plain(text.split("\n")[number])
    if len(line) <= TITLE_LENGTH:
        return line
    cut = line[:TITLE_LENGTH].rsplit(" ", 1)[0] or line[:TITLE_LENGTH]
    return cut.rstrip(" ,.;:-") + "…"


def create(
    db: sqlite3.Connection, user_id: int, project_id: int | None, text: str
) -> tuple[int, list[sqlite3.Row]]:
    """A new traq with this text; returns its id and its links (unchecked)."""
    text = document.normalized(text)
    with dbm.transaction(db):
        now = dbm.stamp()
        traq_id = db.execute(
            "INSERT INTO traqs (user_id, project_id, title, text, created_at, "
            "updated_at) VALUES (?, ?, '', ?, ?, ?)",
            (user_id, project_id, text, now, now),
        ).lastrowid
        return traq_id, _sync_links(db, traq_id)


def set_text(db: sqlite3.Connection, traq_id: int, text: str) -> list[sqlite3.Row]:
    """Replace a traq's text; returns the links it gained (unchecked)."""
    text = document.normalized(text)
    with dbm.transaction(db):
        db.execute(
            "UPDATE traqs SET text = ?, updated_at = ? WHERE id = ? AND text != ?",
            (text, dbm.stamp(), traq_id, text),
        )
        return _sync_links(db, traq_id)


def _sync_links(db: sqlite3.Connection, traq_id: int) -> list[sqlite3.Row]:
    """Make the tracked links match the pages the traq's text links to: new
    pages are added (unchecked), removed ones dropped, and the rest keep their
    live state, even when written another way (`/pull/7` → `/pull/7/files`).
    Returns the new links (with user_id) so the caller can check them."""
    traq = db.execute(
        "SELECT user_id, text FROM traqs WHERE id = ?", (traq_id,)
    ).fetchone()
    pages = link_pages(traq["text"])
    existing = {
        row["page"]: row["url"]
        for row in db.execute(
            "SELECT page, url FROM links WHERE traq_id = ?", (traq_id,)
        )
    }
    for page in existing.keys() - pages.keys():
        db.execute("DELETE FROM links WHERE traq_id = ? AND page = ?", (traq_id, page))
    added = []
    for page, url in pages.items():
        if page not in existing:
            cursor = db.execute(
                "INSERT INTO links (traq_id, page, url, created_at) "
                "VALUES (?, ?, ?, ?)",
                (traq_id, page, url, dbm.stamp()),
            )
            added.append(cursor.lastrowid)
        elif existing[page] != url:
            db.execute(
                "UPDATE links SET url = ? WHERE traq_id = ? AND page = ?",
                (url, traq_id, page),
            )
    update_title(db, traq_id)
    return [
        db.execute(
            "SELECT links.*, ? AS user_id FROM links WHERE id = ?",
            (traq["user_id"], link_id),
        ).fetchone()
        for link_id in added
    ]


def update_title(db: sqlite3.Connection, traq_id: int) -> None:
    """Named after the first line; when that line is a link, after its page
    title (or the URL until the page has been read)."""
    traq = db.execute("SELECT text FROM traqs WHERE id = ?", (traq_id,)).fetchone()
    if traq is None:  # deleted while one of its links was being checked
        return
    number = document.first_line_number(traq["text"])
    url = document.link_lines(traq["text"]).get(number)
    if url:
        link = db.execute(
            "SELECT title FROM links WHERE traq_id = ? AND page = ?",
            (traq_id, page_key(url)),
        ).fetchone()
        title = (link and link["title"]) or url
    else:
        title = first_line(traq["text"]) or "Untitled"
    db.execute("UPDATE traqs SET title = ? WHERE id = ?", (title, traq_id))


def link_changes(link) -> list[str]:
    """What changed on a link since you last marked it seen."""
    return describe_changes(
        dbm.load_json(link["seen_state"]), dbm.load_json(link["state"])
    )


def blocks(text: str, links: dict[str, dict]) -> list[dict]:
    """The text as shown on the traq's page, after its title: runs of text (as
    HTML), and a link card wherever a line is a tracked link. The first line is
    the title, so it's left out, unless it's a link (its card still shows) or
    was too long to be the title in full."""
    lines = text.split("\n")
    cards = {
        number: links[url]
        for number, url in document.link_lines(text).items()
        if url in links
    }
    title = document.first_line_number(text)
    if (
        title is not None
        and title not in cards
        and len(document.plain(lines[title])) <= TITLE_LENGTH
    ):
        lines[title] = ""
    shown, start = [], 0

    def flush(end: int) -> None:
        run = "\n".join(lines[start:end])
        if run.strip():
            shown.append({"kind": "text", "html": document.render(run, start)})

    for number in sorted(cards):
        flush(number)
        shown.append({"kind": "link", **cards[number]})
        start = number + 1
    flush(len(lines))
    return shown


# A list row: the traq, its project's name, and when it was last updated: the
# latest of its text changing and anything it links to changing.
_LIST_ROW = """
    traqs.*, projects.name AS project_name,
    MAX(traqs.updated_at, COALESCE(
        (SELECT MAX(l.updated_at) FROM links AS l WHERE l.traq_id = traqs.id), ''
    )) AS last_updated
"""

# How a list can be sorted (the Title, Created, and Updated column headings).
SORTS = {
    "title": "traqs.title COLLATE NOCASE, traqs.id DESC",
    "created": "traqs.created_at DESC, traqs.id DESC",
    "updated": "last_updated DESC, traqs.id DESC",
}


def list_traqs(
    db: sqlite3.Connection,
    user_id: int,
    *,
    status: str,
    sort: str,
    project_id: int | None = None,
) -> list[sqlite3.Row]:
    """The user's open or done traqs, all of them or one project's."""
    scope = "AND traqs.project_id = ?" if project_id is not None else ""
    return db.execute(
        f"""
        SELECT {_LIST_ROW} FROM traqs
        LEFT JOIN projects ON projects.id = traqs.project_id
        WHERE traqs.user_id = ? AND traqs.status = ? {scope}
        ORDER BY {SORTS[sort]}
        """,
        (user_id, status, *([project_id] if project_id is not None else [])),
    ).fetchall()


def find_traqs(
    db: sqlite3.Connection,
    user_id: int,
    query: str,
    *,
    open_only: bool = False,
    sort: str = "created",
    limit: int = 100,
) -> list[sqlite3.Row]:
    """Traqs whose title, text, or links mention `query`."""
    query = query.strip()
    if not query:
        return []
    pattern = (
        "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )
    columns = ["traqs.title", "traqs.text", "links.title", "links.url"]
    where = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns)
    status = "AND traqs.status = 'open'" if open_only else ""
    return db.execute(
        f"""
        SELECT DISTINCT {_LIST_ROW} FROM traqs
        LEFT JOIN links ON links.traq_id = traqs.id
        LEFT JOIN projects ON projects.id = traqs.project_id
        WHERE traqs.user_id = ? {status} AND ({where})
        ORDER BY {SORTS[sort]} LIMIT ?
        """,
        (user_id, *[pattern] * len(columns), limit),
    ).fetchall()


def recent_traqs(
    db: sqlite3.Connection, user_id: int, limit: int, project_id: int | None = None
) -> list[sqlite3.Row]:
    """The most recent open traqs, from one project or (None) from all."""
    scope = "AND traqs.project_id = ?" if project_id is not None else ""
    params = [user_id, *([project_id] if project_id is not None else []), limit]
    return db.execute(
        f"""
        SELECT traqs.*, projects.name AS project_name FROM traqs
        LEFT JOIN projects ON projects.id = traqs.project_id
        WHERE traqs.user_id = ? AND traqs.status = 'open' {scope}
        ORDER BY traqs.created_at DESC, traqs.id DESC LIMIT ?
        """,
        params,
    ).fetchall()
