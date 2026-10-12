"""Claude Code hooks: keep the items a session works on up to date, and offer rumo for untracked work.

The plugin sends two events to `rumo hook`:

- UserPromptSubmit: remind a linked session of its items and the update rules. The link is the
  session id written in an item's Sessions section (by `/rumo new` or `/rumo link`). A line
  ending in `| closed` (from park/unlink) no longer links, and after `/clear` the id changes, so
  the reminder stops on its own.
- Stop: at the end of a turn that did work, check that it reached the items. A linked session
  whose items did not change gets one more nudge. A session without items that edited files is
  asked once whether to track it (see `untracked`).

The messages carry the rules, so they reach the agent exactly when they matter and nothing has to be
added to the user's global instructions. Runs on every prompt and every stop: stdlib only, UTF-8
regardless of the console, silent on anything unexpected.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from rumo import config, items

RULES = (
    "Steps are what matters. Before you finish this response, if any work happened, edit the item it "
    "belongs to with the Edit tool: in Steps, check what is done, add a step for anything new or left for "
    "later (`- [ ] [agent] ...`, or `- [ ] [manual] ...` for what the user or someone else does), reword "
    "steps that changed, and keep the very next action as the first open step. Never delete an open step "
    "unless the user explicitly asks; switching topics never closes, parks or deletes anything. If the user "
    "starts work that belongs to none of these items, ask once, in one line at the end of your response: "
    "track it as a new item ({create}; this session links to it too), add it as a step to one of these, "
    "or not track it. One short line per step, starting with a verb. Then set `updated`; rewrite Left off "
    "(one line: where it stands or what blocks it) only if that changed, and keep Context to at most 3 "
    "lines. Conversation-only replies need no update. Write content in the configured language (`rumo "
    "index`); keep keys, sections, statuses and tags in English. Never set `done` without every step "
    "checked and the user's confirmation. To stop updating from this session, append `| closed` to its "
    "line in Sessions."
)
SHOWN_STEPS = 3

WORK_TOOLS = {"Edit", "Write", "NotebookEdit", "Bash"}  # a linked turn with these should reach its items
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}  # an untracked session is worth asking about once it edits
ASK_AGAIN_DAYS = 15  # after the user said not to be asked, remind them the option exists
ASK_AGAIN_SESSIONS = 15
REMEMBERED_SESSIONS = 200  # sessions already asked or counted, so each one is asked at most once

# Shell commands that only look around: a turn that ran nothing else did no work to record.
LOOK_COMMANDS = {
    "ls", "cat", "head", "tail", "less", "wc", "pwd", "echo", "printf", "grep", "rg", "find", "fd", "tree",
    "which", "type", "file", "stat", "du", "df", "date", "env", "printenv", "diff", "cd",
}
LOOK_SUBCOMMANDS = {
    "git": {"status", "diff", "log", "show", "branch", "rev-parse", "remote", "blame", "ls-files"},
    "rumo": {"index", "path", "--version"},
}


def open_steps(item: items.Item) -> str:
    """The next open steps, so the agent sees what to check off without opening the file."""
    pending = [s for s in item.steps if not s.done]
    if not pending:
        return "Open steps: none."
    shown = [(f"[{s.kind}] " if s.kind else "") + (s.text if len(s.text) <= 80 else s.text[:79] + "…") for s in pending[:SHOWN_STEPS]]
    more = len(pending) - SHOWN_STEPS
    return "Open steps: " + "; ".join(shown + ([f"(+{more} more)"] if more > 0 else [])) + "."


def how_to_create() -> str:
    """The agent cannot run the /rumo skill itself, but it can follow it when the plugin says where it is."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    skill = Path(root) / "skills" / "rumo" / "SKILL.md" if root else None
    if skill and skill.is_file():
        return f"create it the way `/rumo new` does, following the `new` section of {skill}"
    return "ask them to type `/rumo new`"


def linked_items(cfg: config.Config, session_id: str) -> list[items.Item]:
    return [
        item for item in items.list_items(cfg.folder)
        if any(s.id == session_id and not s.closed for s in item.sessions)
    ]


def reminder(cfg: config.Config, session_id: str) -> str:
    linked = [f"{item.path} (status: {item.status}). {open_steps(item)}" for item in linked_items(cfg, session_id)]
    if not linked:
        return ""
    return "This session is linked to rumo item(s): " + " ".join(linked) + " " + RULES.format(create=how_to_create()) + "\n"


@dataclass
class Turn:
    """What the agent did since the user's last prompt."""

    started: datetime | None = None  # None: the prompt was not found
    tools: list[tuple[str, dict]] = field(default_factory=list)


def last_turn(transcript: Path) -> Turn:
    """Read the transcript backwards up to the last prompt, collecting the tools used after it."""
    tools: list[tuple[str, dict]] = []
    for line in reverse_lines(transcript):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if entry.get("type") == "assistant" and isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_input = block.get("input")
                    tools.append((str(block.get("name", "")), tool_input if isinstance(tool_input, dict) else {}))
        elif entry.get("type") == "user" and not entry.get("isMeta") and is_prompt(content):
            return Turn(parse_time(entry.get("timestamp")), tools[::-1])
    return Turn(None, tools[::-1])


def is_prompt(content: object) -> bool:
    """A user entry the person typed (or a notification that starts a turn), not a tool result."""
    if isinstance(content, str):
        return True
    return isinstance(content, list) and bool(content) and not any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def reverse_lines(path: Path, block: int = 1 << 16) -> Iterator[str]:
    """The file's lines from last to first, without reading a long transcript whole."""
    with path.open("rb") as f:
        position = f.seek(0, os.SEEK_END)
        rest = b""
        while position > 0:
            size = min(block, position)
            position -= size
            f.seek(position)
            lines = (f.read(size) + rest).split(b"\n")
            rest = lines.pop(0)
            for line in reversed(lines):
                if line.strip():
                    yield line.decode("utf-8", "replace")
        if rest.strip():
            yield rest.decode("utf-8", "replace")


