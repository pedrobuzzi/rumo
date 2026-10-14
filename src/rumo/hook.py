"""Claude Code hooks: keep the items a session works on up to date, and offer rumo for untracked work.

The plugin sends three events to `rumo hook`:

- SessionStart: a session without an item learns what is in progress in its repository, so it can
  answer "what is left?" (see `session_start`).
- UserPromptSubmit: remind a linked session of its items and the update rules. The link is the
  session id written in an item's Sessions section (by `/rumo new` or `/rumo link`). A line
  ending in `| closed` (from park/unlink) no longer links, and after `/clear` the id changes, so
  the reminder stops on its own. Steps changed since the agent's last answer (in the TUI, an
  editor or another session) are pointed out once.
- Stop: at the end of a turn that did work, check that it reached the items. A linked session
  whose items did not change gets one more nudge. A session without items that edited files is
  asked once whether to track it (see `untracked`). Either way, note the steps the agent has seen.
  In a linked session, a PR the turn opened goes into the item (see `opened_prs`), and the branch
  is noted, so the TUI can find the PRs opened from it later (`rumo.prs`).

The messages carry the rules, so they reach the agent exactly when they matter and nothing has to be
added to the user's global instructions. Runs on every prompt and every stop: stdlib only, UTF-8
regardless of the console, silent on anything unexpected.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from rumo import config, items, prs

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
    "checked and the user's confirmation. When you check off the step you were working on and the item "
    "still has open steps, end your response with one line in the user's language naming the next one: for "
    "an `[agent]` step offer to start it (\"Next in rumo: <step>. Want me to start?\"), for a `[manual]` step "
    "say it is theirs (\"Next in rumo (yours): <step>.\"). PRs of this work go under `## PRs`, one per line: "
    "`- [open] <link> <short title>` (states: open, draft, merged, closed). rumo adds the PRs it finds; add one "
    "the user gives you, and update a state when you learn it (for example with `gh pr view <link>`). To stop "
    "updating from this session, append `| closed` to its line in Sessions."
)
SHOWN_STEPS = 3
SHOWN_ITEMS = 3  # in progress in this repository, at session start
SEEN_SESSIONS = 20  # sessions whose last seen steps are kept, to tell what changed outside them

WORK_TOOLS = {"Edit", "Write", "NotebookEdit", "Bash"}  # a linked turn with these should reach its items
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}  # an untracked session is worth asking about once it edits
ASK_AGAIN_DAYS = 15  # after the user said not to be asked, remind them the option exists
ASK_AGAIN_SESSIONS = 15
REMEMBERED_SESSIONS = 200  # sessions already asked or counted, so each one is asked at most once

# What opens a PR names it in its output: the command's link on a line of its own, a tool's first link,
# and after a push, the merge request GitLab says is open for the branch.
PR_TOOLS = re.compile(r"create_(?:pull|merge)_request", re.IGNORECASE)  # e.g. mcp__github__create_pull_request
OPEN_MERGE_REQUEST = re.compile(r"View merge request for [^\n]*:\s*\n(?:remote:)?\s*(\S+)")

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


def open_prs(item: items.Item) -> str:
    """The PRs not merged or closed yet, so the agent keeps their states and asks before finishing."""
    pending = [f"{p.name} ({p.state or 'state unknown'})" for p in item.prs if not p.finished]
    return f" Open PRs: {'; '.join(pending)}." if pending else ""


def how_to_create() -> str:
    """The agent cannot run the /rumo skill itself, but it can follow it when the plugin says where it is."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    skill = Path(root) / "skills" / "rumo" / "SKILL.md" if root else None
    if skill and skill.is_file():
        return f"create it the way `/rumo new` does, following the `new` section of {skill}"
    return "ask them to type `/rumo new`"


def linked_items(cfg: config.Config, session_id: str) -> list[items.Item]:
    return [item for item in items.list_items(cfg.folder) if item.linked_to(session_id)]


