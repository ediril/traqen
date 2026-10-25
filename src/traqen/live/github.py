"""Live state for GitHub issues and pull requests."""

import httpx

from .state import CheckFailed, Snapshot, live, unavailable

API = "https://api.github.com"


async def check(client: httpx.AsyncClient, ref: dict, credentials) -> Snapshot:
    token = await credentials.token("github")
    base = f"{API}/repos/{ref['owner']}/{ref['repo']}"
    number = ref["number"]

    issue, token = await _get(client, credentials, token, f"{base}/issues/{number}")
    if isinstance(issue, Snapshot):
        return issue

    is_pull = "pull_request" in issue
    fields = {}
    if is_pull:
        pull, token = await _get(client, credentials, token, f"{base}/pulls/{number}")
        if isinstance(pull, Snapshot):
            return pull
        reviews, token = await _get(
            client, credentials, token, f"{base}/pulls/{number}/reviews?per_page=100"
        )
        if isinstance(reviews, Snapshot):
            return reviews
        fields["State"] = _pull_state(pull)
        fields["Review"] = _review_state(pull, reviews)
    else:
        fields["State"] = _issue_state(issue)
    assignees = [person["login"] for person in issue.get("assignees") or []]
    fields["Assignee"] = ", ".join(assignees) or "Unassigned"
    fields["Comments"] = issue.get("comments", 0)

    return live(
        {
            "title": issue["title"],
            "fields": fields,
            "updated": issue.get("updated_at"),
        }
    )


async def _get(client, credentials, token, url):
    """GET a GitHub API URL; returns (json, token) or (Snapshot, token)."""
    response = await _request(client, url, token)
    if response.status_code == 401 and token:
        token = await credentials.refreshed_token("github", token)
        response = await _request(client, url, token)
    if response.status_code == 200:
        return response.json(), token

    rate_limited = response.status_code == 429 or (
        response.status_code == 403
        and response.headers.get("x-ratelimit-remaining") == "0"
    )
    if rate_limited:
        hint = "" if token else " — connecting GitHub raises the limit"
        raise CheckFailed(f"GitHub rate limit reached{hint}")
    sso = response.headers.get("x-github-sso", "").partition("url=")[2].strip()
    if response.status_code == 403 and sso.startswith("https://github.com/"):
        return unavailable(
            "Your organization requires SSO authorization for Traqen", fix_url=sso
        ), token
    if response.status_code in (403, 404):
        if token:
            return unavailable(
                "Not visible to your GitHub account. If it’s in an organization, "
                "the organization may need to approve Traqen.",
                connect="github",
            ), token
        if credentials.settings.github is None:
            return unavailable("Private or not found"), token
        return unavailable(
            "Private or not found — connect GitHub to keep this live", connect="github"
        ), token
    if response.status_code == 410:
        return unavailable("This issue was deleted"), token
    raise CheckFailed(f"GitHub responded with {response.status_code}")


async def _request(client: httpx.AsyncClient, url: str, token: str | None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        # GitHub redirects renamed repositories and transferred issues. Only
        # api.github.com is ever asked, and httpx drops the token if a
        # redirect leaves it.
        return await client.get(url, headers=headers, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise CheckFailed(f"Couldn’t reach GitHub ({type(exc).__name__})") from exc


def _issue_state(issue: dict) -> str:
    if issue["state"] == "open":
        return "Open"
    if issue.get("state_reason") == "not_planned":
        return "Closed (not planned)"
    return "Closed"


def _pull_state(pull: dict) -> str:
    if pull.get("merged_at"):
        return "Merged"
    if pull["state"] == "closed":
        return "Closed"
    return "Draft" if pull.get("draft") else "Open"


def _review_state(pull: dict, reviews: list[dict]) -> str:
    latest = {}
    for review in reviews:
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[review["user"]["login"]] = review["state"]
    states = set(latest.values())
    if "CHANGES_REQUESTED" in states:
        return "Changes requested"
    if "APPROVED" in states:
        return "Approved"
    if pull.get("requested_reviewers") or pull.get("requested_teams"):
        return "Review requested"
    return "No review yet"
