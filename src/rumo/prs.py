"""Pull requests found on their own: through the GitHub CLI (`gh`), the state of each PR an item lists,
and the PRs opened from the branches its work happened on.

The branches come from the item's session lines and from what the hook notes at the end of each turn
of a linked session (state.json), since a session is often linked on `main` and branches later. All of
it is optional: without `gh`, and for other hosts, an item keeps the states written in it, by the agent
or by the user.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from rumo import config, items

Run = Callable[[list[str], "str | None"], "str | None"]  # gh's arguments and folder -> its output, None on failure
FIELDS = "url,state,isDraft,title"
DEFAULT_BRANCHES = {"main", "master"}  # where work starts, not a PR's branch
NOTED_BRANCHES = 10  # per item
NOTED_ITEMS = 200


def gh() -> Run | None:
    """Run the GitHub CLI, or None when it is not installed."""
    path = shutil.which("gh")
    if path is None:
        return None

    def run(args: list[str], folder: str | None) -> str | None:
        try:
            done = subprocess.run([path, *args], cwd=folder, capture_output=True, text=True, encoding="utf-8", timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout if done.returncode == 0 else None

    return run


def refresh(item: items.Item, noted: list, dropped: set[str], run: Run) -> bool:
    """Bring the item's PRs in line with what gh knows; True if the file changed."""
    return apply(item, check(item, noted, run), dropped)


def check(item: items.Item, noted: list, run: Run) -> dict[str, tuple[str, str]]:
    """What gh says, {link: (state, title)}: the PRs of the item's branches, then each other open GitHub PR."""
    found: dict[str, tuple[str, str]] = {}
    for folder, branch in branches(item, noted):
        if os.path.isdir(folder):
            found |= _read(run(["pr", "list", "--head", branch, "--state", "all", "--json", FIELDS, "--limit", "20"], folder))
    for pr in item.prs:
        link = items.pr_link(pr.url) or pr.url
        if link not in found and not pr.finished and re.search(r"/pull/\d+$", link) and "bitbucket.org" not in link:
            found |= _read(run(["pr", "view", link, "--json", FIELDS], None))
    return found


def apply(item: items.Item, found: dict[str, tuple[str, str]], dropped: set[str]) -> bool:
    """Write the states gh reported, fill missing titles and add new PRs (never one the user deleted)."""
    changed = False
    for link, (state, title) in found.items():
        current = items.load(item.path)  # every write checks the file it read
        pr = next((p for p in current.prs if items.pr_link(p.url) == link), None)
        try:
            if pr is None and link not in dropped:
                changed |= items.add_pr(current, link, state, title)
            elif pr is not None and ((state and state != pr.state) or (title and not pr.title)):
                items.update_pr(current, pr, state=state or pr.state, title=pr.title or title)
                changed = True
        except items.WriteConflict:
            break  # changed meanwhile: the next check picks it up
    return changed


def branches(item: items.Item, noted: list) -> list[tuple[str, str]]:
    """The (folder, branch) pairs the item's work happened on, without the default branches."""
    found = [(s.folder, m[1]) for s in item.sessions if (m := re.search(r"\(([^)]*)\)\s*$", s.repo))]
    found += [(str(n[0]), str(n[1])) for n in noted if isinstance(n, list) and len(n) == 2]
    return [p for p in dict.fromkeys(found) if p[1] not in DEFAULT_BRANCHES and p[1] not in ("", "-", "HEAD")]


def _read(output: str | None) -> dict[str, tuple[str, str]]:
    try:
        data = json.loads(output or "")
    except ValueError:
        return {}
    found = {}
    for entry in data if isinstance(data, list) else [data]:
        if isinstance(entry, dict) and (link := items.pr_link(str(entry.get("url", "")))):
            found[link] = (_state(entry), " ".join(str(entry.get("title") or "").split()))
    return found


def _state(entry: dict) -> str:
    state = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}.get(str(entry.get("state")), "")
    return "draft" if state == "open" and entry.get("isDraft") else state


# ---------- what rumo remembers (state.json) ----------


def branch_at(folder: Path | str) -> tuple[str, str] | None:
    """The repository holding the folder and its checked-out branch, read from `.git` (no `git` call)."""
    start = Path(os.path.abspath(folder))
    root = next((f for f in (start, *start.parents) if (f / ".git").exists()), None)
    if root is None:
        return None
    git = root / ".git"
    try:
        if git.is_file():  # a worktree: `gitdir: <path>`
            text = git.read_text(encoding="utf-8").strip()
            git = root / text.removeprefix("gitdir:").strip()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref: refs/heads/"):
        return None  # detached
    return str(root), head.removeprefix("ref: refs/heads/")


def note_branch(config_path: Path, item_paths: list[str], folder: Path | str) -> None:
    """Remember that these items' work happened on the folder's branch."""
    at = branch_at(folder)
    if at is None or at[1] in DEFAULT_BRANCHES or not item_paths:
        return
    noted = config.load_state(config_path).get("pr_branches")
    noted = noted if isinstance(noted, dict) else {}
    if all(list(at) in noted.get(p, []) for p in item_paths):
        return
    for path in item_paths:
        before = noted.pop(path, [])
        before = [n for n in before if n != list(at)] if isinstance(before, list) else []
        noted[path] = (before + [list(at)])[-NOTED_BRANCHES:]  # the latest last, so the oldest go first
    config.update_state(config_path, pr_branches=dict(list(noted.items())[-NOTED_ITEMS:]))


def noted_branches(state: dict, item_path: str) -> list:
    noted = state.get("pr_branches")
    found = noted.get(item_path) if isinstance(noted, dict) else None
    return found if isinstance(found, list) else []


def drop(config_path: Path, item_path: str, url: str) -> None:
    """Remember a PR the user deleted from the item, so that it is never found and added again."""
    gone = config.load_state(config_path).get("prs_dropped")
    gone = gone if isinstance(gone, dict) else {}
    links = gone.pop(item_path, [])
    links = links if isinstance(links, list) else []
    gone[item_path] = list(dict.fromkeys([*links, items.pr_link(url) or url]))
    config.update_state(config_path, prs_dropped=dict(list(gone.items())[-NOTED_ITEMS:]))


def dropped(state: dict, item_path: str) -> set[str]:
    gone = state.get("prs_dropped")
    links = gone.get(item_path) if isinstance(gone, dict) else None
    return {str(link) for link in links} if isinstance(links, list) else set()
