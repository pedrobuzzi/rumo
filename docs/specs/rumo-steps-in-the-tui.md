# rumo: steps in the TUI, and the agent knowing what is left

## Problem Statement

Steps were the agent's job: the TUI could only check them. To add, fix, reorder or drop one, the user
opened the whole file in an editor or asked an agent. And the agent knew little about steps it did not
write: a step added by hand reached a linked session only if it happened to be among the first three
open ones, a removed step could be added back, and a session without an item knew nothing of the work
in progress when the user asked "what is left?".

## Solution

The TUI edits steps: `a` adds one, and with the Steps list focused, Enter edits, shift+↑↓ moves, `d`
deletes (after confirming) and space checks. The agent hears about it: the next prompt of a linked
session lists what changed in its steps since the last answer. A session without an item learns at
its start what is in progress in its repository, and `rumo now` lists everything in progress. When the
agent finishes a step and more remain, it names the next one.

## User Stories

1. As a user, I want to press `a` on an item and type a step, so that I can note work without opening an editor.
2. As a user, I want a new step to land after the open steps, or right below the step I highlighted, so that it sits where it belongs.
3. As a user, I want to choose `[agent]` or `[manual]` with Tab in the same dialog, and to see that Tab does it, so that I mark who does the step without extra keys.
4. As a user, I want to edit a step's text and kind with Enter, keeping the text so I can fix one word.
5. As a user, I want to move a step with shift+↑↓, so that the first open step is the next action I chose.
6. As a user, I want `d` to delete a step only after I confirm, so that a stray key loses nothing.
7. As a user, I want the footer to show the step keys while the steps have focus, and `tab steps` otherwise, so that I discover them.
8. As a user, I want a done item to refuse new steps and tell me to reopen it, so that "done" keeps meaning every step is checked.
9. As a user, I want a linked session to be told which steps I added, removed, checked or unchecked since its last answer, so that it neither redoes nor re-adds them.
10. As a user, I want the agent not to start a step I added unless I ask.
11. As a user, I want a step I add while the agent works on something else to still be pointed out on my next message.
12. As a user, I want the agent's own edits not to be reported back to it as mine.
13. As a user, in a session without an item, I want the agent to know what is in progress in this repository (up to three items, with their next step), so that it can answer "what is left?".
14. As a user, in a repository with no item in progress, I want the agent to know how many items are in progress elsewhere and that `rumo now` lists them.
15. As a user, I want `rumo now` in the terminal: one line per item in progress with its status, next step and file.
16. As a user, when the agent checks off the step it was working on and more remain, I want it to end with the next one: an offer to start an `[agent]` step, or a note that a `[manual]` one is mine.

## Implementation Decisions

- Items: add, edit, delete and move steps through the same guarded write as every other change (refused when the file changed on disk, or when the step's line no longer holds it). A new step is always one line with a kind; adding to an item without a Steps section creates it before Sessions. Moving swaps with the neighbor, done or open.
- TUI: a step dialog with the text and a kind line (`[agent]  manual  tab switches · enter saves · esc cancels`). Delete reuses the confirmation picker of group deletion. The footer has two key lines and swaps them as the Steps list gains or loses focus.
- Hook, Stop: for a linked session, note the steps of each linked item as the agent has seen them (state.json, last 20 sessions). An item the agent did not edit in that turn keeps the steps noted before, so changes made meanwhile are still reported. This runs on every stop, including one after a nudge.
- Hook, UserPromptSubmit: compare each linked item's steps with that note and, once, list what was added, removed, checked or unchecked. The note is refreshed here only when the Stop hook already keeps one, so an older plugin without the Stop hook never reports the agent's own edits.
- Hook, SessionStart (new event in the plugin): for a session without a linked item, list the items in progress (doing, then waiting) with a session started inside this repository (the nearest folder with `.git`; outside a repository, the same folder only), up to three, with their next step and a pointer to `rumo now` and `/rumo link`; with none here, one line with the count elsewhere; with nothing in progress, nothing.
- The update rules ask the agent to name the next open step after checking one off; after a nudge it still ends without further text.
- `rumo now` is a hidden CLI command; the skill's `now` uses it.

## Testing Decisions

- Items: the file content after each step operation, through the public functions, including conflicts.
- TUI: Textual pilot tests through keys: adding with Tab, adding below the highlighted step, editing, moving, deleting with and without confirming, the done-item refusal and the footer swap.
- Hook: the real `rumo hook` process with Stop, UserPromptSubmit and SessionStart payloads against temporary items and repositories, as the existing hook tests do.

## Out of Scope

- Telling which steps were changed by the user and which by another agent session.
- Reporting reordered steps or edited text as such (an edit shows as a removed and an added step).
- Adding steps to several items at once.

## Further Notes

- Decided in the grilling of 2026-10-12: Q1 c (add, move, edit, delete), Q2 a, Q3 a (Tab, and say so), Q4 a, Q5 b, Q6 b, Q7 b, Q8 to Q15 as recommended.
