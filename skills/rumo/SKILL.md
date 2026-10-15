---
name: rumo
description: Organize the user's local work items (past, now, later) with rumo. Creates items from the session, notes ideas, links this session to an item, adds steps, parks or finishes it, and catches up on recent sessions. Requires the rumo CLI.
disable-model-invocation: true
argument-hint: "[new | idea <text> | link [item] | now | find <text> | status | step <text> | park [reason] | done | unlink | sessions [7d]]"
allowed-tools: Bash(rumo:*)
---

# /rumo

Arguments: `$ARGUMENTS`

rumo keeps one Markdown file per piece of work (an **item**) in a local folder. The user sees them in
the rumo app (`rumo` in a terminal): **Past** (done), **Now** (doing, waiting), **Later** (idea, paused).

## 0. Always start here

Run `rumo index '<DIR>'`, where `<DIR>` is the **original working directory of this session** (where
`claude` was started, not the shell's current folder: `claude --resume` only finds the session from
there). Never read environment variables yourself; the index has what you need. It prints
`# key: value` lines, then one line per item: `slug | status | title | group | tags | left off`.

- `folder`, `config`, `language`, `tags`, `groups` (with colors) and `colors` (the palette).
- `now`: the time to write in `created`, `updated` and `done`.
- `session` and `linked`: this session's id, and the items it already keeps updated (`-` for none).
- `session-line`: the line that links this session to an item. Append it to the item's `## Sessions`.
  - **Full mode**: `session` is an id (Claude Code). Sessions can be linked and resumed.
  - **Simple mode**: `session: none` (another agent, or an old Claude Code). `new`, `idea`, `now`,
    `find`, `step` and `done` work without a Sessions section; `link`, `status`, `park`, `unlink` and
    `sessions` need Claude Code with the rumo plugin, so say so. The menu shows only `new`, `idea`, `now`, `find`.
  - No `session` line: the CLI is older than the plugin. Work in simple mode and tell the user to
    upgrade it (`brew upgrade rumo`, `uv tool upgrade rumo-cli` or `pipx upgrade rumo-cli`).

If `rumo` is not found, stop and tell the user to install it, then run `/rumo` again:
`uv tool install rumo-cli` or `pipx install rumo-cli` (macOS, Linux, Windows), or
`brew install pedrobuzzi/tap/rumo` (macOS, Linux).

Write item content (title, Left off, Context, steps) in the configured `language`; keep the structure
(frontmatter keys, section names, statuses, tags, `[agent]`/`[manual]`) in English. Follow "Writing an
item" every time you create or change an item.

## 1. No subcommand: show the menu

Show a short numbered menu and wait for the choice. Put first the option that best fits the
conversation so far.

- Session **not linked**:
  1. `new`: track what we are doing in this session as an item
  2. `idea <text>`: note something for later without leaving this topic
  3. `link [item]`: continue an existing item in this session
  4. `now`: list what is in progress, with the next step of each
  5. `find <text>`: search past, current and future items
  6. `sessions`: catch up on the sessions open or recent, creating or updating their items
- Session **linked** (name the item or items):
  1. `status`: show Left off and the next steps
  2. `step <text>`: add a step
  3. `new`: track another topic of this session as its own item (both stay linked)
  4. `park [reason]`: pause it with an accurate Left off and stop updating it here
  5. `done`: finish it (checks the steps, asks you first)
  6. `unlink`: stop updating it from this session
  7. `sessions`: catch up on the sessions open or recent, creating or updating their items

## 2. Subcommands

### new
1. **Look for similar items first.** From the index, pick up to 3 items about the same subject or
   goal (wording may differ), favoring `idea` and `paused`. If any, show them (title, status, slug)
   and ask: **promote/continue** one of them, or **create a new** item.
   - Promote/continue: do what `link` does, set `status: doing` if it was `idea`/`paused`/`waiting`,
     and bring its Steps up to date with the conversation.
2. Otherwise create `<folder>/YYYY-MM-DD-<slug>.md` (today; slug from the title in kebab-case without
   accents; add `-2`, `-3` if taken) in the format below: `status: doing`, 1-3 tags from the allowed
   list, `group` only if one clearly fits, Steps (work already done here as `- [x]`, everything pending
   as `- [ ]`, the next action first), a one-line Left off, a Context of at most 3 lines, the PRs
   already opened for this work, and, in full mode, `## Sessions` with the `session-line`.
3. **Suggest a group** (see "Groups").
4. Confirm in one line: the path, then (full mode) "this session now keeps it updated until /clear,
   park or unlink", or (simple mode) "update it with /rumo or in the rumo app".