def reminder(cfg: config.Config, session_id: str) -> str:
    linked = linked_items(cfg, session_id)
    if not linked:
        return ""
    seen = seen_steps(cfg, session_id)
    lines = []
    for item in linked:
        lines.append(f"{item.path} (status: {item.status}). {open_steps(item)}{open_prs(item)}")
        if str(item.path) in seen and (changes := outside_changes(item, seen[str(item.path)])):
            lines.append(changes)
    if seen:  # only once the Stop hook keeps them: an older plugin without it would flag the agent's own edits
        remember_steps(cfg, session_id, linked)
    return "This session is linked to rumo item(s): " + " ".join(lines) + " " + RULES.format(create=how_to_create()) + "\n"


def seen_steps(cfg: config.Config, session_id: str) -> dict:
    """The steps of each linked item as the agent last saw them: {item path: [[done, kind, text], ...]}."""
    seen = config.load_state(cfg.path).get("steps_seen")
    session = seen.get(session_id) if isinstance(seen, dict) else None
    return session if isinstance(session, dict) else {}


def remember_steps(cfg: config.Config, session_id: str, linked: list[items.Item], keep: Collection[str] = ()) -> None:
    """Note the steps the agent has now seen; items in `keep` hold on to what it saw before."""
    seen = config.load_state(cfg.path).get("steps_seen")
    seen = seen if isinstance(seen, dict) else {}
    before = seen.pop(session_id, None)
    before = before if isinstance(before, dict) else {}
    seen[session_id] = {
        str(item.path): before[str(item.path)] if str(item.path) in keep and str(item.path) in before
        else [[s.done, s.kind, s.text] for s in item.steps]
        for item in linked
    }
    config.update_state(cfg.path, steps_seen=dict(list(seen.items())[-SEEN_SESSIONS:]))


def outside_changes(item: items.Item, before: object) -> str:
    """What changed in the item's steps since the agent last saw them, in one message (or "")."""
    was: dict[str, tuple[bool, str]] = {}
    for entry in before if isinstance(before, list) else []:
        if isinstance(entry, list) and len(entry) == 3:
            was[str(entry[2])] = (bool(entry[0]), str(entry[1]))
    now = {s.text for s in item.steps}
    found = {
        "Added": [s.label for s in item.steps if s.text not in was],
        "Removed (do not add them back)": [
            items.Step(done, kind, text, 0).label for text, (done, kind) in was.items() if text not in now
        ],
        "Checked": [s.label for s in item.steps if s.text in was and s.done and not was[s.text][0]],
        "Unchecked": [s.label for s in item.steps if s.text in was and not s.done and was[s.text][0]],
    }
    parts = [f"{name}: {'; '.join(labels)}." for name, labels in found.items() if labels]
    if not parts:
        return ""
    return (
        f"The steps of {item.path.name} changed outside this session since your last response (by the user in "
        "the rumo TUI or an editor, or by another session). " + " ".join(parts) + " Take this into account, "
        "and do not start a new step unless the user asks."
    )


def session_start(cfg: config.Config, session_id: str, payload: dict) -> str:
    """Tell a session without an item what is in progress here, so "what is left?" has an answer."""
    every = items.list_items(cfg.folder)
    if any(item.linked_to(session_id) for item in every):
        return ""  # linked (a resumed session): the prompt reminder already lists its steps
    in_progress = items.timeline(every).now
    if not in_progress:
        return ""
    cwd = Path(str(payload.get("cwd") or os.getcwd())).resolve()
    root = repo_root(cwd)

    def started_here(folder: str) -> bool:
        if not folder or not os.path.isabs(folder):
            return False  # a hand-written line: resolving it against wherever the hook runs would match anything
        path = Path(folder).resolve()
        return path.is_relative_to(root) if root else path == cwd

    here = [item for item in in_progress if any(started_here(s.folder) for s in item.sessions)]
    if not here:
        return (
            f"rumo: {len(in_progress)} {'item' if len(in_progress) == 1 else 'items'} in progress, none from this "
            "repository. If the user asks what is pending or what to do next, run `rumo now`."
        )
    shown = [
        f"{item.title} ({item.status}; next: {item.next_step.label if item.next_step else 'no open steps'})"
        for item in here[:SHOWN_ITEMS]
    ]
    more = len(here) - SHOWN_ITEMS
    return (
        "rumo: in progress in this repository, not linked to this session: " + "; ".join(shown)
        + (f" (+{more} more)" if more > 0 else "") + ". If the user asks what is pending or what to do next, "
        "answer from this; `rumo now` lists everything in progress with each item's file. Do not start any of "
        "it unless the user asks; to continue one in this session, they can type `/rumo link`."
    )


