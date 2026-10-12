"""Set the release version everywhere it lives: `uv run python scripts/bump_version.py 0.2.0`.

pyproject.toml (the PyPI package) and .claude-plugin/plugin.json (the Claude Code plugin) must
always carry the same version; tests/test_packaging.py and the release workflow check it.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        raise SystemExit("usage: python scripts/bump_version.py X.Y.Z")
    version = sys.argv[1]

    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text, n = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{version}"', text, count=1)
    if n != 1:
        raise SystemExit("pyproject.toml: no version line found")
    pyproject.write_text(text, encoding="utf-8")

    plugin = ROOT / ".claude-plugin" / "plugin.json"
    data = json.loads(plugin.read_text(encoding="utf-8"))
    data["version"] = version
    plugin.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"version {version} in pyproject.toml and .claude-plugin/plugin.json")
    print(f"next: commit, then `git tag v{version} && git push origin main v{version}`")


if __name__ == "__main__":
    main()
