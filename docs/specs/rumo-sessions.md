# rumo: catching up on the sessions worked without rumo

## Problem Statement

An item is created or kept current from inside one session, at the moment the work happens. When the
user works on several things at once without rumo, or lets several sessions run without updating their
items, that work is never written down. Catching up means opening each session, remembering what it was
about and running `/rumo new` or `/rumo link` in each one, and nothing stops two sessions from creating
two items for the same thing.

## Solution

`/rumo sessions` reviews, from any session, every Claude Code session open or active since the last
review. A new CLI command, `rumo sessions`, reads the sessions Claude Code keeps on disk and prints a
short digest of each one: where it ran, what the user asked, what it last answered, the files it edited,
the PRs it opened, the item it is linked to and the items that worked on the same branch. From that
digest the agent matches each session to an existing item before thinking of a new one. It then shows
one short plan, grouped by what happens to each item, and writes nothing until the user confirms it.

## User Stories

1. As a user who worked in several sessions without rumo, I want one command that reviews them all, so that I catch up without opening each one.
2. As a user, I want the review to cover the sessions open now and those active since the last review (the last 3 days the first time), and to widen it with `/rumo sessions 7d`, so that each review sees only what is new.
3. As a user, I want each session matched to an existing item before a new one is proposed, so that the review never duplicates an item.
4. As a user, I want several sessions about the same thing to become one item with all of them linked.
5. As a user, I want quick questions and lookups to be proposed as ignored, so that the review does not create noise.
6. As a user, I want to see one structured, short plan (update, link to an existing item, create, ignore) before anything is written, and to adjust it by number or cancel it.
7. As a user, I want an open session that the review links to keep its item current from its next prompt, like any linked session.
8. As a user, I want sessions run by scripts (`claude -p`, the SDK) and sessions in temporary folders left out.
9. As a user, I want an item finished only when the plan says so and I confirm the plan.

## Implementation Decisions

### `rumo sessions [--since <N>d] [--reviewed <time>]`

- Reads Claude Code's folder: `$CLAUDE_CONFIG_DIR`, else `~/.claude`. Transcripts are
  `projects/<folder>/<session id>.jsonl` (subagent transcripts, nested deeper, are not sessions). Open
  sessions come from `sessions/<pid>.json` (`sessionId`, `cwd`, `status`) when its process is alive.
  Both formats are Claude Code's own and undocumented: anything unreadable is skipped, and without the
  registry every session counts as not open.
- A session is listed when its last activity (the last timestamp in its transcript) is after the cut:
  the time of the last review kept in state.json (`sessions_reviewed_at`), or 3 days ago the first time,
  or `--since`, which overrides both. An open session with nothing new since the cut was already
  reviewed and is left out.
- Left out always: `entrypoint` other than `cli` (`claude -p`, the SDK), a folder inside a temporary
  directory, fewer than 3 prompts from the user.
- At most the 20 most recent sessions; the header says how many more there are.
- Output, for the agent:

  ```
  # since: 2026-10-13 18:00   (last review)
  # now: 2026-10-15 09:12
  # sessions: 6 (3 open), 2 more before these: widen with --since
  ## 8923bfe4-5344-40f6-9f14-3dfb73e95046  open
  - last: 2026-10-15 09:10 · started 2026-10-12 12:09
  - where: rumo (main) · /Users/x/repo/rumo
  - title: <the title Claude Code gave the session>
  - linked: 2026-10-11-evoluir-o-tk-para-o-rumo (updated after this session's last activity)
  - closed: <slug of an item this session was linked to and closed>
  - same branch: <slug> (feature/pix)
  - prompts (41): first "<…>" · last: "<…>" / "<…>" / "<…>" / "<…>" / "<…>"
  - last answer: "<…>"
  - edited: src/rumo/app.py, tests/test_app.py (+3 more)
  - prs: https://github.com/acme/web-app/pull/12
  - line: - 2026-10-15 09:12 | rumo (main) | 8923bfe4-… | /Users/x/repo/rumo
  ```

  Lines with nothing to say are omitted. Prompts are the user's own messages (no tool results), a slash
  command shown as `/name args`, each cut to 200 characters; the last answer is the agent's last text,
  cut to 400. `edited` lists the files written with Edit, Write and NotebookEdit, relative to the
  session's folder, up to 8. `prs` uses the hook's own detection of opened PRs. `same branch` lists the
  items not done whose session lines or noted branches (`pr_branches`) have the same repository and
  branch, shared branches (`main`, `master`, `develop`, `dev`, `trunk`) excluded. `line` is the session
  line ready to append: now, the repository and branch the session was last on, its id and the folder it
  started in (where `claude --resume` finds it).
