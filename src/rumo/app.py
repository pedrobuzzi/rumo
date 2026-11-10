"""The `rumo` TUI: a Timeline (Past / Now / Later) on the left, the selected item on the right."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

from rich.cells import cell_len
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Label, ListItem, ListView, Markdown, OptionList, SelectionList, Static
from textual.widgets.option_list import Option

from rumo import config, explore, git, items, prs, spark
from rumo.items import Item, WriteConflict

# Color only ever means the group, so a status is a shape: full when active, empty when not started.
SHAPES = {"doing": "●", "waiting": "◐", "idea": "○", "paused": "◌", "done": "✓", "dropped": "✕"}
# Who acts on a step, in the list and in the detail: what is yours stands out, the agent's stays quiet.
KIND_STYLES = {"manual": "yellow", "agent": "dim", "waiting": "cyan"}
KIND_WIDTH = max(map(len, items.KINDS)) + 1  # the step texts under an item line up
VIEWS = {"timeline": "Timeline", "projects": "Projects", "repos": "Repos", "tree": "Tree"}
HORIZONS: tuple[str, ...] = ("past", "now", "later")
HORIZON_OF = {"doing": "now", "waiting": "now", "idea": "later", "paused": "later", "done": "past", "dropped": "past"}
CYCLE = ("now", "later", "past")  # what `h` steps through, starting where rumo opens
HORIZON_SAYS = {
    "now": "doing and waiting",
    "later": "ideas and paused work",
    "past": f"done and dropped in the last {items.PAST_WINDOW_DAYS} days",
}
IDLE_FADE_DAYS = 60  # an idea untouched this long fades in the list: time to decide
UNGROUPED = "-"  # the "No group" or "No repo" block, which always comes last
MAX_LABEL = 16  # the project label fits the longest group name up to this many characters
NEW_GROUP = "+new"
NO_GROUP = "-none"

def key_line(keys: list[tuple[str, str]], width: int = 0) -> Text:
    """`v view  h now/later/past ...`, broken into lines of `width` between keys, never inside one."""
    text, used = Text(), 0
    for key, label in keys:
        size = cell_len(f"{key} {label}")
        if used and width and used + size > width:
            text.append("\n")
            used = 0
        text.append(key, "bold").append(f" {label}  ", "dim")
        used += size + 2
    return text


KEYS = [
    ("v", "view"), ("h", "now/later/past"), ("s", "status"), ("g", "group"), ("t", "tags"), ("n", "new idea"),
    ("b", "branch"), ("?", "ask"), ("*", "spark"), ("a", "add step"), ("p", "add PR"), ("tab", "steps"), ("x", "hide done"), ("e", "edit"), ("c", "copy resume"),
    ("/", "filter"), ("q", "quit"),
]
STEP_KEYS = [  # while the Steps list has focus
    ("a", "add"), ("b", "to item"), ("enter", "edit"), ("shift+↑↓", "move"), ("d", "delete"), ("space", "check"),
    ("x", "hide done"), ("esc", "back"),
]
PR_KEYS = [  # while the PRs list has focus
    ("p", "add"), ("enter", "open"), ("space", "state"), ("e", "edit"), ("d", "delete"), ("esc", "back"),
]
FAMILY_KEYS = [("enter", "go to"), ("esc", "back")]  # while the Family list has focus
EXPLORATION_KEYS = [("enter", "read"), ("?", "ask"), ("esc", "back")]  # while the Explorations list has focus
# The detail pane is not the list, so here a color may mean a state: the ones forges use for PRs.
PR_COLORS = {"open": config.PALETTE["green"], "draft": "dim", "merged": config.PALETTE["purple"],
             "closed": config.PALETTE["red"], "": "dim"}
PR_CHECK_SECONDS = 600  # how often gh is asked about the PRs while the TUI is open
GIT_CHECK_SECONDS = 10  # how often git is asked what is left in the items' repositories (local, cheap)
GIT_STYLE = "yellow"  # what git has left is yours to commit or push, like a manual step


def status_text(status: str, label: str | None = None) -> Text:
    """`● doing`: the shape stands out only while the item is being worked on."""
    return Text.assemble((SHAPES[status], "" if status == "doing" else "dim"), (f" {label or status}", "dim"))


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def short_date(stamp: str) -> str:
    """`2026-10-06 17:00` -> `Oct 06`: unambiguous in any country, unlike 10/06."""
    try:
        return f"{MONTHS[int(stamp[5:7]) - 1]} {stamp[8:10]}"
    except (ValueError, IndexError):
        return stamp[:10]


TREE_EMPTY = "No families here. Press b on an item to start one: an idea from it, or a step as an item."
EMPTY = {
    "past": f"Nothing done or dropped in the last {items.PAST_WINDOW_DAYS} days.",
    "now": "Nothing in progress. Press h to see what is next.",
    "later": "Nothing for later. Press n to capture an idea.",
}


def family_trees(all_items: list[Item]) -> list[list[tuple[Item, str, str]]]:
    """Each family (an item, the items that came from it and theirs) as rows of (item, the connector before
    its title, the prefix of the lines under it), parent first, children in file order. Items whose `from:`
    point at each other still show, once."""
    by_slug = {i.slug: i for i in all_items}
    kids: dict[str, list[Item]] = {}
    for i in all_items:
        if i.from_slug in by_slug and i.from_slug != i.slug:
            kids.setdefault(i.from_slug, []).append(i)
    seen: set[str] = set()

    def walk(item: Item, head: str, cont: str, out: list[tuple[Item, str, str]]) -> None:
        seen.add(item.slug)
        children = [c for c in kids.get(item.slug, []) if c.slug not in seen]
        out.append((item, head, cont + ("│ " if children else "  ")))
        for n, child in enumerate(children):
            if child.slug not in seen:  # a cycle may have reached it through a sibling
                last = n == len(children) - 1
                walk(child, cont + ("└ " if last else "├ "), cont + ("  " if last else "│ "), out)

    families = []
    roots = [i for i in all_items if i.slug in kids and i.from_slug not in by_slug]
    for root in roots + [i for i in all_items if i.slug in kids]:  # the second pass picks up the cycles
        if root.slug not in seen:
            family: list[tuple[Item, str, str]] = []
            walk(root, "", "", family)
            families.append(family)
    return families


def repo_of(item: Item) -> str:
    """The repository of the item's latest session, without the branch: `web-app (main)` -> `web-app`."""
    return re.sub(r"\s*\([^)]*\)$", "", item.sessions[-1].repo).strip() if item.sessions else ""


def display_title(title: str, project: str) -> str:
    """Drop a leading project name that the label already shows: `PhraseDay: ASO` -> `ASO`.

    The name is compared without case, accents, spaces or punctuation, so `Word Fall 1.3: cesta`
    loses `Word Fall` for the project `WordFall`. The file keeps the full title.
    """
    want = _letters(project)
    if not want:
        return title
    i = matched = 0
    while i < len(title) and matched < len(want):
        letter = _letters(title[i])
        if letter:
            if letter != want[matched : matched + len(letter)]:
                return title
            matched += len(letter)
        i += 1
    if matched < len(want) or (i < len(title) and title[i].isalnum()):
        return title
    rest = title[i:].lstrip(" :-–—·|/")
    return rest or title


