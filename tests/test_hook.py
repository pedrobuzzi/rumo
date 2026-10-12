import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ITEM = """---
title: Migração do logout
status: waiting
---

## Sessions
- 2026-10-11 10:08 | web-app (main) | open-session | /Users/x/repo/web-app
- 2026-10-11 12:00 | web-app (main) | parked-session | /Users/x/repo/web-app | closed
"""


class HookTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "2026-10-11-migração.md").write_text(ITEM, encoding="utf-8")
        self.env = {**os.environ, "RUMO_DIR": str(self.folder), "RUMO_CONFIG": str(self.folder / "none.toml")}

    def run_hook(self, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "rumo.cli", "hook"], input=stdin, capture_output=True, text=True,
            encoding="utf-8", env=self.env,
        )

    def test_reminds_a_linked_session_to_update_its_item(self):
        result = self.run_hook(json.dumps({"session_id": "open-session"}))
        self.assertEqual(result.returncode, 0)
        self.assertIn("2026-10-11-migração.md (status: waiting)", result.stdout)
        for rule in ("Left off", "Steps", "[manual]", "| closed", "language"):
            self.assertIn(rule, result.stdout)

    def test_speaks_utf8_even_on_a_non_utf8_console(self):
        self.env["PYTHONIOENCODING"] = "cp1252"
        self.env["PYTHONUTF8"] = "0"
        result = subprocess.run(
            [sys.executable, "-m", "rumo.cli", "hook"], input=json.dumps({"session_id": "open-session"}).encode(),
            capture_output=True, env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2026-10-11-migração.md", result.stdout.decode("utf-8"))

    def test_stays_silent_for_unknown_or_closed_sessions_and_bad_input(self):
        for stdin in (json.dumps({"session_id": "other"}), json.dumps({"session_id": "parked-session"}), "not json", "{}"):
            result = self.run_hook(stdin)
            self.assertEqual((result.returncode, result.stdout), (0, ""), stdin)


if __name__ == "__main__":
    unittest.main()
