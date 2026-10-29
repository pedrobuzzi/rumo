"""What git knows is left of an item's work: files not committed and commits that no remote has, in the
repositories and branches its sessions worked on.

Read-only and quiet: without git, for a folder gone or outside a repository, or a branch deleted since,
there is nothing to say. A repository without a remote has nothing to push.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rumo import items, prs

Run = Callable[[str, list[str]], "str | None"]  # the repository and git's arguments -> its output, None on failure


@dataclass(frozen=True)
class Pending:
    repo: str  # the repository's folder name
    branch: str
    uncommitted: int  # files changed or new, counted while the branch is checked out
    unpushed: int  # commits on the branch that no remote has

    @property
    def label(self) -> str:
        """`web-app (main): 2 commits not pushed · 1 file not committed`"""
        parts = [f"{_plural(self.unpushed, 'commit')} not pushed" if self.unpushed else "",
                 f"{_plural(self.uncommitted, 'file')} not committed" if self.uncommitted else ""]
        return f"{self.repo} ({self.branch}): " + " · ".join(p for p in parts if p)


def run(root: str, args: list[str]) -> str | None:
    path = shutil.which("git")
    if path is None:
        return None
    try:
        done = subprocess.run([path, "-C", root, *args], capture_output=True, text=True, encoding="utf-8", timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def pending(its: list[items.Item], state: dict, run: Run = run) -> dict[str, list[Pending]]:
    """{item slug: what git says is left}. Several items may have worked on one branch: it shows once, on
    the one updated last."""
    owner: dict[tuple[str, str], items.Item] = {}
    for item in its:
        for place in places(item, prs.noted_branches(state, str(item.path))):
            if place not in owner or item.meta.get("updated", "") > owner[place].meta.get("updated", ""):
                owner[place] = item
    found: dict[str, list[Pending]] = {}
    for (root, branch), item in owner.items():
        if left := check(root, branch, run):
            found.setdefault(item.slug, []).append(left)
    return found


def places(item: items.Item, noted: list) -> list[tuple[str, str]]:
    """The (repository, branch) pairs the item's work happened on, shared branches included."""
    found = [(s.folder, m[1]) for s in item.sessions if (m := re.search(r"\(([^)]*)\)\s*$", s.repo))]
    found += [(str(n[0]), str(n[1])) for n in noted if isinstance(n, list) and len(n) == 2]
    pairs = []
    for folder, branch in found:
        if not folder or branch in ("", "-", "HEAD") or not Path(folder).is_dir():
            continue
        root = prs.repository(folder)
        if (Path(root) / ".git").exists():
            pairs.append((root, branch))
    return list(dict.fromkeys(pairs))


def check(root: str, branch: str, run: Run = run) -> Pending | None:
    unpushed = 0
    if (run(root, ["remote"]) or "").strip():
        count = (run(root, ["rev-list", "--count", f"refs/heads/{branch}", "--not", "--remotes"]) or "").strip()
        unpushed = int(count) if count.isdigit() else 0
    uncommitted = 0
    if (at := prs.branch_at(root)) and at[1] == branch:
        uncommitted = len([line for line in (run(root, ["status", "--porcelain"]) or "").splitlines() if line.strip()])
    return Pending(Path(root).name, branch, uncommitted, unpushed) if uncommitted or unpushed else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"