def _letters(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


# ---------- pickers ----------


class ChoiceScreen(ModalScreen[str | None]):
    """Pick one value; digits 1-9 pick directly, Esc cancels."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, title: str, choices: list[tuple[str, Text]], current: str | None = None, hint: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.current = current
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield OptionList(*self.options())
            if self.hint:
                yield Label(Text(self.hint, "dim"), classes="dialog-hint")

    def options(self) -> list[Option]:
        options = []
        for n, (value, label) in enumerate(self.choices, start=1):
            prefix = f"{n}  " if n <= 9 else "   "
            options.append(Option(Text.assemble((prefix, "dim"), label), id=value))
        return options

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        values = [v for v, _ in self.choices]
        if self.current in values:
            option_list.highlighted = values.index(self.current)
        option_list.focus()

    def on_key(self, event) -> None:
        if event.character and event.character.isdigit() and event.character != "0":
            n = int(event.character)
            if n <= len(self.choices):
                event.stop()
                self.pick(self.choices[n - 1][0])

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """The lists grow with the item and the detail pane scrolls: keep the highlighted row in view."""
        if event.item is not None and event.list_view.has_focus:
            event.item.scroll_visible(animate=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.pick(event.option.id)

    def pick(self, value: str | None) -> None:
        self.dismiss(value)


class GroupScreen(ChoiceScreen):
    """The `g` picker: Enter assigns the highlighted group; r, c and d rename, recolor or delete it.

    With no item (nothing selected) it only manages groups. It stays open after each change.
    """

    BINDINGS = [
        Binding("r", "rename", "rename"),
        Binding("c", "recolor", "color"),
        Binding("d", "delete", "delete"),
    ]

    def __init__(self, owner: RumoApp, item: Item | None) -> None:
        title = f"Group of “{item.title}”" if item else "Groups"
        hint = ("enter assign · " if item else "") + "r rename · c color · d delete · esc close"
        super().__init__(title, [], (item.group or NO_GROUP) if item else None, hint)
        self.owner = owner
        self.item = item
        self.choices = self.group_choices()

    def group_choices(self) -> list[tuple[str, Text]]:
        counts = Counter(i.group for i in self.owner.items.values())
        choices = [
            (g.key, Text.assemble(("▌ ", config.PALETTE.get(g.color, "")), g.name, (f"  {plural(counts[g.key], 'item')}", "dim")))
            for g in self.owner.cfg.groups.values()
        ]
        if self.item:
            choices.append((NO_GROUP, Text("no group", "dim")))
        choices.append((NEW_GROUP, Text("+ new group…", "italic")))
        return choices

    def pick(self, value: str | None) -> None:
        if self.item is None and value in self.owner.cfg.groups:
            return  # nothing to assign the group to
        self.dismiss(value)

    def highlighted_group(self) -> config.Group | None:
        index = self.query_one(OptionList).highlighted
        return self.owner.cfg.groups.get(self.choices[index][0]) if index is not None and index < len(self.choices) else None

    def refresh_choices(self) -> None:
        option_list = self.query_one(OptionList)
        index = option_list.highlighted
        self.choices = self.group_choices()
        option_list.clear_options()
        option_list.add_options(self.options())
        if index is not None:
            option_list.highlighted = min(index, len(self.choices) - 1)

    def action_rename(self) -> None:
        if (group := self.highlighted_group()) is None:
            return

        def renamed(name: str | None) -> None:
            if name and name != group.name:
                self.owner.edit_group(group.key, name=name)
            self.refresh_choices()

        self.app.push_screen(TextScreen(f"Rename “{group.name}”", value=group.name), renamed)

    def action_recolor(self) -> None:
        if (group := self.highlighted_group()) is None:
            return

        def colored(color: str | None) -> None:
            if color and color != group.color:
                self.owner.edit_group(group.key, color=color)
            self.refresh_choices()

        self.app.push_screen(ChoiceScreen(f"Color for “{group.name}”", self.owner.color_choices(group.key), group.color), colored)

    def action_delete(self) -> None:
        if (group := self.highlighted_group()) is None:
            return
        n = sum(i.group == group.key for i in self.owner.items.values())

        def confirmed(answer: str | None) -> None:
            if answer == "delete":
                self.owner.delete_group(group.key)
            self.refresh_choices()

        question = f"Delete group “{group.name}”? {plural(n, 'item')} {'loses' if n == 1 else 'lose'} the group; the items stay."
        choices = [("delete", Text("delete group", "bold")), ("cancel", Text("cancel"))]
        self.app.push_screen(ChoiceScreen(question, choices), confirmed)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def seen_key(step: items.Step) -> str:
    """How rumo remembers a step you saw: a short hash of its text, and whether it was done."""
    return f"{hashlib.sha1(step.text.encode('utf-8')).hexdigest()[:10]}:{int(step.done)}"


def seen_keys(item: Item) -> list[str]:
    """What you saw of an item: its steps (`seen_key`) and its answered explorations (`x<id>`)."""
    return [seen_key(s) for s in item.steps] + [f"x{e.id}" for e in item.explorations if e.id and e.state == "done"]


def news_label(news: dict[str, str] | None) -> str:
    """`+2 ✓1 1 answer`: steps added and done, and answers that landed, since you last looked; `new` for an
    item you never saw."""
    if news is None:
        return "new"
    added, done, answers = (sum(n == kind for n in news.values()) for kind in ("new", "done", "answer"))
    parts = (f"+{added}" if added else "", f"✓{done}" if done else "", plural(answers, "answer") if answers else "")
    return " ".join(part for part in parts if part)


class TextScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, title: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield Input(value=self.value, placeholder=self.placeholder)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class TagScreen(ModalScreen[list[str] | None]):
    """Space toggles a tag, Enter applies, Esc cancels."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "cancel"),
        Binding("enter", "apply", "apply", priority=True),
    ]

    def __init__(self, tags: list[str], selected: list[str]) -> None:
        super().__init__()
        self.tags = tags
        self.selected = selected

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Tags  (space toggles, enter applies)", classes="dialog-title")
            yield SelectionList[str](*((t, t, t in self.selected) for t in self.tags))

    def action_apply(self) -> None:
        chosen = set(self.query_one(SelectionList).selected)
        self.dismiss([t for t in self.tags if t in chosen])


class StepScreen(ModalScreen[tuple[str, str] | None]):
    """A step's text and who acts on it: Tab goes through [agent], [manual] and [waiting], Enter saves."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "cancel"),
        Binding("tab", "switch_kind", "who acts", priority=True),
    ]

    def __init__(self, title: str, text: str = "", kind: str = "agent") -> None:
        super().__init__()
        self.title_text = title
        self.text = text
        self.kind = kind if kind in items.KINDS else "agent"

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            # Editing usually fixes a word: keep the text and put the cursor at its end.
            yield Input(value=self.text, placeholder="one short line, starting with a verb", select_on_focus=False)
            yield Static(self.kind_line(), id="kind", classes="dialog-hint")

    def kind_line(self) -> Text:
        text = Text("who does it  ", "dim")
        for kind in items.KINDS:
            text.append(f"[{kind}]" if kind == self.kind else kind, "bold" if kind == self.kind else "dim")
            text.append("  ")
        return text.append_text(Text.assemble(("tab", "bold"), (" switches\n", "dim"), ("enter", "bold"),
                                              (" saves  ", "dim"), ("esc", "bold"), (" cancels", "dim")))

    def action_switch_kind(self) -> None:
        self.kind = items.KINDS[(items.KINDS.index(self.kind) + 1) % len(items.KINDS)]
        self.query_one("#kind", Static).update(self.kind_line())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = " ".join(event.value.split())
        self.dismiss((text, self.kind) if text else None)


class BranchScreen(ModalScreen[tuple[str, str] | None]):
    """`b`: a new idea that comes from this item or, with Tab, an open step that becomes an item of its own.
    Returns (mode, title), mode "idea" or "step"."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "cancel"),
        Binding("tab", "switch_mode", "mode", priority=True),
    ]

    def __init__(self, title: str, step_text: str | None) -> None:
        super().__init__()
        self.title_text = title
        self.step_text = step_text
        self.mode = "idea"

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"From “{self.title_text}”", classes="dialog-title")
            yield Input(placeholder="a short title", select_on_focus=False)
            yield Static(self.mode_line(), id="mode", classes="dialog-hint")

    def mode_line(self) -> Text:
        text = Text()
        modes = [("idea", "new idea")] + ([("step", f"step becomes an item: {self.step_text}")] if self.step_text else [])
        for mode, label in modes:
            text.append(f"[{label}]" if mode == self.mode else label, "bold" if mode == self.mode else "dim")
            text.append("\n")
        if self.step_text:
            text.append_text(Text.assemble(("tab", "bold"), (" switches  ", "dim")))
        return text.append_text(Text.assemble(("enter", "bold"), (" creates  ", "dim"), ("esc", "bold"), (" cancels", "dim")))

    def action_switch_mode(self) -> None:
        if not self.step_text:
            return
        field = self.query_one(Input)
        self.mode = "step" if self.mode == "idea" else "idea"
        if self.mode == "step" and not field.value.strip():
            field.value = self.step_text
            field.cursor_position = len(field.value)
        elif self.mode == "idea" and field.value == self.step_text:
            field.value = ""
        self.query_one("#mode", Static).update(self.mode_line())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        title = " ".join(event.value.split())
        self.dismiss((self.mode, title) if title else None)