### idea <text>
1. Check the index for a similar item. If one exists, show it and ask whether to still create.
2. Create a small item: frontmatter only (`title`, `status: idea`, `tags`, `created`, `updated`), plus
   `## Context` only if the user gave detail. Do **not** link this session.
3. **Suggest a group** (see "Groups"), in one short question.
4. Confirm in one line and go back to what we were doing.

### link [item]
1. With an argument, find the item by slug or title. Without one, suggest up to 3 items similar to
   the conversation. If ambiguous, ask.
2. Append the `session-line` to `## Sessions` (never delete lines). If the status was `paused` or
   `waiting` and work restarts now, set `doing`. Bring the Steps up to date, then Left off and `updated`.

### now
Run `rumo now` (one line per `doing` or `waiting` item: title, status, next step, file) and list the
title, status and next step of each. In full mode, offer to `link` one.

### find <text>
Search the folder's `*.md` files for the text, ignoring case (the Grep tool, or `grep -il`), then read
only the matches. Answer with the items found (status, when done, what was decided).

### status
Show the linked item's open steps (the first one is the next action), its Left off and the PRs not
merged or closed yet, with their state.

### step <text>
Works in any session, linked or not.
1. **Pick the item.** The linked one; with several linked, the one the step belongs to. With none
   linked (or in simple mode), the item it most likely belongs to (the conversation, this repository,
   the group, the words of the step); ask only when two or more fit equally, listing up to 3 (title, slug).
2. Add `- [ ] [agent] <text>` or `- [ ] [manual] <text>` (manual = the user or someone else does it
   outside the agent: review, deploy, a call), as one short line starting with a verb, in the
   configured language. Put it after the open steps, or first if it is the next action. Update `updated`.
3. Confirm in one line: the step and the item's title. Adding a step never links the session.

### park [reason]
1. Ask, if unclear: `waiting` (blocked on someone or something else) or `paused` (the user chose to
   stop for now).
2. Make sure the Steps tell someone with no context how to resume: every pending piece of work is a
   step and the next action is the first open one. Set Left off to one line: the blocker, or why it
   stops here.
3. Set the status and `updated`, then append ` | closed` to **this session's** line in Sessions.

### done
In simple mode, ask which item to finish (from `rumo now`), since no item is linked.
1. Before changing anything, ask in one message: for each unchecked step, whether it is done, dropped
   (delete it) or stays (then the item is not finished); for each PR not merged or closed, its state
   now; and whether to finish the item.
2. Only after an explicit yes: set `status: done`, add `done: <now>`, update `updated`, and (full mode)
   append ` | closed` to this session's line.

### unlink
Append ` | closed` to this session's line. Nothing else changes.

### sessions [period]
Catches up, from any session, on the Claude Code sessions open or active since the last review.
1. Run `rumo sessions`, or `rumo sessions --since 7d` when the user gives a period (`7d`, `12h`). It
   prints `# since:` and `# now:`, then one block per session, newest first: `## <id>` (`open` while it
   runs), then `last`, `where`, `title`, `linked` (`(up to date)`: the item changed after the session's
   last activity), `closed` (items the session closed), `same branch`, `prompts`, `last answer`,
   `edited`, `prs` and `line`, the session line to append. Lines with nothing to say are left out.
2. Decide each session with the first rule that applies:
   1. `linked`: update that item, unless it is `(up to date)`.
   2. `same branch`: link it to that item.
   3. An item about the same subject (title, Left off, Context; read its file with Read when the index
      line is not enough), favoring `doing`, `paused` and `idea`: link it there, marked as likely.
   4. Several sessions about the same subject that no item covers: one new item for all of them.
   5. A quick question or lookup: ignore. Anything else: a new item.

   Never link a session again to an item in its `closed`. This session counts like any other.
3. Show the plan, answer options included, in the configured language, then stop and wait:

   ```
   6 sessions (2 open) since 13/10 18:00

   Update
    1. Redirect depois do logout ← web-app main · e2e test done, PR #31 open
   Link to an existing item
    2. Pix no checkout (paused → doing) ← web-app feature/pix (open) · same branch, QR code done
   Create
    3. Migrar o CI para GitHub Actions ← api chore/actions
   Ignore
    4. notes · questions about git
   Up to date: 1

   Apply? yes · "without 2" · "3 goes to <item>" · no
   ```

   One line per item: its title, the sessions (repository and branch, `(open)` while running) and what
   changes in a few words. Propose `done` only for work the sessions clearly finished; the user's yes
   is the confirmation `done` needs.