def changed_since(path: Path, since: datetime | None) -> bool:
    if since is None:
        return True  # cannot tell: never nag on a guess
    try:
        return path.stat().st_mtime >= since.timestamp()
    except OSError:
        return False


def stop(cfg: config.Config, session_id: str, payload: dict) -> str:
    if payload.get("stop_hook_active"):
        return ""  # this stop already comes from a nudge: never insist twice in a row
    transcript = payload.get("transcript_path")
    if not transcript:
        return ""
    turn = last_turn(Path(transcript))
    linked = linked_items(cfg, session_id)
    if not linked:
        return untracked(cfg, session_id, turn)
    if not any(name in WORK_TOOLS and not only_looks(name, tool_input) for name, tool_input in turn.tools):
        return ""
    if any(changed_since(item.path, turn.started) for item in linked):
        return ""
    if asks_the_user(payload.get("last_assistant_message")):
        return ""  # the agent waits for an answer (maybe whether to track this); the next reminder follows up
    return (
        f"rumo: this turn did work, but no linked item changed ({', '.join(str(i.path) for i in linked)}). "
        "Update the Steps of the item this work belongs to: check what is done, add what is new or left for "
        "later, never delete open steps. If the work belongs to none of them, ask the user in one line: track "
        "it as a new item, add it as a step to one of these, or not track it. If they already chose not to "
        "track it, or nothing worth recording happened, change nothing. Either way, never repeat or summarize "
        "your previous answer, and end without any further text."
    )


def only_looks(name: str, tool_input: dict) -> bool:
    """A Bash call whose every command only reads (ls, git status, ...) and writes no file."""
    if name != "Bash":
        return False
    command = re.sub(r"\d?>\s*/dev/null|\d>&\d", "", str(tool_input.get("command", "")))
    if ">" in command or re.search(r"\s-(delete|exec|execdir)\b", command):
        return False
    for part in re.split(r"&&|\|\||[;|\n]", command):
        words = [w for w in part.split() if not re.match(r"^\w+=", w)]  # skip FOO=1 prefixes
        if not words:
            continue
        program, rest = words[0], words[1:]
        if program in LOOK_SUBCOMMANDS:
            if not rest or rest[0] not in LOOK_SUBCOMMANDS[program]:
                return False
        elif program not in LOOK_COMMANDS:
            return False
    return True


def asks_the_user(answer: object) -> bool:
    """Whether the agent's final message asks something (any script's question mark)."""
    return isinstance(answer, str) and any(mark in answer for mark in ("?", "？", "؟"))


def untracked(cfg: config.Config, session_id: str, turn: Turn) -> str:
    """Ask a session without items, once, whether to track it; `ask_to_track` says how often to ask."""
    folder = cfg.folder.resolve()
    edited = [
        Path(str(target)) for name, tool_input in turn.tools if name in EDIT_TOOLS
        if (target := tool_input.get("file_path") or tool_input.get("notebook_path"))
    ]
    if not any(not path.resolve().is_relative_to(folder) for path in edited):
        return ""  # nothing edited, or only items (an idea noted with /rumo idea)

    state = config.load_state(cfg.path).get("ask_to_track")
    state = state if isinstance(state, dict) else {}
    seen = [s for s in state.get("seen", []) if isinstance(s, str)]
    if session_id in seen:
        return ""
    state["seen"] = (seen + [session_id])[-REMEMBERED_SESSIONS:]

    today = date.today()
    head = "rumo: this session changed files, but no rumo item tracks it. "
    track = f"should this work be tracked as a rumo item (if yes, {how_to_create()})"
    message = ""
    if cfg.ask_to_track is None:
        message = (
            head + f"Ask the user once, in one short line in their language: {track}, and should you ask like "
            "this in future sessions without an item? Record that second answer with `rumo ask-to-track true` "
            "or `rumo ask-to-track false`. Do not ask again in this session."
        )
        state.update(last_asked=today.isoformat(), sessions=0)
    elif cfg.ask_to_track:
        message = (
            head + f"Ask the user once, in one short line in their language: {track}? If they say to stop "
            "asking, run `rumo ask-to-track false`. Do not ask again in this session."
        )
    else:
        try:
            last = date.fromisoformat(str(state.get("last_asked")))
        except ValueError:
            last = today
        sessions = state.get("sessions")
        sessions = (sessions if isinstance(sessions, int) else 0) + 1
        if sessions >= ASK_AGAIN_SESSIONS or (today - last).days >= ASK_AGAIN_DAYS:
            message = (
                head + "The user chose not to be asked; it has been a while, so remind them once that the "
                f"option exists, in one short line in their language: {track}, and do they want to be asked "
                "again in sessions without an item? If they do, run `rumo ask-to-track true`; otherwise change "
                "nothing. Do not ask again in this session."
            )
            last, sessions = today, 0
        state.update(last_asked=last.isoformat(), sessions=sessions)
    config.update_state(cfg.path, ask_to_track=state)
    return message


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        session_id = payload.get("session_id", "")
        event = payload.get("hook_event_name") or "UserPromptSubmit"
    except (ValueError, OSError, AttributeError):
        return
    if not session_id:
        return

    cfg = config.load()
    if event == "Stop":
        try:
            message = stop(cfg, session_id, payload)
        except Exception:  # a hook must never break the session; stay silent instead
            return
        output = json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": message}}) if message else ""
    else:
        output = reminder(cfg, session_id)
    if output:
        sys.stdout.buffer.write(output.encode("utf-8"))
        sys.stdout.flush()