class AskScreen(ModalScreen[tuple[str | None, str | None] | None]):
    """`?`: a lens from the config (digits pick), keep-digging when set up, the next questions of the latest
    answer, or a question typed in the field. Returns (lens, question)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, title: str, lenses: dict[str, str], next_questions: list[str], deep_available: bool) -> None:
        super().__init__()
        self.title_text = title
        self.choices: list[tuple[str | None, str | None]] = [(name, None) for name in lenses]
        labels = [Text.assemble((f"{name:<12} ", "bold"), (question, "dim")) for name, question in lenses.items()]
        if deep_available:
            self.choices.append((explore.DEEP, None))
            labels.append(Text.assemble((f"{explore.DEEP:<12} ", "bold"),
                                        ("keep-digging, up to 90 minutes (with the question typed below, if any)", "dim")))
        self.choices += [(None, q) for q in next_questions]
        labels += [Text.assemble(("→ ", "dim"), q) for q in next_questions]
        self.labels = labels

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog ask"):
            yield Label(f"Ask about “{self.title_text}”", classes="dialog-title")
            yield OptionList(*(Option(Text.assemble((f"{n}  " if n <= 9 else "   ", "dim"), label), id=str(n))
                               for n, label in enumerate(self.labels, start=1)))
            yield Input(placeholder="or type your own question", select_on_focus=False)
            yield Label(key_line([("1-9", "pick"), ("tab", "type"), ("enter", "ask"), ("esc", "cancel")]), classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_key(self, event) -> None:
        if (event.character and event.character.isdigit() and event.character != "0"
                and self.query_one(OptionList).has_focus and int(event.character) <= len(self.choices)):
            event.stop()
            self.pick(int(event.character) - 1)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.pick(int(event.option.id or "1") - 1)

    def pick(self, index: int) -> None:
        lens, question = self.choices[index]
        typed = " ".join(self.query_one(Input).value.split())
        self.dismiss((lens, typed or None) if lens == explore.DEEP else (lens, question))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        question = " ".join(event.value.split())
        if question:
            self.dismiss((None, question))


class SparkQuestionsScreen(ModalScreen[list[str] | None]):
    """`*` when rumo asks before the ideas: one field per question, blank allowed. Enter on the last one sends,
    Esc leaves the questions waiting. Returns the answers."""

    BINDINGS = [Binding("escape", "dismiss(None)", "later")]

    def __init__(self, direction: str, questions: list[str], answers: list[str]) -> None:
        super().__init__()
        self.direction = direction
        self.questions = questions
        self.answers = answers

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog spark"):
            yield Label("Before the ideas", classes="dialog-title")
            if self.direction:
                yield Static(self.direction, classes="spark-direction")
            for n, question in enumerate(self.questions):
                yield Static(Text(question, "bold"), classes="spark-question")
                given = self.answers[n] if n < len(self.answers) and isinstance(self.answers[n], str) else ""
                yield Input(value=given, placeholder="your answer, or leave it blank", select_on_focus=False)
            yield Label(key_line([("tab", "next"), ("enter", "send"), ("esc", "later")]), classes="dialog-hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        fields = list(self.query(Input))
        n = fields.index(event.input)
        if n + 1 < len(fields):
            fields[n + 1].focus()
        else:
            self.dismiss([" ".join(f.value.split()) for f in fields])


class SparkIdeasScreen(ModalScreen[set[int] | None]):
    """`*` when the ideas are ready: the direction rumo read, then the ideas. A digit, or space on the highlighted
    one, throws it away (and back); Enter keeps the others, Esc leaves them waiting. Returns what was thrown away."""

    BINDINGS = [Binding("escape", "dismiss(None)", "later")]

    def __init__(self, direction: str, ideas: list[spark.Idea], titles: dict[str, str]) -> None:
        super().__init__()
        self.direction = direction
        self.ideas = ideas
        self.titles = titles  # slug -> title of the items the ideas come from
        self.thrown: set[int] = set()

    def prompt(self, n: int) -> Table:
        """The number, then the title, the summary and where it comes from, wrapping under the title."""
        idea, thrown = self.ideas[n], n in self.thrown
        body = Text.assemble(("✕ " if thrown else "", "dim"), (idea.title, "dim strike" if thrown else "bold"))
        if idea.summary:
            body.append(f"\n{idea.summary}", "dim strike" if thrown else "")
        if source := next((self.titles[s] for s in idea.sources if s in self.titles), ""):
            body.append(f"\nfrom {source}", "dim")
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=2)
        grid.add_column(ratio=1)
        grid.add_row(Text(str(n + 1), "dim"), body)
        return grid

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog spark"):
            yield Label("Your direction", classes="dialog-title")
            yield Static(self.direction or "-", classes="spark-direction")
            rows: list[Option | None] = []
            for n in range(len(self.ideas)):  # a rule between them: a blank line would light up with the highlight
                rows += [None] if rows else []
                rows.append(Option(self.prompt(n), id=str(n)))
            yield OptionList(*rows)
            yield Label(key_line([(f"1-{len(self.ideas)}", "throw away or back"), ("enter", "keep the rest"), ("esc", "later")]),
                        classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_key(self, event) -> None:
        options = self.query_one(OptionList)
        if event.character and event.character.isdigit() and 1 <= int(event.character) <= len(self.ideas):
            n = int(event.character) - 1
        elif event.key == "space" and options.highlighted is not None:
            n = options.highlighted
        else:
            return
        event.stop()
        event.prevent_default()
        self.thrown ^= {n}
        options.replace_option_prompt_at_index(n, self.prompt(n))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(set(self.thrown))


class PRScreen(ModalScreen[tuple[str, str, str] | None]):
    """A PR's link, with an optional title after it, and its state: Tab switches the state, Enter saves."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "cancel"),
        Binding("tab", "switch_state", "state", priority=True),
    ]
    def __init__(self, title: str, text: str = "", state: str = "open") -> None:
        super().__init__()
        self.title_text = title
        self.text = text
        # "" (unknown) is offered only to a PR that has no state yet, so that editing it keeps it so.
        self.states = (*items.PR_STATES, "") if state == "" else items.PR_STATES
        self.state = state if state in self.states else "open"

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield Input(value=self.text, placeholder="https://github.com/org/repo/pull/12  optional title", select_on_focus=False)
            yield Static(self.state_line(), id="state", classes="dialog-hint")

    def state_line(self) -> Text:
        text = Text("state  ", "dim")
        for state in self.states:
            name = state or "unknown"
            text.append(f"[{name}]" if state == self.state else name, "bold" if state == self.state else "dim")
            text.append("  ")
        return text.append_text(Text.assemble("\n", ("tab", "bold"), (" switches  ", "dim"), ("enter", "bold"),
                                              (" saves  ", "dim"), ("esc", "bold"), (" cancels", "dim")))

    def action_switch_state(self) -> None:
        self.state = self.states[(self.states.index(self.state) + 1) % len(self.states)]
        self.query_one("#state", Static).update(self.state_line())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        link, _, title = event.value.strip().partition(" ")
        self.dismiss((link, " ".join(title.split()), self.state) if link else None)


def app_version() -> str:
    try:
        return f"rumo {version('rumo-cli')}"
    except PackageNotFoundError:  # run from a source tree that was never installed
        return ""


class KeyBar(Static):
    """The footer's keys, rewrapped to its width whenever the terminal changes size."""

    keys: list[tuple[str, str]] = []

    def show(self, keys: list[tuple[str, str]]) -> None:
        self.keys = keys
        self.update(key_line(keys, self.content_size.width))

    def on_resize(self) -> None:
        self.update(key_line(self.keys, self.content_size.width))


def wrapping_row(mark: Text, text: Text) -> ListItem:
    """A list row whose text wraps, going on under itself rather than under the mark."""
    return ListItem(Label(mark, classes="mark"), Label(text, classes="text"), classes="wrapping")


class StepList(ListView):
    BINDINGS = [
        Binding("space", "app.toggle_step", "toggle step"),
        Binding("enter", "app.edit_step", "edit step"),
        Binding("d", "app.delete_step", "delete step"),
        Binding("shift+up", "app.move_step(-1)", "move up"),
        Binding("shift+down", "app.move_step(1)", "move down"),
    ]

    def on_focus(self) -> None:
        cast(RumoApp, self.app).show_keys("steps")

    def on_blur(self) -> None:
        cast(RumoApp, self.app).show_keys()


class PRList(ListView):
    BINDINGS = [
        Binding("enter", "app.open_pr", "open PR"),
        Binding("space", "app.next_pr_state", "PR state"),
        Binding("e", "app.edit_pr", "edit PR"),
        Binding("d", "app.delete_pr", "delete PR"),
    ]

    def on_focus(self) -> None:
        cast(RumoApp, self.app).show_keys("prs")

    def on_blur(self) -> None:
        cast(RumoApp, self.app).show_keys()


class FamilyList(ListView):
    BINDINGS = [Binding("enter", "app.go_to_family", "go to")]

    def on_focus(self) -> None:
        cast(RumoApp, self.app).show_keys("family")

    def on_blur(self) -> None:
        cast(RumoApp, self.app).show_keys()


class ExplorationList(ListView):
    BINDINGS = [Binding("enter", "app.read_exploration", "read")]

    def on_focus(self) -> None:
        cast(RumoApp, self.app).show_keys("explorations")

    def on_blur(self) -> None:
        cast(RumoApp, self.app).show_keys()


class ExplorationScreen(Screen[None]):
    """One exploration, full screen: the question, the answer and the next questions. Esc goes back."""

    BINDINGS = [Binding("escape", "app.pop_screen", "back"), Binding("q", "app.pop_screen", "back", show=False)]
    CSS = """
    ExplorationScreen #reader { padding: 1 2; }
    ExplorationScreen #reader-keys { height: auto; padding: 0 1; }
    """

    def __init__(self, title: str, exploration: items.Exploration, state: str) -> None:
        super().__init__()
        self.text = self.markdown(title, exploration, state)

    @staticmethod
    def markdown(title: str, e: items.Exploration, state: str) -> str:
        parts = [f"## {e.question}", f"*{e.when}* · {title}"]
        if state == "running":
            parts.append("Still asking… The answer lands here when it is ready.")
        elif state == "failed":
            parts.append(f"**Failed:** {e.error or 'stopped before it answered'}\n\nPress `?` on the item to ask again.")
        else:
            parts.append(e.answer)
        if e.next_questions:
            parts.append("**Next questions** (offered by `?`)\n\n" + "\n".join(f"- {q}" for q in e.next_questions))
        return "\n\n".join(parts)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="reader"):
            yield Markdown(self.text)
        yield Static(key_line([("↑↓", "scroll"), ("esc", "back")]), id="reader-keys")

    def on_mount(self) -> None:
        self.query_one("#reader", VerticalScroll).focus()


# ---------- app ----------


