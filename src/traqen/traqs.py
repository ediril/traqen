"""A traq's text, the links in it, and what it's called.

A traq is what the user wrote, like a sticky note. Each line that is just a
link is tracked (with live state) in `links`; a link inside a sentence stays
plain text. A traq's text is only ever written through `create` and
`set_text`, which keep its links and title in step with it.
"""

import re
import sqlite3

from . import db as dbm
from .live.links import normalize_url, page_key

TITLE_LENGTH = 80

# A pasted link: an explicit http(s) URL, or a bare host followed by a path.
_LINK = re.compile(r"^(https?://\S+|(www\.)?[\w-]+(\.[\w-]+)+/\S*)$", re.IGNORECASE)


def link_in(line: str) -> str | None:
    """The normalized URL when `line` is just a link, else None."""
    line = line.strip()
    if not _LINK.match(line):
        return None
    try:
        return normalize_url(line)
    except ValueError:
        return None


def split_link(text: str) -> tuple[str | None, str]:
    """`"https://… the fix we're waiting on"` -> (url, note); else (None, text)."""
    parts = text.strip().split(None, 1)
    if parts and link_in(parts[0]):
        return parts[0], parts[1].strip() if len(parts) > 1 else ""
    return None, text.strip()


def tidy_new_text(text: str) -> str:
    """A new traq typed as "link + note" on one line keeps the link on a line
    of its own, so it's tracked."""
    first, _, rest = text.strip().partition("\n")
    url, note = split_link(first)
    if url and note:
        first = f"{url}\n{note}"
    return f"{first}\n{rest}".strip()


def append_line(text: str, line: str) -> str:
    """Add a line at the end of a traq's text."""
    return f"{text.rstrip()}\n{line}".strip()


def link_pages(text: str) -> dict[str, str]:
    """The pages a traq's text links to (see `page_key`), in order, each with
    the URL it's first written as; a second link to the same page stays a
    plain line."""
    pages: dict[str, str] = {}
    for line in text.splitlines():
        url = link_in(line)
        if url:
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
    """The first non-empty line, shortened at a word boundary if it's long."""
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(line) <= TITLE_LENGTH:
        return line
    cut = line[:TITLE_LENGTH].rsplit(" ", 1)[0] or line[:TITLE_LENGTH]
    return cut.rstrip(" ,.;:-") + "…"


def create(
    db: sqlite3.Connection, user_id: int, project_id: int | None, text: str
) -> tuple[int, list[sqlite3.Row]]:
    """A new traq with this text; returns its id and its links (unchecked)."""
    with dbm.transaction(db):
        traq_id = db.execute(
            "INSERT INTO traqs (user_id, project_id, title, text, created_at) "
            "VALUES (?, ?, '', ?, ?)",
            (user_id, project_id, text, dbm.stamp()),
        ).lastrowid
        return traq_id, _sync_links(db, traq_id)


def set_text(db: sqlite3.Connection, traq_id: int, text: str) -> list[sqlite3.Row]:
    """Replace a traq's text; returns the links it gained (unchecked)."""
    with dbm.transaction(db):
        db.execute("UPDATE traqs SET text = ? WHERE id = ?", (text, traq_id))
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
    line = next((ln for ln in traq["text"].splitlines() if ln.strip()), "")
    url = link_in(line)
    if url:
        link = db.execute(
            "SELECT title FROM links WHERE traq_id = ? AND page = ?",
            (traq_id, page_key(url)),
        ).fetchone()
        title = (link and link["title"]) or url
    else:
        title = first_line(line) or "Untitled"
    db.execute("UPDATE traqs SET title = ? WHERE id = ?", (title, traq_id))


def blocks(text: str, links: dict[str, dict]) -> list[dict]:
    """The text as shown on the traq's page, after its title: runs of text, and
    a link card wherever a line is a link. The first line is the title, so
    it's left out, unless it's a link (its card still shows) or was too long
    to be the title in full."""
    lines = text.strip().splitlines()
    if lines and not link_in(lines[0]) and len(lines[0].strip()) <= TITLE_LENGTH:
        lines = lines[1:]
    shown, run = [], []

    def flush():
        if "\n".join(run).strip():
            shown.append({"kind": "text", "text": "\n".join(run).strip("\n")})
        run.clear()

    for line in lines:
        url = link_in(line)
        if url and url in links:
            flush()
            shown.append({"kind": "link", **links[url]})
        else:
            run.append(line)
    flush()
    return shown


def find_traqs(
    db: sqlite3.Connection,
    user_id: int,
    query: str,
    *,
    open_only: bool = False,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """Traqs whose title, text, or links mention `query`, newest first."""
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
        SELECT DISTINCT traqs.*, projects.name AS project_name FROM traqs
        LEFT JOIN links ON links.traq_id = traqs.id
        LEFT JOIN projects ON projects.id = traqs.project_id
        WHERE traqs.user_id = ? {status} AND ({where})
        ORDER BY traqs.created_at DESC, traqs.id DESC LIMIT ?
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
