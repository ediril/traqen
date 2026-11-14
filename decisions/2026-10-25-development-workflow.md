# Development Workflow

Date: 2026-10-25

## Decision

- Changes are verified with the test suite, `.venv/bin/pytest`. There is no
  browser test suite yet; interaction work is driven in a browser against a
  throwaway server on another port, stopped when done.
- The owner starts the app with `scripts/run_server.sh [port]` (default 8000,
  reloading on code changes). It refuses a port anything already listens on.
  On another port, `TRAQEN_BASE_URL` in `.env` must match. (Added 2026-11-13.)
- The traq editor's source is `editor/`. After changing it, rebuild the
  checked-in `src/traqen/static/editor.js` with
  `cd editor && npm ci && npm run build` (Node 22+). The built file is never
  edited by hand. (Added 2026-11-13.)
- Versions are `<major>.<work-day>.<increment>`, computed from Git history
  (`version.json`, `build_backend/`; see `README.md`). A version is never set
  by hand.
- Commit subjects follow `git log`: short and lowercase, with an area prefix
  where one fits (`lists: ...`, `traq page: ...`).

## Why

The app needs no Node to run, so the editor is built once and checked in. The
port check exists because, with `--reload`, uvicorn binds only `127.0.0.1` for
"localhost": it starts quietly beside another server on `::1`, and the browser
reaches that other server at localhost instead of Traqen. Versions come from
history so the sidebar is never stale and nobody has to remember to bump one.
