import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    google_sub TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, name)
);

-- A traq is what the user wrote (`text`, Markdown: see document.py). Each line
-- that is just a link is tracked in `links`. `title` is derived from the first line
-- (a link's page title when that line is a link). `updated_at` is when its
-- text last changed.
CREATE TABLE IF NOT EXISTS traqs (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS traqs_user_status ON traqs(user_id, status, created_at);

-- The pages a traq's text links to (kept in step by traqs.create/set_text), one
-- row per page (`page`, see live.links.page_key) under the URL it was first
-- written as, with live state:
--   state         current snapshot (JSON) from the last successful check
--   seen_state    snapshot as of the last time the user marked it seen
--   live_connect  provider to connect to make an unavailable link live
--   live_fix_url  page to fix access at (e.g. GitHub's SSO authorization)
--   updated_at    when the linked thing last changed: its own last-updated time
--                 (GitHub, Jira), or when a check first saw it different (a
--                 public page); NULL until known
-- "Changed since you last saw it" is the difference between state and seen_state.
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    traq_id INTEGER NOT NULL REFERENCES traqs(id) ON DELETE CASCADE,
    page TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    live TEXT NOT NULL DEFAULT 'pending'
        CHECK (live IN ('pending', 'live', 'unavailable', 'error')),
    live_reason TEXT NOT NULL DEFAULT '',
    live_connect TEXT,
    live_fix_url TEXT,
    state TEXT,
    seen_state TEXT,
    checked_at TEXT,
    updated_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (traq_id, page)
);
CREATE INDEX IF NOT EXISTS links_page ON links(page);

-- Images in traqs (added in the editor), shown at /images/<id>. The id is
-- random, so a traq's text names its images by it; an image no traq names any
-- more is dropped a day later (see images.drop_unused).
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL,
    data BLOB NOT NULL,
    created_at TEXT NOT NULL
);

-- Per-user OAuth connections to work tools. Tokens are encrypted (crypto.py).
CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('github', 'jira')),
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    expires_at TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'reconnect')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, provider)
);
"""


@contextmanager
def transaction(db: sqlite3.Connection) -> Iterator[None]:
    """Make the writes inside one change: all of them land, or none do. The
    write lock is taken up front, so read-then-write steps can't interleave
    with another writer. Inside an open transaction, it just joins it."""
    if db.in_transaction:
        yield
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def connect(path: Path) -> sqlite3.Connection:
    # One connection per request or job; it is used from a single task at a time,
    # but FastAPI may create it on a worker thread.
    db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    return db


# Bump whenever SCHEMA changes in a way an existing database can't follow.
SCHEMA_VERSION = 5


def init(path: Path) -> None:
    db = connect(path)
    try:
        has_tables = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table'"
        ).fetchone()
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if has_tables and version != SCHEMA_VERSION:
            raise RuntimeError(
                f"{path} was created by an older version of Traqen. "
                "Delete it to start fresh (there are no migrations yet)."
            )
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        db.close()


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def stamp(moment: datetime | None = None) -> str:
    """A time as stored: UTC, to the second, so stamps sort as text."""
    return (moment or now()).astimezone(UTC).replace(microsecond=0).isoformat()


def ago(delta: timedelta) -> str:
    return stamp(now() - delta)


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def load_json(value: str | None):
    return json.loads(value) if value else None


def dump_json(value) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))
