"""Live state for Jira Cloud issues, read through the user's own connection."""

import httpx

from .state import CheckFailed, Snapshot, live, unavailable

FIELDS = "summary,status,assignee,priority,updated,comment"
RESOURCES = "https://api.atlassian.com/oauth/token/accessible-resources"


async def check(client: httpx.AsyncClient, ref: dict, credentials) -> Snapshot:
    if credentials.settings.atlassian is None:
        return unavailable("Jira live state isn’t set up on this server")
    status = credentials.status("jira")
    if status is None:
        return unavailable("Connect Jira to keep this live", connect="jira")
    token = await credentials.token("jira")
    if token is None:
        return unavailable("Reconnect Jira to keep this live", connect="jira")

    site, token = await _site(client, credentials, token, ref["site"])
    if site is None:
        return unavailable(
            f"Your Jira connection doesn’t include {ref['site'].removeprefix('https://')}"
            " — reconnect and choose that site",
            connect="jira",
        )

    url = (
        f"https://api.atlassian.com/ex/jira/{site['id']}/rest/api/3/issue/"
        f"{ref['key']}?fields={FIELDS}"
    )
    response = await _request(client, url, token)
    if response.status_code == 401:
        token = await credentials.refreshed_token("jira", token)
        response = await _request(client, url, token)
    if response.status_code in (403, 404):
        return unavailable("Issue not found, or not visible to your Jira account")
    if response.status_code != 200:
        raise CheckFailed(f"Jira responded with {response.status_code}")

    fields = response.json()["fields"]
    assignee = fields.get("assignee") or {}
    priority = fields.get("priority") or {}
    return live(
        {
            "title": f"{ref['key']}: {fields.get('summary', '')}",
            "fields": {
                "Status": (fields.get("status") or {}).get("name", "Unknown"),
                "Assignee": assignee.get("displayName") or "Unassigned",
                "Priority": priority.get("name") or "None",
                "Comments": (fields.get("comment") or {}).get("total", 0),
            },
            "updated": fields.get("updated"),
        }
    )


async def _site(client, credentials, token, site_url):
    resources = credentials.meta("jira").get("resources", [])
    match = _find(resources, site_url)
    if match:
        return match, token
    # The user may have granted access to more sites since connecting.
    try:
        resources = await jira_resources(client, token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            raise CheckFailed("Couldn’t list Jira sites") from exc
        token = await credentials.refreshed_token("jira", token)
        resources = await jira_resources(client, token)
    except httpx.HTTPError as exc:
        raise CheckFailed(f"Couldn’t reach Jira ({type(exc).__name__})") from exc
    credentials.save_meta("jira", {**credentials.meta("jira"), "resources": resources})
    return _find(resources, site_url), token


async def jira_resources(client: httpx.AsyncClient, token: str) -> list[dict]:
    """The Jira sites the user's connection can read."""
    response = await client.get(RESOURCES, headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    return [
        {"id": site["id"], "url": site["url"].rstrip("/")} for site in response.json()
    ]


def _find(resources: list[dict], site_url: str) -> dict | None:
    return next((r for r in resources if r["url"].lower() == site_url.lower()), None)


async def _request(client: httpx.AsyncClient, url: str, token: str):
    try:
        return await client.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise CheckFailed(f"Couldn’t reach Jira ({type(exc).__name__})") from exc