4. Write nothing before the answer. On a yes or adjustments, for each item: append the `line` of each of
   its sessions to `## Sessions` (unless that session already has a line there not closed), bring the
   Steps up to date with what the sessions did and left (see "Writing an item"), set Left off, add
   the PRs from `prs` as `[open]`, set the status in the plan and `updated`. Create new items the way
   `new` does, with a group only when an existing one clearly fits.
5. Unless the user cancelled, run `rumo sessions --reviewed "<now>"` with the `# now:` of the listing,
   so the next review starts there. Confirm in one line what changed; open sessions linked now keep
   their items current from their next prompt.

## Writing an item

Steps are the item: done steps are the record of what happened, open steps are the plan. Everything
else stays short.

- **One step, one action, one line.** Start with a verb, keep it under about 70 characters, and add a
  detail (a commit, an id, a command) only when that step needs it. `[agent]` is work the agent does;
  `[manual]` is work the user or someone else does (a review, a deploy, a decision).
- **The first open step is the next action.** Reorder when priorities change.
- **Keep the Steps current while linked.** After every response in which work happened: check what got
  done, add a step for whatever came up or was left for later, reword steps that changed. The hook
  repeats the open steps on every prompt, and nudges you when a turn's work reached no linked item.
- **Never delete an open step** unless the user asks to drop it. Moving on to something else never
  closes, parks or deletes anything: items the session moves away from stay `doing` and linked.
- **The user edits steps too**, in the rumo app or an editor. The hook points out what changed since
  your last response: never add back a step they removed, and do not start one they added unless asked.
- **Name the next step when you finish one.** When you check off the step you were working on and
  open steps remain, end with one line in the user's language: offer to start an `[agent]` step, or
  say a `[manual]` one is theirs.
- **Left off is one line**: where it stands or what blocks it. Not a copy of the next step.
- **Context is at most 3 short lines**: what this is and why it matters. No history, logs, commands or
  list of what was done (the checked steps are that). A link to a longer document is fine.
- **PRs go under `## PRs`**, one per line: `- [open] <link> <short title>` (states: `open`, `draft`,
  `merged`, `closed`). rumo adds the PRs a linked session opens and, with `gh`, the ones opened from
  its branches. Add one the user gives you, and update a state when you learn it (`gh pr view <link>`).
- Conversation-only replies need no update. Never add other sections.

## Several topics in one session

A session can keep several items updated: put each change in the item it belongs to. When the user
starts work that belongs to none of them, ask once, in one line at the end of the response: track it
as a **new item** (do what `new` does; this session links to it too), add it as a **step** to one of
the linked items, or **not track** it. After a "no", do not ask again about that topic.

## Sessions without an item

At the start of such a session the hook lists what is in progress in this repository: answer "what is
left?" from that or from `rumo now`, and start none of it unless asked. When the hook asks you to offer
rumo, record whether to keep offering in future sessions with `rumo ask-to-track true` or
`rumo ask-to-track false`, and run it again whenever the user says to start or stop being asked.

## Groups

A group is an initiative (a project, epic or theme); the app shows each in its own color. Groups are
user content: name them in the configured language. After creating or promoting an item:

1. If an existing group clearly fits, set `group: <key>` on the item.
2. Otherwise, if items **without a group** are clearly part of the same initiative, suggest a new group:
   a short name, a color no group uses yet (when all are taken, the least used one), and the items:
   "These 3 items look like one initiative: A, B, C. Create the group "Auth migration" (blue) and
   apply it to all?"
3. Only on a yes: run `rumo group-add "<name>" <color>` (it prints the new key), then add
   `group: <key>` to each item's frontmatter and update their `updated`.
4. Otherwise leave the item without a group. Never create a group, or move an item from one group to
   another, without the user's confirmation. The user renames, recolors and deletes groups in the app
   (`g`, then `r`, `c` or `d`); if they ask you instead, edit that group's table in the config file.

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

## PRs
- [open] https://github.com/acme/web-app/pull/42 <short title>

## Sessions
- 2026-10-11 10:08 | web-app (main) | <session id> | /absolute/path
```

- Statuses: `idea`, `doing`, `waiting`, `paused`, `done`. `done: <date time>` exists only while done.
- Timestamps: `YYYY-MM-DD HH:MM`, local time: `now` in the index, or `date '+%Y-%m-%d %H:%M'` in a later response.
- Sessions: append only. ` | closed` at the end of a line means that session no longer updates the item.

## Always

- Edit item files with the Edit tool (it refuses if the file changed since you read it; then re-read
  and re-apply). Never rewrite a whole file with Write; creating a new file is fine.
- Never create, update or close anything in Jira, ClickUp or other tools from an item unless the user
  explicitly asks in this session.
