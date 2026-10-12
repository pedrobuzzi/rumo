# rumo views and groups — spec

Status: implemented, not released yet (local file; this project has no issue tracker yet)
Date: 2026-10-12
Builds on [rumo-v1.md](rumo-v1.md). Replaces its stories 14, 15, 18 and 19 and the TUI color rules.

## Problem Statement

The Timeline uses color for two things on the same row: the status word (green for `doing`, yellow for `waiting`) and the title (the group color). With real data the two collide. In a common terminal theme the status yellow renders orange, the same orange as a group, so a waiting item of that group reads as one color, and the eye cannot tell which color means what. Two groups can also end up with the same color, and nothing warns about it.

There is a single way to look at the work, a flat list by time. A developer with several projects running in parallel cannot see one project at a time, and Past and Later always take space even when only Now matters.

Groups can be created from the TUI but not renamed, recolored or removed: the user has to edit the config file, and removing a group there leaves items pointing to a group that no longer exists.

Titles with a colon, written by the agent as `title: "PhraseDay: ASO"`, show the quotes in the TUI and in the agent index.

## Solution

- **Color means group, and only group.** Status loses its color everywhere and becomes a gray shape plus word: `● doing`, `◐ waiting`, `○ idea`, `◌ paused`, `✓` with the date in Past. The section already tells half of the status (Now holds doing and waiting, Later holds idea and paused, Past holds done).
- **Three views of the same items**, switched with one key:
  1. **Timeline**: the flat list by time, as today. A bar `▌` in the group color starts each row; the title is in the normal text color.
  2. **Grouped**: Past in date order with the title in the group color. Now and Later in blocks per group: a header with the group name in its color, and the items below with the title in the group color.
  3. **Projects**: one block per group, holding that group's Past, Now and Later items together, titles in the normal text color.
- `v` goes to the next view; `1`, `2`, `3` go straight to a view. The name of the current view is visible at the top of the list.
- `p` and `l` collapse and expand Past and Later, in every view. A collapsed section shows how many items it hides. While a filter is active, collapsed sections open by themselves so no match stays hidden.
- rumo remembers the view and the collapsed sections between runs.
- **Group management lives in the `g` picker**: on a highlighted group, `r` renames it, `c` changes its color and `d` deletes it after a confirmation. Deleting a group removes it from every item that had it; the items themselves stay.
- A new, more separated palette with the same eight color names. A color is not reused while a free one exists: the color picker marks colors in use, and the agent always picks a free one.
- Quotes around frontmatter values are removed when reading.

## User Stories

### Status without color

1. As a developer, I want status shown as a gray shape and word, so that color on a row only ever means the group.
2. As a developer, I want the shape to go from full (doing) to half (waiting) to empty (idea) to dotted (paused), so that I read how active an item is at a glance.
3. As a developer, I want done items in Past to show a check and their completion date, so that I see when each thing was finished.
4. As a developer, I want the status picker (`s`) and the detail pane to use the same shapes and no status colors, so that the rule holds on every screen.

### Views

5. As a developer, I want a Timeline view where each row starts with a bar in its group color and the title is in the normal text color, so that I read titles easily and still see the group.
6. As a developer, I want a Grouped view where Now and Later are split into blocks per group, so that I see what is going on in each project.
7. As a developer, I want Past in the Grouped view to stay in date order, with the title in the group color, so that it still answers "what did I finish recently?".
8. As a developer, I want a Projects view with one block per group holding its Past, Now and Later items, so that I review a whole project at once.
9. As a developer, I want each group header in the Projects view to show how many items it has in Past, Now and Later, so that I see the size of each project.
10. As a developer, I want `v` to switch to the next view, so that changing views takes one key.
11. As a developer, I want `1`, `2` and `3` to open a view directly, so that I don't cycle through views I don't want.
12. As a developer, I want the current view name shown at the top of the list, so that I always know which view I'm in.
13. As a developer, I want groups in the same order in every view and section (the order of the config file), so that each project is always in the same place.
14. As a developer, I want items without a group to have no bar in the Timeline and to sit in a "No group" block, last, in the other views, so that ungrouped work is still visible.
15. As a developer, I want a group to appear in a section only when it has items there, so that empty headers don't take space.
16. As a developer, I want the selected item to stay selected when I switch views, so that I don't lose my place.
17. As a developer, I want the cursor to skip group and section headers, as it skips section headers today, so that moving through items stays fast.
18. As a developer, I want a footer legend with every group name next to a bar in its color, in every view, so that I learn the colors.
19. As a developer, I want the filter (`/`) to work the same way in every view, so that I can narrow any view to one topic.

