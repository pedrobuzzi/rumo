# rumo: ideas and how work unfolds

## Problem Statement

rumo kept what the user is doing and what they finished, but not what they mean to do next, where
each piece of work came from, or what it turned into. Product ideas lived in a separate tool
(ideas-hub, a local web board that explores ideas with `claude -p` and keep-digging) and went cold
there: on 2026-11-05 none of its 12 ideas had changed in 37 days, 7 were AI suggestions never
accepted or rejected, and no idea had notes. At the same time rumo's Later was empty, and items grew
into whole projects inside one file (75 steps in one, 47 in another) with no way to split them. When
an idea was rejected, the reason was lost, so the same idea could come back as new.

## Solution

Ideas stay ordinary items (status `idea`, horizon Later), and rumo learns four things:

- **Deciding**: a sixth status, `dropped`, asks for the reason in one line. A dropped item spends 14
  days in Past and stays in the index, so the agent brings the reason back when a similar idea shows
  up. Ideas in Later show how many days they have been idle instead of a progress count.
- **Family**: a `from:` field links an item to the one it came from. The detail shows where an item
  came from and what it led to, and a fourth view, Tree, shows whole families.
- **Unfolding**: `b` creates a new idea from the selected item, or turns its next step into an item
  of its own; that step stays in the parent and checks itself when the child is done. Ideas noted in
  a linked session, and open steps left when an item is finished, get `from:` on their own.
