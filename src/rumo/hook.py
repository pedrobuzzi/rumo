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
    "When you finish this response, if anything changed (step done, new step, change of direction, "
    "blocker), update the item with the Edit tool: rewrite Left off (the next concrete action), check "
    "or add Steps (`- [ ] [agent] ...` or `- [ ] [manual] ...` for work done outside the agent; work "
    "with more than one stage and anything not done now becomes a step) and set `updated`. "
    "Conversation-only replies need no update. Write content in the configured language (`rumo index`); "
    "keep keys, sections, statuses and tags in English. Never set `done` without every step checked and "
    "the user's confirmation. To stop updating from this session, append `| closed` to its line in Sessions."
)


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
            linked.append(f"{item.path} (status: {item.status})")

    if linked:
        message = "This session is linked to rumo item(s): " + "; ".join(linked) + ". " + RULES + "\n"
        sys.stdout.buffer.write(message.encode("utf-8"))
        sys.stdout.flush()
