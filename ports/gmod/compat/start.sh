#!/bin/sh
set -eu
cd "$(dirname "$0")/../../.."
python3 ports/gmod/compat/build-plugin.py
exec node ports/gmod/compat/tests/browser.mjs --serve "$@"
