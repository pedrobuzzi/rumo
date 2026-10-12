#!/usr/bin/env bash
# Make Homebrew's Python look like a real developer machine before installing the formula:
# packages installed there with pip leave legacy `*-nspkg.pth` files in its global site-packages,
# and Homebrew's sitecustomize runs them in every Python it starts. One of them (this exact file
# ships with google-cloud-aiplatform) once broke `brew install pedrobuzzi/tap/rumo`.
#
# CI only: it writes into Homebrew's Python, so it refuses to run anywhere else.
set -euo pipefail

if [ "${CI:-}" != "true" ]; then
  echo "refusing to change Homebrew's Python outside CI" >&2
  exit 1
fi

brew install --quiet python@3.13
site="$(brew --prefix)/lib/python3.13/site-packages"
mkdir -p "$site/google/cloud"
cat > "$site/google_cloud_aiplatform-1.139.0-py3.12-nspkg.pth" <<'PTH'
import sys, types, os;p = os.path.join(sys._getframe(1).f_locals['sitedir'], *('google',));importlib = __import__('importlib.util');__import__('importlib.machinery');m = sys.modules.setdefault('google', importlib.util.module_from_spec(importlib.machinery.PathFinder.find_spec('google', [os.path.dirname(p)])));m = m or sys.modules.setdefault('google', types.ModuleType('google'));mp = (m or []) and m.__dict__.setdefault('__path__',[]);(p not in mp) and mp.append(p)
import sys, types, os;p = os.path.join(sys._getframe(1).f_locals['sitedir'], *('google', 'cloud'));importlib = __import__('importlib.util');__import__('importlib.machinery');m = sys.modules.setdefault('google.cloud', importlib.util.module_from_spec(importlib.machinery.PathFinder.find_spec('google.cloud', [os.path.dirname(p)])));m = m or sys.modules.setdefault('google.cloud', types.ModuleType('google.cloud'));mp = (m or []) and m.__dict__.setdefault('__path__',[]);(p not in mp) and mp.append(p);m and setattr(sys.modules['google'], 'cloud', m)
PTH
echo "added a legacy namespace .pth to $site"
