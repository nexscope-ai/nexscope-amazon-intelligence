#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11+ is required: https://www.python.org/downloads/" >&2
  exit 1
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
python3 -m venv .runtime-venv
.runtime-venv/bin/python -m pip install --no-index --find-links wheels --require-hashes -r runtime/requirements.lock
.runtime-venv/bin/python runtime/installer.py
