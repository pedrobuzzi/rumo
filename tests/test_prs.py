import json
import tempfile
import unittest
from pathlib import Path

from rumo import config, items, prs

ITEM = """---
title: Checkout novo
status: doing
updated: 2026-10-11 16:30
---

## PRs
- [open] https://github.com/acme/web-app/pull/12 Checkout novo
- [open] https://github.com/acme/web-app/pull/7
- [merged] https://github.com/acme/web-app/pull/3 Antigo
- [open] https://gitlab.com/acme/api/-/merge_requests/34

## Sessions
- 2026-10-11 10:08 | web-app (feature/checkout) | abc | {repo}
- 2026-10-12 10:08 | web-app (main) | def | {repo}
"""


def pr(number: int, state: str, title: str = "", draft: bool = False) -> dict:
    return {"url": f"https://github.com/acme/web-app/pull/{number}", "state": state, "isDraft": draft, "title": title}


class FakeGh:
    """Answers `gh pr list --head <branch>` and `gh pr view <url>` from canned PRs, and records each call."""

    def __init__(self, branches: dict[str, list[dict]], views: dict[str, dict]) -> None:
        self.branches, self.views, self.calls = branches, views, []

    def __call__(self, args: list[str], folder: str | None) -> str | None:
        self.calls.append((args, folder))
        if args[:2] == ["pr", "list"]:
            found = self.branches.get(args[args.index("--head") + 1])
            return None if found is None else json.dumps(found)
        if args[:2] == ["pr", "view"]:
            found = self.views.get(args[2])
            return None if found is None else json.dumps(found)
        return None


class PullRequestCheckTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.repo = self.root / "web-app"
        self.repo.mkdir()
        self.path = self.root / "checkout.md"
        self.path.write_text(ITEM.format(repo=self.repo), encoding="utf-8")

    def lines(self) -> list[str]:
        return [f"{p.state} {p.name} {p.title}".strip() for p in items.load(self.path).prs]

    def test_takes_the_states_gh_knows_and_adds_the_prs_of_the_items_branches(self):
        gh = FakeGh(
            {"feature/checkout": [pr(12, "MERGED", "Checkout novo"), pr(15, "OPEN", "Frete", draft=True)]},
            {"https://github.com/acme/web-app/pull/7": pr(7, "CLOSED", "Cupom")},
        )
        self.assertTrue(prs.refresh(items.load(self.path), [], set(), gh))
        self.assertEqual(self.lines(), [
            "merged web-app#12 Checkout novo", "closed web-app#7 Cupom", "merged web-app#3 Antigo",
            "open api!34", "draft web-app#15 Frete",
        ])
        # main is where every branch starts, merged is final, and gh only knows GitHub
        self.assertFalse(any("main" in args for args, _ in gh.calls))
        viewed = [args[2] for args, _ in gh.calls if args[:2] == ["pr", "view"]]
        self.assertEqual(viewed, ["https://github.com/acme/web-app/pull/7"])
        self.assertEqual({folder for args, folder in gh.calls if args[:2] == ["pr", "list"]}, {str(self.repo)})

    def test_nothing_changes_when_gh_agrees_or_fails(self):
        before = self.path.read_text()
        self.assertFalse(prs.refresh(items.load(self.path), [], set(), FakeGh({}, {})))
        same = FakeGh({"feature/checkout": [pr(12, "OPEN", "Outro título")]}, {"https://github.com/acme/web-app/pull/7": pr(7, "OPEN")})
        self.assertFalse(prs.refresh(items.load(self.path), [], set(), same))
        self.assertEqual(self.path.read_text(), before)

    def test_a_pr_the_user_deleted_is_not_added_back(self):
        gh = FakeGh({"feature/checkout": [pr(15, "OPEN", "Frete")]}, {})
        prs.refresh(items.load(self.path), [], {"https://github.com/acme/web-app/pull/15"}, gh)
        self.assertNotIn("open web-app#15 Frete", self.lines())

    def test_branches_noted_at_each_stop_count_too(self):
        gh = FakeGh({"fix/cupom": [pr(20, "OPEN", "Cupom")]}, {})
        prs.refresh(items.load(self.path), [[str(self.repo), "fix/cupom"], [str(self.root / "gone"), "x"]], set(), gh)
        self.assertIn("open web-app#20 Cupom", self.lines())
        self.assertNotIn(str(self.root / "gone"), [folder for _, folder in gh.calls])


class BranchNotesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.config = self.root / "config.toml"

    def repo(self, name: str, head: str) -> Path:
        repo = self.root / name
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text(head, encoding="utf-8")
        (repo / "src").mkdir()
        return repo

    def test_reads_the_branch_of_a_folder_inside_a_repository(self):
        repo = self.repo("web-app", "ref: refs/heads/feature/checkout\n")
        self.assertEqual(prs.branch_at(repo / "src"), (str(repo), "feature/checkout"))
        self.repo("detached", "4b825dc642cb6eb9a060e54bf8d69288fbee4904\n")
        self.assertIsNone(prs.branch_at(self.root / "detached"))
        self.assertIsNone(prs.branch_at(self.root))

    def test_reads_the_branch_of_a_worktree(self):
        main = self.repo("web-app", "ref: refs/heads/main\n")
        gitdir = main / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (gitdir / "HEAD").write_text("ref: refs/heads/fix/cupom\n", encoding="utf-8")
        worktree = self.root / "wt"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        self.assertEqual(prs.branch_at(worktree), (str(worktree), "fix/cupom"))

    def test_notes_each_branch_once_per_item_and_skips_main(self):
        repo = self.repo("web-app", "ref: refs/heads/feature/checkout\n")
        prs.note_branch(self.config, ["/items/a.md", "/items/b.md"], repo)
        prs.note_branch(self.config, ["/items/a.md"], repo / "src")
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        prs.note_branch(self.config, ["/items/a.md"], repo)
        state = config.load_state(self.config)
        self.assertEqual(prs.noted_branches(state, "/items/a.md"), [[str(repo), "feature/checkout"]])
        self.assertEqual(prs.noted_branches(state, "/items/b.md"), [[str(repo), "feature/checkout"]])

    def test_remembers_the_prs_the_user_deleted(self):
        prs.drop(self.config, "/items/a.md", "https://github.com/acme/web-app/pull/15")
        self.assertEqual(prs.dropped(config.load_state(self.config), "/items/a.md"), {"https://github.com/acme/web-app/pull/15"})
        self.assertEqual(prs.dropped(config.load_state(self.config), "/items/b.md"), set())


if __name__ == "__main__":
    unittest.main()
