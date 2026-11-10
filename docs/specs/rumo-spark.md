# rumo: new ideas from the ones you already have (`*`)

## Problem Statement

The ideas in rumo only grow when the user has an idea. ideas-hub had a "Sugerir" button that searched
the web for five ideas, but it knew nothing about the user, and its suggestions sat untriaged for weeks
(7 of 12 cards when it was retired). The user wants rumo to read the ideas they already had, old and
recent, understand where they are heading, and be creative from there: few ideas, each one short, and
easy to throw away before it joins the others.

## Solution

`*` in the Timeline starts a spark, in the background like `?`:

1. Claude Code reads the user's ideas, oldest first (ideas, paused and dropped items with their reasons,
   plus the titles of current work), and writes in up to three short lines the direction it sees.
2. Only when the answer would really change the ideas, it first asks one or two short questions. rumo
   says so; `*` opens them; the user answers, or leaves them blank, and the spark goes on.
3. It comes back with 3 ideas: a title, one sentence of at most 20 words, and the idea of the user's it
   builds on.
4. `*` opens them. Any of them is thrown away with one key; Enter adds the rest to Later as ideas. The
   ones thrown away are remembered and never suggested again.

Nothing is created before the user keeps it, and AI runs only when the user presses `*`.

## User Stories

1. As a user, I press `*` anywhere in the Timeline and keep working, or close it, while rumo thinks.
2. As a user, I get a one-line notice when rumo has a question, when the ideas are ready, or when it failed and why; and, if I closed the Timeline, when I open it again.
3. As a user, I see the direction rumo read, so I can tell whether it understood me.
4. As a user, I answer at most two short questions, or leave them blank.
5. As a user, I see 3 ideas, one line each, and throw any away with one key before it joins my ideas.
6. As a user, the ideas I keep land in Later with `from:` the idea they build on, its group, and the sentence as Context, so `?`, `b` and `s` work on them like on any idea.
7. As a user, rumo never suggests an idea I already have, one I threw away, or one I dropped, unless it says what changed.
8. As a user, pressing `*` while a spark runs tells me so; while questions or ideas wait, it opens them.

## Implementation Decisions

### Engine: `src/rumo/spark.py` (stdlib only, like `explore`)

- `rumo spark` (a CLI subcommand hidden from the usage, like `explore`) runs one step: it reads the
  state, builds the prompt, runs Claude Code through `explore.run_claude` (same flags, `WebSearch` and
  `WebFetch` only, `explore.LIMIT`), parses the reply and writes the state. Exit 1 when it failed.
  `spawn(config_path)` starts it detached, logging to `explore.log`, the way `explore.spawn` does.
- State: one file, `<folder>/spark.json` (not `.md`, so never read as an item), written whole through a
  temporary file and a rename. Fields: `state` (`running`, `asking`, `ready`, `failed`; absent when
  idle), `started`, `pid`, `limit`, `error`, `direction`, `questions`, `answers`, `ideas` (each
  `title`, `summary`, `from`), and `discarded` (each `title`, `summary`, `date`; newest first, the last
  100 kept). A `running` state whose process is gone or whose time ran out reads as failed, "stopped
  before it answered", by the rule explorations use. A file that cannot be read counts as idle and is
  kept as `spark.json.bad` before the next write.
- What the prompt gets: each item with status `idea`, `paused` or `dropped`, oldest `created` first, with
  its slug, created date, title, group, status, `from:`, Context, Notes, Left off (for a dropped item,
  the reason), and the questions of its explorations with the first lines of the latest answer, each
  item cut to about 1,500 characters and the whole to 40,000; the titles and groups of the other items,
  as current work; the discarded suggestions; and, on the second run, the questions with the answers.
