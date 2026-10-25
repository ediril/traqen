import pytest

from traqen.live.links import classify, normalize_url
from traqen.live.state import describe_changes


@pytest.mark.parametrize(
    ("url", "source", "checker", "ref"),
    [
        (
            "https://github.com/acme/api/pull/42/files",
            "github",
            "github",
            {"owner": "acme", "repo": "api", "number": 42, "kind": "pull"},
        ),
        (
            "https://github.com/acme/api/issues/7",
            "github",
            "github",
            {"owner": "acme", "repo": "api", "number": 7, "kind": "issue"},
        ),
        ("https://github.com/acme/api", "github", "web", None),
        (
            "https://acme.atlassian.net/browse/SCIM-12",
            "jira",
            "jira",
            {"site": "https://acme.atlassian.net", "key": "SCIM-12"},
        ),
        (
            "https://acme.atlassian.net/jira/software/projects/S/boards/1?selectedIssue=S-3",
            "jira",
            "jira",
            {"site": "https://acme.atlassian.net", "key": "S-3"},
        ),
        ("https://acme.atlassian.net/jira/your-work", "jira", None, None),
        ("https://acme.atlassian.net/wiki/spaces/ENG", "confluence", None, None),
        ("https://acme.slack.com/archives/C1/p123", "slack", None, None),
        ("https://docs.google.com/document/d/abc/edit", "google", None, None),
        ("https://www.figma.com/file/abc", "figma", None, None),
        ("https://status.example.com/incidents/1", "web", "web", None),
    ],
)
def test_classify(url, source, checker, ref):
    link = classify(url)
    assert (link.source, link.checker, link.ref) == (source, checker, ref)


def test_normalize_url_adds_scheme_and_rejects_non_web():
    assert normalize_url(" github.com/acme/api ") == "https://github.com/acme/api"
    with pytest.raises(ValueError):
        normalize_url("ftp://example.com/file")
    with pytest.raises(ValueError):
        normalize_url("")


def test_describe_changes():
    old = {
        "title": "SCIM support",
        "fields": {"Status": "In Review", "Assignee": "Ana", "Comments": 3},
        "updated": "2026-10-01",
    }
    new = {
        "title": "SCIM support",
        "fields": {"Status": "Done", "Assignee": "Ana", "Comments": 5},
        "updated": "2026-10-02",
    }
    assert describe_changes(old, new) == ["Status: In Review → Done", "2 new comments"]
    assert describe_changes(old, old) == []
    assert describe_changes(None, new) == []


def test_describe_changes_for_pages():
    old = {"title": "Status", "fields": {}, "content": "a", "preview": "x"}
    new = {"title": "Status", "fields": {}, "content": "b", "preview": "y"}
    assert describe_changes(old, new) == ["Updated"]
    # Comment counts going down (deleted comments) are not news.
    assert (
        describe_changes({"fields": {"Comments": 3}}, {"fields": {"Comments": 2}}) == []
    )
