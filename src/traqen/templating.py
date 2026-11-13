import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import db as dbm
from .live.links import LABELS
from .version import current_version

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
STATIC_DIR = Path(__file__).parent / "static"


def static_url(name: str) -> str:
    """URL for a static file that changes whenever the file does, so browsers
    never keep a stale stylesheet or script."""
    version = int((STATIC_DIR / name).stat().st_mtime)
    return f"/static/{name}?v={version}"


# Short marks shown in the colored source badge next to each link.
SOURCE_MARKS = {
    "github": "GH",
    "jira": "J",
    "confluence": "C",
    "slack": "S",
    "google": "G",
    "figma": "F",
    "notion": "N",
    "linear": "L",
    "zendesk": "Z",
    "intercom": "I",
    "salesforce": "SF",
    "gong": "Go",
    "web": "↗",
}


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def ago(value: str | None) -> str:
    moment = dbm.parse_time(value)
    if moment is None:
        return "never"
    seconds = int((dbm.now() - moment).total_seconds())
    if seconds < 60:
        return "just now"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"


def local_time(value: str | None, timezone: str) -> str:
    moment = dbm.parse_time(value)
    if moment is None:
        return ""
    local = moment.astimezone(ZoneInfo(timezone))
    today = datetime.now(ZoneInfo(timezone)).date()
    clock = local.strftime("%-I:%M %p")
    if local.date() == today:
        return f"Today {clock}"
    return f"{local.strftime('%a %b %-d')}, {clock}"


def domain(url: str | None) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.") if url else ""


templates.env.globals.update(
    csrf_token=csrf_token,
    static_url=static_url,
    app_version=current_version,
    SOURCE_LABELS=LABELS,
    SOURCE_MARKS=SOURCE_MARKS,
    load_json=dbm.load_json,
)
templates.env.filters.update(ago=ago, local_time=local_time, domain=domain)
