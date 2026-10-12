# rumo v1 — spec

Status: ready-for-agent (local file; this project has no issue tracker yet)
Date: 2026-10-11

## Problem Statement

A developer juggles several pieces of work at once: the bug they are fixing now, the review they are waiting on, the refactor they paused last week, and a pile of "I should look at this someday" ideas. Today that state lives in their head, in scattered terminals and AI agent sessions, and in team tools (Jira, ClickUp) that are too heavy for personal bookkeeping and don't capture the real "where did I stop and what's next".

The consequences:

- After switching context, restarting the computer, or closing a terminal, they lose the thread: what was done, what's left, which agent session had the context.
- Ideas for future work get forgotten, or get re-discovered later as "new" work, duplicating effort.
- They can't easily answer "what did I do last week?" for a standup or a 1:1.
- Related work (several items belonging to the same initiative) isn't visually connected.

A first private version of this tool solved part of it, but was tied to Claude Code sessions, written in Portuguese only, had no notion of future ideas or past work, and no way to group related items.

## Solution

`rumo` is a local, file-based organizer for a developer's own work, across past, present and future.

- Every piece of work is one Markdown file (an **item**) in a single local folder. An idea for later is just an item with status `idea`; it grows into a full item when work starts.
- Running `rumo` in the terminal opens a **Timeline** TUI: **Past** (done items, most recent at the bottom), **Now** (doing / waiting) and **Later** (ideas / paused), with a detail pane showing *Left off*, *Next steps* and linked agent sessions.
- Items can belong to a **group** (an initiative). The item title is colored with the group color, and a footer legend lists every group name in its color.
- The user can change status, group and tags manually from the TUI. AI agents keep items up to date during a session, assign tags automatically from a fixed list, and, before creating an item, look for a similar existing one (typically an idea) to promote instead of duplicating.
- Everything in the app — commands, statuses, sections, tabs, keys, tags — is in English so it works for any nationality. Only the content (titles, context, steps) is in the user's language, set in the config.
- No external sync. If the user wants Jira or ClickUp updated, they ask the agent in the session.

## User Stories

### Seeing my work

1. As a developer, I want to run `rumo` and immediately see what I'm working on now, so that I can pick up without thinking.
2. As a developer, I want the Timeline to show Past, Now and Later as one top-to-bottom list, so that I see my work as a continuous path.
3. As a developer, I want the cursor to start on the first Now item, so that the most relevant thing is selected when the app opens.
4. As a developer, I want each Now item to show its first pending step under the title, so that I know the next action without opening it.
5. As a developer, I want a detail pane with *Left off*, *Context*, *Steps* and *Sessions* for the selected item, so that I can recover full context in seconds.
6. As a developer, I want Past to list done items ordered by completion date, so that I can answer "what did I do recently?".
7. As a developer, I want Past to show the last 14 days by default with a way to reveal older items, so that the list stays short.
8. As a developer, I want Later to list ideas and paused items, so that I can choose what to start next.
9. As a developer, I want waiting items to be visually distinct from doing items, so that I see what is blocked on someone else.
10. As a developer, I want the TUI to refresh automatically when an item file changes on disk, so that updates made by an agent appear without restarting.
11. As a developer, I want to filter the Timeline by text, tag or group with `/`, so that I can focus on one topic.
12. As a developer, I want `Esc` to clear the filter, so that I can return to the full view quickly.

### Groups and colors

13. As a developer, I want to put related items in a group, so that work belonging to the same initiative is connected.
14. As a developer, I want the item title colored with its group color, so that I spot related items at a glance.
15. As a developer, I want a footer legend listing each group name in its color, so that I know what each color means.
16. As a developer, I want items without a group shown in the default text color, so that ungrouped work is still readable.
17. As a developer, I want an item pointing to a group that no longer exists to fall back to the default color, so that a config edit never breaks the view.
18. As a developer, I want to assign a group to the selected item with `g`, so that I can organize without editing files.
19. As a developer, I want to create a new group (name + color) from the `g` picker, so that I don't need to open the config file.
20. As a developer, I want group names to be in my own language, so that they read naturally (they are content, not UI).

### Status

21. As a developer, I want to change an item's status with `s`, so that I control the workflow manually.
22. As a developer, I want the statuses `idea`, `doing`, `waiting`, `paused` and `done`, so that every stage of work has a clear place.
23. As a developer, I want an item to move between Past, Now and Later automatically when its status changes, so that the Timeline always reflects reality.
24. As a developer, I want the completion date recorded when an item becomes `done`, so that Past is ordered correctly.
25. As a developer, I want the completion date removed when a done item is reopened, so that it doesn't show stale history.

