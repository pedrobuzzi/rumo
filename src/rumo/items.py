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

STATUSES = ("idea", "doing", "waiting", "paused", "done", "dropped")
FINISHED = ("done", "dropped")  # both go to Past: the work ended, or the decision was not to do it
KINDS = ("agent", "manual", "waiting")  # who acts: the agent, the user outside it, or someone or something else
ITEM_KIND = "item"  # a step that became an item of its own: `- [ ] [item] <text> → <slug>`
PR_STATES = ("open", "draft", "merged", "closed")  # a PR line without one: nobody has said yet

TIME_FORMAT = "%Y-%m-%d %H:%M"
PAST_WINDOW_DAYS = 14
STALE_GRACE_SECONDS = 300  # a running exploration this far past its limit is taken as failed

_STEP_RE = re.compile(r"^- \[( |x|X)\] (?:\[(agent|manual|waiting|item)\] )?(.*)$")
_ARROW = " → "
_SECTION_RE = re.compile(r"^## (.+?)\s*$")
_PR_RE = re.compile(r"^- (?:\[(open|draft|merged|closed)\] )?(https?://\S+)(?: +(.*))?$")
# A pull or merge request on GitHub (or GitHub Enterprise), GitLab or Bitbucket: the link up to its number.
_PR_LINK_RE = re.compile(r"https?://[\w.-]+(?::\d+)?/(?:[\w.-]+/)+(?:pull|-/merge_requests|pull-requests)/\d+")


class WriteConflict(Exception):
    """The file changed on disk after it was read."""


@dataclass
class Step:
    done: bool
    kind: str  # one of KINDS, or "" when the line has no marker
    text: str
    line: int  # index of the line in the file

    @property
    def label(self) -> str:
        return (f"[{self.kind}] " if self.kind else "") + self.text

    @property
    def target(self) -> str:
        """The slug of the item an `[item]` step became; "" for any other step."""
        return self.text.rpartition(_ARROW)[2].strip() if self.kind == ITEM_KIND and _ARROW in self.text else ""

    @property
    def shown(self) -> str:
        """The text to show: an `[item]` step without the slug it points to."""
        return self.text.rpartition(_ARROW)[0].strip() if self.target else self.text


@dataclass
class Exploration:
    """A question asked about an item and its answer, under `## Explorations`.

        ### 2026-11-05 14:10 <question>
        <!-- rumo:exploration id=3f2a state=done -->
        <answer>
        <!-- rumo:next -->
        - <next question>

    An entry without the marker (written by hand or migrated) is done.
    """

    when: str
    question: str
    id: str = ""
    state: str = "done"  # running, done or failed
    pid: int = 0
    started: str = ""  # YYYY-MM-DDTHH:MM, while running
    limit: int = 0  # seconds the run may take
    error: str = ""
    answer: str = ""
    next_questions: list[str] = field(default_factory=list)
    line: int = 0  # index of the heading line in the file


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
    explorations: list[Exploration] = field(default_factory=list)

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
    def from_slug(self) -> str:
        """The item this one came from (`from:`), by slug; "" when none."""
        return self.meta.get("from", "").strip()

    @property
    def done_at(self) -> str:
        """When the item was finished or dropped; hand-edited ones without the date fall back to `updated`."""
        return self.meta.get("done") or self.meta.get("dropped") or self.meta.get("updated", "")

    def idle_days(self, today: date) -> int | None:
        """Days since an idea last changed; None for other statuses or without a readable `updated`."""
        if self.status != "idea":
            return None
        try:
            return max((today - date.fromisoformat(self.meta.get("updated", "")[:10])).days, 0)  # a clock behind
        except ValueError:
            return None

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
    explorations: list[Exploration] = []
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
        if current == "explorations":
            if line.startswith("### "):
                explorations.append(_exploration_heading(line, i))
            elif explorations:
                explorations[-1].answer += line + "\n"  # sorted out below
            continue
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
        explorations=[_exploration_body(e) for e in explorations],
    )


_EXPLORATION_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) +(.*)$")
_MARKER_RE = re.compile(r"^<!-- rumo:exploration (.*?)\s*-->$")
NEXT_MARKER = "<!-- rumo:next -->"


def _exploration_heading(line: str, i: int) -> Exploration:
    if m := _EXPLORATION_RE.match(line):
        return Exploration(when=m.group(1), question=m.group(2).strip(), line=i)
    return Exploration(when="", question=line[4:].strip(), line=i)


