# rumo: the pull requests of each item

## Problem Statement

An item told what was done and what was left, but not where the code went: the PRs opened for it
lived in the forge, in the terminal history or in the user's memory. Finding the PR of a piece of
work, or knowing whether it was merged before calling the item done, meant leaving rumo.

## Solution

Each item lists its PRs in a `## PRs` section, one link per line with its state (open, draft,
merged, closed) and an optional title. rumo finds them on its own: the hook adds the PR a linked
session opens, and the TUI asks the GitHub CLI about the states and about the PRs opened from the
branches the item's sessions worked on. The agent updates a state when it learns one, and the user
adds, edits, opens and deletes PRs in the TUI. The list shows how many PRs of each item are open.

## User Stories

1. As a user, I want each item to list its PRs with a link and a state, so that I find the code of a piece of work from rumo.
2. As a user, I want a PR my agent opens in a linked session to land in the item without asking, so that no PR is forgotten.
3. As a user, I want rumo to find the PRs opened from the branches an item's sessions worked on, even when the session was linked on `main`, so that PRs opened outside the agent show up too.
4. As a user, I want the states kept current while the TUI is open, when `gh` is installed, so that a merge done by a reviewer shows without my help.
5. As a user, I want the agent to update a PR's state when it learns it, and to add a PR I give it.
6. As a user, I want to add a PR with `p`, and, with the PRs focused, open one in the browser with Enter, change its state with space, edit it with `e` and delete it with `d` (after confirming).
7. As a user, I want a PR I deleted never to be added back by rumo.
8. As a user, I want the detail to color each state the way forges do (green open, gray draft, purple merged, red closed) and to say the state in words.
9. As a user, I want the list row to say how many PRs of the item are still open.
10. As a user, I want `/rumo status` to show the PRs still open, and `/rumo done` to ask about them before finishing.
11. As a user, I want a linked session to be reminded of its open PRs on every prompt, so that it keeps their states.
12. As a user, I want links from any host to work, with short names for GitHub (`web-app#12`), GitLab (`api!34`) and Bitbucket.

## Implementation Decisions

- Items: `## PRs` lines are `- [state] <link> <title>`; the state and the title are optional (no state: unknown). A known host's link is kept up to its number (no `/files`, query or anchor); any other web link is kept as given. Adding skips a PR already listed and creates the section before Sessions. Edits go through the guarded write, like steps.
- Hook, Stop (linked sessions): the first PR link in the output of `gh pr create`, `glab mr create` or a tool named like `create_pull_request` / `create_merge_request` is added as open (draft with `--draft`/`-d` or `draft: true`); one in the output of `git push` (GitLab names the open merge request) is added without a state. Links that were only looked at (`gh pr view`, a web page) are not. With several linked items, the PR goes to the one the turn edited; when that is unclear, the agent is asked once to place it. Whether the agent updated an item is decided before rumo writes the PR, so the steps nudge still works.
- Hook, Stop: the repository and branch of the session's folder are noted per linked item in state.json (read from `.git`, worktrees included; `main` and `master` skipped; 10 per item).
- Hook, UserPromptSubmit: the reminder lists each linked item's PRs not merged or closed, and the rules say how to keep them.
- TUI: a PRs section between Steps and Sessions, hidden when there are none. With `gh` installed, a background thread asks it every 10 minutes (and at start) about the items not done: `gh pr list --head <branch> --state all` in each noted or session folder, then `gh pr view` for each other GitHub PR not merged or closed. New PRs are added, states updated and missing titles filled; a title already written is kept. A PR deleted in the TUI is remembered in state.json and never added again. Without `gh`, the section's title says the states are as written.
- The list row gets a `N PR(s)` column only when the item has PRs not merged or closed.

## Testing Decisions

- Items: the file after each PR operation and the links found in free text, through the public functions.
- rumo.prs: a fake gh (canned answers per branch and per link) checks what is asked, what is written and what is skipped; branch notes against fake `.git` folders.
- Hook: the real `rumo hook` process with transcripts holding tool outputs, as the existing hook tests do.
- TUI: Textual pilot tests through keys, and the background check with a fake gh.

## Out of Scope

- States from GitLab or Bitbucket (`glab` or their APIs); their PRs keep the states written.
- Reviews, checks and comments of a PR.
- Calling GitHub without `gh`.

## Further Notes

- Decided on 2026-10-14: Q1 a `## PRs` section with the state in brackets and an optional title; Q2 rumo finds them on its own; Q3 the agent updates states when it can, and the user edits them too; Q4 to Q6 as recommended.
