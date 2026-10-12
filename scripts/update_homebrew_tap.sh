#!/usr/bin/env bash
# Regenerate, install and test the `rumo` formula in the pedrobuzzi/homebrew-tap tap, then push it.
#
#   scripts/update_homebrew_tap.sh <version> <sdist-url> <sha256> [--dry-run]
#
# The release workflow runs it on macOS after the package is on PyPI. Locally, run it with the
# file:// URL of a freshly built sdist and --dry-run to test the formula without pushing; it
# leaves your Homebrew as it was (it uninstalls the formula and removes the tap it created).
# Pushing needs HOMEBREW_TAP_TOKEN (a token that can write to pedrobuzzi/homebrew-tap).
set -euo pipefail

VERSION="${1:?version}"
URL="${2:?sdist url}"
SHA256="${3:?sha256}"
DRY_RUN="${4:-}"
TAP_NAME="pedrobuzzi/tap"
TAP_REPO="pedrobuzzi/homebrew-tap"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAP_DIR="$(brew --repository)/Library/Taps/pedrobuzzi/homebrew-tap"

export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_FROM_API=1 HOMEBREW_NO_ENV_HINTS=1

created_tap=""
if [ ! -d "$TAP_DIR" ]; then
  # A plain clone works even while the tap repository is still empty (brew tap does not).
  git clone --quiet "https://github.com/$TAP_REPO.git" "$TAP_DIR" 2>/dev/null || git init --quiet -b main "$TAP_DIR"
  created_tap=1
fi
cleanup() {
  if [ "$DRY_RUN" = "--dry-run" ]; then
    brew uninstall --quiet "$TAP_NAME/rumo" >/dev/null 2>&1 || true
    if [ -n "$created_tap" ]; then rm -rf "$TAP_DIR"; fi
  fi
}
trap cleanup EXIT

mkdir -p "$TAP_DIR/Formula"
python3 "$ROOT/scripts/homebrew_formula.py" "$URL" "$SHA256" > "$TAP_DIR/Formula/rumo.rb"
# The package was just uploaded; skip Homebrew's one-day PyPI cooldown for it (its dependencies keep it).
brew update-python-resources --ignore-main-package-cooldown "$TAP_NAME/rumo"
brew install --build-from-source "$TAP_NAME/rumo"
brew test "$TAP_NAME/rumo"
"$(brew --prefix)/bin/rumo" --version | grep -q "rumo $VERSION"

if [ "$DRY_RUN" = "--dry-run" ]; then
  echo "dry run: formula for rumo $VERSION installs and passes its test; not pushed"
  exit 0
fi

: "${HOMEBREW_TAP_TOKEN:?HOMEBREW_TAP_TOKEN is required to push}"
cd "$TAP_DIR"
git add Formula/rumo.rb
if git diff --cached --quiet; then
  echo "formula unchanged; nothing to push"
  exit 0
fi
git -c user.name="rumo release" -c user.email="actions@github.com" commit --quiet -m "rumo $VERSION"
git push --quiet "https://x-access-token:${HOMEBREW_TAP_TOKEN}@github.com/$TAP_REPO.git" HEAD:main
echo "pushed rumo $VERSION to $TAP_REPO"
