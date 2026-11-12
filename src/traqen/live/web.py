"""Live state for public web pages: title plus a fingerprint of the main content."""

import hashlib
import re
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .net import BlockedURL, safe_get
from .state import CheckFailed, Snapshot, live, unavailable

# Page chrome that changes independently of the content people care about.
_CHROME = [
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
]
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_SIGN_IN_WORDS = ("login", "signin", "sign-in", "sign_in", "sso", "auth")


async def check(client: httpx.AsyncClient, url: str) -> Snapshot:
    try:
        page = await safe_get(client, url)
    except BlockedURL as exc:
        return unavailable(str(exc))
    except httpx.HTTPError as exc:
        raise CheckFailed(f"Couldn’t reach the page ({type(exc).__name__})") from exc

    if page.status in (401, 403):
        return unavailable("This page requires signing in")
    if page.status == 404 or page.status == 410:
        return unavailable("Page not found")
    if page.status >= 500 or page.status == 429:
        raise CheckFailed(f"The site responded with {page.status}")
    if page.status >= 400:
        return unavailable(f"The site responded with {page.status}")

    if "html" not in page.content_type:
        name = urlsplit(page.url).path.rsplit("/", 1)[-1] or page.url
        return live({"title": name, "fields": {}, "content": _fingerprint(page.body)})

    soup = BeautifulSoup(page.body, "html.parser")
    if _is_sign_in_wall(url, page.url, soup):
        return unavailable("This page requires signing in")

    title = _title(soup) or urlsplit(page.url).hostname
    for tag in soup(_CHROME):
        tag.decompose()
    # Text a visitor doesn't see (a hidden error message, say) isn't content.
    for tag in soup.select('[hidden], [aria-hidden="true"]'):
        tag.decompose()
    for tag in soup.select("[style]"):
        if _HIDDEN_STYLE.search(tag.get("style", "")):
            tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = " ".join(root.get_text(" ").split())
    return live(
        {
            "title": title,
            "fields": {},
            "content": _fingerprint(text.encode()),
            "preview": text[:240],
        }
    )


def _title(soup: BeautifulSoup) -> str | None:
    meta = soup.find("meta", attrs={"property": "og:title"})
    if meta and meta.get("content", "").strip():
        return meta["content"].strip()
    if soup.title and soup.title.string:
        return " ".join(soup.title.string.split())
    return None


def _is_sign_in_wall(requested: str, final: str, soup: BeautifulSoup) -> bool:
    if soup.find("input", attrs={"type": "password"}):
        return True
    if urlsplit(requested).hostname != urlsplit(final).hostname:
        path = urlsplit(final).path.lower()
        return any(word in path for word in _SIGN_IN_WORDS)
    return False


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]
