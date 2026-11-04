#!/bin/sh
set -eu
cd "$(dirname "$0")"
hotel_python=python3
for candidate in .runtime/*/bin/python3; do
    if [ -x "$candidate" ]; then
        hotel_python="$candidate"
        break
    fi
done
if [ "$#" -eq 0 ]; then
    set -- gui
fi
exec "$hotel_python" -B -m hotel_app "$@"