### Tags

26. As a developer, I want a fixed list of English tags in the config, so that tags stay consistent and international.
27. As a developer, I want the agent to choose tags automatically from that list, so that I don't have to classify every item.
28. As a developer, I want to add or remove tags on the selected item with `t`, so that I can correct the agent.
29. As a developer, I want a tag that isn't in the config list still shown (dimmed), so that hand edits are never lost.

### Steps

30. As a developer, I want steps marked `[agent]` or `[manual]`, so that I see what the AI can do and what I or someone else must do.
31. As a developer, I want to toggle the selected step with `space`, so that I can mark manual work as done.
32. As a developer, I want pending steps to survive when I switch topics, so that nothing gets forgotten.

### Capturing ideas

33. As a developer, I want to press `n` in the TUI and type a title to create an idea, so that I capture it in seconds.
34. As a developer, I want a new idea to be a small file (title, status, tags, dates), so that capturing costs nothing.
35. As a developer, I want to tell the agent `/rumo idea <text>` in the middle of a session, so that I note something for later without losing focus.
36. As a developer, I want the agent to warn me if a similar idea already exists when I add one, so that I don't create duplicates.

### Editing

37. As a developer, I want to press `e` to open the selected item in my `$EDITOR`, so that I can edit anything freely.
38. As a developer, I want the TUI to reload the item after the editor closes, so that my edits show immediately.
39. As a developer, I want the TUI never to overwrite a file that changed since it was read, so that agent and human edits don't clobber each other.
40. As a developer, I want a clear message when a write is refused because of a conflict, so that I know to retry.

### Agent sessions

41. As a developer, I want to type `/rumo` with no arguments and get a menu of options suited to the current session, so that I don't need to memorize commands.
42. As a developer, I want `/rumo new` to create an item from the current conversation, so that work in progress gets tracked.
43. As a developer, I want `/rumo new` to search existing items first and offer to promote a similar idea, link to a similar item, or create a new one, so that past notes are reused.
44. As a developer, I want `/rumo link [item]` to link this session to an existing item, suggesting similar ones when no argument is given, so that I can continue earlier work in a new session.
45. As a developer, I want `/rumo now` to list my Now items with their next step in the chat, so that I can choose what to resume.
46. As a developer, I want `/rumo find <text>` to search Past, Now and Later, so that I can ask "what did I do about X?".
47. As a developer, I want `/rumo status` to show *Left off* and next steps of the linked item, so that I can re-orient mid-session.
48. As a developer, I want `/rumo step <text>` to add a step to the linked item, so that I record follow-ups as they appear.
49. As a developer, I want `/rumo park [reason]` to set the item to waiting or paused, write an accurate *Left off*, and stop this session from updating it, so that switching topics never loses context.
50. As a developer, I want `/rumo done` to check that all steps are checked and ask for my confirmation before marking done, so that nothing is closed prematurely.
51. As a developer, I want `/rumo unlink` to stop this session from updating the item without `/clear`, so that I can keep chatting about something else.
52. As a developer, I want a linked session to keep *Left off*, *Steps* and `updated` current after every response where something changed, so that the item is always accurate.
53. As a developer, I want the agent to write item content in my configured language, so that the content reads naturally to me.
54. As a developer, I want the agent never to create an item on its own initiative, so that my list only contains what I chose to track.
55. As a developer, I want each session linked to an item to be recorded with date, repo, branch, session id and folder, so that I can resume it later.
56. As a developer, I want to press `c` on a session in the TUI to copy the resume command, so that I can reopen the agent session in its original folder.
57. As a developer, I want `/clear` to stop the old session from updating the item, so that a fresh conversation starts unlinked.

### Installation

58. As a developer, I want a single install script that installs the `rumo` command, the `/rumo` skill, the hook and the agent rules, so that setup takes one step.
59. As a developer, I want the installer to be idempotent and back up any file it changes, so that re-running it is safe.
60. As a developer, I want the installer to create the items folder and a default config with English tags and my language, so that it works out of the box.
61. As a developer, I want the installer to remove leftovers of earlier installs (skill, hook, rules block) without touching any data folder, so that they never conflict.
62. As a developer, I want an uninstall that removes the skill, hook and rules but keeps my items, so that I never lose data.
63. As a developer, I want `rumo` to only open the TUI, with no user-facing subcommands, so that the interface stays simple.
64. As a developer outside Brazil, I want every UI label in English, so that I can use the tool without knowing Portuguese.

