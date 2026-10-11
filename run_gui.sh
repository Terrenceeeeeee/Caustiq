#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 unified_gui.py "$@"