- What it asks for (English prompt, content in `cfg.language`): read the ideas oldest to newest and
  find what the user keeps coming back to (people, problems, kind of product, way of charging) and what
  they dropped and why; write the direction in at most three short lines. Then 3 ideas the user has not
  had: one close to the direction, one that crosses two of their ideas, one that keeps the direction's
  core but moves somewhere else. None may repeat or reword an existing idea or a discarded suggestion,
  or come back to a dropped one without saying what changed. A quick web search checks that each one
  answers a real need today; no links. Title at most 8 words; summary one sentence of at most 20 words,
  saying what it is and for whom. On the first run only, it may instead return one or two short
  questions, and only when the answer would change the ideas.
- Reply: JSON only, either `{"direction": "...", "questions": ["..."]}` or `{"direction": "...",
  "ideas": [{"title": "...", "summary": "...", "from": ["<slug>"]}]}`. The parser takes the outermost
  `{...}` (a code fence around it is fine). Ideas: at most 3 kept, at least 1 needed; `from` slugs that
  are not items are dropped. Questions: at most 2; on the second run a reply with questions and no
  ideas fails, "asked again instead of answering".

### Timeline: `app.py`

- `*` (Textual key `asterisk`, shown in the keys line as `* spark`):
  - Claude Code not found: the same warning as `?`.
  - No item with status `idea`, `paused` or `dropped`: "No ideas to read yet. Press n to note one."
  - Running: "Still reading your ideas. A notice comes when it is done."
  - Asking: opens the questions. Ready: opens the ideas.
  - Otherwise (idle or failed): spawns `rumo spark` and says "Reading your ideas…".
- `check_changes` also watches `spark.json`. Moving out of `running` gives one notice: "rumo has a
  question before the ideas. Press *.", "3 new ideas. Press * to see them.", or "Spark failed: <reason>".
  On start, a spark waiting in `asking` or `ready` gives the same notice once.
- Questions screen: the direction, dim, then each question with an input. Tab moves; Enter on the last
  one sends, blank answers allowed; Esc closes and leaves the questions waiting. Sending writes the
  answers and spawns the second run.
- Ideas screen: "Your direction" and the direction, then the 3 ideas, each as a number, the title in
  bold, the summary, and "from <title>" dim. A digit, or space on the highlighted one, throws it away
  and back (`✕`, dim, struck). Enter keeps the others; Esc closes and leaves them waiting.
- Keeping: each kept idea is created with `items.create_idea(folder, title, group=<the group of its
  first from>, parent=<its first from>, context=<summary>)`; the thrown-away ones go to `discarded`;
  `ideas`, `questions`, `answers` and `state` are cleared. Notice "2 ideas added to Later." and the first
  new one is selected. New ideas stay unread ("new") in the list.

### Docs

README: the `*` key, what goes to Claude Code (the privacy note that covers `?`), and `spark.json`.
CONTRIBUTING: `spark.py` in the layout and this spec in the list.

## Testing Decisions

- `spark.py`, with a fake `claude` script as in `test_explore.py`: a reply with questions, then a second
  run with the answers in the prompt; a reply with ideas; a JSON reply inside a code fence; more than 3
  ideas cut to 3; unknown `from` slugs dropped; no ideas, garbage, a non-zero exit and a timeout each
  fail with a reason; questions on the second run fail; a dead `running` state reads as failed; the
  prompt lists ideas oldest first with drop reasons, the discarded suggestions, and the current work as
  titles only; an unreadable `spark.json` is kept as `.bad`.
- Timeline, with the pilot, a fake spawner and a prepared `spark.json`: `*` with no ideas; `*` while
  running; asking opens the questions, Enter writes the answers and spawns; ready opens the ideas,
  throwing one away and Enter creates 2 items with `from:`, group and Context and records the third in
  `discarded`; Esc leaves them waiting; the notices on each change and on start.
- CLI: `rumo spark` exits 1 on failure and stays out of the usage.

## Out of Scope

- Choosing how many ideas, a theme or an angle.
- Sparks from the agent (`/rumo`) or as a visible shell command.
- Asking for another idea in place of one thrown away (throw it away and press `*` again).
- Exploring a suggestion before keeping it (keep it, then `?`).

## Further Notes

- A spark costs one Claude Code run, two when it asks; about the same as a `?`.
