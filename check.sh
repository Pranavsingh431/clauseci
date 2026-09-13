#!/usr/bin/env bash
# One command that proves every integration still works. Run it before you
# build, and again any time something behaves strangely.
set -uo pipefail
cd "$(dirname "$0")"
PY=./.venv/bin/python
[ -x "$PY" ] || { echo "no venv — run: python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"; exit 1; }
[ -f token.json ] || { echo "!! token.json missing — run: $PY prep/generate_token.py"; }
exec $PY prep/smoke/smoke.py "$@"
