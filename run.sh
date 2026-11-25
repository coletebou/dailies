#!/bin/sh
# Start the Dailies server on 127.0.0.1:8765 for the project in $DAILIES_PROJECT_DIR (default: current directory).
#   DAILIES_PROJECT_DIR=/path/to/project ./run.sh
# Uses ./.venv if present, else python3. Build the web app once first: (cd web && npm install && npm run build)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
export DAILIES_PROJECT_DIR="${DAILIES_PROJECT_DIR:-$(pwd)}"
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY=python3
cd "$HERE/server"
exec "$PY" -m dailies_server
