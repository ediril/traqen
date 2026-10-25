# Traqen

Traqen is a lightweight personal work-tracking app for people whose active work is scattered across tools like Jira, Slack, Google Docs, GitHub, Figma, Notion, email, browser tabs, and private notes.

It is designed for software developers, product managers, engineering leads, technical founders, support engineers, and anyone else who has to keep track of messy, ongoing work that does not fit cleanly into one formal system.

Traqen is not a project management app. It is not a task manager. It is not a second brain. It is a small, focused place to track the active things a person is responsible for, along with the links, notes, reminders, decisions, and context needed to keep those things from falling through the cracks.

The closest thing is the "Save for Later"/"Remind me" feature and the "Later" tab in Slack

## Running the app

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env    # then fill in the secrets and OAuth clients
.venv/bin/uvicorn traqen.app:app --reload
```

Open http://localhost:8000. The database is created on first start.

Sign-in is off by default in `.env.example` (`TRAQEN_SIGN_IN=none`): you are signed in automatically as a single local user. This only works with a localhost `TRAQEN_BASE_URL`; turning on Google sign-in is tracked in TODO.md.

### Browser button

The **Save to Traqen** button at the bottom of the sidebar is a bookmarklet: drag it to your bookmarks bar, then click it on any page to save that page (plus any selected text, as its note) to a traq. It points at `TRAQEN_BASE_URL`, so reinstall it if that changes.

### OAuth apps

Sign-in uses Google once it's turned on, and live state for private items uses each person's own GitHub and Jira accounts. Register one OAuth app per provider and put its ID and secret in `.env`. The redirect URIs are:

| Provider | Where to register | Redirect URI |
|---|---|---|
| Google (when `TRAQEN_SIGN_IN=google`) | Google Cloud console → Credentials → OAuth client ID (Web) | `{TRAQEN_BASE_URL}/auth/google/callback` |
| GitHub (optional) | GitHub → Settings → Developer settings → OAuth Apps | `{TRAQEN_BASE_URL}/connections/github/callback` |
| Jira (optional) | developer.atlassian.com → Console → OAuth 2.0 integration, permission `read:jira-work` | `{TRAQEN_BASE_URL}/connections/jira/callback` |

Without GitHub configured, public GitHub issues and PRs are still live (through the unauthenticated API, which has a low rate limit). Without Jira configured, Jira links stay plain links.

### Live state

Links are checked when added, when their traq is opened (if the last check is more than 5 minutes old), on "Refresh", and by a background refresher inside the app process every `TRAQEN_REFRESH_MINUTES`. Archived traqs are not refreshed. Run a single worker process so there is only one refresher.

### Versioning

Versions are `<major>.<work-day>.<increment>`, computed from Git history the same way as in hook-line-sync:

- [`version.json`](version.json) sets the major number and its baseline commit. We're on major `0` (pre-release), counted from the first commit, `e2b266b` ("traqen: first public release", October 25, 2026).
- The baseline day is `0`. Each later date with commits advances the work-day number by one, however many idle days pass.
- The increment counts that date's commits, starting at `1`. Only first-parent history counts, using each commit's committer date.

Running from a Git checkout, the app computes the version from history the same way and refreshes it whenever a commit lands, so the sidebar is never stale. Builds also write it into `src/traqen/_version.py` (`build_backend/`), which installed copies without Git use. To start a new major, update `version.json` with the new major and a baseline commit; that commit becomes `<major>.0.1`.

### Tests

```bash
.venv/bin/pytest
```

## License

Traqen is free for personal and other uses permitted by the
[PolyForm Strict License 1.0.0](LICENSE), including its permissions for listed
noncommercial organizations. This license grants no rights to modify or
redistribute the software.

Commercial use outside those permissions requires a separate paid license.
Contact [Emrah Diril](mailto:emrah@diril.org) for pricing and a commercial
agreement before beginning such use.

Traqen is source-available under these terms.
