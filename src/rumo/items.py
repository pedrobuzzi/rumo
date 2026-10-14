"""Items: one Markdown file per piece of work, in a single local folder.

There is no index or database. Every reader (the TUI, the hook, the agent) reads the
folder, and every write checks that the file did not change since it was read, so a
human in the TUI and an agent session never overwrite each other.

Stdlib only: the hook imports this module on every prompt.
"""

from __future__ import annotations

import os
import re
import shlex
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

STATUSES = ("idea", "doing", "waiting", "paused", "done")
KINDS = ("agent", "manual")  # who does a step: the agent, or the user (or someone else) outside it
PR_STATES = ("open", "draft", "merged", "closed")  # a PR line without one: nobody has said yet

TIME_FORMAT = "%Y-%m-%d %H:%M"
PAST_WINDOW_DAYS = 14

_STEP_RE = re.compile(r"^- \[( |x|X)\] (?:\[(agent|manual)\] )?(.*)$")
_SECTION_RE = re.compile(r"^## (.+?)\s*$")
_PR_RE = re.compile(r"^- (?:\[(open|draft|merged|closed)\] )?(https?://\S+)(?: +(.*))?$")
# A pull or merge request on GitHub (or GitHub Enterprise), GitLab or Bitbucket: the link up to its number.
_PR_LINK_RE = re.compile(r"https?://[\w.-]+(?::\d+)?/(?:[\w.-]+/)+(?:pull|-/merge_requests|pull-requests)/\d+")


class WriteConflict(Exception):
    """The file changed on disk after it was read."""


@dataclass
class Step:
    done: bool
    kind: str  # "agent", "manual" or "" when the line has no marker
    text: str
    line: int  # index of the line in the file

    @property
    def label(self) -> str:
        return (f"[{self.kind}] " if self.kind else "") + self.text


@dataclass
class PR:
    state: str  # one of PR_STATES, or "" when unknown
    url: str
    title: str
    line: int

    @property
    def name(self) -> str:
        """`https://github.com/acme/web-app/pull/12` -> `web-app#12`; a GitLab merge request is `api!34`."""
        if m := re.match(r"https?://[^/]+/(?:.+/)?([^/]+)/(?:pull|pull-requests)/(\d+)$", self.url):
            return f"{m[1]}#{m[2]}"
        if m := re.match(r"https?://[^/]+/(?:.+/)?([^/]+)/-/merge_requests/(\d+)$", self.url):
            return f"{m[1]}!{m[2]}"
        return re.sub(r"^https?://", "", self.url).rstrip("/")

    @property
    def finished(self) -> bool:
        return self.state in ("merged", "closed")


@dataclass
class Session:
    when: str
    repo: str
    id: str
    folder: str
    closed: bool

    @property
    def resume_command(self) -> str:
        return self.resume_command_for(windows=os.name == "nt")

    def resume_command_for(self, windows: bool) -> str:
        """A command that reopens the agent session from its original folder, in the user's shell."""
        if windows:  # PowerShell, where `&&` is missing before 7 and single quotes differ
            return f'cd "{self.folder}"; claude --resume {self.id}'
        return f"cd {shlex.quote(self.folder)} && claude --resume {self.id}"


@dataclass
class Item:
    path: Path
    mtime: float
    meta: dict[str, str]
    sections: dict[str, str]
    steps: list[Step] = field(default_factory=list)
    prs: list[PR] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.slug

    @property
    def status(self) -> str:
        status = self.meta.get("status", "")
        return status if status in STATUSES else "doing"

    @property
    def group(self) -> str:
        return self.meta.get("group", "")

    @property
    def tags(self) -> list[str]:
        return _parse_list(self.meta.get("tags", ""))

    @property
    def done_at(self) -> str:
        """When the item was finished; hand-edited done items without `done:` fall back to `updated`."""
        return self.meta.get("done") or self.meta.get("updated", "")

    @property
    def next_step(self) -> Step | None:
        return next((s for s in self.steps if not s.done), None)

    def linked_to(self, session_id: str) -> bool:
        """Whether that agent session keeps this item updated: its line is there and not closed."""
        return any(s.id == session_id and not s.closed for s in self.sessions)


