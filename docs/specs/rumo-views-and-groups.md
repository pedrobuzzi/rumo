# rumo views and groups — spec

Status: implemented, not released yet (local file; this project has no issue tracker yet)
Date: 2026-10-12
Builds on [rumo-v1.md](rumo-v1.md). Replaces its stories 4, 14, 15, 18 and 19 and the TUI color rules.

## Problem Statement

The list is hard to read at a glance. With real data:

- Rows wrap. A long next step runs past the pane and continues at column 0, so the list loses its shape and reads like running text.
- One item takes two or three lines (title, next step, the wrap), so six items become fifteen lines.
- The project shows up in three places (a header, the title color and the "PhraseDay:" at the start of the title) and stands out in none: everything has the same color and weight.
- Color means two things on the same row: the status (green `doing`, yellow `waiting`) and the group. In a common terminal theme the status yellow renders as the orange of a group.
- `doing` is repeated on almost every row, and the step count moves with the title length, so there is no column for the eye to follow.
- Two groups can share a color and nothing tells them apart.
- Past and Later always take space, even when only Now matters.

Other gaps:

- There is no way to see what is open in each repository, although every agent session records it.
- Groups can be created from the TUI but not renamed, recolored or removed, and removing one in the config file leaves items pointing to a group that no longer exists.
- Titles with a colon, written by the agent as `title: "PhraseDay: ASO"`, show the quotes.

## Solution

