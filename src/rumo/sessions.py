"""The sessions Claude Code ran lately, digested for `rumo sessions`: where each one ran, what the user
asked, what it answered last, what it edited and which items it touches, so the agent can catch their
items up in one review (`/rumo sessions`).

Claude Code keeps a transcript per session in `<claude>/projects/<folder>/<session id>.jsonl` and a file
per running session in `<claude>/sessions/<pid>.json`. Neither format is documented: anything unreadable
is skipped, and without the registry no session counts as open.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rumo import hook, items, prs

FIRST_REVIEW_DAYS = 3
MIN_PROMPTS = 3  # fewer, with no file edited and few tool calls: a quick question, not work to track
MIN_TOOL_CALLS = 10
FULL = 20  # sessions shown whole; older ones come without prompts, answer and edits
LAST_PROMPTS = 5
PROMPT_CHARS = 200
ANSWER_CHARS = 400
EDITED = 8
NOT_TYPED = ("<local-command", "<task-notification", "<bash-stdout", "<bash-stderr", "[Request interrupted")
HOUSEKEEPING = {  # Claude Code's own commands, which say nothing about the work
    "clear", "compact", "context", "cost", "usage", "model", "effort", "fast", "config", "status", "help", "resume",
    "login", "logout", "permissions", "memory", "mcp", "plugin", "reload-plugins", "hooks", "agents", "doctor",
    "theme", "ide", "exit", "rewind",
}


@dataclass
class Recorded:
    """One session, as its transcript tells it."""

    id: str
    started: datetime
    last: datetime
    folder: str  # where it started: `claude --resume` finds it from there
    cwd: str  # where it was last
    branch: str
    entrypoint: str = ""
    title: str = ""
    prompts: list[str] = field(default_factory=list)
    answer: str = ""
    edited: list[str] = field(default_factory=list)  # the latest first
    prs: list[str] = field(default_factory=list)
    tool_calls: int = 0
    open: bool = False

    @property
    def repo(self) -> str:
        if not self.cwd:
            return "-"
        return os.path.basename(str(hook.repo_root(Path(self.cwd)) or self.cwd))


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def recent(claude: Path, since: datetime) -> list[Recorded] | None:
    """The sessions worth reviewing with activity after `since`, newest first; None without Claude Code."""
    projects = claude / "projects"
    if not projects.is_dir():
        return None
    found: dict[str, Recorded] = {}
    for path in projects.glob("*/*.jsonl"):  # subagents live deeper: they are not sessions
        try:
            if path.stat().st_mtime < since.timestamp():
                continue
        except OSError:
            continue
        session = read(path)
        if session and session.last > since and worth_reviewing(session):
            if session.id not in found or found[session.id].last < session.last:
                found[session.id] = session
    running = open_ids(claude)
    for session in found.values():
        session.open = session.id in running
    return sorted(found.values(), key=lambda s: s.last, reverse=True)


def worth_reviewing(session: Recorded) -> bool:
    """Typed by a person (not `claude -p` or the SDK), outside a temporary folder, and more than a quick question."""
    return (session.entrypoint in ("", "cli") and not temporary(session.folder)
            and (len(session.prompts) >= MIN_PROMPTS or bool(session.edited) or session.tool_calls >= MIN_TOOL_CALLS))


def temporary(folder: str) -> bool:
    path = os.path.realpath(folder)
    roots = {os.path.realpath(p) for p in (tempfile.gettempdir(), "/tmp", "/var/folders")}
    return any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in roots)


def read(path: Path) -> Recorded | None:
    """The session in a transcript, read line by line; None when it holds no message."""
    session: Recorded | None = None
    title = entrypoint = answer = ""
    tool_calls = 0
    prompts: list[str] = []
    edited: dict[str, None] = {}
    asked: dict[str, tuple[str, dict]] = {}  # the calls that may open a PR, until their result comes
    pr_calls: list[tuple[str, dict]] = []
    pr_outputs: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "ai-title":
                    title = " ".join(str(entry.get("aiTitle") or "").split())
                if entry.get("type") not in ("user", "assistant") or (at := local_time(entry.get("timestamp"))) is None:
                    continue
                cwd, branch = str(entry.get("cwd") or ""), str(entry.get("gitBranch") or "")
                branch = "" if branch == "HEAD" else branch  # what Claude Code says outside a branch
                if session is None:
                    session = Recorded(str(entry.get("sessionId") or path.stem), at, at, cwd, cwd, branch)
                session.last = max(session.last, at)
                if cwd:  # the branch belongs to the folder the entry was written in
                    session.cwd, session.branch = cwd, branch
                    session.folder = session.folder or cwd
                entrypoint = entrypoint or str(entry.get("entrypoint") or "")
                message = entry.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if entry["type"] == "assistant" and isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and str(block.get("text", "")).strip():
                            answer = str(block["text"])
                        elif block.get("type") == "tool_use":
                            tool_calls += 1
                            name, tool_input = str(block.get("name", "")), block.get("input")
                            tool_input = tool_input if isinstance(tool_input, dict) else {}
                            file = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
                            if name in hook.EDIT_TOOLS and file and not agent_notes(file, path):
                                edited.pop(file, None)
                                edited[file] = None
                            if may_open_pr(name, tool_input):
                                asked[str(block.get("id"))] = (name, tool_input)
                elif entry["type"] == "user" and isinstance(content, list) and not hook.is_prompt(content):
                    for block in content:
                        if isinstance(block, dict) and (call := asked.pop(str(block.get("tool_use_id")), None)):
                            pr_calls.append(call)
                            pr_outputs.append(hook.text_of(block.get("content")))
                elif entry["type"] == "user" and (text := typed(entry)):
                    prompts.append(text)
    except OSError:
        return None
    if session is None:
        return None
    session.title, session.entrypoint, session.prompts = title, entrypoint, prompts
    session.answer, session.edited, session.tool_calls = one_line(answer), list(edited)[::-1], tool_calls
    session.prs = [link for link, _ in hook.opened_prs(hook.Turn(None, pr_calls, pr_outputs))]
    return session


def agent_notes(file: str, transcript: Path) -> bool:
    """A file the agent keeps for itself (its memory, a scratchpad page), not work on the project."""
    claude = transcript.parent.parent.parent
    return temporary(file) or os.path.realpath(file).startswith(os.path.realpath(claude) + os.sep)


def may_open_pr(name: str, tool_input: dict) -> bool:
    if hook.PR_TOOLS.search(name):
        return True
    command = str(tool_input.get("command", "")) if name == "Bash" else ""
    return bool(command) and (hook.creates_pr(command) is not None or "git push" in command)


def typed(entry: dict) -> str:
    """What the person typed in this entry, on one line ("" for tool results, notifications, skill text...)."""
    origin = entry.get("origin")
    if entry.get("isMeta") or entry.get("isCompactSummary") or (isinstance(origin, dict) and origin.get("kind") != "human"):
        return ""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not hook.is_prompt(content):
        return ""
    text = (content if isinstance(content, str)
            else " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
            if isinstance(content, list) else "").strip()
    if command := re.search(r"<command-name>(.*?)</command-name>", text, re.S):
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        name = command[1].strip().lstrip("/")
        if name in HOUSEKEEPING:
            return ""
        text = f"/{name} {args[1].strip() if args else ''}"
    elif bash := re.fullmatch(r"<bash-input>(.*)</bash-input>", text, re.S):
        text = f"! {bash[1]}"
    elif text.startswith(NOT_TYPED):
        return ""
    return one_line(re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", "[pasted text]", text, flags=re.S))


def one_line(text: str) -> str:
    return " ".join(text.split())


def cut(text: str, size: int) -> str:
    return text if len(text) <= size else text[: size - 1] + "…"


def local_time(value: object) -> datetime | None:
    """A transcript time (UTC) as the local time items are written in, without a zone."""
    at = hook.parse_time(value)
    return at.astimezone().replace(tzinfo=None) if at and at.tzinfo else at


def open_ids(claude: Path) -> set[str]:
    """The sessions whose process is still running, from Claude Code's registry."""
    running: set[str] = set()
    for path in (claude / "sessions").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("pid"), int) and alive(data["pid"]):
            running.add(str(data.get("sessionId") or ""))
    return running


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # os.kill would end the process there
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # someone else's process
    except OSError:
        return False
    return True


