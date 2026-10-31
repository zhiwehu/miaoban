#!/bin/bash
# Restart this project's local voice backend. Optional: --no-voice.
set -e
cd "$(dirname "$0")"
exec /usr/bin/env python3 restart_backend.py "$@"
