#!/usr/bin/env bash
# Start Traqen at http://localhost:<port>, reloading on code changes.
# Usage: scripts/run_server.sh [port]   (default 8000)
# On another port, set TRAQEN_BASE_URL in .env to match: the app's links and
# the bookmarklet point there.
set -euo pipefail

cd "$(dirname "$0")/.."
port="${1:-8000}"

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "No virtualenv yet. Set it up first:" >&2
  echo '  uv venv && uv pip install -e ".[dev]"' >&2
  exit 1
fi

# With --reload, uvicorn binds only 127.0.0.1 for "localhost", so it starts
# fine next to a server on ::1, and the browser (trying ::1 first) reaches
# that server at localhost instead of Traqen. Refuse a port anything listens on.
if command -v lsof >/dev/null && taken=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN); then
  echo "Port $port is taken (pick another: scripts/run_server.sh 8008):" >&2
  echo "$taken" >&2
  exit 1
fi

exec .venv/bin/uvicorn traqen.app:app --reload --host localhost --port "$port"