### Collapsing Past and Later

20. As a developer, I want `p` to collapse and expand Past, so that I can focus on current work.
21. As a developer, I want `l` to collapse and expand Later, so that ideas don't distract me.
22. As a developer, I want collapsing to work in every view, including inside each project block of the Projects view, so that the keys always mean the same thing.
23. As a developer, I want a collapsed section header to say how many items it hides and which key opens it, so that I know what is there.
24. As a developer, I want collapsed sections to open by themselves while a filter is active, so that a match is never hidden.
25. As a developer, I want the selection to move to the first Now item when the selected item is collapsed away, so that something visible is always selected.
26. As a developer, I want rumo to open in the view I last used, with Past and Later as I left them, so that my setup survives a restart.
27. As a developer, I want a missing or broken remembered state to fall back to the Timeline with everything open, so that it never stops rumo from starting.

### Managing groups in the `g` picker

28. As a developer, I want the `g` picker to show each group with a bar in its color and how many items it has, so that I see my groups before choosing.
29. As a developer, I want to rename the highlighted group with `r`, starting from its current name, so that I fix a name without opening the config file.
30. As a developer, I want renaming to keep every item in the group, so that a rename never moves work around.
31. As a developer, I want to change the highlighted group's color with `c`, with its current color selected, so that I can fix colors that look alike.
32. As a developer, I want the color picker to mark which group already uses each color, so that I avoid repeating one.
33. As a developer, I want to delete the highlighted group with `d`, after a confirmation that says how many items lose the group, so that I don't delete one by accident.
34. As a developer, I want deleting a group to remove it from every item that had it, done items included, without deleting any item, so that no item points to a group that doesn't exist.
35. As a developer, I want a delete to stop, keep the group, and tell me which item changed on disk if one of its items was edited meanwhile, so that I can retry without losing anyone's edit.
36. As a developer, I want the picker to stay open after a rename, color change or delete, showing the updated groups, so that I can do several changes in a row.
37. As a developer, I want the picker to show which keys do what (`enter` assign, `r` rename, `c` color, `d` delete), so that I discover the actions.
38. As a developer, I want `g` to open the group list even with no item selected, to manage or create groups, so that groups can be set up before there is work in them.
39. As a developer, I want `r`, `c` and `d` to do nothing on "no group" and "+ new group…", so that a stray key changes nothing.
40. As a developer, I want a group whose config block cannot be edited automatically (for example written as an inline table) to show a message pointing to the config file, so that I know how to change it by hand.
41. As a developer, I want comments and everything else in my config file kept when a group is renamed, recolored or deleted, so that my hand edits survive.

### Colors

42. As a developer, I want the eight group colors far enough apart that yellow and orange, red and orange, and purple and magenta are easy to tell apart, so that groups never look alike.
43. As a developer, I want the color names in the config to stay the same, so that my existing groups keep working.
44. As a developer, I want a new group to start with a free color selected in the picker, so that the obvious choice avoids repeats.
45. As a developer, I want the agent to always propose a color not used by another group, and repeat one only when all eight are in use, so that suggested groups stay distinct.

### Titles

