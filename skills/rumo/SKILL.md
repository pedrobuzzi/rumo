---
name: rumo
description: Organize the user's local work items (past, now, later) with rumo. Creates items from the session, notes ideas, links this session to an item, parks or finishes it. Only runs when the user types /rumo. Requires the rumo CLI.
disable-model-invocation: true
argument-hint: "[new | idea <text> | link [item] | now | find <text> | status | step <text> | park [reason] | done | unlink]"
---

# /rumo

Arguments: `$ARGUMENTS`

rumo keeps one Markdown file per piece of work (an **item**) in a local folder. The user sees
them in the `rumo` Timeline: **Past** (done), **Now** (doing, waiting), **Later** (idea, paused).

## 0. Always start here

1. Run `rumo index`. It prints the items folder, config path, content **language**, allowed **tags**,
   **groups** (with colors) and the available colors, then one line per item:
   `slug | status | title | group | tags | left off`.
   If the command is not found, stop and tell the user to install the CLI first, then run `/rumo` again:
   - `uv tool install rumo-cli` (macOS, Linux, Windows), or `pipx install rumo-cli`
   - `brew install pedrobuzzi/tap/rumo` (macOS, Linux)
2. Check the mode by reading `$CLAUDE_CODE_SESSION_ID` (`echo "$CLAUDE_CODE_SESSION_ID"`):
   - **Full mode** (it has a value, Claude Code): find out whether this session is already linked, i.e.
     an item whose `## Sessions` has a line with that id that does **not** end in `| closed`:
     `grep -l "$CLAUDE_CODE_SESSION_ID" "<folder>"/*.md` and check that line.
   - **Simple mode** (empty, another agent or an old Claude Code): sessions can't be linked or resumed.
     `new`, `idea`, `now`, `find`, `step` and `done` work without a Sessions section; `link`, `status`,
     `park` and `unlink` are not available, so say they need Claude Code with the rumo plugin.
     The menu shows only `new`, `idea`, `now`, `find`.

Write all item content (title, Left off, Context, steps) in the configured **language**. Keep the
structure (frontmatter keys, section names, statuses, tags, `[agent]`/`[manual]`) in English.
Follow "Writing an item" below every time you create or change an item.

## 1. No subcommand: show the menu

Show a short numbered menu and wait for the choice. Put first the option that best fits the
conversation so far.

- Session **not linked**:
  1. `new`: track what we are doing in this session as an item
  2. `idea <text>`: note something for later without leaving this topic
  3. `link [item]`: continue an existing item in this session
  4. `now`: list what is in progress, with the next step of each
  5. `find <text>`: search past, current and future items
- Session **linked** (name the item or items):
  1. `status`: show Left off and the next steps
  2. `step <text>`: add a step
  3. `new`: track another topic of this session as its own item (both stay linked)
  4. `park [reason]`: pause it with an accurate Left off and stop updating it here
  5. `done`: finish it (checks the steps, asks you first)
  6. `unlink`: stop updating it from this session

## 2. Subcommands

### new
1. **Look for similar items first.** From the index, pick up to 3 items about the same subject or
   goal (wording may differ), favoring `idea` and `paused`. If any, show them (title, status,
   slug) and ask: **promote/continue** one of them, or **create a new** item.
   - Promote/continue: do what `link` does, set `status: doing` if it was `idea`/`paused`/`waiting`,
     and bring its Steps up to date with the conversation.
2. Otherwise create `<folder>/YYYY-MM-DD-<slug>.md` (today; slug from the title in kebab-case
   without accents; add `-2`, `-3` if taken) with the format below: `status: doing`, tags chosen
   from the allowed list (1-3), `group` only if one clearly fits, Steps (work already done here as
   `- [x]`, everything pending as `- [ ]`, the next action first), a one-line Left off, a Context of
   at most 3 lines, and, in full mode, the session line.
3. **Suggest a group** (see "Groups" below).
4. Confirm in one line: path + (full mode) "this session now keeps it updated until /clear, park or
   unlink", or (simple mode) "update it with /rumo or in the rumo Timeline".

