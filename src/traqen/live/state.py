"""Live-state snapshots and how changes between them are described."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Snapshot:
    """Result of checking one link.

    `state` is what gets stored and compared:
      title    the thing's current title
      fields   ordered display fields that matter for change detection
      content  hash of a page's main content (public pages only)
      updated  source's own last-updated time (shown, not compared)
      preview  short text preview (shown, not compared)
    """

    live: str  # 'live' or 'unavailable'
    state: dict | None = None
    reason: str = ""
    # How the user can make an unavailable link live: connect their account
    # with a provider, or fix access on a page (e.g. GitHub's SSO authorization).
    connect: str | None = None
    fix_url: str | None = None


def live(state: dict) -> Snapshot:
    return Snapshot("live", state=state)


def unavailable(
    reason: str, *, connect: str | None = None, fix_url: str | None = None
) -> Snapshot:
    return Snapshot("unavailable", reason=reason, connect=connect, fix_url=fix_url)


class CheckFailed(Exception):
    """A transient failure (timeout, rate limit, 5xx): keep the last known state."""


class ConnectionRevoked(Exception):
    """The user's token was rejected and could not be refreshed."""

    def __init__(self, provider: str):
        super().__init__(provider)
        self.provider = provider


COUNTERS = {"Comments": ("new comment", "new comments")}


def describe_changes(old: dict | None, new: dict | None) -> list[str]:
    if not old or not new:
        return []
    changes = []
    if old.get("title") != new.get("title") and old.get("title"):
        changes.append(f"Title changed to “{new.get('title')}”")
    old_fields, new_fields = old.get("fields", {}), new.get("fields", {})
    for name, value in new_fields.items():
        previous = old_fields.get(name)
        if previous == value:
            continue
        if name in COUNTERS:
            if (
                isinstance(previous, int)
                and isinstance(value, int)
                and value > previous
            ):
                singular, plural = COUNTERS[name]
                count = value - previous
                changes.append(f"{count} {singular if count == 1 else plural}")
            continue
        if previous is None:
            changes.append(f"{name}: {value}")
        else:
            changes.append(f"{name}: {previous} → {value}")
    if old.get("content") != new.get("content"):
        changes.append("Updated")
    return changes