# ---------- the digest ----------


def digest(found: list[Recorded], all_items: list[items.Item], state: dict, now: datetime, folder: Path) -> list[str]:
    """The lines `rumo sessions` prints after its header: one block per session. Edits to the items (in
    `folder`) are rumo's own bookkeeping, not work."""
    kept = os.path.realpath(folder)
    for session in found:
        session.edited = [f for f in session.edited if os.path.dirname(os.path.realpath(f)) != kept]
    lines = [f"# sessions: {len(found)} ({sum(s.open for s in found)} open)"
             + (f"; the {len(found) - FULL} oldest without prompts, last answer and edits" if len(found) > FULL else "")]
    for n, session in enumerate(found):
        lines += block(session, all_items, state, now, brief=n >= FULL)
    return lines


def block(session: Recorded, all_items: list[items.Item], state: dict, now: datetime, brief: bool) -> list[str]:
    last = session.last.strftime(items.TIME_FORMAT)
    where = f"{session.repo} ({session.branch or '-'})"
    linked = [i for i in all_items if i.linked_to(session.id)]
    closed = [i for i in all_items if i not in linked and any(s.id == session.id for s in i.sessions)]
    lines = [f"## {session.id}" + ("  open" if session.open else ""),
             f"- last: {last} · started {session.started.strftime(items.TIME_FORMAT)}",
             f"- where: {where} · {session.folder}"]
    if session.title:
        lines.append(f"- title: {session.title}")
    if linked:
        lines.append("- linked: " + ", ".join(i.slug + (" (up to date)" if i.meta.get("updated", "") >= last else "") for i in linked))
    if closed:
        lines.append("- closed: " + ", ".join(i.slug for i in closed))
    if same := same_branch(session, [i for i in all_items if i not in linked and i not in closed], state):
        lines.append(f"- same branch: {', '.join(i.slug for i in same)} ({session.branch})")
    if not brief:
        if session.prompts:
            first, rest = session.prompts[0], session.prompts[1:][-LAST_PROMPTS:]
            lines.append(f'- prompts ({len(session.prompts)}): first "{cut(first, PROMPT_CHARS)}"'
                         + (" · last: " + " / ".join(f'"{cut(p, PROMPT_CHARS)}"' for p in rest) if rest else ""))
        if session.answer:
            lines.append(f'- last answer: "{cut(session.answer, ANSWER_CHARS)}"')
        if session.edited:
            shown = [relative(f, session) for f in session.edited[:EDITED]]
            more = len(session.edited) - EDITED
            lines.append(f"- edited: {', '.join(shown)}" + (f" (+{more} more)" if more > 0 else ""))
        if session.prs:
            lines.append(f"- prs: {', '.join(session.prs)}")
    lines.append(f"- line: - {now.strftime(items.TIME_FORMAT)} | {where} | {session.id} | {session.folder}")
    return lines


def same_branch(session: Recorded, candidates: list[items.Item], state: dict) -> list[items.Item]:
    """The items not done whose work happened on this session's repository and branch (not a shared one)."""
    if session.branch in prs.SHARED_BRANCHES or session.branch in ("", "-", "HEAD"):
        return []
    here = (prs.repository(session.cwd), session.branch)
    return [i for i in candidates if i.status != "done" and any(
        (prs.repository(folder), branch) == here for folder, branch in prs.branches(i, prs.noted_branches(state, str(i.path))))]


def relative(file: str, session: Recorded) -> str:
    """The file from the root of the repository the session was last in (or its folder), when inside it."""
    if not session.cwd:
        return file
    root = hook.repo_root(Path(session.cwd)) or hook.repo_root(Path(session.folder)) or Path(session.cwd)
    try:
        return str(Path(file).relative_to(root))
    except ValueError:
        return file
