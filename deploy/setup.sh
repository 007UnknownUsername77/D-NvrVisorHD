#!/bin/sh
# First-run helper. Asks for the recorder address and the RTSP account.
set -eu
cd "$(dirname "$0")/.."
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi
exec python3 deploy/setup.py "$@"