def repo_root(folder: Path) -> Path | None:
    return next((f for f in (folder, *folder.parents) if (f / ".git").exists()), None)


@dataclass
class Turn:
    """What the agent did since the user's last prompt."""

    started: datetime | None = None  # None: the prompt was not found
    tools: list[tuple[str, dict]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)  # what each tool returned, in the same order


def last_turn(transcript: Path) -> Turn:
    """Read the transcript backwards up to the last prompt, collecting the tools used after it."""
    tools: list[tuple[str, dict]] = []
    outputs: list[str] = []
    results: dict[str, str] = {}  # read before their calls, since the file is read backwards
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
                    outputs.append(results.get(str(block.get("id")), ""))
        elif entry.get("type") == "user" and not entry.get("isMeta") and is_prompt(content):
            return Turn(parse_time(entry.get("timestamp")), tools[::-1], outputs[::-1])
        elif entry.get("type") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[str(block.get("tool_use_id"))] = text_of(block.get("content"))
    return Turn(None, tools[::-1], outputs[::-1])


def text_of(content: object) -> str:
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return content if isinstance(content, str) else ""


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
    linked = linked_items(cfg, session_id)
    transcript = payload.get("transcript_path")
    turn = last_turn(Path(transcript)) if transcript else None
    # Whether the agent changed an item, told before rumo adds a PR to one itself.
    reached = turn is not None and any(changed_since(item.path, turn.started) for item in linked)
    added: list[str] = []
    unplaced: list[str] = []
    if linked:
        # An item the agent did not edit in this turn keeps the steps it saw before, so a step the user
        # added meanwhile is still pointed out on the next prompt.
        edited = {str(Path(str(t)).resolve()) for name, i in (turn.tools if turn else []) if name in EDIT_TOOLS
                  if (t := i.get("file_path"))}
        commands = " ".join(str(i.get("command", "")) for name, i in (turn.tools if turn else []) if name == "Bash")
        edited |= {str(i.path.resolve()) for i in linked if i.path.name in commands}  # sed, a heredoc...
        remember_steps(cfg, session_id, linked, keep={str(i.path) for i in linked if str(i.path.resolve()) not in edited})
        # The branch and the PRs go to the item this work is for: the only one, or the one the turn edited.
        target = owner(linked, edited)
        if target is not None:
            prs.note_branch(cfg.path, [str(target.path)], str(payload.get("cwd") or os.getcwd()))
        if turn is not None:
            added, unplaced = add_opened_prs(linked, target, turn)
    if payload.get("stop_hook_active") or turn is None:
        return ""  # a stop that comes from a nudge: never insist twice in a row
    if not linked:
        return untracked(cfg, session_id, turn)
    if unplaced:
        return (
            f"rumo: this turn opened {', '.join(unplaced)}, and this session keeps several items updated. Add each "
            "under `## PRs` of the item it belongs to (`- [open] <link> <short title>`), then end without any "
            "further text."
        )
    if not any(name in WORK_TOOLS and not only_looks(name, tool_input) for name, tool_input in turn.tools):
        return ""
    if reached:
        return ""
    if asks_the_user(payload.get("last_assistant_message")):
        return ""  # the agent waits for an answer (maybe whether to track this); the next reminder follows up
    return (
        f"rumo: this turn did work, but no linked item changed ({', '.join(str(i.path) for i in linked)}). "
        + (f"rumo already added {', '.join(added)} to its `## PRs`. " if added else "") +
        "Update the Steps of the item this work belongs to: check what is done, add what is new or left for "
        "later, never delete open steps. If the work belongs to none of them, ask the user in one line: track "
        "it as a new item, add it as a step to one of these, or not track it. If they already chose not to "
        "track it, or nothing worth recording happened, change nothing. Either way, never repeat or summarize "
        "your previous answer, and end without any further text."
    )


