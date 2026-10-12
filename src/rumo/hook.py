"""Claude Code `UserPromptSubmit` hook: remind a linked session to keep its item up to date.

The link is the session id written in an item's Sessions section (by `/rumo new` or
`/rumo link`). A line ending in `| closed` (from park/unlink) no longer links, and after
`/clear` the id changes, so the reminder stops on its own.

The reminder carries the update rules, so they reach the agent exactly when they matter and
nothing has to be added to the user's global instructions.
Runs on every prompt: stdlib only, UTF-8 regardless of the console, silent on anything unexpected.
"""

from __future__ import annotations

import json
import sys

from rumo import config, items

RULES = (
    "Steps are what matters. Before you finish this response, if any work happened, edit the item "
    "with the Edit tool: in Steps, check what is done, add a step for anything new or left for later "
    "(`- [ ] [agent] ...`, or `- [ ] [manual] ...` for what the user or someone else does), reword steps "
    "that changed, and keep the very next action as the first open step. One short line per step, "
    "starting with a verb. Then set `updated`; rewrite Left off (one line: where it stands or what "
    "blocks it) only if that changed, and keep Context to at most 3 lines. Conversation-only replies "
    "need no update. Write content in the configured language (`rumo index`); keep keys, sections, "
    "statuses and tags in English. Never set `done` without every step checked and the user's "
    "confirmation. To stop updating from this session, append `| closed` to its line in Sessions."
)
SHOWN_STEPS = 3


def open_steps(item: items.Item) -> str:
    """The next open steps, so the agent sees what to check off without opening the file."""
    pending = [s for s in item.steps if not s.done]
    if not pending:
        return "Open steps: none."
    shown = [(f"[{s.kind}] " if s.kind else "") + (s.text if len(s.text) <= 80 else s.text[:79] + "…") for s in pending[:SHOWN_STEPS]]
    more = len(pending) - SHOWN_STEPS
    return "Open steps: " + "; ".join(shown + ([f"(+{more} more)"] if more > 0 else [])) + "."


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        session_id = payload.get("session_id", "")
    except (ValueError, OSError, AttributeError):
        return
    if not session_id:
        return

    linked = []
    for item in items.list_items(config.load().folder):
        if any(s.id == session_id and not s.closed for s in item.sessions):
            linked.append(f"{item.path} (status: {item.status}). {open_steps(item)}")

    if linked:
        message = "This session is linked to rumo item(s): " + " ".join(linked) + " " + RULES + "\n"
        sys.stdout.buffer.write(message.encode("utf-8"))
        sys.stdout.flush()
