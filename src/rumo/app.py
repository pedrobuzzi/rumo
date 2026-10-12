"""The `rumo` TUI: a Timeline (Past / Now / Later) on the left, the selected item on the right."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
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

STATUS_STYLE = {"idea": "cyan", "doing": "green", "waiting": "yellow", "paused": "grey50", "done": "blue"}
NEW_GROUP = "+new"
NO_GROUP = "-none"

KEYS = Text.assemble(
    *(
        part
        for key, label in [
            ("s", "status"), ("g", "group"), ("t", "tags"), ("n", "new idea"), ("e", "edit"),
            ("space", "step"), ("c", "copy resume"), ("/", "filter"), ("q", "quit"),
        ]
        for part in ((key, "bold"), (f" {label}   ", "dim"))
    )
)


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

    def __init__(self, title: str, choices: list[tuple[str, Text]], current: str | None = None) -> None:
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            options = []
            for n, (value, label) in enumerate(self.choices, start=1):
                prefix = f"{n}  " if n <= 9 else "   "
                options.append(Option(Text.assemble((prefix, "dim"), label), id=value))
            yield OptionList(*options)

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
                self.dismiss(self.choices[n - 1][0])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class TextScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield Input(placeholder=self.placeholder)

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
    .dialog OptionList, .dialog SelectionList { height: auto; max-height: 20; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
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
        self.mtimes: dict[str, float] = {}

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

    def group_style(self, item: Item) -> str:
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

        options: list[Option] = []
        if not self.items:
            options.append(Option(Text(
                "No items yet. Press n to capture an idea, or run /rumo new in an agent session.", "dim"
            ), id="empty", disabled=True))
        if tl.past or tl.older:
            options.append(self.header("Past", f"last {items.PAST_WINDOW_DAYS} days" if not self.show_older else "all", "past"))
            if tl.older:
                options.append(Option(Text(f"  … {tl.older} older (enter to show)", "dim italic"), id="older"))
            options += [Option(self.row(i, short_date(i.done_at)), id=f"item:{i.slug}") for i in tl.past]
        if tl.now:
            options.append(self.header("Now", str(len(tl.now)), "now"))
            options += [Option(self.row(i, i.status, next_step=True), id=f"item:{i.slug}") for i in tl.now]
        if tl.later:
            options.append(self.header("Later", str(len(tl.later)), "later"))
            options += [Option(self.row(i, i.status), id=f"item:{i.slug}") for i in tl.later]

        timeline.clear_options()
        timeline.add_options(options)

        ids = [o.id for o in options]
        target = f"item:{self.current}" if self.current else None
        if target not in ids:
            first_now = f"item:{tl.now[0].slug}" if tl.now else None
            first_any = next((i for i in ids if i and i.startswith("item:")), None)
            target = first_now or first_any
        self.current = target.removeprefix("item:") if target else None
        if target:
            timeline.highlighted = ids.index(target)

    @staticmethod
    def header(name: str, note: str, key: str) -> Option:
        return Option(Text.assemble((name, "bold"), (f"  {note}", "dim")), id=f"header:{key}", disabled=True)

    def row(self, item: Item, prefix: str, next_step: bool = False) -> Text:
        text = Text.assemble((f"{prefix:<8}", STATUS_STYLE.get(prefix, "dim")), (item.title, self.group_style(item)))
        if item.steps:
            done = sum(s.done for s in item.steps)
            text.append(f"  {done}/{len(item.steps)}", "dim")
        if next_step and (step := item.next_step):
            text.append(f"\n        ↳ {step.text}", "dim")
        return text

    def legend(self) -> Text:
        text = Text()
        if self.cfg.error:
            text.append(f"Config error, using defaults: {self.cfg.error}\n", "bold red")
        if not self.cfg.groups:
            return text.append("No groups yet. Press g on an item to create one.", "dim")
        for group in self.cfg.groups.values():
            text.append("● ", config.PALETTE.get(group.color, ""))
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

        line = Text.assemble((item.title, f"bold {self.group_style(item)}"), "\n", (item.status, STATUS_STYLE[item.status]))
        if group := self.cfg.groups.get(item.group):
            line.append("   group ", "dim")
            line.append(group.name, config.PALETTE.get(group.color, ""))
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
        choices = [(s, Text(s, STATUS_STYLE[s])) for s in items.STATUSES]

        def chosen(status: str | None) -> None:
            if status and status != item.status:
                self.write(lambda: items.set_status(item, status))

        self.push_screen(ChoiceScreen(f"Status of “{item.title}”", choices, item.status), chosen)

    def action_group(self) -> None:
        item = self.current_item()
        if item is None:
            return
        choices = [
            (g.key, Text(g.name, config.PALETTE.get(g.color, ""))) for g in self.cfg.groups.values()
        ]
        choices += [(NO_GROUP, Text("no group", "dim")), (NEW_GROUP, Text("+ new group…", "italic"))]

        def chosen(key: str | None) -> None:
            if key == NEW_GROUP:
                self.new_group(item)
            elif key is not None:
                self.write(lambda: items.set_group(item, None if key == NO_GROUP else key))

        self.push_screen(ChoiceScreen(f"Group of “{item.title}”", choices, item.group or NO_GROUP), chosen)

    def new_group(self, item: Item) -> None:
        def named(name: str | None) -> None:
            if not name:
                return
            colors = [(c, Text(f"● {c}", hex_)) for c, hex_ in config.PALETTE.items()]

            def colored(color: str | None) -> None:
                if color:
                    key = config.add_group(self.config_path, name, color)
                    self.write(lambda: items.set_group(item, key))

            self.push_screen(ChoiceScreen(f"Color for “{name}”", colors), colored)

        self.push_screen(TextScreen("New group name", "e.g. Auth migration"), named)

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