## Implementation Decisions

### Product rules

- The project is named `rumo`. The command is `rumo`; the distribution name is `rumo-cli` because `rumo` is taken on PyPI (placeholder package). Installation is defined in [rumo-distribution.md](rumo-distribution.md).
- All UI text, statuses, section names, frontmatter keys, tags and key hints are English. Content is written in the configured `language`.
- No migration of earlier data. Examples are neutral.
- No Jira/ClickUp integration of any kind.

### Item file (one Markdown file per item)

- Location: the items folder (default `~/rumo/`). File name: `YYYY-MM-DD-slug.md` using the creation date; slug is the title in kebab-case without accents; on collision append `-2`, `-3`.
- Frontmatter keys: `title`, `status`, `group` (optional), `tags` (inline list), `created`, `updated`, `done` (only while status is `done`). Values are simple `key: value`; lists are inline `[a, b]`. No YAML library: the hook must stay stdlib-only and fast.
- Statuses: `idea`, `doing`, `waiting`, `paused`, `done`. Unknown status is read as `doing`.
- Sections (all optional, any order tolerated): `## Left off`, `## Context`, `## Steps`, `## Sessions`.
- Steps: `- [ ] [agent] text` / `- [x] [manual] text`.
- Sessions: append-only lines `- <date time> | <repo> (<branch>) | <session id> | <absolute folder>`, optionally followed by `| closed`. A closed line no longer links that session (used by park/unlink).
- An idea created quickly has only frontmatter.

Example item (from the design discussion):

```markdown
---
title: Redirect depois do logout
status: doing
group: auth
tags: [bug, frontend]
created: 2026-10-11 10:08
updated: 2026-10-11 16:30
---

## Left off
Ajustar o redirect para a home; falta o teste e2e.

## Steps
- [x] [agent] Ajustar o handler de logout
- [ ] [manual] Pedir review da PR

## Sessions
- 2026-10-11 10:08 | web-app (main) | 7134aad0-... | /Users/you/repo/web-app
```

### Config

- File: `~/.config/rumo/config.toml`, read with the stdlib TOML reader. Env var `RUMO_DIR` overrides the folder (used by tests).
- Keys: `folder`, `language` (e.g. `pt-BR`), `tags` (list), and `[groups.<key>]` tables with `name` and `color`.
- Colors come from a fixed named palette that reads well on dark and light terminals: `blue`, `green`, `yellow`, `orange`, `red`, `magenta`, `purple`, `cyan`.
- Default tags: `bug`, `feature`, `refactor`, `research`, `spike`, `review`, `infra`, `deploy`, `meeting`, `docs`, `support`, `tech-debt`.
- Creating a group from the TUI appends a new `[groups.<key>]` block to the config file as text (no TOML writer needed).
- Missing config falls back to defaults; a malformed config shows an error in the TUI footer and uses defaults instead of crashing.

### Modules

- **Items module** (deep module, main test seam). Interface, roughly: resolve folder; list items; load one item; save with an mtime conflict check (atomic replace); set status (handles `done` timestamp add/remove); set group; set tags; toggle step; create idea from a title; group items into the Timeline (Past / Now / Later, with the 14-day Past window); produce the compact one-line-per-item index used for similarity search. Raises a dedicated conflict error when the file changed on disk.
- **Config module.** Load with defaults; expose language, tags, groups (key, name, color); append a group.
- **Hook.** `UserPromptSubmit` entry point. Reads `session_id` from stdin, scans item files for an open (non-closed) session line with that id, and prints a reminder naming the linked item(s) and their status. Stdlib only. Silent when nothing matches.
- **TUI (Textual).** Timeline list on the left, detail pane on the right, footer with group legend and key hints. Keys: `↑↓`/`Enter` navigate, `Tab` switch pane, `space` toggle step, `s` status, `g` group, `t` tags, `n` new idea, `e` open in `$EDITOR`, `c` copy resume command, `/` filter, `Esc` clear filter, `q` quit. Polls the folder for changes (about every 2 s, as today).
- **CLI entry.** `rumo` with no arguments opens the TUI. Hidden internal subcommands for machines and the installer: `hook`, `index` (prints the similarity index for the agent), `path` (prints the items folder), `install`, `uninstall`. They are not shown in help text.
- **Installer.** Installs the command, symlinks the `/rumo` skill into the agent's skills folder, registers the hook in agent settings, writes the rules block between `<!-- rumo:start -->` and `<!-- rumo:end -->` in the agent's global instructions, creates the folder and default config. Idempotent, with backups, and never deletes data folders. Replaced by the Claude Code plugin in [rumo-distribution.md](rumo-distribution.md).
- **Agent resources.** One `/rumo` skill with subcommands, plus the rules block. No-argument behavior: if the session is linked, offer `status`, `step`, `park`, `done`, `unlink`; otherwise offer `new`, `idea`, `link`, `now`, `find`. Similarity search: the agent runs the hidden `index` subcommand, judges similarity itself (no embeddings, no database), and presents promote / link / create-new choices. The agent writes with its Edit tool only (never full rewrites), keeping the conflict guarantee.