def owner(linked: list[items.Item], edited: set[str]) -> items.Item | None:
    """The item this turn's work is for: the only linked one, or the only one it edited; None if unclear."""
    targets = linked if len(linked) == 1 else [i for i in linked if str(i.path.resolve()) in edited]
    return targets[0] if len(targets) == 1 else None


def opened_prs(turn: Turn) -> list[tuple[str, str]]:
    """The PRs this turn opened, with their state."""
    found: dict[str, str] = {}
    for (name, tool_input), output in zip(turn.tools, turn.outputs):
        if PR_TOOLS.search(name):
            links = items.pr_links(output)[:1]
            state = "draft" if tool_input.get("draft") is True else "open"
        elif name == "Bash" and (state := creates_pr(str(tool_input.get("command", "")))) is not None:
            # gh and glab print the new link on a line of its own; anything else the call printed is not it
            alone = [link for line in output.splitlines() if (link := line.strip().removeprefix("remote:").strip())
                     and items.pr_links(link) == [link]]
            links = alone[-1:]
        elif name == "Bash" and any(words[:2] == ["git", "push"] for words in commands_in(str(tool_input.get("command", "")))):
            links = [m for m in OPEN_MERGE_REQUEST.findall(output) if items.pr_links(m) == [m]][:1]
            state = ""  # it exists, but the push does not say whether it is still open
        else:
            continue
        for link in links:
            found.setdefault(link, state)
    return list(found.items())


def creates_pr(command: str) -> str | None:
    """"draft" or "open" when the command runs `gh pr create` or `glab mr create`, else None."""
    for words in commands_in(command):
        if words[:3] == ["gh", "pr", "create"]:
            return "draft" if "--draft" in words or "-d" in words else "open"
        if words[:3] == ["glab", "mr", "create"]:
            return "draft" if "--draft" in words else "open"  # glab's -d is --description
    return None


def commands_in(command: str) -> list[list[str]]:
    """The words of each command in a shell line (`a && b; c | d`), quotes kept together."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:  # an unclosed quote
        words = command.split()
    found: list[list[str]] = [[]]
    for word in words:
        if word and all(c in "();<>|&" for c in word):
            found.append([])
        elif not (found[-1] == [] and re.match(r"^\w+=", word)):  # FOO=1 before the program
            found[-1].append(word)
    return [w for w in found if w]


def add_opened_prs(linked: list[items.Item], target: items.Item | None, turn: Turn) -> tuple[list[str], list[str]]:
    """Add the PRs the turn opened to the item it worked on: (added, the ones left for the agent to place)."""
    known = {items.pr_link(p.url) for item in linked for p in item.prs}
    opened = [(link, state) for link, state in opened_prs(turn) if link not in known]
    added: list[str] = []
    for n, (link, state) in enumerate(opened):
        if target is None:
            return added, [link for link, _ in opened[n:]]
        try:
            items.add_pr(items.load(target.path), link, state)
        except (items.WriteConflict, OSError):
            return added, [link for link, _ in opened[n:]]
        added.append(link)
    return added, []


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
    handlers = {"Stop": stop, "SessionStart": session_start}
    if event in handlers:
        try:
            message = handlers[event](cfg, session_id, payload)
        except Exception:  # a hook must never break the session; stay silent instead
            return
        output = json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}) if message else ""
    else:
        output = reminder(cfg, session_id)
    if output:
        sys.stdout.buffer.write(output.encode("utf-8"))
        sys.stdout.flush()
