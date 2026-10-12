import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PackagingTest(unittest.TestCase):
    """The repo is a PyPI package, a Claude Code plugin and its marketplace at once: keep them in step."""

    def test_versions_match(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual(plugin, pyproject)

    def test_marketplace_serves_this_repo_as_the_rumo_plugin(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(market["name"], "rumo")
        self.assertEqual([(p["name"], p["source"]) for p in market["plugins"]], [("rumo", "./")])

    def test_skill_and_hook_are_where_plugins_and_npx_skills_look(self):
        skill = (ROOT / "skills" / "rumo" / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?m)^name: rumo$")
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        command = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertIn("rumo hook", command)

    def test_homebrew_formula_ignores_the_users_global_python_packages(self):
        # A legacy `*-nspkg.pth` in Homebrew Python's global site-packages (left by packages installed
        # there with pip) breaks pip's isolated builds inside the formula's virtualenv.
        import subprocess
        import sys

        formula = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "homebrew_formula.py"), "https://example.com/rumo.tar.gz", "0" * 64],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("virtualenv_install_with_resources(system_site_packages: false)", formula)

    def test_bump_script_updates_both_versions(self):
        script = (ROOT / "scripts" / "bump_version.py").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"pyproject\.toml", script) and re.search(r"plugin\.json", script))


if __name__ == "__main__":
    unittest.main()
