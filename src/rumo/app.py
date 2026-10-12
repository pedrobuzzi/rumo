"""The `rumo` TUI: a Timeline (Past / Now / Later) on the left, the selected item on the right."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

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
VIEWS = {"timeline": "Timeline", "grouped": "Grouped", "projects": "Projects"}
UNGROUPED = "-"  # the "No group" block, which always comes last
NEW_GROUP = "+new"
NO_GROUP = "-none"

KEYS = Text.assemble(
    *(
        part
        for key, label in [
            ("v", "view"), ("p", "past"), ("l", "later"), ("s", "status"), ("g", "group"), ("t", "tags"),
            ("n", "new idea"), ("e", "edit"), ("space", "step"), ("c", "copy resume"), ("/", "filter"), ("q", "quit"),
        ]
        for part in ((key, "bold"), (f" {label}  ", "dim"))
    )
)


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


class StepList(ListView):
    BINDINGS = [Binding("space", "app.toggle_step", "toggle step")]


# ---------- app ----------


class RumoApp(App):
    TITLE = "rumo"
    CSS = """
    Screen { layout: vertical; }
    #filter { display: none; }
    #filter.on { display: block; }
    #main { height: 1fr; }
    #timeline { width: 45%; height: 1fr; border: round $primary-darken-1; }
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
        Binding("2", "view('grouped')", show=False),
        Binding("3", "view('projects')", show=False),
        Binding("p", "toggle_past", "past"),
        Binding("l", "toggle_later", "later"),
        Binding("s", "status", "status"),
        Binding("g", "group", "group"),
        Binding("t", "tags", "tags"),
        Binding("n", "new_idea", "new idea"),
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
        self.past_open = True
        self.later_open = True
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

        # A filter opens collapsed sections, so that no match stays hidden.
        filtering = bool(self.filter_text.strip())
        past_open, later_open = self.past_open or filtering, self.later_open or filtering

        options: list[Option] = []
        if not self.items:
            options.append(Option(Text(
                "No items yet. Press n to capture an idea, or run /rumo new in an agent session.", "dim"
            ), id="empty", disabled=True))
        build = {"timeline": self.timeline_rows, "grouped": self.grouped_rows, "projects": self.project_rows}[self.view]
        options += build(tl, past_open, later_open)

        timeline.clear_options()
        timeline.add_options(options)
        timeline.border_title = self.view_tabs()

        ids = [o.id for o in options]
        target = f"item:{self.current}" if self.current else None
        if target not in ids:
            first_now = f"item:{tl.now[0].slug}" if tl.now else None
            first_any = next((i for i in ids if i and i.startswith("item:")), None)
            target = first_now or first_any
        self.current = target.removeprefix("item:") if target else None
        if target:
            timeline.highlighted = ids.index(target)

    # Timeline: one list by time; a bar in the group color starts each row, titles stay plain.
    def timeline_rows(self, tl: items.Timeline, past_open: bool, later_open: bool) -> list[Option]:
        def flat(next_step: bool = False) -> Callable[[list[Item]], list[Option]]:
            return lambda its: [self.item_option(i, bar=True, next_step=next_step) for i in its]

        return [
            *self.section("past", tl.past, tl.older, past_open, flat()),
            *self.section("now", tl.now, 0, True, flat(next_step=True)),
            *self.section("later", tl.later, 0, later_open, flat()),
        ]

    # Grouped: Past by date with titles in the group color; Now and Later in one block per group.
    def grouped_rows(self, tl: items.Timeline, past_open: bool, later_open: bool) -> list[Option]:
        return [
            *self.section("past", tl.past, tl.older, past_open, lambda its: [self.item_option(i, colored=True) for i in its]),
            *self.section("now", tl.now, 0, True, lambda its: self.group_blocks("now", its, next_step=True)),
            *self.section("later", tl.later, 0, later_open, lambda its: self.group_blocks("later", its)),
        ]

    def group_blocks(self, section: str, its: list[Item], next_step: bool = False) -> list[Option]:
        rows = []
        for key, group, members in self.by_group(its):
            rows.append(Option(self.group_label(group, str(len(members))), id=f"group:{section}:{key}", disabled=True))
            rows += [self.item_option(i, colored=True, indent=1, next_step=next_step) for i in members]
        return rows

    # Projects: one block per group holding its Past, Now and Later items.
    def project_rows(self, tl: items.Timeline, past_open: bool, later_open: bool) -> list[Option]:
        rows = []
        if past_open and tl.older:
            rows.append(self.older_option(tl.older, "older done items"))
        for key, group, members in self.by_group([*tl.past, *tl.now, *tl.later]):
            parts = {
                "past": [i for i in members if i.status == "done"],
                "now": [i for i in members if i.status in ("doing", "waiting")],
                "later": [i for i in members if i.status in ("paused", "idea")],
            }
            shown = {"past": past_open, "now": True, "later": later_open}
            counts = " · ".join(f"{len(its)} {name}{'' if shown[name] else ' ▸'}" for name, its in parts.items() if its)
            rows.append(Option(self.group_label(group, counts), id=f"group:{key}", disabled=True))
            for name, its in parts.items():
                if shown[name]:
                    rows += [self.item_option(i, indent=1, next_step=name == "now") for i in its]
        return rows

    def section(
        self, key: str, its: list[Item], older: int, is_open: bool, body: Callable[[list[Item]], list[Option]]
    ) -> list[Option]:
        if not its and not older:
            return []
        name = key.capitalize()
        if key == "now":
            label = Text.assemble(("  ", ""), (name, "bold"), (f"  {len(its)}", "dim"))
        elif is_open:
            note = ("all" if self.show_older else f"last {items.PAST_WINDOW_DAYS} days") if key == "past" else str(len(its))
            label = Text.assemble(("▾ ", "dim"), (name, "bold"), (f"  {note}", "dim"))
        else:
            label = Text.assemble(("▸ ", "dim"), (name, "bold"), (f"  {len(its) + older} hidden · {key[0]} opens", "dim"))
        rows = [Option(label, id=f"header:{key}", disabled=True)]
        if is_open:
            if older:
                rows.append(self.older_option(older, "older"))
            rows += body(its)
        return rows

    @staticmethod
    def older_option(count: int, what: str) -> Option:
        return Option(Text(f"  … {count} {what} (enter to show)", "dim italic"), id="older")

    def by_group(self, its: list[Item]) -> list[tuple[str, config.Group | None, list[Item]]]:
        """Items bucketed by group, in config order, with ungrouped (or unknown-group) items last."""
        buckets: dict[str, list[Item]] = {}
        for i in its:
            buckets.setdefault(i.group if i.group in self.cfg.groups else UNGROUPED, []).append(i)
        return [(key, self.cfg.groups.get(key), buckets[key]) for key in [*self.cfg.groups, UNGROUPED] if key in buckets]

    @staticmethod
    def group_label(group: config.Group | None, note: str) -> Text:
        if group is None:
            return Text.assemble(("No group", "bold dim"), (f"  {note}", "dim"))
        return Text.assemble((group.name, f"bold {config.PALETTE.get(group.color, '')}"), (f"  {note}", "dim"))

    def item_option(self, item: Item, bar: bool = False, colored: bool = False, indent: int = 0, next_step: bool = False) -> Option:
        return Option(self.row(item, bar, colored, indent, next_step), id=f"item:{item.slug}")

    def row(self, item: Item, bar: bool, colored: bool, indent: int, next_step: bool) -> Text:
        color = self.group_color(item)
        text = Text(" " * indent)
        if bar:
            text.append("▌ " if color else "  ", color)
        label = short_date(item.done_at) if item.status == "done" else item.status
        text.append_text(status_text(item.status, f"{label:<8}"))
        text.append(item.title, color if colored else "")
        if item.steps:
            done = sum(s.done for s in item.steps)
            text.append(f"  {done}/{len(item.steps)}", "dim")
        if next_step and (step := item.next_step):
            text.append("\n" + " " * (indent + (2 if bar else 0) + 10) + f"↳ {step.text}", "dim")
        return text

    def view_tabs(self) -> Text:
        text = Text(" ")
        for n, (key, name) in enumerate(VIEWS.items(), start=1):
            text.append(f"{n} {name}", "bold" if key == self.view else "dim")
            text.append("  " if n < len(VIEWS) else " ")
        return text

    def legend(self) -> Text:
        text = Text()
        if self.cfg.error:
            text.append(f"Config error, using defaults: {self.cfg.error}\n", "bold red")
        if not self.cfg.groups:
            return text.append("No groups yet. Press g on an item to create one.", "dim")
        for group in self.cfg.groups.values():
            text.append("▌ ", config.PALETTE.get(group.color, ""))
            text.append(group.name + "    ")
        return text

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
            line.append("   group ", "dim")
            line.append(f"▌ {group.name}", config.PALETTE.get(group.color, ""))
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
            steps.index = min(step_index or 0, len(item.steps) - 1)
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
        self.layout_changed()

    def action_toggle_past(self) -> None:
        self.past_open = not self.past_open
        self.layout_changed()

    def action_toggle_later(self) -> None:
        self.later_open = not self.later_open
        self.layout_changed()

    def layout_changed(self) -> None:
        self.save_state()
        self.build_timeline()
        self.show_detail()

    # The view and collapsed sections survive restarts, in a file of their own: config.toml only
    # ever changes when the user edits a group.
    def state_path(self) -> Path:
        return self.config_path.parent / "state.json"

    def load_state(self) -> None:
        try:
            data = json.loads(self.state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if data.get("view") in VIEWS:
            self.view = data["view"]
        if isinstance(past_open := data.get("past_open"), bool):
            self.past_open = past_open
        if isinstance(later_open := data.get("later_open"), bool):
            self.later_open = later_open

    def save_state(self) -> None:
        state = {"view": self.view, "past_open": self.past_open, "later_open": self.later_open}
        try:
            self.state_path().write_text(json.dumps(state) + "\n", encoding="utf-8")
        except OSError:
            pass  # remembering is a convenience; never fail an action over it

    def action_toggle_step(self) -> None:
        item = self.current_item()
        index = self.query_one("#steps", ListView).index
        if item is None or index is None or index >= len(item.steps):
            return
        step = item.steps[index]
        self.write(lambda: items.toggle_step(item, step))

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