def _exploration_body(e: Exploration) -> Exploration:
    """Split the raw lines gathered after the heading into marker, answer and next questions."""
    answer: list[str] = []
    in_next = False
    for line in e.answer.splitlines():
        stripped = line.strip()
        if m := _MARKER_RE.match(stripped):
            attrs = {k: v.strip('"') for k, v in re.findall(r'(\w+)=("[^"]*"|\S+)', m.group(1))}
            e.id = attrs.get("id", "")
            e.state = attrs.get("state", "done") if attrs.get("state") in ("running", "done", "failed") else "done"
            e.pid = int(attrs["pid"]) if attrs.get("pid", "").isdigit() else 0
            e.started = attrs.get("started", "")
            e.limit = int(attrs["limit"]) if attrs.get("limit", "").isdigit() else 0
            e.error = attrs.get("error", "")
        elif stripped == NEXT_MARKER:
            in_next = True
        elif in_next and stripped.startswith("- "):
            e.next_questions.append(stripped[2:].strip())
        elif not in_next:
            answer.append(line)
    e.answer = "\n".join(answer).strip()
    return e


def exploration_state(e: Exploration, now: datetime, alive: Callable[[int], bool]) -> str:
    """The entry's state, taking as failed a run whose process is gone or that is well past its limit."""
    if e.state != "running":
        return e.state
    if e.pid <= 0 or not alive(e.pid):
        return "failed"
    try:
        started = datetime.strptime(e.started, "%Y-%m-%dT%H:%M")
    except ValueError:
        return "running"
    return "failed" if (now - started).total_seconds() > (e.limit or 600) + STALE_GRACE_SECONDS else "running"


@dataclass
class Timeline:
    past: list[Item]  # done, oldest first so the list flows into Now
    older: int  # done items hidden by the Past window
    now: list[Item]  # doing, then waiting
    later: list[Item]  # paused, then ideas


def timeline(all_items: list[Item], today: date | None = None, show_older: bool = False) -> Timeline:
    cutoff = ((today or date.today()) - timedelta(days=PAST_WINDOW_DAYS)).isoformat()
    done = sorted((i for i in all_items if i.status in FINISHED), key=lambda i: i.done_at)
    past = done if show_older else [i for i in done if i.done_at[:10] >= cutoff]

    def by_status(order: tuple[str, ...]) -> list[Item]:
        chosen = sorted((i for i in all_items if i.status in order), key=lambda i: i.meta.get("updated", ""), reverse=True)
        return sorted(chosen, key=lambda i: order.index(i.status))  # stable: keeps recency inside each status

    return Timeline(past, len(done) - len(past), by_status(("doing", "waiting")), by_status(("paused", "idea")))


