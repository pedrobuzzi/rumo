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

TIME_FORMAT = "%Y-%m-%d %H:%M"
PAST_WINDOW_DAYS = 14

_STEP_RE = re.compile(r"^- \[( |x|X)\] (?:\[(agent|manual)\] )?(.*)$")
_SECTION_RE = re.compile(r"^## (.+?)\s*$")


class WriteConflict(Exception):
    """The file changed on disk after it was read."""


@dataclass
class Step:
    done: bool
    kind: str  # "agent", "manual" or "" when the line has no marker
    text: str
    line: int  # index of the line in the file


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


def load(path: Path) -> Item:
    mtime = path.stat().st_mtime
    lines = path.read_text(encoding="utf-8").splitlines()
    meta, body_start = _read_frontmatter(lines)

    sections: dict[str, list[str]] = {}
    steps: list[Step] = []
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
        i = step.line + offset
        m = _STEP_RE.match(lines[i]) if i < len(lines) else None
        if not m or m.group(3) != step.text:
            raise WriteConflict(item.path.name)
        mark = " " if m.group(1) != " " else "x"
        lines[i] = f"- [{mark}] " + lines[i][6:]

    _save(item, edit, _stamp(now))


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now()).strftime(TIME_FORMAT)


def _save(item: Item, edit: Callable[[list[str], int], None], stamp: str) -> None:
    """Apply `edit` to the file's lines and bump `updated`, unless the file changed since it was read.

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