### idea <text>
1. Check the index for a similar item. If one exists, show it and ask whether to still create.
2. Create a small item: frontmatter only (`title`, `status: idea`, `tags`, `created`, `updated`),
   plus `## Context` only if the user gave detail. Do **not** link this session.
3. **Suggest a group** (see "Groups" below), in one short question.
4. Confirm in one line and go back to what we were doing.

### link [item]
1. With an argument, find the item by slug or title. Without one, suggest up to 3 items similar to
   the conversation. If ambiguous, ask.
2. Append the session line to `## Sessions` (never delete lines). If the status was `paused` or
   `waiting` and work restarts now, set `doing`. Bring the Steps up to date, then Left off and
   `updated`.

### now
Run `rumo now` (one line per `doing` or `waiting` item: title, status, next step, file) and list
them: title, status and next step. Offer to `link` one.

### find <text>
Search titles and content: `grep -il "<text>" "<folder>"/*.md`, then read only the matches.
Answer the question with the items found (status, when done, what was decided).

### status
Show the linked item's open steps (the first one is the next action) and its Left off.

### step <text>
Works in any session, linked or not.
1. **Pick the item.** The linked one; with several linked, the one the step belongs to. With none
   linked (or in simple mode), pick from the index the item it most likely belongs to (the
   conversation so far, this repository, the group, the words of the step) and add it there, saying
   which in the confirmation. Ask only when two or more items fit equally: list up to 3 (title, slug).
2. Add `- [ ] [agent] <text>` or `- [ ] [manual] <text>` (manual = the user or someone else does it
   outside the agent: review, deploy, a call), as one short line starting with a verb, in the
   configured language. Put it after the open steps, or first if it is the next action. Update
   `updated`.
3. Confirm in one line: the step and the item's title. Adding a step never links the session.

### park [reason]
1. Ask, if unclear: `waiting` (blocked on someone or something else) or `paused` (the user chose to
   stop for now).
2. Make sure the Steps tell someone with no context how to resume: every pending piece of work is
   a step and the next action is the first open one. Set Left off to one line: the blocker, or why
   it stops here.
3. Set the status and `updated`, then append ` | closed` to **this session's** line in Sessions.

### done
In simple mode, ask which item to finish (from `now`) since no item is linked.
1. If any step is unchecked, list them and ask whether they are done, dropped (delete the step) or
   should stay (then do not finish).
2. Ask for explicit confirmation. Only then set `status: done`, add `done: <now>`, update
   `updated`, and (full mode) append ` | closed` to this session's line.

### unlink
Append ` | closed` to this session's line. Nothing else changes.

## Writing an item

Steps are the item. Done steps are the record of what happened; open steps are the plan. Everything
else stays short.

- **One step, one action, one line.** Start with a verb, keep it under about 70 characters, and
  include a detail (a commit, an id, a command) only when that step needs it. `[agent]` is work the
  agent does; `[manual]` is work the user or someone else does (a review, a deploy, a decision).
- **The first open step is the next action.** Reorder when priorities change.
- **Keep the Steps current while linked.** After every response in which work happened: check what
  got done, add a step for whatever came up or was left for later, reword steps that changed. The
  hook repeats the open steps on every prompt so you can check them off, and nudges you at the end
  of a turn whose work reached no linked item.
- **Never delete an open step** unless the user explicitly asks to drop it. Open steps are what keeps
  half-done work from being forgotten; moving on to something else never closes or deletes them.
- **The user edits steps too**, in the rumo TUI or an editor. The hook points out what changed since
  your last response: take it into account, never add back a step they removed, and do not start a
  step they added unless they ask.
- **Name the next step when you finish one.** When you check off the step you were working on and the
  item still has open steps, end with one line in the user's language: for an `[agent]` step offer
  to start it, for a `[manual]` step say it is theirs.
- **Left off is one line**: where it stands or what blocks it. Not a copy of the next step.
- **Context is at most 3 short lines**: what this is and why it matters. No history, no logs, no
  list of what was done (the checked steps are that), no commands. A link to a longer document is
  fine.