def index(all_items: list[Item], today: date | None = None) -> str:
    """One line per item, for an agent to judge similarity without opening every file."""
    lines = []
    for item in all_items:
        parts = [item.slug, item.status, item.title]
        if item.group:
            parts.append(f"group: {item.group}")
        if item.tags:
            parts.append(f"tags: {', '.join(item.tags)}")
        if item.from_slug:
            parts.append(f"from: {item.from_slug}")
        if (idle := item.idle_days(today or date.today())) is not None:
            parts.append(f"idle: {idle}d")
        if left_off := item.sections.get("left off", "").split("\n")[0].strip():
            parts.append(f"left off: {left_off[:120]}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def parent_of(all_items: list[Item], item: Item) -> Item | None:
    """The item named by `from:`, if it still exists."""
    if not item.from_slug:
        return None
    return next((i for i in all_items if i.slug == item.from_slug and i.path != item.path), None)


def children(all_items: list[Item], item: Item) -> list[Item]:
    """The items whose `from:` names this one, in file order."""
    return [i for i in all_items if i.from_slug == item.slug and i.path != item.path]


# Words too common to say two items are about the same thing (only words of 5 letters or more count).
STOPWORDS = {
    "about", "after", "antes", "before", "being", "could", "depois", "entre", "fazer", "from", "minha", "nossa",
    "other", "outra", "outro", "porque", "quando", "should", "sobre", "their", "there", "these", "those", "todas",
    "todos", "where", "which", "while", "without", "would", "versao", "version",
}


def _words(text: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]+", plain) if len(w) >= 5 and w not in STOPWORDS}


def similar(all_items: list[Item], text: str, limit: int = 3) -> list[Item]:
    """Items that look like the same subject: they share 2 meaningful words with the text (1 when the
    text has only 1 or 2), in title or Left off. Most shared first, then the most recently updated."""
    wanted = _words(text)
    if not wanted:
        return []
    need = 1 if len(wanted) <= 2 else 2
    scored = []
    for item in all_items:
        shared = len(wanted & _words(item.title + " " + item.sections.get("left off", "")))
        if shared >= need:
            scored.append((shared, item.meta.get("updated", ""), item))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [item for _, _, item in scored[:limit]]


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


def create_idea(
    folder: Path, title: str, now: datetime | None = None, *, group: str = "", tags: list[str] | tuple[str, ...] = (),
    parent: str = "", status: str = "idea", context: str = "",
) -> Path:
    """Create a small item, an idea unless told otherwise; it grows into a full item when work starts.

    `parent` is the slug of the item it came from (`from:`).
    """
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    stamp = _stamp(now)
    title = " ".join(title.split())
    base = f"{stamp[:10]}-{slugify(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    path, n = folder / f"{base}.md", 2
    while path.exists():
        path, n = folder / f"{base}-{n}.md", n + 1
    lines = ["---", f"title: {_quoted(title)}", f"status: {status}"]
    lines += [f"group: {group}"] if group else []
    lines += [f"from: {parent}"] if parent else []
    lines += [f"tags: [{', '.join(tags)}]", f"created: {stamp}", f"updated: {stamp}", "---"]
    if context.strip():
        lines += ["", "## Context", context.strip()]
    with path.open("x", encoding="utf-8") as f:  # "x": never overwrite a file created meanwhile
        f.write("\n".join(lines) + "\n")
    return path


def _quoted(value: str) -> str:
    """A title as the agent writes it: quoted when a colon would make it look like YAML."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"' if ": " in value else value


def set_status(item: Item, status: str, now: datetime | None = None, reason: str | None = None) -> None:
    """Change the status. `done` and `dropped` record when; dropping takes a one-line reason, kept as Left off."""
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    reason = " ".join((reason or "").split())
    if status == "dropped" and not reason:
        raise ValueError("dropping an item needs a reason")
    stamp = _stamp(now)

    def edit(lines: list[str], _: int) -> None:
        _set_meta(lines, "status", status)
        _set_meta(lines, "done", stamp if status == "done" else None)
        _set_meta(lines, "dropped", stamp if status == "dropped" else None)
        if status == "dropped":
            _replace_section(lines, "Left off", [reason])

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


def step_to_item(item: Item, step: Step, title: str, now: datetime | None = None) -> Path:
    """Turn an open step into an item of its own (doing, same group and tags, `from:` this item).

    The step stays in this item as `- [ ] [item] <title> → <slug>`, and `sync_item_steps` checks it
    when the new item is done.
    """
    if step.done or step.kind == ITEM_KIND:
        raise ValueError("only an open step can become an item")
    title = " ".join(title.replace(_ARROW.strip(), "->").split())
    if not title:
        raise ValueError("an item needs a title")
    child = create_idea(item.path.parent, title, now=now, group=item.group, tags=item.tags, parent=item.slug,
                        status="doing")

    def edit(lines: list[str], offset: int) -> None:
        lines[_step_at(item, lines, step, offset)] = f"- [ ] [{ITEM_KIND}] {title}{_ARROW}{child.stem}"

    try:
        _save(item, edit, _stamp(now))
    except WriteConflict:
        child.unlink(missing_ok=True)
        raise
    return child


def sync_item_steps(all_items: list[Item]) -> list[Path]:
    """Check each open `[item]` step whose item is done, and reopen a checked one whose item is not.

    Returns the files changed. `updated` stays: nobody worked on the parent, rumo only followed the child.
    """
    by_slug = {i.slug: i for i in all_items}
    changed = []
    for item in all_items:
        flips = [s for s in item.steps if s.target in by_slug and s.done != (by_slug[s.target].status == "done")]
        if not flips:
            continue

        def edit(lines: list[str], offset: int, item: Item = item, flips: list[Step] = flips) -> None:
            for step in flips:
                i = _step_at(item, lines, step, offset)
                lines[i] = f"- [{' ' if step.done else 'x'}] " + lines[i][6:]

        try:
            _save(item, edit, None)
        except (WriteConflict, OSError):
            continue  # changing right now: the next sync catches it
        changed.append(item.path)
    return changed


def add_note(item: Item, text: str, now: datetime | None = None) -> None:
    """Append `- YYYY-MM-DD <text>` to `## Notes`, a section placed after Context (or Left off)."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("a note needs text")
    stamp = _stamp(now)
    line = f"- {stamp[:10]} {text}"
    _save(item, lambda lines, _: _put_section(
        lines, "Notes", [line], after=("context", "left off"), before=("steps", "prs", "explorations", "sessions"),
    ), stamp)


def start_exploration(
    item: Item, question: str, id: str, pid: int, limit: int, now: datetime | None = None,
) -> None:
    """Add a running entry at the end of `## Explorations` (a section placed before Sessions)."""
    moment = now or datetime.now()
    question = " ".join(question.split())
    entry = [
        f"### {_stamp(moment)} {question}",
        f"<!-- rumo:exploration id={id} state=running pid={pid} started={moment:%Y-%m-%dT%H:%M} limit={limit} -->",
    ]
    _save(item, lambda lines, _: _put_section(lines, "Explorations", entry, before=("sessions",), gap=True), _stamp(now))


def finish_exploration(
    item: Item, id: str, answer: str, next_questions: list[str], now: datetime | None = None,
) -> None:
    body = [f"<!-- rumo:exploration id={id} state=done -->", *answer.strip().splitlines()]
    if next_questions:
        body += ["", NEXT_MARKER, *(f"- {' '.join(q.split())}" for q in next_questions)]
    _save(item, lambda lines, _: _replace_entry(item, lines, id, body), _stamp(now))


def fail_exploration(item: Item, id: str, error: str, now: datetime | None = None) -> None:
    error = " ".join(error.replace("-->", "").replace('"', "'").split())[:200]
    _save(item, lambda lines, _: _replace_entry(item, lines, id, [
        f'<!-- rumo:exploration id={id} state=failed error="{error}" -->',
    ]), _stamp(now))


def _replace_entry(item: Item, lines: list[str], id: str, body: list[str]) -> None:
    """Replace what follows an exploration's heading (found by its id), up to the next entry or section."""
    marker = re.compile(rf"^<!-- rumo:exploration id={re.escape(id)}(\s|-->)")
    at = next((i for i, line in enumerate(lines) if marker.match(line.strip())), None)
    if at is None:
        raise WriteConflict(item.path.name)
    heading = next(i for i in range(at, -1, -1) if lines[i].startswith("### ") or i == 0)
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("### ") or _SECTION_RE.match(lines[i])),
               len(lines))
    trailing = [""] if end < len(lines) else []
    lines[heading + 1:end] = body + trailing


