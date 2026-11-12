"""Checking links and recording their live state."""

import asyncio
import logging
import sqlite3
from datetime import timedelta

import httpx

from .. import db as dbm
from ..config import Settings
from ..crypto import TokenCipher
from ..integrations import PROVIDERS, Credentials
from ..traqs import update_title
from . import github, jira, web
from .links import classify
from .state import (
    CheckFailed,
    ConnectionRevoked,
    Snapshot,
    unavailable,
)

log = logging.getLogger("traqen.live")

CONCURRENCY = 6
# Opening a traq checks its links, unless they were checked moments ago.
ON_OPEN_MAX_AGE = timedelta(minutes=1)
# A whole check, every request in it included. The client's timeout only
# bounds each read, so a page that trickles bytes could otherwise hold a
# check (and the refresh waiting on it) open for as long as it likes.
CHECK_TIMEOUT_SECONDS = 20


def http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(8.0),
        headers={"User-Agent": f"Traqen/0.1 (+{settings.base_url})"},
    )


async def check_url(client: httpx.AsyncClient, url: str, credentials) -> Snapshot:
    link = classify(url)
    if link.checker == "github":
        return await github.check(client, link.ref, credentials)
    if link.checker == "jira":
        return await jira.check(client, link.ref, credentials)
    if link.checker == "web":
        return await web.check(client, url)
    return unavailable(link.unsupported_reason)


def is_checkable(url: str) -> bool:
    return classify(url).checker is not None


def due_links(
    db: sqlite3.Connection,
    max_age: timedelta,
    *,
    traq_id: int | None = None,
) -> list[sqlite3.Row]:
    """Links in open traqs whose last check is older than `max_age`. A link
    that has never been checked is always due, so it gets its first state even
    when there's nothing live to check (e.g. a Slack link); after that, only
    links that can be live are checked again."""
    query = """
        SELECT links.*, traqs.user_id FROM links
        JOIN traqs ON traqs.id = links.traq_id
        WHERE traqs.status = 'open'
          AND (links.checked_at IS NULL OR links.checked_at <= ?)
    """
    params: list = [dbm.ago(max_age)]
    if traq_id is not None:
        query += " AND traqs.id = ?"
        params.append(traq_id)
    rows = db.execute(query, params).fetchall()
    return [
        row for row in rows if row["checked_at"] is None or is_checkable(row["url"])
    ]


async def refresh(
    db: sqlite3.Connection,
    settings: Settings,
    cipher: TokenCipher,
    links: list[sqlite3.Row],
) -> None:
    """Check the given links (rows must include user_id) and store each result
    as soon as it's in, so one slow or failing link holds up no other."""
    if not links:
        return
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with http_client(settings) as client:
        credentials = {
            user_id: Credentials(db, cipher, settings, client, user_id)
            for user_id in {link["user_id"] for link in links}
        }

        async def run(link):
            async with semaphore:
                outcome = await check(client, link, credentials[link["user_id"]])
            try:
                record(db, link, outcome)
            except Exception:
                log.exception("Saving the check of %s failed", link["url"])

        await asyncio.gather(*(run(link) for link in links))


async def check(
    client: httpx.AsyncClient, link: sqlite3.Row, credentials: Credentials
) -> Snapshot | Exception:
    """One link's check; a failure is returned (to record on this link only),
    never raised."""
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            return await check_url(client, link["url"], credentials)
    except (CheckFailed, ConnectionRevoked) as exc:
        return exc
    except TimeoutError:
        return CheckFailed("Check took too long")
    except httpx.HTTPError as exc:
        return CheckFailed(f"Check failed ({type(exc).__name__})")
    except Exception:
        # An unexpected response must not break the page or the refresh of
        # other links.
        log.exception("Checking %s failed", link["url"])
        return CheckFailed("Check failed unexpectedly")


def record(
    db: sqlite3.Connection, link: sqlite3.Row, outcome: Snapshot | Exception
) -> None:
    with dbm.transaction(db):
        _record(db, link, outcome)


def _record(
    db: sqlite3.Connection, link: sqlite3.Row, outcome: Snapshot | Exception
) -> None:
    # The link (or its traq) may have been deleted while it was being checked;
    # then these updates match nothing.
    checked = dbm.stamp()
    if isinstance(outcome, ConnectionRevoked):
        label = PROVIDERS[outcome.provider]
        outcome = unavailable(
            f"Reconnect {label} to keep this live", connect=outcome.provider
        )
    if isinstance(outcome, Exception):
        # Transient: keep the last known state, flag that it may be out of date.
        db.execute(
            "UPDATE links SET live = 'error', live_reason = ?, checked_at = ? "
            "WHERE id = ?",
            (str(outcome) or "Check failed", checked, link["id"]),
        )
        return
    if outcome.live == "unavailable":
        db.execute(
            """
            UPDATE links SET live = 'unavailable', live_reason = ?,
                live_connect = ?, live_fix_url = ?, checked_at = ?
            WHERE id = ?
            """,
            (outcome.reason, outcome.connect, outcome.fix_url, checked, link["id"]),
        )
        return

    new_state = dbm.dump_json(outcome.state)
    db.execute(
        """
        UPDATE links SET live = 'live', live_reason = '', live_connect = NULL,
            live_fix_url = NULL, title = COALESCE(?, title), state = ?,
            seen_state = COALESCE(seen_state, ?), checked_at = ?
        WHERE id = ?
        """,
        (outcome.state.get("title") or None, new_state, new_state, checked, link["id"]),
    )
    update_title(db, link["traq_id"])