46. As a developer, I want a title written as `title: "PhraseDay: ASO"` shown as `PhraseDay: ASO`, so that the quotes an agent adds around a colon never show up.
47. As a developer, I want the agent index to show titles without those quotes too, so that the agent sees the same title I do.

## Implementation Decisions

### Status rendering

- One map from status to shape: `doing ●`, `waiting ◐`, `idea ○`, `paused ◌`, `done ✓`. Rows show shape and word in the dim text color; the `doing` shape uses the normal text color, which is the only emphasis. Past rows show `✓` and the short completion date instead of the word.
- The status color map is removed. The status picker lists shape and word; the detail pane shows the status the same way and the group as a bar plus name in its color. The detail pane title uses the normal text color.

### Views

- A view is one of `timeline`, `grouped`, `projects`. The TUI keeps the current view and two flags, Past open and Later open, and rebuilds the list from them. Sorting stays as in v1: Past oldest first so it flows into Now, Now by doing then waiting, Later by paused then idea, each by recency.
- Group order is the order of the `[groups.*]` tables in the config file; "No group" comes last. A group key with no config entry is treated as no group, as in v1.
- **Timeline**: v1 sections; each row starts with a one-character bar in the group color (blank without a group); titles in the normal text color.
- **Grouped**: Past as in the Timeline but without the bar and with the title in the group color. Now and Later: per group, a header row (group name in its color, item count dim) followed by its items, indented one column, titles in the group color. Now items keep the next-step line.
- **Projects**: per group, a header row with the name in its color and counts (`2 past · 3 now · 1 later`; a collapsed part gets `▸`), followed by its Past, Now and Later items in that order, indented one column, titles in the normal text color. Now items keep the next-step line. The "… N older" line appears once, above the first block, when Past is open.
- Section and group headers are disabled list rows, so the cursor skips them as it does today.
- The current view is shown above the list as the three names with the current one highlighted (for example in the list border title).
- Keys on the main screen: `v` next view, `1` `2` `3` a view directly, `p` toggle Past, `l` toggle Later. The footer key hints list `v`, `p` and `l` first. Now never collapses.
- A collapsed section keeps its header with `▸` and `N hidden · p opens` (or `l`); an open one shows `▾`. While the filter text is not empty, both sections render open; the flags themselves do not change.
- The legend shows a bar in the group color and the group name, in every view.

### Remembered state

- A small JSON file named `state.json` next to the config file holds the view and the two flags. It is written when one of them changes and read at startup. Read errors, unknown values or a missing file mean the defaults (Timeline, both open); write errors are ignored. The "show older" toggle is not remembered.
- The state is not stored in `config.toml`, so the user's config is only changed by explicit group actions.

### Group management in the `g` picker

- The `g` picker becomes a dedicated screen. Rows: each group (bar in its color, name, item count across all statuses), then "no group" and "+ new group…" when an item is selected. A hint line lists `enter assign · r rename · c color · d delete · esc close`.
- `enter` on a group assigns it to the item and closes the picker; digits `1`-`9` keep picking directly, as today.
- `r` opens a text field with the current name; saving rewrites only the `name` of that group. The key never changes, so items are not touched.
- `c` opens the color picker with the group's current color highlighted; each color shows the names of the groups using it. Saving rewrites only the `color` of that group.
- `d` opens a confirmation stating the group name and how many items lose it, with "delete group" and "cancel". On confirm, the group is first removed from every item with that key through the existing set-group operation (which bumps `updated`); only when all succeed is the group's block removed from the config. If an item raises a write conflict, the delete stops, the group stays, and a notification names the item.
- After `r`, `c` or `d` the picker reloads and stays open, keeping the highlight on the same row (or the nearest one after a delete).
- With no item selected (empty folder, or nothing visible), `g` opens the same screen in manage mode: titled "Groups", without "no group", `enter` does nothing on groups, and "+ new group…" creates a group without assigning it.
- When creating a group, the color picker highlights the first color no group uses.

### Config

