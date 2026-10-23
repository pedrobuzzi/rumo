#!/usr/bin/env bash
# Run the smoke test written for the rumo that got installed:
#
#   scripts/smoke_test_installed.sh [<expected-version>] [<python of the install>]
#
# A release passes its version, and the checkout is that release: its own smoke test runs. Without
# one (every Monday, a push, by hand), PyPI and the tap have the last release while main may already
# check what is not released yet, so the smoke test comes from that release's tag.
set -euo pipefail

if [ -n "${1:-}" ]; then
  exec bash scripts/smoke_test.sh "$@"
fi

installed="$(rumo --version | tr -d '\r')"
installed="${installed#rumo }"
git fetch --quiet --depth=1 origin tag "v$installed"
script="${RUNNER_TEMP:-$(mktemp -d)}/smoke_test-v$installed.sh"
git show "v$installed:scripts/smoke_test.sh" > "$script"
echo "smoke test of v$installed"
exec bash "$script" "$installed" "${2:-}"