def load(path: Path) -> Item:
    mtime = path.stat().st_mtime
    lines = path.read_text(encoding="utf-8").splitlines()
    meta, body_start = _read_frontmatter(lines)

    sections: dict[str, list[str]] = {}
    steps: list[Step] = []
    prs: list[PR] = []
    sessions: list[Session] = []
    current = ""
    for i in range(body_start, len(lines)):
        line = lines[i]
        if m := _SECTION_RE.match(line):
            current = m.group(1).strip().lower()
            sections[current] = []
            continue
        if not current:
            continue
        sections[current].append(line)
        if current == "steps" and (m := _STEP_RE.match(line)):
            steps.append(Step(m.group(1) != " ", m.group(2) or "", m.group(3), i))
        elif current == "prs" and (m := _PR_RE.match(line)):
            prs.append(PR(m.group(1) or "", m.group(2), (m.group(3) or "").strip(), i))
        elif current == "sessions" and line.startswith("- "):
            parts = [p.strip() for p in line[2:].split("|")]
            if len(parts) >= 4:
                closed = len(parts) >= 5 and parts[4].lower() == "closed"
                sessions.append(Session(parts[0], parts[1], parts[2], parts[3], closed))

    return Item(
        path=path,
        mtime=mtime,
        meta=meta,
        sections={k: "\n".join(v).strip() for k, v in sections.items()},
        steps=steps,
        prs=prs,
        sessions=sessions,
    )


@dataclass
class Timeline:
    past: list[Item]  # done, oldest first so the list flows into Now
    older: int  # done items hidden by the Past window
    now: list[Item]  # doing, then waiting
    later: list[Item]  # paused, then ideas


def timeline(all_items: list[Item], today: date | None = None, show_older: bool = False) -> Timeline:
    cutoff = ((today or date.today()) - timedelta(days=PAST_WINDOW_DAYS)).isoformat()
    done = sorted((i for i in all_items if i.status == "done"), key=lambda i: i.done_at)
    past = done if show_older else [i for i in done if i.done_at[:10] >= cutoff]

    def by_status(order: tuple[str, ...]) -> list[Item]:
        chosen = sorted((i for i in all_items if i.status in order), key=lambda i: i.meta.get("updated", ""), reverse=True)
        return sorted(chosen, key=lambda i: order.index(i.status))  # stable: keeps recency inside each status

    return Timeline(past, len(done) - len(past), by_status(("doing", "waiting")), by_status(("paused", "idea")))


