"""Recognize what a pasted link points to."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit

LABELS = {
    "github": "GitHub",
    "jira": "Jira",
    "confluence": "Confluence",
    "slack": "Slack",
    "google": "Google Drive",
    "figma": "Figma",
    "notion": "Notion",
    "linear": "Linear",
    "zendesk": "Zendesk",
    "intercom": "Intercom",
    "salesforce": "Salesforce",
    "gong": "Gong",
    "web": "Web",
}

# Tools whose pages sit behind a company sign-in and that have no integration yet.
# Fetching them would only return a login page, so they stay plain links.
_SIGNED_IN_TOOLS = [
    ("slack", ("slack.com",)),
    ("google", ("docs.google.com", "drive.google.com", "sheets.google.com")),
    ("figma", ("figma.com",)),
    ("notion", ("notion.so",)),
    ("linear", ("linear.app",)),
    ("zendesk", ("zendesk.com",)),
    ("intercom", ("intercom.com",)),
    ("salesforce", ("salesforce.com", "force.com")),
    ("gong", ("gong.io",)),
]

_GITHUB_ITEM = re.compile(r"^/([\w.-]+)/([\w.-]+)/(issues|pull)/(\d+)(?:/|$)")
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


@dataclass(frozen=True)
class Link:
    source: str
    ref: dict | None = None

    @property
    def label(self) -> str:
        return LABELS[self.source]

    @property
    def checker(self) -> str | None:
        """Which live-state checker handles this link, if any."""
        if self.source in ("github", "jira") and self.ref:
            return self.source
        if self.source in ("web", "github"):
            # GitHub pages other than issues/PRs are fetched like any public page.
            return "web"
        return None

    @property
    def unsupported_reason(self) -> str:
        if self.source == "jira":
            return "Live state is available for Jira issue links"
        return f"Live state for {self.label} isn’t available yet"


def normalize_url(raw: str) -> str:
    url = raw.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Enter a web link (http or https)")
    return url


def page_key(url: str) -> str:
    """What a link points to, to tell whether two links are the same thing: a
    GitHub issue/PR or Jira issue by its reference, whatever the tab or anchor;
    any other page by its URL without the #fragment (the page's server never
    sees it), and with the scheme and host in lowercase."""
    link = classify(url)
    if link.source == "github" and link.ref:
        ref = link.ref
        return f"github:{ref['owner']}/{ref['repo']}#{ref['number']}".lower()
    if link.source == "jira" and link.ref:
        return f"jira:{link.ref['site']}/{link.ref['key']}".lower()
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def classify(url: str) -> Link:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()

    if _host_matches(host, "github.com"):
        match = _GITHUB_ITEM.match(parts.path)
        if match:
            owner, repo, kind, number = match.groups()
            return Link(
                "github",
                {
                    "owner": owner,
                    "repo": repo,
                    "number": int(number),
                    "kind": "pull" if kind == "pull" else "issue",
                },
            )
        return Link("github")

    if host.endswith(".atlassian.net"):
        if parts.path.startswith("/wiki"):
            return Link("confluence")
        key = _jira_issue_key(parts.path, parts.query)
        if key:
            return Link("jira", {"site": f"https://{host}", "key": key})
        return Link("jira")

    for source, domains in _SIGNED_IN_TOOLS:
        if any(_host_matches(host, domain) for domain in domains):
            return Link(source)

    return Link("web")


def _jira_issue_key(path: str, query: str) -> str | None:
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) >= 2 and segments[0] == "browse":
        candidate = segments[1].upper()
        if _JIRA_KEY.match(candidate):
            return candidate
    for name in ("selectedIssue", "issueKey"):
        for value in parse_qs(query).get(name, []):
            candidate = value.upper()
            if _JIRA_KEY.match(candidate):
                return candidate
    return None
