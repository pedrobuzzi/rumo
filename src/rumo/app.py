"""The `rumo` TUI: a Timeline (Past / Now / Later) on the left, the selected item on the right."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Markdown, OptionList, SelectionList, Static
from textual.widgets.option_list import Option

from rumo import config, items
from rumo.items import Item, WriteConflict

# Color only ever means the group, so a status is a shape: full when active, empty when not started.
SHAPES = {"doing": "●", "waiting": "◐", "idea": "○", "paused": "◌", "done": "✓"}
VIEWS = {"timeline": "Timeline", "projects": "Projects", "repos": "Repos"}
HORIZONS: tuple[str, ...] = ("past", "now", "later")
CYCLE = ("now", "later", "past")  # what `h` steps through, starting where rumo opens
HORIZON_SAYS = {
    "now": "doing and waiting",
    "later": "ideas and paused work",
    "past": f"done in the last {items.PAST_WINDOW_DAYS} days",
}
UNGROUPED = "-"  # the "No group" or "No repo" block, which always comes last
MAX_LABEL = 16  # the project label fits the longest group name up to this many characters
NEW_GROUP = "+new"
NO_GROUP = "-none"

def key_line(keys: list[tuple[str, str]]) -> Text:
    return Text.assemble(*(part for key, label in keys for part in ((key, "bold"), (f" {label}  ", "dim"))))


KEYS = key_line([
    ("v", "view"), ("h", "now/later/past"), ("s", "status"), ("g", "group"), ("t", "tags"), ("n", "new idea"),
    ("a", "add step"), ("tab", "steps"), ("e", "edit"), ("c", "copy resume"), ("/", "filter"), ("q", "quit"),
])
STEP_KEYS = key_line([  # while the Steps list has focus
    ("a", "add"), ("enter", "edit"), ("shift+↑↓", "move"), ("d", "delete"), ("space", "check"), ("esc", "back"),
])


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


EMPTY = {
    "past": f"Nothing finished in the last {items.PAST_WINDOW_DAYS} days.",
    "now": "Nothing in progress. Press h to see what is next.",
    "later": "Nothing for later. Press n to capture an idea.",
}


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
    """A step's text and who does it: Tab switches between [agent] and [manual], Enter saves."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "cancel"),
        Binding("tab", "switch_kind", "agent/manual", priority=True),
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


