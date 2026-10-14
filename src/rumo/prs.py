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
from datetime import date, timedelta
from pathlib import Path

from rumo import config, items

Run = Callable[[list[str], "str | None"], "str | None"]  # gh's arguments and folder -> its output, None on failure
FIELDS = "url,state,isDraft,title"
SHARED_BRANCHES = {"main", "master", "develop", "dev", "trunk"}  # where work starts or meets, not a PR's own branch
NOTED_BRANCHES = 10  # per item
NOTED_ITEMS = 200


def gh() -> Run | None:
    """Run the GitHub CLI, or None when it is not installed."""
    path = shutil.which("gh")
    if path is None:
        return None

    def run(args: list[str], folder: str | None) -> str | None:
        try:
            done = subprocess.run([path, *args], cwd=folder, capture_output=True, text=True, encoding="utf-8", timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout if done.returncode == 0 else None

    return run


Found = dict[str, tuple[str, str, str]]  # link -> (state, title, created at)


def refresh(item: items.Item, noted: list, dropped: set[str], run: Run, asked: dict | None = None) -> bool:
    """Bring the item's PRs in line with what gh knows; True if the file changed.

    `asked` keeps gh's answer per repository and branch, so that items sharing one ask once per check.
    """
    return apply(item, check(item, noted, run, asked), dropped)


def check(item: items.Item, noted: list, run: Run, asked: dict | None = None) -> dict[str, tuple[str, str]]:
    """What gh says, {link: (state, title)}: the PRs of the item's branches, then each other open GitHub PR."""
    asked = {} if asked is None else asked
    since = _since(item)
    found: dict[str, tuple[str, str]] = {}
    for folder, branch in branches(item, noted):
        if not os.path.isdir(folder):
            continue
        key = (_repository(folder), branch)
        if key not in asked:
            asked[key] = _read(run(["pr", "list", "--head", branch, "--state", "all", "--json", f"{FIELDS},createdAt",
                                    "--limit", "20"], key[0]))
        # A branch name used again later is not this item's: only PRs opened since the item began count.
        found |= {link: (state, title) for link, (state, title, created) in asked[key].items() if not created or created[:10] >= since}
    for pr in item.prs:
        link = items.pr_link(pr.url) or pr.url
        if link not in found and not pr.finished and re.search(r"/pull/\d+$", link) and "bitbucket.org" not in link:
            found |= {link: (state, title) for link, (state, title, _) in _read(run(["pr", "view", link, "--json", FIELDS], None)).items()}
    return found


def apply(item: items.Item, found: dict[str, tuple[str, str]], dropped: set[str]) -> bool:
    """Write the states gh reported, fill missing titles and add new PRs (never one the user deleted).

    `updated` stays as it was: gh learned something, nobody worked on the item.
    """
    changed = False
    for link, (state, title) in found.items():
        try:
            current = items.load(item.path)  # every write checks the file it read
            pr = next((p for p in current.prs if items.pr_link(p.url) == link), None)
            if pr is None and link not in dropped:
                changed |= items.add_pr(current, link, state, title, touch=False)
            elif pr is not None and ((state and state != pr.state) or (title and not pr.title)):
                items.update_pr(current, pr, state=state or pr.state, title=pr.title or title, touch=False)
                changed = True
        except (items.WriteConflict, OSError):
            break  # changed, moved or deleted meanwhile: the next check picks it up
    return changed


def branches(item: items.Item, noted: list) -> list[tuple[str, str]]:
    """The (folder, branch) pairs the item's work happened on, without the shared branches."""
    found = [(s.folder, m[1]) for s in item.sessions if (m := re.search(r"\(([^)]*)\)\s*$", s.repo))]
    found += [(str(n[0]), str(n[1])) for n in noted if isinstance(n, list) and len(n) == 2]
    return [p for p in dict.fromkeys(found) if p[1] not in SHARED_BRANCHES and p[1] not in ("", "-", "HEAD")]


def _since(item: items.Item) -> str:
    """The day before the item began (its creation or first session; a day early for time zones), or ""."""
    days = [d[:10] for d in [item.meta.get("created", ""), *(s.when for s in item.sessions)] if re.match(r"\d{4}-\d\d-\d\d", d)]
    try:
        return (date.fromisoformat(min(days)) - timedelta(days=1)).isoformat() if days else ""
    except ValueError:
        return ""


def _repository(folder: str) -> str:
    """The repository holding the folder, so a subfolder and the root ask gh once; the folder outside one."""
    start = Path(os.path.abspath(folder))
    return str(next((f for f in (start, *start.parents) if (f / ".git").exists()), start))


def _read(output: str | None) -> Found:
    try:
        data = json.loads(output or "")
    except ValueError:
        return {}
    found = {}
    for entry in data if isinstance(data, list) else [data]:
        if isinstance(entry, dict) and (link := items.pr_link(str(entry.get("url", "")))):
            found[link] = (_state(entry), " ".join(str(entry.get("title") or "").split()), str(entry.get("createdAt") or ""))
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
    if at is None or at[1] in SHARED_BRANCHES or not item_paths:
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