def _section_end(lines: list[str], start: int) -> int:
    """One past the last non-blank line of the section whose heading is at `start`."""
    end = next((i for i in range(start + 1, len(lines)) if _SECTION_RE.match(lines[i])), len(lines))
    return max((i for i in range(start, end) if lines[i].strip()), default=start) + 1


def _put_section(
    lines: list[str], name: str, body: list[str], after: tuple[str, ...] = (), before: tuple[str, ...] = (),
    gap: bool = False,
) -> None:
    """Append `body` to the section, or create the section after the first of `after` that exists, else
    before the first of `before` that exists, else at the end. `gap` separates entries with a blank line."""
    heads = _headings(lines)
    if name.lower() in heads:
        end = _section_end(lines, heads[name.lower()])
        lines[end:end] = ([""] if gap else []) + body
        return
    pos = next((_section_end(lines, heads[a]) for a in after if a in heads), None)
    if pos is None:
        pos = min((heads[b] for b in before if b in heads), default=len(lines))
    block = [f"## {name}", *body]
    if pos > 0 and lines[pos - 1].strip():
        block.insert(0, "")
    if pos < len(lines) and lines[pos].strip():
        block.append("")
    lines[pos:pos] = block


def _replace_section(lines: list[str], name: str, body: list[str]) -> None:
    """Make the section hold exactly `body`; a new one goes first in the body, before every other section."""
    heads = _headings(lines)
    if name.lower() in heads:
        start = heads[name.lower()]
        lines[start + 1:_section_end(lines, start)] = body
        return
    _put_section(lines, name, body, before=tuple(heads))


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