def index(all_items: list[Item]) -> str:
    """One line per item, for an agent to judge similarity without opening every file."""
    lines = []
    for item in all_items:
        parts = [item.slug, item.status, item.title]
        if item.group:
            parts.append(f"group: {item.group}")
        if item.tags:
            parts.append(f"tags: {', '.join(item.tags)}")
        if left_off := item.sections.get("left off", "").split("\n")[0].strip():
            parts.append(f"left off: {left_off[:120]}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def list_items(folder: Path) -> list[Item]:
    found = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            found.append(load(path))
        except (OSError, UnicodeDecodeError):
            continue  # being written or unreadable: it shows up on the next read
    return found


def slugify(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-") or "item"


def create_idea(folder: Path, title: str, now: datetime | None = None) -> Path:
    """Create a small item with status `idea`; it grows into a full item when work starts."""
    stamp = _stamp(now)
    base = f"{stamp[:10]}-{slugify(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    path, n = folder / f"{base}.md", 2
    while path.exists():
        path, n = folder / f"{base}-{n}.md", n + 1
    text = f"---\ntitle: {title.strip()}\nstatus: idea\ntags: []\ncreated: {stamp}\nupdated: {stamp}\n---\n"
    with path.open("x", encoding="utf-8") as f:  # "x": never overwrite a file created meanwhile
        f.write(text)
    return path


def set_status(item: Item, status: str, now: datetime | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    stamp = _stamp(now)

    def edit(lines: list[str], _: int) -> None:
        _set_meta(lines, "status", status)
        _set_meta(lines, "done", stamp if status == "done" else None)

    _save(item, edit, stamp)


def set_group(item: Item, group: str | None, now: datetime | None = None) -> None:
    _save(item, lambda lines, _: _set_meta(lines, "group", group or None), _stamp(now))


def set_tags(item: Item, tags: list[str], now: datetime | None = None) -> None:
    _save(item, lambda lines, _: _set_meta(lines, "tags", f"[{', '.join(tags)}]"), _stamp(now))


def toggle_step(item: Item, step: Step, now: datetime | None = None) -> None:
    def edit(lines: list[str], offset: int) -> None:
        i = _step_at(item, lines, step, offset)
        lines[i] = f"- [{' ' if step.done else 'x'}] " + lines[i][6:]

    _save(item, edit, _stamp(now))


def add_step(item: Item, text: str, kind: str, after: Step | None = None, now: datetime | None = None) -> int:
    """Add an open step below `after`, or else after the last open step (a new Steps section if none).

    Returns where the new step lands among the item's steps.
    """
    line = _step_line(False, kind, text)
    anchor = after or next((s for s in reversed(item.steps) if not s.done), item.steps[-1] if item.steps else None)

    def edit(lines: list[str], offset: int) -> None:
        if anchor:
            lines.insert(_step_at(item, lines, anchor, offset) + 1, line)
            return
        headings = _headings(lines)
        if "steps" in headings:
            lines.insert(headings["steps"] + 1, line)
        elif "sessions" in headings:
            lines[headings["sessions"]:headings["sessions"]] = ["## Steps", line, ""]
        else:
            lines.extend(([""] if lines and lines[-1].strip() else []) + ["## Steps", line])

    _save(item, edit, _stamp(now))
    return item.steps.index(anchor) + 1 if anchor else 0


def edit_step(item: Item, step: Step, text: str, kind: str, now: datetime | None = None) -> None:
    line = _step_line(step.done, kind, text)

    def edit(lines: list[str], offset: int) -> None:
        lines[_step_at(item, lines, step, offset)] = line

    _save(item, edit, _stamp(now))


def delete_step(item: Item, step: Step, now: datetime | None = None) -> None:
    def edit(lines: list[str], offset: int) -> None:
        del lines[_step_at(item, lines, step, offset)]

    _save(item, edit, _stamp(now))


def swap_steps(item: Item, step: Step, other: Step, now: datetime | None = None) -> None:
    """Swap two steps' lines: moving a step up or down swaps it with the step shown next to it."""
    def edit(lines: list[str], offset: int) -> None:
        i, j = _step_at(item, lines, step, offset), _step_at(item, lines, other, offset)
        lines[i], lines[j] = lines[j], lines[i]

    _save(item, edit, _stamp(now))


def pr_link(url: str) -> str | None:
    """The link to keep for a PR: a known host's PR up to its number (no `/files`, query or anchor),
    any other web link as given, or None for anything that is not a link."""
    url = url.strip()
    if m := _PR_LINK_RE.match(url):
        return m.group(0)
    return url if re.fullmatch(r"https?://\S+", url) else None


def pr_links(text: str) -> list[str]:
    """The PRs a text links to (a command's output, a message), once each, in order."""
    return list(dict.fromkeys(_PR_LINK_RE.findall(text)))


def add_pr(
    item: Item, url: str, state: str = "", title: str = "", now: datetime | None = None, touch: bool = True,
) -> bool:
    """Add a PR at the end of the PRs section (a new one before Sessions if none). False if already there.

    `touch=False` keeps `updated`: for what rumo learns on its own, which is not work on the item.
    """
    link = pr_link(url)
    if link is None:
        raise ValueError(f"not a link: {url}")
    if any(pr_link(p.url) == link for p in item.prs):
        return False
    line = _pr_line(state, link, title)

    def edit(lines: list[str], _: int) -> None:
        headings = _headings(lines)
        if "prs" in headings:
            start = headings["prs"] + 1
            end = next((i for i in range(start, len(lines)) if _SECTION_RE.match(lines[i])), len(lines))
            last = next((i for i in range(end - 1, start - 1, -1) if lines[i].strip()), headings["prs"])
            lines.insert(last + 1, line)
        elif "sessions" in headings:
            lines[headings["sessions"]:headings["sessions"]] = ["## PRs", line, ""]
        else:
            lines.extend(([""] if lines and lines[-1].strip() else []) + ["## PRs", line])

    _save(item, edit, _stamp(now) if touch else None)
    return True


def update_pr(
    item: Item, pr: PR, state: str | None = None, title: str | None = None, url: str | None = None,
    now: datetime | None = None, touch: bool = True,
) -> None:
    link = pr.url if url is None else pr_link(url)
    if link is None:
        raise ValueError(f"not a link: {url}")
    line = _pr_line(pr.state if state is None else state, link, pr.title if title is None else title)

    def edit(lines: list[str], offset: int) -> None:
        lines[_pr_at(item, lines, pr, offset)] = line

    _save(item, edit, _stamp(now) if touch else None)


def delete_pr(item: Item, pr: PR, now: datetime | None = None) -> None:
    def edit(lines: list[str], offset: int) -> None:
        del lines[_pr_at(item, lines, pr, offset)]

    _save(item, edit, _stamp(now))


def _pr_line(state: str, url: str, title: str) -> str:
    if state and state not in PR_STATES:
        raise ValueError(f"unknown PR state: {state}")
    title = " ".join(title.split())
    return "- " + (f"[{state}] " if state else "") + url + (f" {title}" if title else "")


def _pr_at(item: Item, lines: list[str], pr: PR, offset: int) -> int:
    """The PR's line index now, or WriteConflict when that line no longer holds the PR."""
    i = pr.line + offset
    m = _PR_RE.match(lines[i]) if i < len(lines) else None
    if not m or m.group(2) != pr.url:
        raise WriteConflict(item.path.name)
    return i


def _headings(lines: list[str]) -> dict[str, int]:
    """Where each section starts: {lowercase name: line index}."""
    return {
        m.group(1).strip().lower(): i
        for i in range(_frontmatter_end(lines)[0] + 1, len(lines))
        if (m := _SECTION_RE.match(lines[i]))
    }


def _step_line(done: bool, kind: str, text: str) -> str:
    text = " ".join(text.split())  # one line, whatever was pasted
    if not text:
        raise ValueError("a step needs text")
    if kind not in KINDS:
        raise ValueError(f"unknown step kind: {kind}")
    return f"- [{'x' if done else ' '}] [{kind}] {text}"


def _step_at(item: Item, lines: list[str], step: Step, offset: int) -> int:
    """The step's line index now, or WriteConflict when that line no longer holds the step."""
    i = step.line + offset
    m = _STEP_RE.match(lines[i]) if i < len(lines) else None
    if not m or m.group(3) != step.text:
        raise WriteConflict(item.path.name)
    return i


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now()).strftime(TIME_FORMAT)


def _save(item: Item, edit: Callable[[list[str], int], None], stamp: str | None) -> None:
    """Apply `edit` to the file's lines and bump `updated` (unless stamp is None), unless the file
    changed since it was read.

    `edit` gets the offset to add to body line numbers recorded at load time: a file without
    frontmatter gets one prepended, and an unclosed one gets its closing `---` inserted.
    """
    if item.path.stat().st_mtime != item.mtime:
        raise WriteConflict(item.path.name)
    lines = item.path.read_text(encoding="utf-8").splitlines()
    offset = 0
    if not lines or lines[0].strip() != "---":
        lines[:0] = ["---", "---"]
        offset = 2
    else:
        end, closed = _frontmatter_end(lines)
        if not closed:
            lines.insert(end, "---")
            offset = 1
    edit(lines, offset)
    if stamp is not None:
        _set_meta(lines, "updated", stamp)
    tmp = item.path.with_name(item.path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, item.path)


def _frontmatter_end(lines: list[str]) -> tuple[int, bool]:
    """Index of the line that ends the frontmatter (which starts at line 0) and whether it is a real `---`.

    A hand edit may drop the closing `---`; then the frontmatter ends at the first line that
    cannot be a `key: value` (a blank line or a section heading).
    """
    for i in range(1, len(lines)):
        line = lines[i].strip()
        if line == "---":
            return i, True
        if not line or line.startswith("#") or ":" not in line:
            return i, False
    return len(lines), False


def _set_meta(lines: list[str], key: str, value: str | None) -> None:
    """Set (or remove, when value is None) a frontmatter key, keeping every other line."""
    end, _ = _frontmatter_end(lines)
    for i in range(1, end):
        if lines[i].partition(":")[0].strip() == key:
            if value is None:
                del lines[i]
            else:
                lines[i] = f"{key}: {value}"
            return
    if value is not None:
        lines.insert(end, f"{key}: {value}")


def _parse_list(value: str) -> list[str]:
    value = value.strip().removeprefix("[").removesuffix("]")
    return [v.strip() for v in value.split(",") if v.strip()]


def _read_frontmatter(lines: list[str]) -> tuple[dict[str, str], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    end, closed = _frontmatter_end(lines)
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = _unquote(value.strip())
    return meta, end + 1 if closed else end


def _unquote(value: str) -> str:
    """`"PhraseDay: ASO"` -> `PhraseDay: ASO`: agents quote values that contain a colon, YAML style."""
    if len(value) < 2 or value[0] != value[-1] or value[0] not in "\"'":
        return value
    inner = value[1:-1]
    return re.sub(r'\\(["\\])', r"\1", inner) if value[0] == '"' else inner
