#!/bin/sh
# god2iso.py launcher for Linux / macOS
# Usage:  ./god2iso.sh convert <path-to-.live-or-folder>
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$DIR/god2iso.py" "$@"