- Conversation-only replies need no update. Never add sections.

## Several topics in one session

A session can be linked to several items, and keeps all of them up to date.

- Put each change in the item it belongs to.
- When the user starts work that belongs to none of the linked items, ask once, in one line at the
  end of the response: track it as a **new item** (do what `new` does, including the search for
  similar items; this session links to it too), add it as a **step** to one of the linked items, or
  **not track** it. Do not ask again about that topic after a "no".
- Items the session moves away from stay `doing` and linked, with their open steps, so they show in
  Now until the user parks or finishes them. Never park or close one unless the user asks.

## Sessions without an item

At the start of such a session, the hook lists what is in progress in this repository (or says how
many items are in progress elsewhere). When the user asks what is pending or what to do next, answer
from that, or run `rumo now`. Do not start any of it unless they ask.

In Claude Code, when a session without a linked item edits files, the hook asks you to offer rumo
once. The first time on a machine it also asks whether to keep offering in future sessions: record
the answer with `rumo ask-to-track true` or `rumo ask-to-track false` (the `ask_to_track` key in the
config). Run the same command whenever the user says to start or stop being asked.

## Groups

A group is an initiative (a project, epic or theme). The TUI shows every group in its own color
(the only thing color means there) and lists the groups in its footer legend. Groups are user
content: name them in the configured language.

After creating or promoting an item:

1. If an existing group (from the index) clearly fits, set `group: <key>` on the item.
2. Otherwise, if one or more items **without a group** are clearly part of the same initiative as
   this one, suggest a new group: a short name, a color not used by the existing groups (the index
   lists groups with their colors, and the available colors; only when all are in use, take the
   least used one), and the items it would apply to:
   "These 3 items look like one initiative: A, B, C. Create the group "Auth migration" (blue) and
   apply it to all?"
3. Only if the user says yes: run `rumo group-add "<name>" <color>` (it prints the new key), then add
   `group: <key>` to the frontmatter of each item (with Edit) and update their `updated`.
4. If nothing fits, or the user says no, leave the item without a group. Never create a group, or
   move an item from one group to another, without the user's confirmation.
5. Renaming, recoloring and deleting groups is done by the user in the TUI (`g`, then `r`, `c` or
   `d`). If the user asks you to do it instead, edit that group's table in the config file.

## Session line

Run this, replacing `<DIR>` with the **original working directory of this session** (where `claude`
was started, not the shell's current folder; `claude --resume` only finds the session from there):

```bash
D='<DIR>'; echo "- $(date '+%Y-%m-%d %H:%M') | $(basename "$(git -C "$D" rev-parse --show-toplevel 2>/dev/null || echo "$D")") ($(git -C "$D" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '-')) | $CLAUDE_CODE_SESSION_ID | $D"
```

## Item format

```markdown
---
title: <short title, in the configured language>
status: doing
group: <group key from the index, optional>
tags: [bug, frontend]
created: 2026-10-11 10:08
updated: 2026-10-11 16:30
---

## Left off
<one line: where it stands or what blocks it>

## Context
<at most 3 short lines: what this is and why it matters>

## Steps
- [x] [agent] <done: one short line, starting with a verb>
- [ ] [agent] <the next action>
- [ ] [manual] <what the user or someone else does>

## Sessions
- 2026-10-11 10:08 | web-app (main) | <session id> | /absolute/path
```

- Statuses: `idea`, `doing`, `waiting`, `paused`, `done`. `done: <date time>` exists only while done.
- Timestamps: `date '+%Y-%m-%d %H:%M'`.
- Sessions: append only. ` | closed` at the end of a line means that session no longer updates the item.

## Always

- Edit item files with the Edit tool (it refuses if the file changed since you read it; then re-read
  and re-apply). Never rewrite a whole file with Write; creating a new file is fine.
- Never create, update or close anything in Jira, ClickUp or other tools from an item unless the
  user explicitly asks in this session.