- **One item, one line**, plus one dim line with its next step when it has one. Nothing wraps: what does not fit ends in "…".
- **Fixed columns**: status symbol, project label, title, progress (or the completion date in Past).
- **The project label** is the group name on a background of the group color, always in the same column. Color and name together: two groups with similar colors are still told apart by the name.
- **Color only means the group.** Status is a gray symbol: `●` doing, `◐` waiting, `○` idea, `◌` paused, `✓` done, explained by a legend in the footer.
- **Titles stay plain** and, on screen, drop a leading project name that the label already shows ("PhraseDay: ASO e conversão" shows as "ASO e conversão"). The file keeps the full title.
- **One horizon at a time.** The list shows Now; `p` shows only Past and `p` again goes back to Now; `l` shows only Later and `l` again goes back. rumo always opens on Now. While a filter is active, matches from all three horizons are shown.
- **Three views**, switched with `v` (next) or `1` `2` `3`:
  1. **Timeline**: the items of the current horizon. In Now, items of the same project sit together, the most recent first.
  2. **Projects**: one block per group, opened by a line with the project label and the counts of its items in Past, Now and Later. Rows inside drop the label.
  3. **Repos**: one block per repository (the one of the item's latest agent session), opened by a line with the repository name and the counts. Rows keep the project label. Items without a session go to "No repo", last.
- rumo remembers the view between runs.
- **Group management lives in the `g` picker**: on a highlighted group, `r` renames it, `c` changes its color and `d` deletes it after a confirmation. Deleting a group removes it from every item that had it; the items stay.
- A more separated palette with the same eight color names. New groups start with a free color; the color picker shows which groups use each color.
- Quotes around frontmatter values are removed when reading.

## User Stories

### Reading the list

1. As a developer, I want each item on one line with its next step on one dim line below, so that I read the list without wrapped text.
2. As a developer, I want text that does not fit to end in "…" instead of wrapping, so that the list keeps its shape at any width.
3. As a developer, I want the status symbol, project label, title and progress in fixed columns, so that my eye can go straight down a column.
4. As a developer, I want the project shown as a label with the group name on the group color, always in the same column, so that I know the project at a glance.
5. As a developer, I want items without a group to have an empty label column, so that ungrouped work is still aligned.
6. As a developer, I want the title in the normal text color, so that it is easy to read on any project color.
7. As a developer, I want a leading project name removed from the title on screen when the label already shows it, so that I don't read the project twice.
8. As a developer, I want my files to keep their full titles, so that the trimming is only a display choice.
9. As a developer, I want status as a gray symbol (● doing, ◐ waiting, ○ idea, ◌ paused, ✓ done) with a legend in the footer, so that status takes two columns and no color.
10. As a developer, I want done items to show their completion date in the right column, so that Past answers "when did I finish it?".
11. As a developer, I want the next step shown under every item that has one, cut to one line, so that I know the next action without opening the item.

### Horizons

12. As a developer, I want rumo to open on Now, so that what I'm doing comes first.
13. As a developer, I want `p` to show only Past and `p` again to go back to Now, so that I can look back without the rest in the way.
14. As a developer, I want `l` to show only Later and `l` again to go back to Now, so that I can pick what to start next.
15. As a developer, I want `p` from Later to go straight to Past and `l` from Past straight to Later, so that I don't have to pass through Now.
16. As a developer, I want the horizons and their item counts shown under the list, with the current one highlighted, so that I know where I am and what is hidden.
17. As a developer, I want the horizon to apply in every view, so that the keys always mean the same thing.
18. As a developer, I want a filter to show matches from Past, Now and Later together, under their headers, so that a match is never hidden.
19. As a developer, I want Past to keep the 14-day window with a row to show older items, so that it stays short.
20. As a developer, I want an empty horizon to say so and suggest what to do, so that an empty list is never confusing.

### Views

21. As a developer, I want a Timeline view with the items of the current horizon, so that I see my work in time.
22. As a developer, I want Now in the Timeline to keep items of the same project together, the most recent first inside each project, so that labels form blocks of color.
23. As a developer, I want Past in date order and Later with paused before ideas, as today, so that the other horizons keep their meaning.
24. As a developer, I want a Projects view with one block per group, opened by a line with the project label and the counts of its Past, Now and Later items, so that I review one project at a time.
25. As a developer, I want rows inside a project block to drop the label, so that the project is not repeated on every row.
26. As a developer, I want a Repos view with one block per repository, opened by the repository name and its counts, so that I see what is open in each codebase.
27. As a developer, I want an item to belong to the repository of its latest agent session, so that it shows where I last worked on it.
28. As a developer, I want items without any session in a "No repo" block, last, so that ideas not started yet are still listed.
29. As a developer, I want rows in a repository block to keep the project label, so that I see which project each one belongs to.
30. As a developer, I want projects in config order and repositories in alphabetical order, with "No group" and "No repo" last, so that blocks don't move around.
31. As a developer, I want a block to appear only when it has items in the current horizon, so that empty blocks don't take space.
32. As a developer, I want `v` to go to the next view and `1`, `2`, `3` to open a view directly, so that switching takes one key.
33. As a developer, I want the current view name highlighted above the list, so that I always know which view I'm in.
34. As a developer, I want the selected item to stay selected when I switch views, so that I don't lose my place.
35. As a developer, I want the cursor to skip block lines and messages, so that moving through items stays fast.
36. As a developer, I want rumo to open in the view I last used, so that my preferred view survives a restart.
37. As a developer, I want a missing or broken remembered view to fall back to the Timeline, so that it never stops rumo from starting.

### Managing groups in the `g` picker

38. As a developer, I want the `g` picker to show each group with its color and how many items it has, so that I see my groups before choosing.
39. As a developer, I want to rename the highlighted group with `r`, starting from its current name, so that I fix a name without opening the config file.
40. As a developer, I want renaming to keep every item in the group, so that a rename never moves work around.
41. As a developer, I want to change the highlighted group's color with `c`, with its current color selected and the groups using each color named, so that I avoid repeating one.
42. As a developer, I want to delete the highlighted group with `d`, after a confirmation that says how many items lose the group, so that I don't delete one by accident.
43. As a developer, I want deleting a group to remove it from every item that had it, done items included, without deleting any item, so that no item points to a group that doesn't exist.
44. As a developer, I want a delete to stop, keep the group and tell me which item changed on disk if one of its items was edited meanwhile, so that no one's edit is lost.
45. As a developer, I want the picker to stay open after a rename, color change or delete, so that I can make several changes in a row.
46. As a developer, I want the picker to show which keys do what, so that I discover the actions.
47. As a developer, I want `g` to open the group list even with no item selected, to manage or create groups, so that groups can be set up before there is work in them.
48. As a developer, I want a group that cannot be edited automatically to show a message pointing to the config file, so that I know how to change it by hand.
49. As a developer, I want comments and everything else in my config file kept when a group changes, so that my hand edits survive.

### Colors and titles

50. As a developer, I want the eight group colors far enough apart, so that groups never look alike.
51. As a developer, I want the color names in the config to stay the same, so that my existing groups keep working.
52. As a developer, I want a new group to start with a free color, and the agent to propose a free color, so that groups stay distinct.
53. As a developer, I want a title written as `title: "PhraseDay: ASO"` shown without the quotes, in the TUI and in the agent index, so that agent quoting never shows up.

## Implementation Decisions

### Rows

- Each item row is a borderless grid with four columns: status symbol (2 cells), project label (as wide as the longest group name, up to 16 characters, plus one space on each side; longer names end in "…"; bold dark text on the group color; blank without a group), title (takes the rest, no wrap, ends in "…"), and the right column (6 cells, right-aligned: `done/total` steps, or the short completion date for done items). A second grid row holds the next step, prefixed with `↳ `, in the title column, dim, cut to one line. Rows without a next step have one line.
- Rows inside a Projects block drop the label column.
- The status symbol is the only status mark: the `doing` symbol in the normal text color, the others dim. The status picker and the detail pane use the same symbols with the word next to them.
- Display title: when the item has a known group and the title starts with that group's name (compared without case, accents, spaces or punctuation) followed by a non-alphanumeric character, the matched part and the separators after it (spaces, `:`, `-`, `–`, `—`, `·`, `|`, `/`) are dropped. An empty result keeps the full title. The detail pane always shows the full title.

### Horizons

- The TUI keeps the current horizon (`past`, `now`, `later`), starting at `now` and never remembered. `p` sets `past`, or `now` when already in `past`; `l` does the same for `later`.
- The horizons are shown under the list (the list's bottom border title) as `p Past N · Now N · l Later N`, with the current one highlighted. Past counts the items inside the 14-day window.
- While the filter text is not empty, every view shows the matching items of all three horizons: the Timeline under `Past`, `Now` and `Later` headers, and Projects and Repos with the rows of each block in Past, Now, Later order.
- Past in any view starts with the "… N older (enter to show)" row when older done items exist.
- An empty horizon shows one disabled message row: Past "Nothing finished in the last 14 days.", Now "Nothing in progress. Press l to see what is next.", Later "Nothing for later. Press n to capture an idea."

### Views

- Views are `timeline`, `projects`, `repos`, in that order for `v` and `1` `2` `3`. The view names are shown above the list (the list's top border title), the current one highlighted.
- **Timeline**: the rows of the current horizon. Now is ordered by group (config order, no group last) and, inside a group, as today (doing then waiting, most recent first). Past is oldest first; Later is paused then idea, most recent first.
- **Projects**: per group with items in the current horizon, a disabled block row (label, a rule filling the width, and the counts `N now · N later · N past`, leaving out zeros), then its rows without the label. Groups in config order; "No group" last.
- **Repos**: the repository of an item is the repository name of its last session line, without the branch. Per repository with items in the current horizon, a disabled block row (name in bold, the rule, the counts), then its rows with the label. Repositories in alphabetical order; "No repo" last.
- Block and message rows are disabled list options, so the cursor skips them. The selected item stays selected across views and horizons when it is still visible; otherwise the first item row is selected.
- The footer shows the status legend and the key hints (`v` view, `p` past, `l` later first). The group legend is gone: labels carry the names. The detail pane shows the group as the same label.
- The list takes 55% of the width (it was 45%), so titles are cut less.

### Remembered state

- A small JSON file named `state.json` next to the config file holds the view. It is written when the view changes and read at startup; read errors, unknown values or a missing file mean the Timeline; write errors are ignored.

### Group management in the `g` picker

- The `g` picker is a dedicated screen. Rows: each group (a bar in its color, name, item count), then "no group" and "+ new group…" when an item is selected. A hint line lists `enter assign · r rename · c color · d delete · esc close`.
- `enter` on a group assigns it to the item and closes the picker; digits `1`-`9` keep picking directly.
- `r` opens a text field with the current name and rewrites only that group's `name`. The key never changes, so items are not touched.
- `c` opens the color picker with the group's color highlighted and the names of the other groups next to each color; it rewrites only that group's `color`.
- `d` asks for confirmation, stating how many items lose the group. On confirm, the group is first removed from every item with that key through the existing set-group operation (which bumps `updated`); only when all succeed is the group's block removed from the config. A write conflict stops the delete, keeps the group and names the item.
- After `r`, `c` or `d` the picker reloads and stays open on the same row (or the nearest one after a delete).
- With no item selected, `g` opens the same screen titled "Groups", without "no group"; `enter` does nothing on groups, and "+ new group…" creates a group without assigning it.
- When creating a group, the color picker highlights the first color no group uses (when all are used, the least used one).

### Config

- Two operations beside "append a group": update a group (name and/or color) and delete a group. Both edit the file as text: find the `[groups.<key>]` table header (tolerating spaces and a quoted key), treat everything up to the next table header as the table, and replace the `name` or `color` line (adding it when missing) or remove the table. Everything else in the file is kept byte for byte.
- When the table cannot be found in that form, the operation raises a dedicated error and the TUI shows "Edit the group in <config path>".
- Palette (same names, new values): blue `#5b8cff`, cyan `#2ab7c9`, green `#4fc26b`, yellow `#d2c43c`, orange `#f08a3b`, red `#ee5f74`, magenta `#e070c8`, purple `#9d7cf2`.

### Items

- Reading the frontmatter strips one pair of matching surrounding quotes (`"…"` or `'…'`) from a value; inside double quotes, `\"` and `\\` are unescaped. Writing is unchanged.

### Agent resources

- The `/rumo` skill proposes a color no other group uses (the least used one when all are taken), and leaves renaming, recoloring and deleting groups to the user in the TUI unless asked.
- The README describes the views, the horizons and the keys.

## Testing Decisions

- Same principle as v1: drive the highest seam with real files in a temp folder and assert on files and on what the user sees; no assertions on private helpers.
- Rows are checked as plain text rendered at a fixed width (symbol, label, trimmed title, right column, next step, the "…" cut), not as colors.
- **TUI** (Textual pilot):
  - The app opens on Now; `p` and `l` switch to Past and Later and back; `p` from Later goes to Past; the horizon line highlights the current one and shows the counts.
  - Each view lists the expected row ids in the expected order for each horizon (Now clustered by group in the Timeline; blocks in config order or alphabetical order; "No group" and "No repo" last; blocks only with items in the horizon).
  - A filter shows matching items from all horizons.
  - An empty horizon shows its message.
  - The selected item stays selected across views; the remembered view is restored by a new app instance, and a broken state file falls back to the Timeline.
  - Group management in `g` as already covered: rename, recolor, delete with confirm and cancel, the conflict that keeps the group, manage mode with nothing selected.
- **Config**: update and delete keep the rest of the file byte for byte; unknown keys and inline tables raise the dedicated error.
- **Items**: quoted titles load without quotes; the index prints them unquoted.
- Colors are checked by eye in a real terminal.

## Out of Scope

- Agent subcommands to rename, recolor or delete groups.
- Showing more than one horizon at a time outside of a filter.
- Remembering the horizon between runs.
- Reordering groups from the TUI (order is the config file order).
- Custom colors outside the named palette, and per-theme palettes.
- Changing titles in the files (the trimming is display only).
- Linking groups to repositories automatically.

## Further Notes

- Decided by the user in the "Evolução do rumo" artifact, round 4: views Timeline, Projects and Repos (Grouped removed); a project label with a colored background; the next step under every item, cut to one line; status as a symbol with a footer legend; Now grouped by project; the project name hidden from titles on screen; one horizon at a time, with `p` and `l` toggling back to Now, in every view.
- Kept from earlier rounds: color only for the group, the palette, no repeated colors, group management inside `g`, remembering the view, quotes removed.
- Proposed by the assistant and not yet reviewed: the horizon line under the list, the empty-horizon messages, alphabetical repository order, the label width following the longest group name (up to 16), and the Repos block for items without sessions.
- Repository names come from the session lines, so an item last worked on before a folder was renamed shows the old name until a new session is linked.
- Release as 0.2.0, following [rumo-distribution.md](rumo-distribution.md), only after the user has tested locally.
