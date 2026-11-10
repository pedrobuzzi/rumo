import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ITEM = """---
title: Migração do logout
status: doing
---

## Sessions
- 2026-10-11 10:08 | web-app (main) | open-session | /Users/x/repo/web-app
- 2026-10-11 12:00 | web-app (main) | parked-session | /Users/x/repo/web-app | closed
"""


class AgentCommandsTest(unittest.TestCase):
    """The CLI reads the session id itself: Claude Code refuses to run a command that expands a variable."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.folder = self.root / "items"
        self.folder.mkdir()
        (self.folder / "2026-10-11-migracao.md").write_text(ITEM, encoding="utf-8")
        self.env = {**os.environ, "RUMO_DIR": str(self.folder), "RUMO_CONFIG": str(self.root / "none.toml")}
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)

    def rumo(self, *args: str, session: str = "") -> subprocess.CompletedProcess:
        env = {**self.env, "CLAUDE_CODE_SESSION_ID": session} if session else self.env
        return subprocess.run([sys.executable, "-m", "rumo.cli", *args], capture_output=True, text=True,
                              encoding="utf-8", env=env)

    def test_index_names_the_session_and_the_items_it_keeps_updated(self):
        out = self.rumo("index", session="open-session").stdout
        self.assertIn("# session: open-session\n", out)
        self.assertIn("# linked: 2026-10-11-migracao\n", out)
        self.assertIn("# linked: -\n", self.rumo("index", session="parked-session").stdout)

    def test_index_says_what_git_has_left_of_the_linked_items_work(self):
        self.assertIn("# git-left: -\n", self.rumo("index", session="open-session").stdout)
        repo = self.root / "web-app"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        (repo / "draft.py").write_text("x", encoding="utf-8")
        (self.folder / "2026-10-12-pix.md").write_text(
            f"---\ntitle: Pix\nstatus: doing\n---\n\n## Sessions\n- 2026-10-12 10:00 | web-app (main) | s1 | {repo}\n",
            encoding="utf-8")
        self.assertIn("# git-left: web-app (main): 1 file not committed\n", self.rumo("index", session="s1").stdout)

    def test_index_gives_the_time_to_stamp_changes_with(self):
        self.assertRegex(self.rumo("index").stdout, r"\n# now: \d{4}-\d\d-\d\d \d\d:\d\d\n")

    def test_index_without_a_session_id_says_sessions_cannot_be_linked(self):
        out = self.rumo("index").stdout
        self.assertIn("# session: none", out)
        self.assertNotIn("# linked:", out)
        self.assertNotIn("# session-line:", out)

    def session_line(self, folder: Path) -> str:
        out = self.rumo("index", str(folder), session="s1").stdout
        return next(line for line in out.splitlines() if line.startswith("# session-line: "))[16:]

    def test_index_gives_the_line_that_links_the_session_started_in_that_folder(self):
        repo = self.root / "web-app"
        (repo / "src").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "feature/x", str(repo)], check=True)
        self.assertRegex(self.session_line(repo / "src"), r"^- \d{4}-\d\d-\d\d \d\d:\d\d \| web-app \(feature/x\) \| s1 \| "
                         + re.escape(str(repo / "src")) + "$")

    def test_speaks_utf8_even_on_a_non_utf8_console(self):
        # Windows pipes default to the locale's code page; the agent reads the output as UTF-8.
        self.env |= {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
        for command in ("index", "now"):
            self.assertIn("Migração do logout", self.rumo(command, session="open-session").stdout, command)

    def test_outside_a_repository_the_folder_names_the_session(self):
        notes = self.root / "notes"
        notes.mkdir()
        self.assertTrue(self.session_line(notes).endswith(f"| notes (-) | s1 | {notes}"))


DROPPED = """---
title: Controle de ponto pra obra
status: dropped
dropped: 2026-10-02 18:00
updated: 2026-10-02 18:00
---

## Left off
Mestres de obra não pagam por app; o dono da obra já usa planilha.
"""


class IdeaCommandTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.folder = self.root / "items"
        self.folder.mkdir()
        (self.folder / "2026-09-17-controle-de-ponto-pra-obra.md").write_text(DROPPED, encoding="utf-8")
        config = self.root / "config.toml"
        config.write_text('[groups.produtos]\nname = "Produtos"\ncolor = "cyan"\n', encoding="utf-8")
        self.env = {**os.environ, "RUMO_DIR": str(self.folder), "RUMO_CONFIG": str(config)}

    def rumo(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "rumo.cli", *args], capture_output=True, text=True,
                              encoding="utf-8", env=self.env)

    def test_notes_an_idea_and_names_a_similar_dropped_one_with_its_reason(self):
        done = self.rumo("idea", "Ponto eletrônico pra obra", "--group", "produtos")
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        path = Path(lines[0])
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("title: Ponto eletrônico pra obra\nstatus: idea\ngroup: produtos\n", text)
        self.assertEqual(lines[1:], ["similar: Controle de ponto pra obra (dropped: Mestres de obra não pagam por app; "
                                     "o dono da obra já usa planilha.)"])

    def test_an_unrelated_idea_lists_nothing_similar(self):
        done = self.rumo("idea", "Tradutor de cardápio")
        self.assertEqual(len(done.stdout.splitlines()), 1)

    def test_without_a_title_or_with_an_unknown_group_it_shows_the_usage(self):
        for args in (("idea",), ("idea", "  "), ("idea", "X", "--group", "nope"), ("idea", "X", "--group")):
            done = self.rumo(*args)
            self.assertEqual(done.returncode, 2, args)
            self.assertIn("usage: rumo idea", done.stderr)
        self.assertEqual(len(list(self.folder.glob("*.md"))), 1)

    def test_a_flag_is_never_taken_as_a_title(self):
        helped = self.rumo("idea", "--help")
        self.assertEqual(helped.returncode, 0)
        self.assertIn("usage: rumo idea", helped.stdout)
        self.assertEqual(self.rumo("idea", "-x").returncode, 2)
        self.assertEqual(len(list(self.folder.glob("*.md"))), 1)

    def test_the_help_lists_it(self):
        self.assertIn('rumo idea "<title>"', self.rumo("--help").stdout)
        self.assertNotIn("spark", self.rumo("--help").stdout)  # the Timeline runs it, on `*`


if __name__ == "__main__":
    unittest.main()