- Two new operations beside "append a group": update a group (name and/or color) and delete a group. Both edit the file as text, like the append: find the `[groups.<key>]` table header (tolerating spaces and a quoted key), treat everything up to the next table header as the table, and replace the `name` or `color` line inside it (adding it when missing) or remove the table. Everything else in the file is kept byte for byte.
- When the table cannot be found in that form, the operation raises a dedicated error and the TUI shows "Edit the group in <config path>".
- The palette keeps its eight names and gets new values: blue `#5b8cff`, cyan `#2ab7c9`, green `#4fc26b`, yellow `#d2c43c`, orange `#f08a3b`, red `#ee5f74`, magenta `#e070c8`, purple `#9d7cf2`.

### Items

- Reading the frontmatter strips one pair of matching surrounding quotes (`"…"` or `'…'`) from a value; inside double quotes, `\"` and `\\` are unescaped. Writing is unchanged.

### Agent resources

- The `/rumo` skill's group rule says: propose a color no other group uses; only when all eight are in use, reuse the least used one. The skill does not rename, recolor or delete groups; the user does that in the TUI or asks the agent to edit the config by hand.
- The README key table adds `v`, `1` `2` `3`, `p`, `l`, and the `r` / `c` / `d` keys inside `g`.

## Testing Decisions

- Same principle as v1: drive the highest seam with real files in a temp folder and assert on files and what the user sees; no assertions on private helpers.
- **TUI** (Textual pilot, existing seam):
  - `v` cycles Timeline → Grouped → Projects → Timeline, and `1` `2` `3` jump; assert the row ids of each view (group header rows exist only in Grouped and Projects, in config order, with "No group" last).
  - The selected item stays selected across view switches.
  - `p` and `l` hide and show Past and Later rows in each view; the collapsed header reports the hidden count; a filter shows matches from a collapsed section.
  - A new app instance with the same config path opens in the remembered view with the remembered flags; a corrupt state file falls back to the defaults.
  - In the `g` picker: `r` renames (config name changes, item file unchanged, legend shows the new name); `c` recolors (config color changes); `d` then confirm removes the group from the config and from the item, and the item file still exists; `d` then cancel changes nothing; the picker stays open after each action.
  - `g` with an empty folder opens manage mode and can create a group.
  - The status shown on a row is shape plus word (checked on the rendered row text, not on colors).
- **Config**: update and delete keep comments and the other groups byte for byte; a quoted or spaced table header is found; an unknown key or an inline table raises the dedicated error.
- **Items**: a quoted title, a single-quoted title and a title with an escaped quote load without the quotes; the index prints them unquoted.
- Colors themselves are not asserted; they are checked by eye in a real terminal.

## Out of Scope

- Agent subcommands to rename, recolor or delete groups.
- Collapsing Now, or collapsing single groups.
- Reordering groups from the TUI (order is the config file order).
- Custom colors outside the named palette, and per-theme (light/dark terminal) palettes.
- Removing the project name from existing titles (the user chose to keep it).
- Changing the color of any existing group automatically (the user can do it with `g` → `c`).

## Further Notes

- Decided by the user in the "Evolução do rumo" artifact (rounds 2 and 3): color only for the group; the three views (Timeline with a bar and neutral title, Grouped with Past by date, Projects); switching with `v` and `1`-`3`; collapsing Past and Later with `p` and `l` in every view; filter opens collapsed sections; remembering view and collapsed sections; shape plus word for status; the new palette; no repeated colors; keeping the project name in titles; group management inside `g` instead of a separate key.
- Proposed by the assistant and **not yet explicitly reviewed**: the `r` / `c` / `d` keys inside the picker and its hint line, manage mode when nothing is selected, `state.json` next to the config, the Projects header counts, the exact palette values, Past staying oldest first (the prototype showed it newest first).
- Deleting a group bumps `updated` on its items, so they move up within their status in Now and Later.
- Release as 0.2.0, following [rumo-distribution.md](rumo-distribution.md) and only after the user has tested locally.
