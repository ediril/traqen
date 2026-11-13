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
scripts/run_server.sh
```

Open http://localhost:8000. The script reloads the server when the code changes. If the port is taken, pass another one (`scripts/run_server.sh 8008`) and set `TRAQEN_BASE_URL` in `.env` to match, since the app's links and the bookmarklet point there. The database is created on first start.

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

Links are checked when added, when their traq is opened (if the last check is more than a minute old), and by a background refresher inside the app process every `TRAQEN_REFRESH_MINUTES`. Archived traqs are not refreshed. Run a single worker process so there is only one refresher.

### Traq editor

A traq's text is stored as Markdown (CommonMark with tables, strikethrough, and checklists) and edited in a [Tiptap](https://tiptap.dev) editor, so you never see the Markdown. The editor is built into one file, `src/traqen/static/editor.js`, which is checked in; the app doesn't need Node to run. After changing anything under `editor/`, rebuild it (Node 22+):

```bash
cd editor && npm ci && npm run build
```

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

Traqen is source-available under the [PolyForm Strict License 1.0.0](LICENSE).
Use is free of charge for the purposes permitted by that license, including:

- Any noncommercial purpose.
- The personal uses listed in the license, such as personal study and hobby
  projects, without any anticipated commercial application.
- Use by charitable organizations, educational institutions, public research
  organizations, public safety or health organizations, environmental protection
  organizations, and government institutions, regardless of funding source or
  obligations resulting from funding.

This license grants no rights to modify or redistribute Traqen or create works
based on it. The full license governs; this summary does not grant additional
permissions.

Commercial use outside those permissions requires a separate paid license.
Contact [Emrah Diril](mailto:emrah@diril.org) for pricing and a commercial
agreement before beginning such use.
