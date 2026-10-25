#!/bin/sh
set -eu
cd "$(dirname "$0")"
voicehub_python="${VOICEHUB_PYTHON:-.venv/bin/python}"
exec "$voicehub_python" -m voicehub "$@"