- **Exploring**: `?` asks a question about an item (a ready-made lens or the user's own) and runs
  Claude Code in the background; the answer lands in a `## Explorations` section of the item, ending
  with three next questions. A deep lens runs keep-digging. A `## Notes` section keeps what the user
  learned first-hand, and it outweighs any research.

The 12 ideas of ideas-hub move into rumo, in a new group, and ideas-hub is retired. Everything stays
Markdown on the user's machine; rumo never runs git, and AI runs only when the user presses `?`.

## User Stories

Deciding

1. As a user, I want to note an idea from any shell with `rumo idea "<title>"`, without opening anything, and be told about similar items, so that capturing costs seconds and I don't duplicate.
2. As a user, I want to set an item to `dropped` with `s` and `6`, writing why in one line, so that the decision and its reason are kept.
3. As a user, I want a dropped item to leave Later, show in Past with `✕` for 14 days and stay searchable with `/` and `/rumo find`.
4. As a user, I want the agent to tell me the reason when I note an idea similar to one I dropped, so that I don't rediscover dead ideas.
5. As a user, I want each idea in Later to show how many days it has been idle, dimmed after 60 days, so that I see what is going cold.
6. As a user, I want a `## Notes` section for facts I learned (conversations, prices, numbers), shown in the detail and added by the agent with `/rumo note <text>`.
7. As a user, I want `/rumo ideas [Nd]` to review the ideas idle for more than 30 days (or N) and propose one plan (keep, drop with reason, start, merge, retry), writing nothing until I say yes.

Family and unfolding

8. As a user, I want an item to say where it came from (`from:`) and the detail to list its parent and children, each one reachable with Enter.
9. As a user, I want `b` on an item to create a new idea linked to it, in Later, with the same group and tags.
10. As a user, I want `b` to turn the next open step (or the highlighted one, with the steps focused) into an item of its own, in Now as doing; the step stays in the parent, points to the child, and checks itself when the child is done.
11. As a user, I want an idea I note in a session linked to one item to get that item as `from:`.
12. As a user, I want `/rumo done` to offer the open steps I won't do now as ideas linked to the item, and to suggest up to three ideas that came up in the work, only with my OK.
13. As a user, I want a Tree view (`4`, and `v` cycles to it) that shows each family whose members are in the current horizon, with the members outside it dimmed.

Exploring

14. As a user, I want `?` on an item to offer the lenses from my config, the three next questions of its latest exploration and a field for my own question.
15. As a user, I want the question to run in the background, so that I keep using the list; the detail shows it as running, and a notice says when the answer is ready.
16. As a user, I want the answer saved under `## Explorations` with the date and the question, in my content language, at most about 350 words, citing sources when it states facts from the web, and ending with three next questions.
17. As a user, I want the detail to list only the questions, and Enter on one to open the full answer.
18. As a user, I want an exploration that fails, times out or is cut off to be marked failed with the reason, and `?` to offer it again.
19. As a user, I want a deep lens that runs keep-digging when I configured it, keeping its raw output in `research/` and the synthesis in the item.
20. As a user, I want an exploration to keep running if I close the Timeline, and at most one running per item.

Migration

21. As a user, I want the ideas of ideas-hub in rumo, in a Produtos group, with their notes, explorations, parent links, last verdict or next step, and their research kept in files next to the items.
22. As a user, I want the suggestion ideas-hub archived to come in as dropped, and the suggestions I never triaged to say so in Left off.

## Implementation Decisions

### Phase 1: deciding (no AI)

- Items: `STATUSES` gains `dropped`, after `done`. Like `done: <time>`, `dropped: <time>` is set when an item becomes dropped and removed when it leaves. Dropping takes a one-line reason, written as Left off. The Timeline puts dropped items in Past with done ones, under the same 14-day window; their shape is `✕` and their title dim. The Past banner says "done and dropped in the last 14 days".
- Items: `from: <slug>` in the frontmatter names the parent, by the slug `rumo index` prints. Children are found by scanning; nothing is written twice. A `from:` naming no item is ignored.
- Items: new sections `## Notes` (one dated line per fact, `- YYYY-MM-DD <text>`) after Context, and `## Explorations` after PRs, before Sessions (format in phase 4). Missing sections are fine, as today.
- TUI, list: an item with status `idea` shows idle days since `updated` in the right column (`38d`), dimmed past 60. Paused items keep their progress.
- TUI, detail: Family (`from ↑` and `led to ↓` lines; Tab focuses them, Enter goes there), Notes, Explorations (questions only), and Research (files under `<folder>/research/<slug>/`, when any). Notes and Explorations show for ideas, paused and dropped items, and for any item that has them.
- TUI, `s`: options 1 to 6; 6 opens a one-line "Why?" field; Enter drops, Esc cancels. An empty reason is refused.
- CLI: `rumo idea "<title>" [--group <key>]` creates the idea as `n` does, prints its path, then up to three similar items (shared words in title and Left off, ignoring short and common words), with their status and, for dropped ones, the reason. It is the first subcommand meant for people, so it goes in the help; this changes the v1 decision that `rumo` only opens the Timeline.
- Index: each line also carries `from:` and, for ideas, `idle: <N>d`. Left off is already there, so a dropped item's reason reaches the agent's similarity check.
- Skill: `idea` and `new` show the reason when a similar item is dropped, and ask before creating. New `note <text>` appends to Notes of the linked item (or asks which). New `drop [reason]` drops the linked or chosen item, asking for the reason when missing.
- Compatibility: an older rumo reads `dropped` as `doing`, so the command and the plugin are released together.

### Phase 2: migration and review

- A one-off script, kept in the ideas-hub repository (it reads private data and means nothing to other rumo users), writes the items into `RUMO_DIR` and is run first against a temporary folder for review.
- Mapping: `title` as is; `status` viva or empty becomes `idea`, pausada `paused`, descartada `dropped` with `status_motivo` as Left off; a card in `arquivadas/` becomes dropped with "Sugestão da IA recusada na triagem (sem motivo registrado)"; a card still in the suggestions column gets Left off "Sugestão da IA de DD/MM, ainda não triada."; `tipo` becomes a tag; `parent` becomes `from:`; `created` gets ` 00:00`; `updated` from the card; `## Ideia` becomes Context; `## Notas` becomes Notes; each exploration becomes an Explorations entry marked done; the latest Veredito or Próximo passo, shortened to one line, becomes Left off when no other rule set it.
- Research (internet research, competitors, keep-digging refinement, final summary) goes verbatim to `<folder>/research/<slug>/ideas-hub.md`, with the keep-digging folder path when there is one.
- The group comes from `rumo group-add "Produtos" cyan`. ideas-hub gets a README note saying it is retired and where the ideas went; its code is left as is. Neither the script nor rumo runs git.
- Skill: `ideas [Nd]` reads the index (ideas with `idle` over N days, 30 by default) and the files it needs, and shows one plan, one line per idea with a short reason taken from the files: keep, drop (with the reason it would write), start, merge, retry the research, or triage the untriaged one by one. Nothing is written until the user says yes or adjusts, as in `sessions`.

### Phase 3: unfolding

- Steps: a new kind, `- [ ] [item] <text> → <slug>`, for a step that became an item. Older versions read it as a step with no marker and the text `[item] …`, which is harmless.
- TUI, `b`: a dialog with a title field and, when the item has an open step, Tab between "new idea from this item" and "next step becomes an item" (with the steps focused, the highlighted step). The second fills the title with the step's text. The new item gets the parent's group and tags and `from:` the parent; the first mode creates an idea, the second an item in doing and rewrites the step to the `[item]` form.
- Mirroring: a sync in `items` checks each open `[item]` step whose child is done (and reopens it if the child leaves done). The TUI runs it on its folder poll and the hook at Stop, since the agent edits files directly. A step checked this way is not news.
- Skill: `idea` sets `from:` to the linked item when exactly one is linked, and asks when several are. `done` offers the open steps as linked ideas and suggests up to three ideas from the session, created only with the user's OK.
- TUI, Tree: views become Timeline, Projects, Repos, Tree (`4`). A family is an item with children plus all its descendants, in file order under each parent, drawn with `├ └ │`. Tree shows the families with at least one member in the current horizon, members outside it dimmed; items with no family stay out. All item keys (`s`, `b`, `?`, `e`, `g`, `t`) work there.

### Phase 4: exploring in the background

- CLI: `rumo explore <slug> <lens>` or `rumo explore <slug> --ask "<question>"`, hidden from the help like `index`. It refuses when the item already has a running exploration, writes a running entry, runs the engine, then replaces the entry with the answer or the failure. Every write goes through the guarded write and re-reads on conflict, since the user may edit the item meanwhile.
- Entry format, inside `## Explorations`:

  ```markdown
  ### 2026-11-05 14:10 Como validar em uma semana, gastando pouco?
  <!-- rumo:exploration id=3f2a state=done -->
  …answer, with its headings demoted to ####…
  <!-- rumo:next -->
  - …
  - …
  - …
  ```

  States are `running` (with `pid=` and `started=`), `done` and `failed` (with `error=` on one line). A running entry whose process is gone, or that is older than its time limit plus five minutes, is read as failed.
- TUI, `?`: the dialog lists the lenses in config order (1 to 9), the next questions of the item's latest done exploration, and a free field. Enter starts `rumo explore` as a detached process (its own session on macOS and Linux, a detached process on Windows; output to a log in the config folder), shows "Asking…" and returns to the list. When the poll sees the entry done or failed, a notice names the item, and a new answer counts as news since you looked.
- TUI, detail: Explorations lists `date · question`, with `…` while running and `failed` when it failed. Tab focuses them; Enter opens the answer in a full-screen reader (Markdown), Esc goes back.
- Engine: `claude -p` with the prompt on stdin, `--setting-sources project`, `--strict-mcp-config`, `--no-session-persistence` and `--allowedTools WebSearch,WebFetch`, the environment variable `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, and the rumo folder as its working folder. That keeps the user's CLAUDE.md, plugins and hooks (rumo's own included) out of the answer and the session out of `/rumo sessions`. Without the executable, `?` says Claude Code was not found and names the config key. Time limit: 10 minutes.
- Prompt: ported from ideas-hub's `prompts/explorar.md`, in English, with the content language from the config. It gets the title, Context, Left off, Notes, the earlier explorations (newest first, trimmed to about 6,000 characters) and the start of the research files. Rules: answer the question directly, at most about 350 words; search the web only for real-world facts and link the source; be honest when the answer weakens the idea; the Notes are facts the user checked and outweigh research; no `#` or `##`; end with `<!-- rumo:next -->` and exactly three short questions (the most uncertain point, a different scenario, what would help decide).
- Lenses: a `[lenses]` table in the config, name to question, in the order shown. Defaults: `open` (what is still unclear), `pessimistic` (how this fails), `validate` (the cheapest real test, in one week), `competitors` (who does it, what they charge, where they fail), `smallest` (the smallest version that would still solve it). The user rewrites them in their language.
- Deep lens: shown only when `keep_digging` points to an executable. It runs it with the question file, `-o <folder>/research/<slug>/dig-<stamp>` and `-n <deep_iterations>` (3 by default), up to 90 minutes, and the entry gets `synthesis/final_answer.md` plus key findings, with the folder path.
- Config: `explore_command` (default `claude`), `keep_digging` (unset by default), `deep_iterations`, `[lenses]`.
- README: the "nothing leaves your machine" line says that `?` sends the item's text to Claude through the user's own Claude Code, and nothing else does.

## Testing Decisions

- Items, through the public functions: dropped transitions and timestamps, `from:` and children, Notes and Explorations parsing (all states, stale running), idle days, Past with dropped items, the `[item]` step and how an older pattern reads it, the step sync.
- CLI: `rumo idea` output and similar items; `rumo explore` against a fake `claude` and a fake keep-digging (scripts in `tests/`, as ideas-hub does): done, failed, timeout, cut off, refusal while running, conflict with an edit made during the run.
- TUI: Textual pilot through keys (`s` then 6, `b` in both modes, `4`, `?` with an injected spawner) and a poll that finds a finished entry.
- Skill: checked in real `claude -p` sessions, as for earlier releases.
- Migration: run against a copy of `ideas/` into a temporary `RUMO_DIR`, then read the result before the real run.

## Out of Scope

- Columns that run AI when a card moves, a server or browser view, and automatic commits.
- AI looking for new ideas on its own (it can become a lens later).
- Manual ordering and scores for ideas.
- Comparing ideas side by side and a one-page summary to send (phase 5, its own spec).
- Exploring from the agent (`/rumo explore`) and engines other than Claude Code.

## Further Notes

- Decided by the user on 2026-11-05, in the artifact "Ideias no rumo": the AI explores in the background inside rumo (the artifact recommended the agent; the user chose this); family in the detail plus a Tree view; `dropped` with a reason; product ideas in the same list, in a Produtos group; heavy research as explore lenses; migrate ideas-hub and retire it.
- Calls made in this spec, to check while reading: the reason goes to Left off, not a new field; an exploration never changes Left off, steps or status; explorations survive closing the Timeline; Tree follows the current horizon (the prototype ignored it); lens names and defaults are in English, rewritten by the user; the migration script lives in ideas-hub; research is found by folder, not listed in the item.
- One phase per minor version, the command and the plugin together.
