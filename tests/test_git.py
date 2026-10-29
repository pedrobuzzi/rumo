import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from rumo import git, items

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def sh(folder: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=folder, env=GIT_ENV, check=True, capture_output=True)


def commit(folder: Path, name: str) -> None:
    (folder / name).write_text(name, encoding="utf-8")
    sh(folder, "add", name)
    sh(folder, "commit", "-q", "-m", name)


class GitTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.folder = self.root / "items"
        self.folder.mkdir()
        origin = self.root / "origin.git"
        sh(self.root, "init", "-q", "--bare", "-b", "main", str(origin))
        self.repo = self.root / "web-app"
        sh(self.root, "clone", "-q", str(origin), str(self.repo))
        sh(self.repo, "checkout", "-q", "-b", "main")
        commit(self.repo, "first")
        sh(self.repo, "push", "-q", "-u", "origin", "main")
        (self.repo / "src").mkdir()  # sessions often start in a subfolder

    def item(self, slug: str, sessions: list[tuple[str, Path | str]], updated: str = "2026-10-20 10:00",
             status: str = "doing") -> items.Item:
        lines = "".join(f"- 2026-10-20 09:00 | x ({branch}) | s{n} | {folder}\n" for n, (branch, folder) in enumerate(sessions))
        path = self.folder / f"{slug}.md"
        path.write_text(f"---\ntitle: {slug}\nstatus: {status}\nupdated: {updated}\n---\n\n## Sessions\n{lines}",
                        encoding="utf-8")
        return items.load(path)

    def pending(self, *its: items.Item, state: dict | None = None) -> dict[str, list[str]]:
        return {slug: [p.label for p in found] for slug, found in git.pending(list(its), state or {}).items()}

    def test_says_what_is_not_committed_and_not_pushed_on_the_branch_the_item_worked_on(self):
        commit(self.repo, "second")
        commit(self.repo, "third")
        (self.repo / "first").write_text("changed", encoding="utf-8")
        (self.repo / "new-file").write_text("new", encoding="utf-8")
        found = self.pending(self.item("logout", [("main", self.repo / "src")]))
        self.assertEqual(found, {"logout": ["web-app (main): 2 commits not pushed · 2 files not committed"]})

    def test_a_clean_and_pushed_branch_says_nothing(self):
        self.assertEqual(self.pending(self.item("logout", [("main", self.repo)])), {})

    def test_files_count_only_on_the_branch_checked_out_and_commits_on_any(self):
        sh(self.repo, "checkout", "-q", "-b", "feature/pix")
        commit(self.repo, "pix")
        sh(self.repo, "checkout", "-q", "main")
        (self.repo / "first").write_text("changed", encoding="utf-8")
        found = self.pending(self.item("pix", [("feature/pix", self.repo)]))
        self.assertEqual(found, {"pix": ["web-app (feature/pix): 1 commit not pushed"]})

    def test_branches_the_hook_noted_count_too(self):
        sh(self.repo, "checkout", "-q", "-b", "feature/pix")
        commit(self.repo, "pix")
        pix = self.item("pix", [("main", self.repo)])
        state = {"pr_branches": {str(pix.path): [[str(self.repo), "feature/pix"]]}}
        self.assertEqual(self.pending(pix, state=state), {"pix": ["web-app (feature/pix): 1 commit not pushed"]})

    def test_a_repository_without_a_remote_has_nothing_to_push(self):
        local = self.root / "notes"
        local.mkdir()
        sh(local, "init", "-q", "-b", "main")
        commit(local, "first")
        (local / "draft").write_text("x", encoding="utf-8")
        self.assertEqual(self.pending(self.item("notes", [("main", local)])), {"notes": ["notes (main): 1 file not committed"]})

    def test_each_branch_shows_once_on_the_item_that_worked_there_last(self):
        commit(self.repo, "second")
        old = self.item("old", [("main", self.repo)], updated="2026-10-10 10:00")
        recent = self.item("recent", [("main", self.repo / "src")], updated="2026-10-21 10:00")
        self.assertEqual(self.pending(old, recent), {"recent": ["web-app (main): 1 commit not pushed"]})

    def test_folders_gone_branches_gone_and_folders_outside_a_repository_say_nothing(self):
        outside = self.root / "outside"
        outside.mkdir()
        it = self.item("odd", [("main", "/nowhere/web-app"), ("gone", self.repo), ("-", self.repo), ("main", outside), ("main", "")])
        self.assertEqual(self.pending(it), {})