### Agent rules (behavioral contract)

- Never create an item unless the user runs `/rumo new` or `/rumo idea`.
- While linked, after each response where something changed, update *Left off*, *Steps* and `updated`. Conversation-only replies need no update.
- Work with more than one stage becomes steps; anything not done now becomes a pending step tagged `[agent]` or `[manual]`.
- `done` only with all steps checked and explicit user confirmation.
- Session lines are append-only; park/unlink append `| closed` to this session's line instead of deleting it.
- Tags are chosen from the config list; content is in the configured language.
- Never touch external tools (Jira, ClickUp, etc.) unless the user explicitly asks in the session.

## Testing Decisions

- Good tests exercise external behavior through the highest seam: write real files in a temp folder, call the public interface or drive the real process/UI, and assert on resulting files and output. No assertions on private helpers or internal data layout.
- Tests use the stdlib `unittest` runner, as today (`uv run python -m unittest discover -s tests`).
- **Items module** (primary seam): temp folder via `RUMO_DIR`. Cover parsing of every field and section, tolerance to missing sections and unknown values, status transitions including `done` timestamp add/remove, conflict detection on concurrent change, idea creation and slug collisions, Timeline grouping and the 14-day Past window, group fallback for unknown keys, and the index output. Prior art: the existing tickets tests.
- **Hook**: run as a subprocess with JSON on stdin; assert the reminder for a linked session, silence for unknown ids, and silence for a `closed` session line. Prior art: the existing hook test in the tickets tests.
- **Installer**: fake HOME; assert idempotency, backups, the rules block markers, skill symlink, hook registration, and that uninstall keeps the items folder. Prior art: the existing installer tests.
- **TUI** (new seam): Textual's `run_test()` pilot against a temp folder. Press keys (`s`, `g`, `t`, `n`, `space`, `/`) and assert the file changes on disk and which section (Past / Now / Later) an item appears in; assert the footer legend lists the configured groups.
- **Config**: covered through the items module and TUI tests (defaults, malformed file, group append).
- The `/rumo` skill and rules are prompts for the agent; they are verified manually in a real session, not by automated tests.

## Out of Scope

- Jira, ClickUp or any external sync (manual via the agent session only).
- Migration of data from earlier tools.
- `rumo standup` summary, stale `waiting` alerts, archiving old done items, manual ordering of Later, due dates.
- Support for agents other than Claude Code (the file format stays agent-neutral so this can be added later).
- Semantic or embedding-based search, databases, indexes on disk.
- Multi-user or shared folders; Windows support.
- Translating the UI (the UI is English only by design).

## Further Notes

- Decisions confirmed by the user in conversation: the name `rumo`; English UI with content in the user's language; ideas as a status of the same item; the Timeline layout; colored titles; a footer legend with group names; automatic tags; similarity search when creating; `rumo` only opens the TUI; the `/rumo` menu and subcommand list; no Jira/ClickUp; no migration; the item file format (design part 1).
- Proposed by the assistant and **not yet explicitly reviewed** by the user: config details (TOML, color palette, default tags, group creation from the TUI), exact TUI keys, the 14-day Past window, `| closed` session lines for park/unlink, and hidden internal subcommands.
- Name research: `docket` was rejected (ticket-docket ships a `docket` command for the same purpose). `rumo` collides only with Rumo S.A. (a Brazilian rail logistics company, unrelated field) and dormant packages; trademark registries were not checked.
- The README screenshot must be regenerated with neutral example data.
