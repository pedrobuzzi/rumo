#!/usr/bin/env bash
# Check an installed rumo the way a new user's first run uses it:
#
#   scripts/smoke_test.sh [<expected-version>] [<python of the install>]
#
# `rumo` must be on PATH. With a version, `rumo --version` must match it. With the install's
# Python, the TUI module must import (that loads Textual and every other dependency).
# The install-check workflow runs it after each install channel; CI runs it on the dev install.
set -euo pipefail

EXPECTED="${1:-}"
PYTHON="${2:-}"

version="$(rumo --version)"
echo "$version"
if [ -n "$EXPECTED" ] && [ "$version" != "rumo $EXPECTED" ]; then
  echo "::error::expected rumo $EXPECTED, got: $version"
  exit 1
fi

# A throwaway folder and config, so the check never touches real items.
dir="${RUNNER_TEMP:-$(mktemp -d)}/rumo-smoke-$$"
mkdir -p "$dir"
export RUMO_DIR="$dir/items" RUMO_CONFIG="$dir/config.toml"

rumo index | grep -q "^# folder: " || { echo "::error::rumo index printed no folder"; exit 1; }

hook_output="$(echo '{"session_id": "smoke-test"}' | rumo hook)"
if [ -n "$hook_output" ]; then
  echo "::error::rumo hook spoke for an unlinked session: $hook_output"
  exit 1
fi

if [ -n "$PYTHON" ]; then
  "$PYTHON" -c "import rumo.app, textual; print('textual', textual.__version__)"
fi

echo "smoke test passed"