class RumoApp(App):
    TITLE = "rumo"
    CSS = """
    Screen { layout: vertical; }
    #filter { display: none; }
    #filter.on { display: block; }
    #main { height: 1fr; }
    #timeline { width: 55%; height: 1fr; border: round $primary-darken-1; }
    #timeline > .option-list--option-disabled { color: $text; }
    #timeline:focus { border: round $primary; }
    #detail { border: round $secondary-darken-1; padding: 0 1; }
    #header { margin-bottom: 1; }
    .section { text-style: bold; color: $accent; margin-top: 1; }
    #steps, #prs, #family, #explorations, #sessions { height: auto; }
    .wrapping { layout: horizontal; }
    .wrapping > .text, #sessions Label { width: 1fr; }
    Markdown { margin: 0; padding: 0; }
    #footer { height: auto; }
    #legend, #keys { height: auto; padding: 0 1; }
    #legend { width: 1fr; }
    #version { width: auto; padding: 0 1; color: $text-muted; }
    ModalScreen { align: center middle; }
    .dialog { width: 60; height: auto; max-height: 80%; border: round $primary; background: $surface; padding: 0 1; }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    .dialog-hint { margin-top: 1; }
    .dialog OptionList, .dialog SelectionList { height: auto; max-height: 20; }
    .dialog.ask { width: 90; }
    .dialog.spark { width: 80; }
    .spark-direction { color: $text-muted; }
    .spark-question { margin-top: 1; }
    .dialog.spark OptionList { max-height: 24; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("v", "next_view", "view"),
        Binding("1", "view('timeline')", show=False),
        Binding("2", "view('projects')", show=False),
        Binding("3", "view('repos')", show=False),
        Binding("4", "view('tree')", show=False),
        Binding("h", "next_horizon", "horizon"),
        Binding("s", "status", "status"),
        Binding("g", "group", "group"),
        Binding("t", "tags", "tags"),
        Binding("n", "new_idea", "new idea"),
        Binding("b", "branch", "branch"),
        Binding("question_mark", "ask", "ask"),
        Binding("asterisk", "spark", "spark"),
        Binding("a", "add_step", "add step"),
        Binding("p", "add_pr", "add PR"),
        Binding("x", "toggle_done", "done steps"),
        Binding("e", "edit", "edit"),
        Binding("c", "copy_resume", "copy resume"),
        Binding("slash", "filter", "filter"),
        Binding("escape", "clear_filter", "clear filter", show=False),
    ]

    def __init__(self, config_path: Path | None = None, today: date | None = None, gh: prs.Run | None = None,
                 run_git: git.Run | None = None, spawner: Callable[..., object] = explore.spawn,
                 spark_spawner: Callable[..., object] = spark.spawn) -> None:
        super().__init__()
        self.spawner = spawner  # starts `rumo explore` detached; tests pass a recorder
        self.spark_spawner = spark_spawner  # starts `rumo spark` detached, the same way
        self.spark_proc: subprocess.Popen | None = None  # the spark started here, until it ends
        self.spark_from: tuple[str, str] = ("", "")  # spark.json (state, started) when it was started
        self.spark_seen: tuple[str, str] | None = None  # (state, started) of spark.json at the last look
        self.spawned: list[subprocess.Popen] = []  # reaped on the poll
        self.endings: dict[str, str] | None = None  # "<path>#<id>" -> state of each exploration, at the last reload
        self.gh = gh  # asks GitHub about the PRs in the background; None leaves the states as written
        self.run_git = run_git  # asks git what is left in the items' repositories; None says nothing
        self.git_left: dict[str, list[git.Pending]] = {}  # item slug -> what git has left of its work
        self.config_path = config_path or config.default_path()
        self.cfg = config.load(self.config_path)
        self.today = today
        self.items: dict[str, Item] = {}
        self.current: str | None = None
        self.filter_text = ""
        self.show_older = False
        self.view = "timeline"
        self.hide_done = False  # done steps hidden in the detail of every item (remembered)
        self.shown: list[items.Step] = []  # the steps the list shows, in order
        self.family: list[str] = []  # the slugs the Family list shows, in order
        self.explorations: list[items.Exploration] = []  # the ones the Explorations list shows, newest first
        self.horizon = "now"  # one horizon at a time; never remembered, rumo always opens on Now
        self.mtimes: dict[str, float] = {}
        self.viewed: dict[str, list[str]] | None = None  # item path -> its steps when you last looked (state.json)
        self.shown_slug: str | None = None  # the item in the detail, marked as seen once you move on
        self.load_state()

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter by title, tag, group or text (Esc clears)", id="filter")
        with Horizontal(id="main"):
            yield OptionList(id="timeline")
            with VerticalScroll(id="detail"):
                yield Static(id="header")
                yield Markdown(id="body")
                yield Label("Steps", classes="section", id="steps-title")
                yield StepList(id="steps")
                yield Label("PRs", classes="section", id="prs-title")
                yield PRList(id="prs")
                yield Label("Family", classes="section", id="family-title")
                yield FamilyList(id="family")
                yield Label("Explorations", classes="section", id="explorations-title")
                yield ExplorationList(id="explorations")
                yield Label("Research", classes="section", id="research-title")
                yield Static(id="research")
                yield Label("Sessions  (c copies the resume command)", classes="section")
                yield ListView(id="sessions")
        with Horizontal(id="footer"):
            yield Static(id="legend")
            yield Static(app_version(), id="version")
        yield KeyBar(id="keys")

    def on_mount(self) -> None:
        self.cfg.folder.mkdir(parents=True, exist_ok=True)
        if not self.cfg.error:
            config.ensure_file(self.config_path, self.cfg.folder, self.cfg.language)
        self.reload()
        self.show_keys()
        self.query_one("#timeline", OptionList).focus()
        self.set_interval(2.0, self.check_changes)
        if self.gh is not None:
            self.check_prs()
            self.set_interval(PR_CHECK_SECONDS, self.check_prs)
        if self.run_git is not None:
            self.check_git()
            self.set_interval(GIT_CHECK_SECONDS, self.check_git)

    def check_prs(self) -> None:
        self.run_worker(self.ask_about_prs, thread=True, exclusive=True, group="prs", exit_on_error=False)

    def ask_about_prs(self) -> None:
        """In a thread: bring the PRs of every item not done in line with gh; the file watcher shows the result."""
        if self.gh is None:
            return
        state = config.load_state(self.config_path)
        asked: dict = {}  # each repository and branch once per check
        for item in items.list_items(self.cfg.folder):
            if item.status in items.FINISHED:
                continue
            key = str(item.path)
            try:
                prs.refresh(item, prs.noted_branches(state, key), prs.dropped(state, key), self.gh, asked)
            except Exception:  # one odd item must not stop the others
                continue

    def check_git(self) -> None:
        self.run_worker(self.ask_git, thread=True, exclusive=True, group="git", exit_on_error=False)

    def ask_git(self) -> None:
        """In a thread: what git has left in the repositories of the items on the timeline (not the older Past)."""
        if self.run_git is None:
            return
        tl = items.timeline(list(self.items.values()), today=self.today)
        left = git.pending([*tl.now, *tl.later, *tl.past], config.load_state(self.config_path), self.run_git)
        if left != self.git_left:
            self.call_from_thread(self.show_git, left)

    def show_git(self, left: dict[str, list[git.Pending]]) -> None:
        self.git_left = left
        self.build_timeline()
        self.show_detail()

    # ---------- data ----------

    def read_mtimes(self) -> dict[str, float]:
        files = list(self.cfg.folder.glob("*.md"))
        for extra in (self.config_path, spark.state_path(self.cfg.folder)):
            if extra.exists():
                files.append(extra)
        mtimes = {}
        for path in files:
            try:
                mtimes[str(path)] = path.stat().st_mtime
            except OSError:
                continue
        return mtimes

    def check_changes(self) -> None:
        self.spawned = [p for p in self.spawned if p.poll() is None]
        if self.read_mtimes() != self.mtimes:
            self.reload()
        elif self.spark_seen and self.spark_seen[0] == "running":  # a run that dies writes nothing
            self.announce_spark()
        if self.spark_proc is not None and self.spark_proc.poll() is not None:
            proc, self.spark_proc = self.spark_proc, None
            if proc.returncode and self.spark_key() == self.spark_from:  # it broke before writing a word
                self.notify(f"Spark failed: it stopped before it started. See {self.config_path.parent / 'explore.log'}.",
                            severity="warning")

    def spark_key(self) -> tuple[str, str]:
        data = spark.load(self.cfg.folder)
        return str(data.get("state", "")), str(data.get("started", ""))

    def reload(self) -> None:
        self.cfg = config.load(self.config_path)
        found = items.list_items(self.cfg.folder)
        before = {str(i.path): seen_keys(i) for i in found}
        if synced := items.sync_item_steps(found):  # an `[item]` step follows its item; rumo did it, so no news
            for path in synced:
                self.saw(path, before.get(str(path), []))
            found = items.list_items(self.cfg.folder)
        self.mtimes = self.read_mtimes()
        self.items = {i.slug: i for i in found}
        self.announce_endings(found)
        self.announce_spark()
        if self.viewed is None:
            self.viewed = {str(i.path): seen_keys(i) for i in self.items.values()}
            self.save_viewed()
        self.build_timeline()
        self.show_detail()
        self.query_one("#legend", Static).update(self.legend())

    def announce_endings(self, found: list[Item]) -> None:
        """A notice when an exploration ends, started here or not: the answer is ready, or why it failed."""
        now = datetime.now()
        states = {f"{i.path}#{e.id}": (i, e, items.exploration_state(e, now, explore.pid_alive))
                  for i in found for e in i.explorations if e.id}
        if self.endings is not None:
            for key, (item, e, state) in states.items():
                if state != "running" and self.endings.get(key, "running") == "running":
                    if state == "done":
                        self.notify(f"Answer ready: “{item.title}”. It is under Explorations.")
                    else:
                        self.notify(f"Asking about “{item.title}” failed: {e.error or 'stopped before it answered'}",
                                    severity="warning")
        self.endings = {key: state for key, (_, _, state) in states.items()}

    def announce_spark(self) -> None:
        """A notice when a spark has questions, ideas or failed, and once on start for what still waits. Keyed by
        state and start time: the answers you write, or a run seen only once it ended, change nothing."""
        data = spark.load(self.cfg.folder)
        state, error = spark.current(data, datetime.now(), explore.pid_alive)
        seen = (state, str(data.get("started", "")))
        if seen != self.spark_seen and (self.spark_seen is not None or state in ("asking", "ready")):
            if state == "asking":
                self.notify("rumo has a question before the ideas. Press *.")
            elif state == "ready":
                self.notify(f"{plural(len(spark.ideas_of(data)), 'new idea')}. Press * to see them.")
            elif state == "failed":
                self.notify(f"Spark failed: {error}", severity="warning")
        self.spark_seen = seen

    def group_color(self, item: Item) -> str:
        group = self.cfg.groups.get(item.group)
        return config.PALETTE.get(group.color, "") if group else ""

    def matches(self, item: Item) -> bool:
        term = self.filter_text.lower().strip()
        if not term:
            return True
        group = self.cfg.groups.get(item.group)
        haystack = [item.title, item.slug, item.status, item.group, group.name if group else "", *item.tags]
        haystack += item.sections.values()
        return term in " ".join(haystack).lower()

    def write(self, operation: Callable[[], object]) -> None:
        item = self.current_item()
        before = seen_keys(item) if item else []
        try:
            operation()
        except WriteConflict:
            self.notify("The item just changed on disk. Reloaded; try again.", severity="warning")
        if item:
            self.saw(item.path, before)
        self.reload()

    # ---------- timeline ----------

    def build_timeline(self) -> None:
        timeline = self.query_one("#timeline", OptionList)
        visible = [i for i in self.items.values() if self.matches(i)]
        tl = items.timeline(visible, today=self.today, show_older=self.show_older)

        # One horizon at a time; a filter shows all three, so that no match stays hidden.
        filtering = bool(self.filter_text.strip())
        shown = list(HORIZONS) if filtering else [self.horizon]
        build = {"timeline": self.timeline_rows, "projects": self.project_rows, "repos": self.repo_rows,
                 "tree": self.tree_rows}[self.view]
        if not self.items:
            options = [self.message("No items yet. Press n to capture an idea, or run /rumo new in an agent session.")]
        elif filtering:
            options = build(tl, shown) or [self.message("No matches.")]
        else:
            empty = TREE_EMPTY if self.view == "tree" else EMPTY[self.horizon]
            options = [self.banner(tl), *(build(tl, shown) or [self.message(empty)])]

        timeline.clear_options()
        timeline.add_options(options)
        timeline.border_title = self.view_tabs()
        timeline.border_subtitle = self.horizon_tabs(tl)

        ids = [o.id or "" for o in options]
        target = f"item:{self.current}" if self.current else None
        if target not in ids:
            target = next((i for i in ids if i.startswith("item:")), None)
        self.current = target.removeprefix("item:") if target else None
        if target:
            timeline.highlighted = ids.index(target)

    def horizons(self, tl: items.Timeline) -> dict[str, list[Item]]:
        return {"past": tl.past, "now": tl.now, "later": tl.later}

    # Timeline: the items of the horizon; in Now, items of the same project sit together.
    def timeline_rows(self, tl: items.Timeline, shown: list[str]) -> list[Option]:
        horizons = self.horizons(tl)
        horizons["now"] = [i for _, _, members in self.by_group(tl.now) for i in members]
        rows: list[Option] = []
        for horizon in shown:
            its = horizons[horizon]
            older = tl.older if horizon == "past" else 0
            if len(shown) > 1:
                if not its and not older:
                    continue
                rows.append(Option(Text(f" {horizon.upper()} ", "bold reverse"), id=f"header:{horizon}", disabled=True))
            if older:
                rows.append(self.older_option(older))
            rows += [self.item_option(i) for i in its]
        return rows

    # Projects: one block per group; the block line carries the label, so rows drop it.
    def project_rows(self, tl: items.Timeline, shown: list[str]) -> list[Option]:
        blocks = [(f"project:{key}", self.label(group) if group else Text("No group", "bold dim"), members)
                  for key, group, members in self.by_group([*tl.past, *tl.now, *tl.later])]
        return self.block_rows(tl, shown, blocks, label=False)

    # Repos: one block per repository of the item's latest session; rows keep the project label.
    def repo_rows(self, tl: items.Timeline, shown: list[str]) -> list[Option]:
        buckets: dict[str, list[Item]] = {}
        for i in [*tl.past, *tl.now, *tl.later]:
            buckets.setdefault(repo_of(i) or UNGROUPED, []).append(i)
        keys = sorted(k for k in buckets if k != UNGROUPED) + ([UNGROUPED] if UNGROUPED in buckets else [])
        blocks = [(f"repo:{key}", Text("No repo", "bold dim") if key == UNGROUPED else Text(key, "bold"), buckets[key])
                  for key in keys]
        return self.block_rows(tl, shown, blocks, label=True)

    def block_rows(
        self, tl: items.Timeline, shown: list[str], blocks: list[tuple[str, Text, list[Item]]], label: bool
    ) -> list[Option]:
        horizons = self.horizons(tl)
        rows: list[Option] = []
        if "past" in shown and tl.older:
            rows.append(self.older_option(tl.older))
        for block_id, head, members in blocks:
            slugs = {i.slug for i in members}
            its = [i for horizon in shown for i in horizons[horizon] if i.slug in slugs]
            if not its:
                continue
            counts = " · ".join(
                f"{n} {horizon}" for horizon in ("now", "later", "past")
                if (n := sum(i.slug in slugs for i in horizons[horizon]))
            )
            rows.append(Option(self.block(head, counts, gap=bool(rows)), id=block_id, disabled=True))
            rows += [self.item_option(i, label=label) for i in its]
        return rows

    # Tree: the families with a member in the horizon; the members outside it stay, dim, to keep the shape.
    def tree_rows(self, tl: items.Timeline, shown: list[str]) -> list[Option]:
        horizons = self.horizons(tl)
        inside = {i.slug for horizon in shown for i in horizons[horizon]}
        rows: list[Option] = []
        if "past" in shown and tl.older:
            rows.append(self.older_option(tl.older))
        for n, family in enumerate(family_trees(list(self.items.values()))):
            if not any(member.slug in inside for member, _, _ in family):
                continue
            if rows:
                rows.append(Option(Text(""), id=f"gap:{n}", disabled=True))
            rows += [Option(self.row(member, True, (head, under), faded=member.slug not in inside), id=f"item:{member.slug}")
                     for member, head, under in family]
        return rows

    @staticmethod
    def block(head: Text, counts: str, gap: bool) -> Table:
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="crop")
        grid.add_column(no_wrap=True, justify="right")
        if gap:
            grid.add_row("", "", "")
        grid.add_row(head, Text("─" * 400, "dim"), Text(counts, "dim"))
        return grid

    @staticmethod
    def message(text: str) -> Option:
        return Option(Text(text, "dim"), id="empty", disabled=True)

    @staticmethod
    def older_option(count: int) -> Option:
        return Option(Text(f"… {count} older (enter to show)", "dim italic"), id="older")

    def by_group(self, its: list[Item]) -> list[tuple[str, config.Group | None, list[Item]]]:
        """Items bucketed by group, in config order, with ungrouped (or unknown-group) items last."""
        buckets: dict[str, list[Item]] = {}
        for i in its:
            buckets.setdefault(i.group if i.group in self.cfg.groups else UNGROUPED, []).append(i)
        return [(key, self.cfg.groups.get(key), buckets[key]) for key in [*self.cfg.groups, UNGROUPED] if key in buckets]

    @property
    def name_width(self) -> int:
        return min(max((len(g.name) for g in self.cfg.groups.values()), default=0), MAX_LABEL)

    def label(self, group: config.Group | None) -> Text:
        """The project label: the name on its color, as wide as the longest name; blank without a group."""
        width = self.name_width
        if group is None:
            return Text(" " * (width + 2))
        name = group.name if len(group.name) <= width else group.name[: width - 1].rstrip() + "…"
        color = config.PALETTE.get(group.color)
        return Text(f" {name:<{width}} ", f"bold #0d1118 on {color}" if color else "bold reverse")

    def item_option(self, item: Item, label: bool = True) -> Option:
        return Option(self.row(item, label), id=f"item:{item.slug}")

    def row(self, item: Item, label: bool, tree: tuple[str, str] = ("", ""), faded: bool = False) -> Table:
        """Symbol, project label, title and progress (or the date in Past) on one line; below, the next open
        steps (`step_lines` of them at most), each saying who acts, then what git has left of the work.
        In Tree, `tree` holds the connector before the title and the prefix of the lines below."""
        group = self.cfg.groups.get(item.group)
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(width=1, no_wrap=True)
        if label:
            grid.add_column(width=self.name_width + 2, no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        open_prs = sum(not p.finished for p in item.prs)
        news = news_label(self.news(item))
        cells = [Text(news, "bold")] if news else []  # what changed since you last looked
        cells += [Text(plural(open_prs, "PR"), "dim")] if open_prs else []
        for _ in cells:
            grid.add_column(no_wrap=True)
        grid.add_column(width=6, no_wrap=True, justify="right")
        idle = item.idle_days(self.today or date.today())
        if item.status in items.FINISHED:
            right = short_date(item.done_at)
        elif idle is not None:
            right = f"{idle}d"  # how long the idea has waited for a decision
        else:
            right = f"{sum(s.done for s in item.steps)}/{len(item.steps)}" if item.steps else ""
        symbol = Text(SHAPES[item.status], "" if item.status == "doing" and not faded else "dim")
        title = Text.assemble((tree[0], "dim"), (display_title(item.title, group.name if group else ""),
                                                 "dim" if faded or item.status == "dropped" or (idle or 0) > IDLE_FADE_DAYS else ""))
        grid.add_row(symbol, *([self.label(group)] if label else []), title, *cells, Text(right, "dim"))
        pending = [s for s in item.steps if not s.done] if item.status not in items.FINISHED else []
        below = [(step.kind, KIND_STYLES.get(step.kind, "dim"), step.shown) for step in pending[: self.cfg.step_lines]]
        below += [("git", GIT_STYLE, left.label) for left in self.git_left.get(item.slug, [])]
        for n, (who, style, text) in enumerate(below):
            line = Text.assemble((tree[1], "dim"), ("↳ " if n == 0 else "  ", "dim"), (f"{who:<{KIND_WIDTH}}", style), (text, "dim"))
            grid.add_row("", *([""] if label else []), line, *[""] * len(cells), "")
        return grid

    def view_tabs(self) -> Text:
        text = Text(" ")
        for n, (key, name) in enumerate(VIEWS.items(), start=1):
            text.append(f"{n} {name}", "bold" if key == self.view else "dim")
            text.append("  " if n < len(VIEWS) else " ")
        return text

    def horizon_tabs(self, tl: items.Timeline) -> Text:
        counts = {horizon: len(its) for horizon, its in self.horizons(tl).items()}
        text = Text(" ")
        for horizon in HORIZONS:
            name = horizon.upper() if horizon == self.horizon else horizon.capitalize()
            text.append(f"{name} {counts[horizon]}", "bold" if horizon == self.horizon else "dim")
            text.append(" " if horizon == HORIZONS[-1] else "  ")
        return text

    def banner(self, tl: items.Timeline) -> Option:
        """The list's first row: which horizon this is, what it holds, and where `h` goes next."""
        count = len(self.horizons(tl)[self.horizon])
        following = CYCLE[(CYCLE.index(self.horizon) + 1) % len(CYCLE)]
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(no_wrap=True, justify="right")
        grid.add_row(
            Text(f" {self.horizon.upper()} ", "bold reverse"),
            Text(f"{plural(count, 'item')} · {HORIZON_SAYS[self.horizon]}", "dim"),
            Text.assemble(("h", "bold"), (f" ▸ {following.capitalize()}", "dim")),
        )
        grid.add_row("", "", "")
        return Option(grid, id=f"horizon:{self.horizon}", disabled=True)

    def legend(self) -> Text:
        text = Text()
        if self.cfg.error:
            text.append(f"Config error, using defaults: {self.cfg.error}\n", "bold red")
        return text.append("  ".join(f"{shape} {status}" for status, shape in SHAPES.items()), "dim")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != "timeline":
            return
        option_id = event.option.id or ""
        if option_id.startswith("item:") and option_id.removeprefix("item:") != self.current:
            self.current = option_id.removeprefix("item:")
            self.show_detail()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """The lists grow with the item and the detail pane scrolls: keep the highlighted row in view."""
        if event.item is not None and event.list_view.has_focus:
            event.item.scroll_visible(animate=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "timeline" and event.option.id == "older":
            self.show_older = True
            self.build_timeline()

    # ---------- detail ----------

    def current_item(self) -> Item | None:
        return self.items.get(self.current) if self.current else None

    def show_detail(self) -> None:
        item = self.current_item()
        if self.shown_slug != (item.slug if item else None):
            left = self.items.get(self.shown_slug) if self.shown_slug else None
            self.shown_slug = item.slug if item else None
            if left and self.mark_viewed(left):  # you moved on: what it shows now is what you saw
                self.build_timeline()
        header = self.query_one("#header", Static)
        body = self.query_one("#body", Markdown)
        steps = self.query_one("#steps", ListView)
        pull_requests = self.query_one("#prs", ListView)
        sessions = self.query_one("#sessions", ListView)
        step_index, pr_index, session_index = steps.index, pull_requests.index, sessions.index
        steps.clear()
        pull_requests.clear()
        sessions.clear()
        self.shown = []
        has_prs = bool(item and item.prs)
        pull_requests.display = self.query_one("#prs-title", Label).display = has_prs
        self.show_family(item)
        self.show_explorations(item)
        self.show_research(item)
        if item is None:
            self.query_one("#steps-title", Label).update("Steps")
            header.update(Text("Nothing selected.", "dim"))
            body.update("")
            return

        line = Text.assemble((item.title, "bold"), "\n", status_text(item.status))
        if group := self.cfg.groups.get(item.group):
            line.append("   ")
            line.append_text(self.label(group))
        if item.tags:
            line.append("   tags ", "dim")
            for tag in item.tags:
                line.append(tag + " ", "" if tag in self.cfg.tags else "dim")
        if updated := item.meta.get("updated"):
            line.append(f"   updated {updated}", "dim")
        for left in self.git_left.get(item.slug, []):
            line.append_text(Text.assemble("\n", ("git  ", GIT_STYLE), left.label))
        header.update(line)

        parts = []
        for name in ("left off", "context", "notes"):
            if content := item.sections.get(name):
                parts.append(f"**{name.capitalize()}**\n\n{content}")
        body.update("\n\n".join(parts))

        self.shown = [s for s in item.steps if not (self.hide_done and s.done)]
        done = sum(s.done for s in item.steps)
        news = {text: kind for text, kind in (self.news(item) or {}).items() if kind != "answer"}  # answers: Explorations
        hints = [f"{news_label(news)} since you looked"] if news else []
        if done:
            hints.append(f'{done} done hidden · "x" shows them' if self.hide_done else f'{done} done · "x" hides them')
        self.query_one("#steps-title", Label).update(Text.assemble("Steps", (f"  {' · '.join(hints)}", "dim") if hints else ""))
        steps.extend(self.step_row(s, news.get(s.text, "")) for s in self.shown)
        if has_prs:
            guessed = self.gh is None and any(not p.finished and "/pull/" in p.url for p in item.prs)
            self.query_one("#prs-title", Label).update(Text.assemble(
                "PRs", ("  (enter opens" + (" · no gh: states as written" if guessed else " the link") + ")", "dim")
            ))
            pull_requests.extend(self.pr_row(p) for p in item.prs)
            self.highlight(pull_requests, pr_index or 0)
        sessions.extend(
            ListItem(Label(Text.assemble((s.when, "dim"), "  ", s.repo, "  ", (s.id[:8], "cyan"), (" closed" if s.closed else "", "dim"))))
            for s in reversed(item.sessions)
        )
        if self.shown:
            self.select_step(step_index or 0)
        if item.sessions:
            sessions.index = min(session_index or 0, len(item.sessions) - 1)

    def show_family(self, item: Item | None) -> None:
        """Where the item came from (`from:`) and the items it led to; Enter goes to one."""
        rows = self.query_one("#family", ListView)
        index = rows.index
        rows.clear()
        everything = list(self.items.values())
        parent = items.parent_of(everything, item) if item else None
        family = ([("↑ from  ", parent)] if parent else []) + [("↓ led to  ", c) for c in (items.children(everything, item) if item else [])]
        self.family = [member.slug for _, member in family]
        rows.display = self.query_one("#family-title", Label).display = bool(family)
        rows.extend(wrapping_row(Text(arrow, "dim"), Text.assemble(
            (SHAPES[member.status], "" if member.status == "doing" else "dim"), " ", member.title,
        )) for arrow, member in family)
        if family:
            self.highlight(rows, index or 0)

    def show_explorations(self, item: Item | None) -> None:
        """The questions asked about the item, newest first: `Oct 11 · <question>`, then how it went."""
        rows = self.query_one("#explorations", ListView)
        index = rows.index
        rows.clear()
        self.explorations = list(reversed(item.explorations)) if item else []
        rows.display = self.query_one("#explorations-title", Label).display = bool(self.explorations)
        now = datetime.now()
        news = (self.news(item) or {}) if item else {}
        for e in self.explorations:
            state = items.exploration_state(e, now, explore.pid_alive)
            after = {"running": ("  asking…", "dim italic"), "failed": (f"  failed: {e.error or 'stopped before it answered'}", "red")}
            mark = ("  new", "bold") if f"x{e.id}" in news else ""
            rows.append(wrapping_row(Text(f"{short_date(e.when)} · ", "dim"), Text.assemble(e.question, after.get(state, ""), mark)))
        if self.explorations:
            self.highlight(rows, index or 0)

    def show_research(self, item: Item | None) -> None:
        """The files under `<folder>/research/<slug>/`: what ideas-hub and keep-digging left about the item."""
        shown = self.query_one("#research", Static)
        folder = self.cfg.folder / "research" / item.slug if item else None
        try:
            found = sorted(p for p in folder.iterdir() if not p.name.startswith(".")) if folder else []
        except OSError:
            found = []
        shown.display = self.query_one("#research-title", Label).display = bool(found)
        if found and folder:
            names = "\n".join(p.name + ("/" if p.is_dir() else "") for p in found)
            shown.update(Text.assemble((f"research{os.sep}{folder.name}{os.sep}  (in the rumo folder)\n", "dim"), names))

    def action_go_to_family(self) -> None:
        """Select the highlighted family member in the list, switching to its horizon."""
        rows = self.query_one("#family", ListView)
        if rows.index is not None and rows.index < len(self.family):
            self.go_to(self.family[rows.index])

    def go_to(self, slug: str) -> None:
        """Select that item in the list, switching to its horizon, and focus the list."""
        if (target := self.items.get(slug)) is None:
            self.notify(f"No item {slug} in {self.cfg.folder}.", severity="warning")
            return
        if not self.matches(target):
            self.query_one("#filter", Input).value = ""
            self.filter_text = ""
        self.horizon = HORIZON_OF[target.status]
        if target.status in items.FINISHED and target not in items.timeline(list(self.items.values()), today=self.today).past:
            self.show_older = True  # finished before the Past window
        self.current = target.slug
        self.build_timeline()
        self.show_detail()
        self.query_one("#timeline", OptionList).focus()

    def action_read_exploration(self) -> None:
        rows = self.query_one("#explorations", ListView)
        item = self.current_item()
        if item is None or rows.index is None or rows.index >= len(self.explorations):
            return
        e = self.explorations[rows.index]
        self.push_screen(ExplorationScreen(item.title, e, items.exploration_state(e, datetime.now(), explore.pid_alive)))

    @staticmethod
    def pr_row(pr: items.PR) -> ListItem:
        """`● web-app#12  Logout novo  open`, the name linked to the PR."""
        color = PR_COLORS.get(pr.state, "dim")
        return wrapping_row(Text("● " if pr.state else "? ", color), Text.assemble(
            (pr.name, Style(link=pr.url)), f"  {pr.title}" if pr.title else "", (f"  {pr.state or 'state unknown'}", color),
        ))

    @staticmethod
    def step_row(step: items.Step, news: str = "") -> ListItem:
        kind = f"[{step.kind}] " if step.kind else ""
        mark = ({"new": "  new", "done": "  done since you looked"}.get(news, ""), "bold")
        if step.done:
            return wrapping_row(Text(f"[x] {kind}", "dim strike"), Text.assemble((step.shown, "dim strike"), mark))
        return wrapping_row(Text.assemble("[ ] ", (kind, KIND_STYLES.get(step.kind, "dim"))), Text.assemble(step.shown, mark))

    # ---------- actions ----------

    def action_next_view(self) -> None:
        names = list(VIEWS)
        self.action_view(names[(names.index(self.view) + 1) % len(names)])

    def action_view(self, view: str) -> None:
        self.view = view
        self.save_state()
        self.build_timeline()
        self.show_detail()

    def action_next_horizon(self) -> None:
        """`h` steps Now -> Later -> Past -> Now."""
        self.horizon = CYCLE[(CYCLE.index(self.horizon) + 1) % len(CYCLE)]
        self.build_timeline()
        self.show_detail()

    # The view survives restarts, in state.json: config.toml only ever changes when the user
    # edits a group.
    def load_state(self) -> None:
        state = config.load_state(self.config_path)
        if (view := state.get("view")) in VIEWS:
            self.view = view
        self.hide_done = state.get("hide_done") is True
        viewed = state.get("steps_viewed")
        if isinstance(viewed, dict):
            self.viewed = {str(k): [str(x) for x in v] for k, v in viewed.items() if isinstance(v, list)}

    def save_state(self) -> None:
        config.update_state(self.config_path, view=self.view, hide_done=self.hide_done)

    # What changed since you last looked at an item: its steps then are kept as `seen_key`s, taken when you
    # move on to another item or quit. The first run takes everything as seen.

    def news(self, item: Item) -> dict[str, str] | None:
        """The steps added or done since you last looked at the item, {text: "new" or "done"}; None for an
        item you have not seen yet."""
        if self.viewed is None:
            return {}
        seen = self.viewed.get(str(item.path))
        if seen is None:
            return None
        was: dict[str, set[str]] = {}
        for key in seen:
            text, _, done = key.partition(":")
            was.setdefault(text, set()).add(done)
        found = {}
        for step in item.steps:
            text, _, done = seen_key(step).partition(":")
            if text not in was:
                found[step.text] = "new"
            elif done == "1" and "1" not in was[text]:
                found[step.text] = "done"
        for e in item.explorations:
            if e.id and e.state == "done" and f"x{e.id}" not in was:
                found[f"x{e.id}"] = "answer"
        return found

    def mark_viewed(self, item: Item) -> bool:
        if self.viewed is None or self.viewed.get(str(item.path)) == (keys := seen_keys(item)):
            return False
        self.viewed[str(item.path)] = keys
        self.save_viewed()
        return True

    def saw(self, path: Path, before: list[str]) -> None:
        """What you just changed yourself is no news: it joins what you have seen of the item."""
        try:
            after = seen_keys(items.load(path))
        except (OSError, ValueError):
            return
        seen = self.viewed.get(str(path)) if self.viewed is not None else None
        if self.viewed is None or seen is None:
            return
        if fresh := [k for k in after if k not in before and k not in seen]:
            self.viewed[str(path)] = seen + fresh
            self.save_viewed()

    def save_viewed(self) -> None:
        if self.viewed is not None:
            self.viewed = {path: keys for path, keys in self.viewed.items() if Path(path).exists()}
            config.update_state(self.config_path, steps_viewed=self.viewed)

    async def action_quit(self) -> None:
        if item := self.current_item():
            self.mark_viewed(item)
        await super().action_quit()

    def action_toggle_done(self) -> None:
        self.hide_done = not self.hide_done
        self.save_state()
        self.show_detail()
        focus = "steps" if self.query_one("#steps", ListView).has_focus else "prs" if self.query_one("#prs", ListView).has_focus else ""
        self.show_keys(focus)

    def show_keys(self, focus: str = "") -> None:
        """The footer: the keys of the list that has focus ("steps", "prs"), and `x` saying what it does now."""
        done = ("x", "show done" if self.hide_done else "hide done")
        shown = {"steps": STEP_KEYS, "prs": PR_KEYS, "family": FAMILY_KEYS, "explorations": EXPLORATION_KEYS}.get(focus, KEYS)
        self.query_one("#keys", KeyBar).show([done if key == "x" else (key, label) for key, label in shown])

    def action_toggle_step(self) -> None:
        item = self.current_item()
        index = self.query_one("#steps", ListView).index
        if item is None or index is None or index >= len(self.shown):
            return
        step = self.shown[index]
        if step.kind == items.ITEM_KIND:
            self.notify("This step follows its own item: it is checked when that item is done.")
            return
        self.write(lambda: items.toggle_step(item, step))

    def focused_step(self) -> tuple[Item, int] | None:
        """The current item and the index (among the shown steps) of its highlighted step, when the Steps list has focus."""
        item = self.current_item()
        steps = self.query_one("#steps", ListView)
        if item is None or not steps.has_focus or steps.index is None or steps.index >= len(self.shown):
            return None
        return item, steps.index

    def select_step(self, index: int) -> None:
        if self.shown:
            self.highlight(self.query_one("#steps", ListView), index)

    @staticmethod
    def highlight(rows: ListView, index: int) -> None:
        rows.index = max(0, min(index, len(rows.children) - 1))

        # Right after a rebuild the highlight lands on a row about to be removed: draw it again after.
        def draw() -> None:
            for n, row in enumerate(rows.query(ListItem)):
                row.highlighted = n == rows.index

        rows.call_after_refresh(draw)

    def action_add_step(self) -> None:
        """Below the highlighted step while the Steps list has focus, else after the open steps."""
        item = self.current_item()
        if item is None:
            return
        if item.status in items.FINISHED:
            self.notify(f"This item is {item.status}. Reopen it first (s), then add the step.", severity="warning")
            return
        focused = self.focused_step()
        after = self.shown[focused[1]] if focused else None

        def added(answer: tuple[str, str] | None) -> None:
            if answer:
                landed: list[int] = []
                self.write(lambda: landed.append(items.add_step(item, *answer, after=after)))
                fresh = self.current_item()
                if landed and fresh and landed[0] < len(fresh.steps) and fresh.steps[landed[0]] in self.shown:
                    self.select_step(self.shown.index(fresh.steps[landed[0]]))

        self.push_screen(StepScreen(f"New step in “{item.title}”"), added)

    def action_edit_step(self) -> None:
        if (focused := self.focused_step()) is None:
            return
        item, index = focused
        step = self.shown[index]
        if step.kind == items.ITEM_KIND:
            self.go_to(step.target)
            return

        def edited(answer: tuple[str, str] | None) -> None:
            if answer and answer != (step.text, step.kind):
                self.write(lambda: items.edit_step(item, step, *answer))

        self.push_screen(StepScreen("Edit step", step.text, step.kind), edited)

    def action_move_step(self, delta: int) -> None:
        if (focused := self.focused_step()) is None:
            return
        item, index = focused
        if not 0 <= index + delta < len(self.shown):
            return
        step, other = self.shown[index], self.shown[index + delta]  # the neighbor on screen: hidden steps stay put
        self.write(lambda: items.swap_steps(item, step, other))
        self.select_step(index + delta)

    def action_delete_step(self) -> None:
        if (focused := self.focused_step()) is None:
            return
        item, index = focused
        step = self.shown[index]

        def confirmed(answer: str | None) -> None:
            if answer == "delete":
                self.write(lambda: items.delete_step(item, step))

        choices = [("delete", Text("delete step", "bold")), ("cancel", Text("cancel"))]
        self.push_screen(ChoiceScreen(f"Delete the step “{step.text}”?", choices), confirmed)

    def focused_pr(self) -> tuple[Item, items.PR] | None:
        item = self.current_item()
        rows = self.query_one("#prs", ListView)
        if item is None or not rows.has_focus or rows.index is None or rows.index >= len(item.prs):
            return None
        return item, item.prs[rows.index]

    def action_add_pr(self) -> None:
        item = self.current_item()
        if item is None:
            return

        def added(answer: tuple[str, str, str] | None) -> None:
            if not answer:
                return
            link, title, state = answer
            if items.pr_link(link) is None:
                self.notify(f"Not a link: {link}", severity="warning")
            elif any(items.pr_link(p.url) == items.pr_link(link) for p in item.prs):
                self.notify("This PR is already there.", severity="warning")
            else:
                self.write(lambda: items.add_pr(item, link, state, title))

        self.push_screen(PRScreen(f"New PR in “{item.title}”"), added)

    def action_open_pr(self) -> None:
        if focused := self.focused_pr():
            self.open_url(focused[1].url)

    def action_next_pr_state(self) -> None:
        if (focused := self.focused_pr()) is None:
            return
        item, pr = focused
        states = items.PR_STATES
        following = states[(states.index(pr.state) + 1) % len(states)] if pr.state in states else states[0]
        self.write(lambda: items.update_pr(item, pr, state=following))

    def action_edit_pr(self) -> None:
        if (focused := self.focused_pr()) is None:
            return
        item, pr = focused

        def edited(answer: tuple[str, str, str] | None) -> None:
            if not answer:
                return
            link, title, state = answer
            if items.pr_link(link) is None:
                self.notify(f"Not a link: {link}", severity="warning")
            elif (link, title, state) != (pr.url, pr.title, pr.state):
                self.write(lambda: items.update_pr(item, pr, state=state, title=title, url=link))

        self.push_screen(PRScreen("Edit PR", f"{pr.url} {pr.title}".strip(), pr.state), edited)

    def action_delete_pr(self) -> None:
        if (focused := self.focused_pr()) is None:
            return
        item, pr = focused

        def confirmed(answer: str | None) -> None:
            if answer == "delete":
                prs.drop(self.config_path, str(item.path), pr.url)  # so that gh never adds it back
                self.write(lambda: items.delete_pr(item, pr))

        choices = [("delete", Text("delete PR", "bold")), ("cancel", Text("cancel"))]
        self.push_screen(ChoiceScreen(f"Delete {pr.name} from this item?", choices), confirmed)

    def action_status(self) -> None:
        item = self.current_item()
        if item is None:
            return
        choices = [(s, status_text(s)) for s in items.STATUSES]

        def chosen(status: str | None) -> None:
            if status == "dropped" and status != item.status:
                self.push_screen(TextScreen(f"Why drop “{item.title}”?", "the reason, kept in Left off"), dropped)
            elif status and status != item.status:
                self.write(lambda: items.set_status(item, status))

        def dropped(reason: str | None) -> None:
            if reason:  # no reason, no drop: it is what stops the idea from coming back unexamined
                self.write(lambda: items.set_status(item, "dropped", reason=reason))

        self.push_screen(ChoiceScreen(f"Status of “{item.title}”", choices, item.status), chosen)

    def action_group(self) -> None:
        item = self.current_item()

        def chosen(key: str | None) -> None:
            if key == NEW_GROUP:
                self.new_group(item)
            elif key is not None and (target := item) is not None:
                self.write(lambda: items.set_group(target, None if key == NO_GROUP else key))

        self.push_screen(GroupScreen(self, item), chosen)

    def new_group(self, item: Item | None) -> None:
        def named(name: str | None) -> None:
            if not name:
                return

            def colored(color: str | None) -> None:
                if not color:
                    return
                key = config.add_group(self.config_path, name, color)
                if (target := item) is None:
                    self.reload()
                else:
                    self.write(lambda: items.set_group(target, key))

            self.push_screen(ChoiceScreen(f"Color for “{name}”", self.color_choices(), self.suggested_color()), colored)

        self.push_screen(TextScreen("New group name", "e.g. Auth migration"), named)

    def color_choices(self, group_key: str | None = None) -> list[tuple[str, Text]]:
        """Every palette color, naming the other groups that already use it."""
        users: dict[str, list[str]] = {}
        for g in self.cfg.groups.values():
            if g.key != group_key:
                users.setdefault(g.color, []).append(g.name)
        return [
            (c, Text.assemble((f"▌ {c}", hex_), (f"  {', '.join(users[c])}" if c in users else "", "dim")))
            for c, hex_ in config.PALETTE.items()
        ]

    def suggested_color(self) -> str:
        """The first color no group uses; when all are taken, the least used one."""
        counts = Counter(g.color for g in self.cfg.groups.values())
        return min(config.PALETTE, key=lambda c: counts[c])

    def edit_group(self, key: str, name: str | None = None, color: str | None = None) -> None:
        try:
            config.update_group(self.config_path, key, name=name, color=color)
        except config.GroupNotEditable:
            self.notify(f"Edit the group in {self.config_path}", severity="warning")
        self.reload()

    def delete_group(self, key: str) -> None:
        """Take the group off its items first, so that no item is left pointing to a missing group."""
        for item in [i for i in self.items.values() if i.group == key]:
            try:
                items.set_group(item, None)
            except WriteConflict:
                self.notify(f"{item.path.name} changed on disk, so the group was kept. Try again.", severity="warning")
                self.reload()
                return
        try:
            config.delete_group(self.config_path, key)
        except config.GroupNotEditable:
            self.notify(f"Edit the group in {self.config_path}", severity="warning")
        self.reload()

    def action_tags(self) -> None:
        item = self.current_item()
        if item is None:
            return
        tags = list(self.cfg.tags) + [t for t in item.tags if t not in self.cfg.tags]

        def chosen(selected: list[str] | None) -> None:
            if selected is not None and selected != item.tags:
                self.write(lambda: items.set_tags(item, selected))

        self.push_screen(TagScreen(tags, item.tags), chosen)

    def action_new_idea(self) -> None:
        def created(title: str | None) -> None:
            if title:
                path = items.create_idea(self.cfg.folder, title, now=datetime.now())
                if self.viewed is not None:
                    self.viewed[str(path)] = []
                    self.save_viewed()
                self.current = path.stem
                self.horizon = "later"
                self.reload()

        self.push_screen(TextScreen("New idea", "a short title"), created)

    def action_ask(self) -> None:
        """Ask Claude Code about the item, in the background: the answer lands under Explorations."""
        item = self.current_item()
        if item is None:
            return
        if not explore.available(self.cfg.explore_command):
            self.notify(f"Claude Code was not found ({self.cfg.explore_command}). Set explore_command in {self.config_path}.",
                        severity="warning")
            return
        now = datetime.now()
        if any(items.exploration_state(e, now, explore.pid_alive) == "running" for e in item.explorations):
            self.notify("Still asking about this item. The answer lands under Explorations.", severity="warning")
            return
        latest = next((e for e in reversed(item.explorations) if e.state == "done"), None)
        deep = bool(self.cfg.keep_digging) and explore.available(self.cfg.keep_digging)

        def chosen(answer: tuple[str | None, str | None] | None) -> None:
            if not answer:
                return
            try:
                started = self.spawner(self.config_path, item.slug, *answer)
            except OSError as e:
                self.notify(f"Could not start rumo explore: {e}", severity="error")
                return
            if isinstance(started, subprocess.Popen):
                self.spawned.append(started)
            self.notify(f"Asking about “{item.title}”… The answer lands under Explorations.")

        self.push_screen(AskScreen(item.title, self.cfg.lenses, latest.next_questions if latest else [], deep), chosen)

    def action_spark(self) -> None:
        """New ideas from the ones you have, read by Claude Code in the background; questions first if it needs."""
        data = spark.load(self.cfg.folder)
        state, _ = spark.current(data, datetime.now(), explore.pid_alive)
        if state == "running" or (self.spark_proc is not None and self.spark_proc.poll() is None):
            self.notify("Still reading your ideas. A notice comes when it is done.")
        elif state == "asking":
            def answered(answers: list[str] | None) -> None:
                if answers is not None:
                    spark.answer(self.cfg.folder, answers)
                    self.start_spark()

            answers = data.get("answers")
            self.push_screen(SparkQuestionsScreen(str(data.get("direction") or ""), spark.questions_of(data),
                                                  answers if isinstance(answers, list) else []), answered)
        elif state == "ready":
            ideas = spark.ideas_of(data)
            titles = {i.slug: i.title for i in self.items.values()}
            self.push_screen(SparkIdeasScreen(str(data.get("direction") or ""), ideas, titles),
                             lambda thrown: self.keep_ideas(ideas, thrown))
        elif not spark.readable(list(self.items.values())):
            self.notify("No ideas to read yet. Press n to note one.")
        else:
            self.start_spark()

    def start_spark(self) -> None:
        if not explore.available(self.cfg.explore_command):
            self.notify(f"Claude Code was not found ({self.cfg.explore_command}). Set explore_command in {self.config_path}.",
                        severity="warning")
            return
        before = self.spark_key()
        try:
            started = self.spark_spawner(self.config_path)
        except OSError as e:
            self.notify(f"Could not start rumo spark: {e}", severity="error")
            return
        if isinstance(started, subprocess.Popen):
            self.spawned.append(started)
            self.spark_proc, self.spark_from = started, before
        self.notify("Reading your ideas…")

    def keep_ideas(self, ideas: list[spark.Idea], thrown: set[int] | None) -> None:
        """Enter on the ideas: the ones not thrown away join Later, unread; the others never come back."""
        if thrown is None:
            return
        created = spark.keep(self.cfg.folder, list(self.items.values()), ideas, thrown)
        self.notify(f"{plural(len(created), 'idea')} added to Later." if created
                    else "Nothing added. Those ideas will not be suggested again.")
        if created:  # selected before the reload, or its highlight would land after go_to's and win
            self.current, self.horizon = created[0].stem, "later"
        self.reload()
        if created:
            self.go_to(created[0].stem)

    def action_branch(self) -> None:
        """A new idea from this item, or its next open step (the highlighted one in Steps) as an item."""
        item = self.current_item()
        if item is None:
            return
        focused = self.focused_step()
        candidates = [self.shown[focused[1]]] if focused else item.steps
        step = next((s for s in candidates if not s.done and s.kind != items.ITEM_KIND), None)
        if item.status in items.FINISHED:
            step = None

        def chosen(answer: tuple[str, str] | None) -> None:
            if not answer:
                return
            mode, title = answer
            created: list[Path] = []
            if mode == "step" and (picked := step) is not None:
                self.write(lambda: created.append(items.step_to_item(item, picked, title, now=datetime.now())))
            else:
                self.write(lambda: created.append(items.create_idea(
                    self.cfg.folder, title, now=datetime.now(), group=item.group, tags=item.tags, parent=item.slug)))
            if created:
                if self.viewed is not None:
                    self.viewed[str(created[0])] = []
                    self.save_viewed()
                self.reload()
                self.go_to(created[0].stem)

        self.push_screen(BranchScreen(item.title, step.shown if step else None), chosen)

    def action_edit(self) -> None:
        item = self.current_item()
        if item is None:
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
        before = seen_keys(item)
        with self.suspend():
            subprocess.run([*shlex.split(editor, posix=os.name != "nt"), str(item.path)], check=False)
        self.saw(item.path, before)
        self.reload()

    def action_copy_resume(self) -> None:
        item = self.current_item()
        if item is None or not item.sessions:
            self.notify("This item has no sessions.", severity="warning")
            return
        sessions = self.query_one("#sessions", ListView)
        recent = list(reversed(item.sessions))
        index = sessions.index if sessions.has_focus and sessions.index is not None else 0
        command = recent[index].resume_command
        if shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=command.encode(), check=False)
        else:
            self.copy_to_clipboard(command)  # OSC 52: works in most terminals outside macOS
        self.notify(f"Copied: {command}")

    def action_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.add_class("on")
        field.focus()

    def action_clear_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.value = ""
        field.remove_class("on")
        self.query_one("#timeline", OptionList).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.filter_text = event.value
            self.build_timeline()
            self.show_detail()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.query_one("#timeline", OptionList).focus()