class StepList(ListView):
    BINDINGS = [
        Binding("space", "app.toggle_step", "toggle step"),
        Binding("enter", "app.edit_step", "edit step"),
        Binding("d", "app.delete_step", "delete step"),
        Binding("shift+up", "app.move_step(-1)", "move up"),
        Binding("shift+down", "app.move_step(1)", "move down"),
    ]

    def on_focus(self) -> None:
        self.app.query_one("#keys", Static).update(STEP_KEYS)

    def on_blur(self) -> None:
        self.app.query_one("#keys", Static).update(KEYS)


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
    #steps, #sessions { height: auto; max-height: 12; }
    Markdown { margin: 0; padding: 0; }
    #legend, #keys { height: auto; padding: 0 1; }
    ModalScreen { align: center middle; }
    .dialog { width: 60; height: auto; max-height: 80%; border: round $primary; background: $surface; padding: 0 1; }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    .dialog-hint { margin-top: 1; }
    .dialog OptionList, .dialog SelectionList { height: auto; max-height: 20; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("v", "next_view", "view"),
        Binding("1", "view('timeline')", show=False),
        Binding("2", "view('projects')", show=False),
        Binding("3", "view('repos')", show=False),
        Binding("h", "next_horizon", "horizon"),
        Binding("s", "status", "status"),
        Binding("g", "group", "group"),
        Binding("t", "tags", "tags"),
        Binding("n", "new_idea", "new idea"),
        Binding("a", "add_step", "add step"),
        Binding("e", "edit", "edit"),
        Binding("c", "copy_resume", "copy resume"),
        Binding("slash", "filter", "filter"),
        Binding("escape", "clear_filter", "clear filter", show=False),
    ]

    def __init__(self, config_path: Path | None = None, today: date | None = None) -> None:
        super().__init__()
        self.config_path = config_path or config.default_path()
        self.cfg = config.load(self.config_path)
        self.today = today
        self.items: dict[str, Item] = {}
        self.current: str | None = None
        self.filter_text = ""
        self.show_older = False
        self.view = "timeline"
        self.horizon = "now"  # one horizon at a time; never remembered, rumo always opens on Now
        self.mtimes: dict[str, float] = {}
        self.load_state()

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter by title, tag, group or text (Esc clears)", id="filter")
        with Horizontal(id="main"):
            yield OptionList(id="timeline")
            with VerticalScroll(id="detail"):
                yield Static(id="header")
                yield Markdown(id="body")
                yield Label("Steps", classes="section")
                yield StepList(id="steps")
                yield Label("Sessions  (c copies the resume command)", classes="section")
                yield ListView(id="sessions")
        yield Static(id="legend")
        yield Static(KEYS, id="keys")

    def on_mount(self) -> None:
        self.cfg.folder.mkdir(parents=True, exist_ok=True)
        if not self.cfg.error:
            config.ensure_file(self.config_path, self.cfg.folder, self.cfg.language)
        self.reload()
        self.query_one("#timeline", OptionList).focus()
        self.set_interval(2.0, self.check_changes)

    # ---------- data ----------

    def read_mtimes(self) -> dict[str, float]:
        files = list(self.cfg.folder.glob("*.md"))
        if self.config_path.exists():
            files.append(self.config_path)
        mtimes = {}
        for path in files:
            try:
                mtimes[str(path)] = path.stat().st_mtime
            except OSError:
                continue
        return mtimes

    def check_changes(self) -> None:
        if self.read_mtimes() != self.mtimes:
            self.reload()

    def reload(self) -> None:
        self.cfg = config.load(self.config_path)
        self.mtimes = self.read_mtimes()
        self.items = {i.slug: i for i in items.list_items(self.cfg.folder)}
        self.build_timeline()
        self.show_detail()
        self.query_one("#legend", Static).update(self.legend())

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
        try:
            operation()
        except WriteConflict:
            self.notify("The item just changed on disk. Reloaded; try again.", severity="warning")
        self.reload()

    # ---------- timeline ----------

    def build_timeline(self) -> None:
        timeline = self.query_one("#timeline", OptionList)
        visible = [i for i in self.items.values() if self.matches(i)]
        tl = items.timeline(visible, today=self.today, show_older=self.show_older)

        # One horizon at a time; a filter shows all three, so that no match stays hidden.
        filtering = bool(self.filter_text.strip())
        shown = list(HORIZONS) if filtering else [self.horizon]
        build = {"timeline": self.timeline_rows, "projects": self.project_rows, "repos": self.repo_rows}[self.view]
        if not self.items:
            options = [self.message("No items yet. Press n to capture an idea, or run /rumo new in an agent session.")]
        elif filtering:
            options = build(tl, shown) or [self.message("No matches.")]
        else:
            options = [self.banner(tl), *(build(tl, shown) or [self.message(EMPTY[self.horizon])])]

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

    def row(self, item: Item, label: bool) -> Table:
        """Symbol, project label, title and progress (or the date in Past) on one line; the next step below."""
        group = self.cfg.groups.get(item.group)
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(width=1, no_wrap=True)
        if label:
            grid.add_column(width=self.name_width + 2, no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(width=6, no_wrap=True, justify="right")
        if item.status == "done":
            right = short_date(item.done_at)
        else:
            right = f"{sum(s.done for s in item.steps)}/{len(item.steps)}" if item.steps else ""
        symbol = Text(SHAPES[item.status], "" if item.status == "doing" else "dim")
        title = Text(display_title(item.title, group.name if group else ""))
        grid.add_row(symbol, *([self.label(group)] if label else []), title, Text(right, "dim"))
        if item.status != "done" and (step := item.next_step):
            grid.add_row("", *([""] if label else []), Text(f"↳ {step.text}", "dim"), "")
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

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "timeline" and event.option.id == "older":
            self.show_older = True
            self.build_timeline()

    # ---------- detail ----------

    def current_item(self) -> Item | None:
        return self.items.get(self.current) if self.current else None

    def show_detail(self) -> None:
        item = self.current_item()
        header = self.query_one("#header", Static)
        body = self.query_one("#body", Markdown)
        steps = self.query_one("#steps", ListView)
        sessions = self.query_one("#sessions", ListView)
        step_index, session_index = steps.index, sessions.index
        steps.clear()
        sessions.clear()
        if item is None:
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
        header.update(line)

        parts = []
        for name in ("left off", "context"):
            if content := item.sections.get(name):
                parts.append(f"**{name.capitalize()}**\n\n{content}")
        body.update("\n\n".join(parts))

        steps.extend(ListItem(Label(self.step_label(s))) for s in item.steps)
        sessions.extend(
            ListItem(Label(Text.assemble((s.when, "dim"), "  ", s.repo, "  ", (s.id[:8], "cyan"), (" closed" if s.closed else "", "dim"))))
            for s in reversed(item.sessions)
        )
        if item.steps:
            self.select_step(step_index or 0)
        if item.sessions:
            sessions.index = min(session_index or 0, len(item.sessions) - 1)

    @staticmethod
    def step_label(step: items.Step) -> Text:
        kind = f"[{step.kind}] " if step.kind else ""
        if step.done:
            return Text(f"[x] {kind}{step.text}", "dim strike")
        return Text.assemble("[ ] ", (kind, "yellow" if step.kind == "manual" else "dim"), step.text)

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
        if (view := config.load_state(self.config_path).get("view")) in VIEWS:
            self.view = view

    def save_state(self) -> None:
        config.update_state(self.config_path, view=self.view)

    def action_toggle_step(self) -> None:
        item = self.current_item()
        index = self.query_one("#steps", ListView).index
        if item is None or index is None or index >= len(item.steps):
            return
        step = item.steps[index]
        self.write(lambda: items.toggle_step(item, step))

    def focused_step(self) -> tuple[Item, int] | None:
        """The current item and its highlighted step, when the Steps list has focus."""
        item = self.current_item()
        steps = self.query_one("#steps", ListView)
        if item is None or not steps.has_focus or steps.index is None or steps.index >= len(item.steps):
            return None
        return item, steps.index

    def select_step(self, index: int) -> None:
        item = self.current_item()
        if not item or not item.steps:
            return
        steps = self.query_one("#steps", ListView)
        steps.index = max(0, min(index, len(item.steps) - 1))

        # Right after a rebuild the highlight lands on a row about to be removed: draw it again after.
        def highlight() -> None:
            for n, row in enumerate(steps.query(ListItem)):
                row.highlighted = n == steps.index

        steps.call_after_refresh(highlight)

    def action_add_step(self) -> None:
        """Below the highlighted step while the Steps list has focus, else after the open steps."""
        item = self.current_item()
        if item is None:
            return
        if item.status == "done":
            self.notify("This item is done. Reopen it first (s), then add the step.", severity="warning")
            return
        focused = self.focused_step()
        after = item.steps[focused[1]] if focused else None

        def added(answer: tuple[str, str] | None) -> None:
            if answer:
                landed: list[int] = []
                self.write(lambda: landed.append(items.add_step(item, *answer, after=after)))
                if landed:
                    self.select_step(landed[0])

        self.push_screen(StepScreen(f"New step in “{item.title}”"), added)

    def action_edit_step(self) -> None:
        if (focused := self.focused_step()) is None:
            return
        item, index = focused
        step = item.steps[index]

        def edited(answer: tuple[str, str] | None) -> None:
            if answer and answer != (step.text, step.kind):
                self.write(lambda: items.edit_step(item, step, *answer))

        self.push_screen(StepScreen("Edit step", step.text, step.kind), edited)

    def action_move_step(self, delta: int) -> None:
        if (focused := self.focused_step()) is None:
            return
        item, index = focused
        moved: list[bool] = []
        self.write(lambda: moved.append(items.move_step(item, item.steps[index], delta)))
        if moved and moved[0]:
            self.select_step(index + delta)

    def action_delete_step(self) -> None:
        if (focused := self.focused_step()) is None:
            return
        item, index = focused
        step = item.steps[index]

        def confirmed(answer: str | None) -> None:
            if answer == "delete":
                self.write(lambda: items.delete_step(item, step))

        choices = [("delete", Text("delete step", "bold")), ("cancel", Text("cancel"))]
        self.push_screen(ChoiceScreen(f"Delete the step “{step.text}”?", choices), confirmed)

    def action_status(self) -> None:
        item = self.current_item()
        if item is None:
            return
        choices = [(s, status_text(s)) for s in items.STATUSES]

        def chosen(status: str | None) -> None:
            if status and status != item.status:
                self.write(lambda: items.set_status(item, status))

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
                self.current = path.stem
                self.horizon = "later"
                self.reload()

        self.push_screen(TextScreen("New idea", "a short title"), created)

    def action_edit(self) -> None:
        item = self.current_item()
        if item is None:
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
        with self.suspend():
            subprocess.run([*shlex.split(editor, posix=os.name != "nt"), str(item.path)], check=False)
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