- Transcripts are read line by line, never whole, and only the lines that matter are parsed.
- `--reviewed <time>` saves that time as the cut and prints nothing; the agent passes the `# now:` of
  the listing it reviewed, so a session active during the review is seen again next time.
- `rumo sessions` without Claude Code's folder says so and exits 0.

### Skill: `/rumo sessions [N d]`

Only in full mode (Claude Code); in simple mode it says it needs Claude Code. In both menus.

1. Run `rumo sessions` (with `--since` when given).
2. Decide each session, in this order:
   1. Linked (a line without `| closed`): update that item. If the item was updated after the session's
      last activity, it is up to date and nothing changes.
   2. `same branch`: link to that item.
   3. An item about the same subject (title, Left off, Context), favoring `doing`, `paused` and `idea`:
      link, marked as likely.
   4. Several sessions about the same thing: one new item with all of them linked.
   5. Nothing similar: a new item.
   6. A quick question or lookup: ignore.

   A session is never linked again to an item it closed; its newer work is matched like any session
   without an item.
3. Show the plan and wait:

   ```
   6 sessions (3 open, 3 recent) since 13/10 18:00

   Update
    1. Evoluir o tk para o rumo ← rumo-aa (open) · new Left off, +2 done, +1 step
   Link to an existing item
    2. Pix no checkout (paused → doing) ← web-app feature/pix · same branch
   Create
    3. Jogo de associação de idiomas ← 2 sessions in language-game-association
   Ignore
    4. repo-03 · a quick question about git
   Up to date: 1

   Apply? y = all · "without 2" · "3 links to <item>" · n = cancel
   ```

   Written in the configured language. Each line names the item, the sessions (by repository and
   branch, open ones marked) and the change in a few words: status, Left off, steps done and added, PRs.
   `done` appears only for work the sessions clearly finished.
4. After "y" or an adjustment, apply: append each session's `line` to its item's Sessions, update Left
   off, Steps, PRs and `updated`, create the new items (format and rules of `new`; a group only when an
   existing one clearly fits), set the statuses in the plan. After "n", write nothing.
5. Run `rumo sessions --reviewed <now>`, unless the plan was cancelled.

An open session linked this way gets its item in the hook's reminder from its next prompt and keeps it
current from then on.

## Testing Decisions

- `rumo sessions`: the real CLI against a fake Claude Code folder built in the test (registry files,
  transcripts with prompts, answers, edits, `gh pr create` outputs, `claude -p` and temporary-folder
  sessions): what is listed and what is left out, the cut (state, `--since`, `--reviewed`), the cap,
  `linked`, `closed`, `same branch`, `line`, a broken transcript and a dead process.
- Skill: a live run of `/rumo sessions` over a fake folder with one linked session, one on the same
  branch as a paused item, two about the same new subject and one quick question: the plan groups
  them as expected and nothing is written before the answer.

## Out of Scope

- The hook suggesting `/rumo sessions` when several sessions have no item.
- A shortcut in the TUI.
- Sessions of other agents (Codex, Cursor) and of other computers.

## Further Notes

- Decided on 2026-10-15: Q1 the name `sessions`; Q2 open sessions plus those active since the last
  review (3 days the first time, `7d` widens); Q3 to Q8 as recommended (match order, the digest from the
  CLI, one plan with adjustments by number, open sessions linked normally, `done` only when in the plan,
  hook suggestion and TUI shortcut later).
