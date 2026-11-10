import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from rich.console import Console
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Markdown, OptionList, Static

from rumo import config, items, prs, spark
from rumo.app import (ExplorationScreen, GroupScreen, RumoApp, SparkIdeasScreen, SparkQuestionsScreen,
                      short_date)

TODAY = date(2026, 10, 11)


def item(folder: Path, slug: str, status: str, updated: str, extra: str = "", body: str = "", title: str = "") -> Path:
    path = folder / f"{slug}.md"
    path.write_text(f"---\ntitle: {title or slug}\nstatus: {status}\nupdated: {updated}\n{extra}---\n{body}", encoding="utf-8")
    return path


def session(repo: str) -> str:
    return f"## Sessions\n- 2026-10-11 09:00 | {repo} (main) | abc | /x/{repo}\n"


class AppTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.pop("RUMO_DIR", None)
        self.root = Path(tempfile.mkdtemp())
        self.folder = self.root / "items"
        self.folder.mkdir()
        self.config = self.root / "config.toml"
        self.config.write_text(config.default_text(self.folder, "pt-BR"), encoding="utf-8")
        config.add_group(self.config, "Migração auth", "blue")
        config.add_group(self.config, "Pagamentos", "green")

        item(self.folder, "shipped", "done", "2026-10-09 18:00", extra="done: 2026-10-09 18:00\n")
        self.logout = item(
            self.folder, "logout", "doing", "2026-10-11 10:00", extra="group: migracao-auth\ntags: [bug]\n",
            body="## Left off\nFalta o e2e.\n\n## Steps\n- [x] [agent] Ajustar handler\n- [ ] [manual] Pedir review\n",
        )
        item(self.folder, "waiting-infra", "waiting", "2026-10-10 10:00")
        item(self.folder, "cache-idea", "idea", "2026-10-08 10:00")

    def app(self, gh=None) -> RumoApp:
        return RumoApp(config_path=self.config, today=TODAY, gh=gh)

    @staticmethod
    def rows(app: RumoApp) -> list[str]:
        """The list below the horizon banner."""
        timeline = app.query_one("#timeline", OptionList)
        ids = [str(timeline.get_option_at_index(i).id) for i in range(timeline.option_count)]
        return [i for i in ids if not i.startswith("horizon:")]

    @staticmethod
    def highlighted(app: RumoApp) -> str:
        timeline = app.query_one("#timeline", OptionList)
        assert timeline.highlighted is not None
        return str(timeline.get_option_at_index(timeline.highlighted).id)

    @staticmethod
    def row_text(app: RumoApp, option_id: str, width: int = 80) -> str:
        """The row as plain text, the way a terminal of that width shows it."""
        console = Console(width=width, color_system=None)
        with console.capture() as capture:
            console.print(app.query_one("#timeline", OptionList).get_option(option_id).prompt)
        return capture.get()

    @staticmethod
    def current(app: RumoApp, title: str | None) -> str:
        """The highlighted name in a border title (views on top, horizons below)."""
        m = re.search(r"\[bold\]\s*(?:[pl1-4] )?([A-Z]\w*)", str(title))
        return m.group(1) if m else ""

    def current_view(self, app: RumoApp) -> str:
        return self.current(app, app.query_one("#timeline", OptionList).border_title)

    @staticmethod
    def current_horizon(app: RumoApp) -> str:
        """The horizon its banner, the list's first row, announces."""
        timeline = app.query_one("#timeline", OptionList)
        first = str(timeline.get_option_at_index(0).id) if timeline.option_count else ""
        return first.removeprefix("horizon:").capitalize() if first.startswith("horizon:") else ""

    @staticmethod
    def dialog_options(app: RumoApp) -> OptionList:
        return app.screen.query_one(OptionList)

    # ---------- reading the list ----------

    async def test_opens_on_now_with_a_status_legend(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.rows(app), ["item:logout", "item:waiting-infra"])
            self.assertEqual(self.highlighted(app), "item:logout")
            self.assertEqual(self.current_view(app), "Timeline")
            self.assertEqual(self.current_horizon(app), "Now")
            subtitle = str(app.query_one("#timeline", OptionList).border_subtitle)
            for part in ("Past 1", "NOW 2", "Later 1"):
                self.assertIn(part, subtitle)
            legend = str(app.query_one("#legend", Static).render())
            self.assertIn("● doing", legend)
            self.assertIn("◐ waiting", legend)

    async def test_the_version_sits_quietly_at_the_end_of_the_legend(self):
        from importlib.metadata import version

        app = self.app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            shown = app.query_one("#version", Static)
            self.assertEqual(str(shown.render()), f"rumo {version('rumo-cli')}")
            legend = app.query_one("#legend", Static)
            self.assertEqual(shown.region.y, legend.region.y)
            self.assertEqual(shown.region.right, 120)  # flush with the right edge of the screen

    async def test_a_row_is_symbol_label_title_progress_and_next_step(self):
        item(self.folder, "pix", "doing", "2026-10-11 09:00", title="Pagamentos: Pix no checkout",
             extra="group: pagamentos\n", body="## Steps\n- [ ] [agent] Gerar o QR code\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            logout = self.row_text(app, "item:logout").splitlines()
            self.assertEqual(len(logout), 2)
            self.assertRegex(logout[0], r"^● +Migração auth +logout +1/2 *$")
            self.assertRegex(logout[1], r"^ +↳ manual  Pedir review *$")
            pix = self.row_text(app, "item:pix").splitlines()[0]
            self.assertRegex(pix, r"^● +Pagamentos +Pix no checkout +0/1 *$")
            self.assertRegex(self.row_text(app, "item:waiting-infra").splitlines()[0], r"^◐ +waiting-infra *$")

    async def test_a_row_shows_the_next_open_steps_up_to_step_lines_each_saying_who_acts(self):
        item(self.folder, "pix", "doing", "2026-10-11 09:00", title="Pix no checkout", body=(
            "## Steps\n- [x] [agent] Ler a doc do PSP\n- [ ] [manual] Pedir a chave ao banco\n"
            "- [ ] [waiting] Resposta do banco\n- [ ] [agent] Gerar o QR code\n"))

        def steps_shown(lines: int | None) -> list[str]:
            return [line.strip() for line in rows[lines][1:]]

        rows = {}
        for lines in (None, 1, 6):
            text = config.default_text(self.folder, "pt-BR")
            if lines:
                text = text.replace("step_lines = 2", f"step_lines = {lines}")
            self.config.write_text(text if lines else text.replace("step_lines = 2", "# step_lines"), encoding="utf-8")
            app = self.app()
            async with app.run_test() as pilot:
                await pilot.pause()
                rows[lines] = self.row_text(app, "item:pix").splitlines()
        self.assertEqual(steps_shown(None), ["↳ manual  Pedir a chave ao banco", "waiting Resposta do banco"])
        self.assertEqual(steps_shown(1), ["↳ manual  Pedir a chave ao banco"])
        self.assertEqual(steps_shown(6), [  # fewer open steps than lines: only those, aligned under the first
            "↳ manual  Pedir a chave ao banco", "waiting Resposta do banco", "agent   Gerar o QR code"])
        self.assertEqual(rows[6][2].index("waiting"), rows[6][1].index("manual"))

    async def test_the_label_fits_the_longest_group_name_up_to_16_characters(self):
        config.add_group(self.config, "Plataforma de pagamentos", "red")
        item(self.folder, "pix", "doing", "2026-10-11 09:00", extra="group: plataforma-de-pagamentos\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertRegex(self.row_text(app, "item:pix").splitlines()[0], r"^● +Plataforma de p… +pix *$")
            self.assertRegex(self.row_text(app, "item:logout").splitlines()[0], r"^● +Migração auth +logout +1/2 *$")

    async def test_long_text_is_cut_instead_of_wrapped(self):
        item(self.folder, "long", "doing", "2026-10-11 12:00", title="Um título muito comprido " * 6,
             body="## Steps\n- [ ] [agent] " + "um passo muito comprido " * 8 + "\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            lines = self.row_text(app, "item:long", width=60).splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("…", lines[0])
            self.assertTrue(lines[0].rstrip().endswith("0/1"))
            self.assertTrue(lines[1].rstrip().endswith("…"))

    async def test_now_keeps_items_of_the_same_project_together(self):
        item(self.folder, "pix", "doing", "2026-10-11 11:00", extra="group: pagamentos\n")
        item(self.folder, "sso", "doing", "2026-10-11 09:00", extra="group: migracao-auth\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.rows(app), ["item:logout", "item:sso", "item:pix", "item:waiting-infra"])

    # ---------- horizons ----------

    async def test_h_cycles_now_later_past_one_at_a_time(self):
        app = self.app()
        async with app.run_test() as pilot:
            for rows, horizon in [
                (["item:cache-idea"], "Later"),
                (["item:shipped"], "Past"),
                (["item:logout", "item:waiting-infra"], "Now"),
                (["item:cache-idea"], "Later"),
            ]:
                await pilot.press("h")
                await pilot.pause()
                self.assertEqual(self.rows(app), rows, horizon)
                self.assertEqual(self.current_horizon(app), horizon)
                self.assertEqual(self.highlighted(app), rows[0])

    async def test_a_banner_says_which_horizon_is_shown_and_where_h_goes(self):
        app = self.app()
        async with app.run_test() as pilot:
            for name, says, then in [
                ("now", "doing and waiting", "h ▸ Later"),
                ("later", "ideas and paused work", "h ▸ Past"),
                ("past", "done and dropped in the last 14 days", "h ▸ Now"),
            ]:
                await pilot.pause()
                banner = self.row_text(app, f"horizon:{name}")
                self.assertIn(name.upper(), banner)
                self.assertIn(says, banner)
                self.assertIn(then, banner)
                await pilot.press("h")
            await pilot.press("2")
            await pilot.pause()
            self.assertEqual(self.current_horizon(app), "Now")  # in every view

    async def test_past_shows_the_date_and_hides_older_items_until_asked(self):
        item(self.folder, "ancient", "done", "2026-08-01 10:00", extra="done: 2026-08-01 10:00\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("h", "h")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["older", "item:shipped"])
            self.assertRegex(self.row_text(app, "item:shipped").splitlines()[0], r"^✓ +shipped +Oct 09 *$")
            app.query_one("#timeline", OptionList).highlighted = 1  # below the banner
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["item:ancient", "item:shipped"])

    async def test_an_empty_horizon_says_so(self):
        (self.folder / "cache-idea.md").unlink()
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("h")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["empty"])
            self.assertIn("Nothing for later", self.row_text(app, "empty"))

    async def test_a_filter_shows_matches_from_every_horizon(self):
        item(self.folder, "cache-done", "done", "2026-10-10 18:00", extra="done: 2026-10-10 18:00\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("slash", *"cache")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["header:past", "item:cache-done", "header:later", "item:cache-idea"])
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["item:logout", "item:waiting-infra"])

    async def test_filter_narrows_the_timeline(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("slash", *"migração")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["header:now", "item:logout"])

    # ---------- views ----------

    async def test_v_cycles_timeline_projects_and_repos_and_digits_jump(self):
        item(self.folder, "pix", "doing", "2026-10-11 11:00", extra="group: pagamentos\n", body=session("billing"))
        item(self.folder, "sso", "paused", "2026-10-11 08:00", extra="group: migracao-auth\n", body=session("web-app"))
        self.logout.write_text(self.logout.read_text(encoding="utf-8") + "\n" + session("web-app"), encoding="utf-8")
        timeline = ["item:logout", "item:pix", "item:waiting-infra"]
        projects = ["project:migracao-auth", "item:logout", "project:pagamentos", "item:pix", "project:-", "item:waiting-infra"]
        repos = ["repo:billing", "item:pix", "repo:web-app", "item:logout", "repo:-", "item:waiting-infra"]
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.rows(app), timeline)
            for key, expected, name in [("v", projects, "Projects"), ("v", repos, "Repos"), ("v", ["empty"], "Tree"),
                                        ("v", timeline, "Timeline"), ("3", repos, "Repos"), ("2", projects, "Projects"),
                                        ("4", ["empty"], "Tree"), ("1", timeline, "Timeline")]:
                await pilot.press(key)
                await pilot.pause()
                self.assertEqual(self.rows(app), expected, key)
                self.assertEqual(self.current_view(app), name)

            await pilot.press("2", "h")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["project:migracao-auth", "item:sso", "project:-", "item:cache-idea"])
            block = self.row_text(app, "project:migracao-auth")
            self.assertIn("Migração auth", block)
            self.assertIn("1 now · 1 later", block)
            self.assertNotIn("Migração", self.row_text(app, "item:sso"))
            await pilot.press("3")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["repo:web-app", "item:sso", "repo:-", "item:cache-idea"])
            self.assertIn("Migração auth", self.row_text(app, "item:sso"))
            self.assertIn("No repo", self.row_text(app, "repo:-"))

    async def test_the_selected_item_stays_selected_across_views(self):
        app = self.app()
        async with app.run_test() as pilot:
            timeline = app.query_one("#timeline", OptionList)
            timeline.highlighted = timeline.get_option_index("item:waiting-infra")
            for key in ("v", "v", "1"):  # Tree shows only families: waiting-infra has none
                await pilot.press(key)
                await pilot.pause()
                self.assertEqual(self.highlighted(app), "item:waiting-infra")

    async def test_remembers_the_view_between_runs(self):
        async with self.app().run_test() as pilot:
            await pilot.press("2", "h", "h")
            await pilot.pause()
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.current_view(app), "Projects")
            self.assertEqual(self.current_horizon(app), "Now")

    async def test_a_broken_remembered_state_falls_back_to_the_timeline(self):
        for text in ("{broken", '{"view": "grouped"}', "[]"):
            (self.root / "state.json").write_text(text, encoding="utf-8")
            app = self.app()
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertEqual(self.current_view(app), "Timeline", text)
                self.assertEqual(self.rows(app), ["item:logout", "item:waiting-infra"], text)

    # ---------- actions ----------

    async def test_s_marks_done_and_the_item_moves_to_past(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("s", "5")  # 5 = done
            await pilot.pause()
            item_ = items.load(self.logout)
            self.assertEqual(item_.status, "done")
            self.assertIn("done", item_.meta)
            self.assertNotIn("item:logout", self.rows(app))
            await pilot.press("h", "h")
            await pilot.pause()
            self.assertIn("item:logout", self.rows(app))

    async def test_s_drops_with_a_reason_and_the_item_moves_to_past(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("s", "6", "enter")  # 6 = dropped; without a reason nothing changes
            await pilot.pause()
            self.assertEqual(items.load(self.logout).status, "doing")
            await pilot.press("s", "6", *"O PSP já resolve", "enter")
            await pilot.pause()
            item_ = items.load(self.logout)
            self.assertEqual((item_.status, item_.sections["left off"]), ("dropped", "O PSP já resolve"))
            self.assertIn("dropped", item_.meta)
            self.assertNotIn("item:logout", self.rows(app))
            await pilot.press("h", "h")
            await pilot.pause()
            self.assertEqual(self.current_horizon(app), "Past")
            self.assertIn("item:logout", self.rows(app))
            self.assertRegex(self.row_text(app, "item:logout"), r"^✕ +Migração auth +logout +\w{3} \d\d *\n$")
            self.assertIn("done and dropped in the last 14 days", self.row_text(app, "horizon:past"))
            self.assertTrue(self.faded(app, "logout", "logout"))
            self.assertIn("✕ dropped", str(app.query_one("#legend", Static).render()))

    async def test_ideas_show_how_long_they_sit_and_fade_after_60_days(self):
        item(self.folder, "old-idea", "idea", "2026-07-01 10:00")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("h")
            await pilot.pause()
            self.assertRegex(self.row_text(app, "item:cache-idea"), r"^○ +cache-idea +3d *\n$")
            self.assertRegex(self.row_text(app, "item:old-idea"), r"^○ +old-idea +102d *\n$")
            console = Console(width=80, color_system="standard")
            for slug, faded in (("cache-idea", False), ("old-idea", True)):
                with console.capture() as capture:
                    console.print(app.query_one("#timeline", OptionList).get_option(f"item:{slug}").prompt)
                self.assertEqual(bool(re.search(rf"\x1b\[(?:\d+;)*2(?:;\d+)*m{slug}", capture.get())), faded, slug)

    async def test_n_captures_an_idea_and_shows_it_in_later(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("n", *"Cache na API", "enter")
            await pilot.pause()
            [path] = [p for p in self.folder.glob("*cache-na-api.md")]
            self.assertEqual(items.load(path).status, "idea")
            self.assertEqual(self.current_horizon(app), "Later")
            self.assertIn(f"item:{path.stem}", self.rows(app))
            self.assertEqual(self.highlighted(app), f"item:{path.stem}")

    async def test_g_assigns_an_existing_group_or_creates_one(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("g", "2")  # second group
            await pilot.pause()
            self.assertEqual(items.load(self.logout).group, "pagamentos")

            await pilot.press("g", "4", *"Plataforma", "enter", "8")  # 4 = new group, 8 = cyan
            await pilot.pause()
            self.assertEqual(items.load(self.logout).group, "plataforma")
            self.assertEqual(config.load(self.config).groups["plataforma"].color, "cyan")
            self.assertIn("Plataforma", self.row_text(app, "item:logout"))

            await pilot.press("g", "4")  # no group (now after three groups)
            await pilot.pause()
            self.assertEqual(items.load(self.logout).group, "")

    async def test_g_renames_recolors_and_deletes_groups_and_stays_open(self):
        pix = item(self.folder, "pix", "doing", "2026-10-11 09:00", extra="group: pagamentos\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("g")
            await pilot.pause()
            picker = app.screen
            self.assertIsInstance(picker, GroupScreen)
            self.assertIn("1 item", str(self.dialog_options(app).get_option("migracao-auth").prompt))

            await pilot.press("r")  # the item's own group is highlighted
            await pilot.pause()
            field = app.screen.query_one(Input)
            self.assertEqual(field.value, "Migração auth")
            field.value = "Auth nova"
            await pilot.press("enter")
            await pilot.pause()
            self.assertIs(app.screen, picker)
            self.assertEqual(config.load(self.config).groups["migracao-auth"].name, "Auth nova")
            self.assertEqual(items.load(self.logout).group, "migracao-auth")
            self.assertIn("Auth nova", str(self.dialog_options(app).get_option("migracao-auth").prompt))

            await pilot.press("c")
            await pilot.pause()
            self.assertIn("Pagamentos", str(self.dialog_options(app).get_option("green").prompt))
            await pilot.press("8")  # cyan
            await pilot.pause()
            self.assertIs(app.screen, picker)
            self.assertEqual(config.load(self.config).groups["migracao-auth"].color, "cyan")

            await pilot.press("down", "d")
            await pilot.pause()
            self.assertIn("1 item", str(app.screen.query_one(".dialog-title", Static).render()))
            await pilot.press("2")  # cancel
            await pilot.pause()
            self.assertIn("pagamentos", config.load(self.config).groups)
            await pilot.press("d", "1")  # delete group
            await pilot.pause()
            self.assertIs(app.screen, picker)
            self.assertNotIn("pagamentos", config.load(self.config).groups)
            self.assertTrue(pix.exists())
            self.assertEqual(items.load(pix).group, "")

            await pilot.press("escape")
            await pilot.pause()
            self.assertIn("Auth nova", self.row_text(app, "item:logout"))
            self.assertNotIn("Pagamentos", self.row_text(app, "item:pix"))
            self.assertEqual(items.load(self.logout).group, "migracao-auth")

    async def test_g_with_nothing_selected_manages_and_creates_groups(self):
        for path in self.folder.glob("*.md"):
            path.unlink()
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("g")
            await pilot.pause()
            picker = app.screen
            options = self.dialog_options(app)
            self.assertEqual([options.get_option_at_index(i).id for i in range(options.option_count)],
                             ["migracao-auth", "pagamentos", "+new"])
            await pilot.press("1")  # nothing to assign to
            await pilot.pause()
            self.assertIs(app.screen, picker)

            await pilot.press("3", *"Plataforma", "enter")
            await pilot.pause()
            colors = self.dialog_options(app)
            assert colors.highlighted is not None
            self.assertEqual(colors.get_option_at_index(colors.highlighted).id, "yellow")  # first free color
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(config.load(self.config).groups["plataforma"].color, "yellow")

    async def test_deleting_a_group_stops_when_one_of_its_items_changed_on_disk(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("g")
            await pilot.pause()
            stat = self.logout.stat()
            os.utime(self.logout, (stat.st_atime, stat.st_mtime + 10))
            await pilot.press("d", "1")
            await pilot.pause()
            self.assertIn("migracao-auth", config.load(self.config).groups)
            self.assertEqual(items.load(self.logout).group, "migracao-auth")

    async def test_t_toggles_tags(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("t", "space", "down", "space", "enter")  # bug off, feature on
            await pilot.pause()
            self.assertEqual(items.load(self.logout).tags, ["feature"])

    async def test_space_toggles_the_selected_step(self):
        app = self.app()
        async with app.run_test() as pilot:
            app.query_one("#steps").focus()
            await pilot.press("down", "space")
            await pilot.pause()
            self.assertEqual([s.done for s in items.load(self.logout).steps], [True, True])
            # the list is rebuilt after each change, and the step stays highlighted
            self.assertEqual([row.highlighted for row in app.query_one("#steps").query(ListItem)], [False, True])

    def steps(self) -> list[str]:
        return [f"{'x' if s.done else ' '} {s.kind} {s.text}" for s in items.load(self.logout).steps]

    async def test_a_adds_a_step_after_the_open_ones_and_tab_switches_agent_manual_and_waiting(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("a")
            hint = str(app.screen.query_one("#kind", Static).render())
            self.assertIn("tab", hint)
            self.assertIn("[agent]", hint)
            self.assertIn("waiting", hint)
            await pilot.press(*"Atualizar docs", "tab", "enter")
            await pilot.pause()
            self.assertEqual(self.steps()[-1], "  manual Atualizar docs")
            self.assertEqual(app.query_one("#steps", ListView).index, 2)
            await pilot.press("a", *"Resposta do time", "tab", "tab", "enter")
            await pilot.pause()
            self.assertEqual(self.steps()[-1], "  waiting Resposta do time")
            self.assertIn("[ ] [waiting] Resposta do time", self.shown_steps(app))

    async def test_in_the_steps_a_adds_below_and_enter_edits_the_highlighted_step(self):
        app = self.app()
        async with app.run_test() as pilot:
            app.query_one("#steps").focus()
            await pilot.press("a", *"Revisar o fluxo", "enter")
            await pilot.pause()
            self.assertEqual(self.steps(), ["x agent Ajustar handler", "  agent Revisar o fluxo", "  manual Pedir review"])
            await pilot.press("down", "enter", *" do time", "tab", "enter")
            await pilot.pause()
            self.assertEqual(self.steps()[2], "  waiting Pedir review do time")  # manual -> waiting

    async def test_shift_arrows_move_a_step_and_d_deletes_it_after_confirming(self):
        app = self.app()
        async with app.run_test() as pilot:
            app.query_one("#steps").focus()
            await pilot.press("down", "shift+up")
            await pilot.pause()
            self.assertEqual(self.steps(), ["  manual Pedir review", "x agent Ajustar handler"])
            self.assertEqual(app.query_one("#steps", ListView).index, 0)
            await pilot.press("d", "escape")
            await pilot.pause()
            self.assertEqual(len(self.steps()), 2)
            await pilot.press("d", "1")
            await pilot.pause()
            self.assertEqual(self.steps(), ["x agent Ajustar handler"])

    async def test_a_done_item_takes_no_new_steps(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("h", "h")
            self.assertEqual(app.current, "shipped")
            before = (self.folder / "shipped.md").read_text()
            await pilot.press("a")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ModalScreen)
            self.assertEqual((self.folder / "shipped.md").read_text(), before)

    async def test_a_narrow_footer_wraps_between_keys_never_inside_one(self):
        app = self.app()
        async with app.run_test(size=(60, 30)) as pilot:
            await pilot.pause()
            lines = str(app.query_one("#keys", Static).render()).splitlines()
            self.assertGreater(len(lines), 1)
            for line in lines:
                self.assertLessEqual(len(line.rstrip()), 58, line)
                self.assertRegex(line, r"^(\S+ [^ ]+( [^ ]+)?  )*\S+ \S+( \S+)?\s*$", line)
            self.assertIn("q quit", " ".join(lines))

    async def test_the_footer_shows_the_step_keys_while_the_steps_have_focus(self):
        app = self.app()
        async with app.run_test() as pilot:
            keys = lambda: str(app.query_one("#keys", Static).render())
            self.assertIn("tab steps", keys())
            app.query_one("#steps").focus()
            await pilot.pause()
            self.assertIn("shift+↑↓ move", keys())
            self.assertIn("d delete", keys())
            await pilot.press("escape")
            await pilot.pause()
            self.assertIn("tab steps", keys())

    @staticmethod
    def row_labels(app: RumoApp, selector: str) -> list[str]:
        return ["".join(str(label.render()) for label in row.query(Label)) for row in app.query_one(selector).query(ListItem)]

    async def go_to(self, app: RumoApp, pilot, slug: str) -> None:
        for _ in range(8):
            if self.highlighted(app) == f"item:{slug}":
                return
            await pilot.press("down")
            await pilot.pause()
        self.fail(f"{slug} not in the list")

    async def test_the_detail_shows_where_an_item_came_from_and_what_it_led_to(self):
        item(self.folder, "e2e-idea", "idea", "2026-10-10 10:00", extra="from: logout\n", title="Testes e2e do logout")
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(self.row_labels(app, "#family"), ["↓ led to  ○ Testes e2e do logout"])
            app.query_one("#family", ListView).focus()
            await pilot.press("enter")  # goes to that item, in its own horizon
            await pilot.pause()
            self.assertEqual((self.current_horizon(app), self.highlighted(app)), ("Later", "item:e2e-idea"))
            self.assertTrue(app.query_one("#timeline", OptionList).has_focus)
            self.assertEqual(self.row_labels(app, "#family"), ["↑ from  ● logout"])
            await pilot.press("h", "h")  # Later -> Past -> Now
            await pilot.pause()
            await self.go_to(app, pilot, "waiting-infra")
            self.assertFalse(app.query_one("#family", ListView).display)  # no family, no section

    async def test_the_detail_shows_notes_explorations_and_research_and_enter_reads_an_answer(self):
        started = datetime.now().strftime("%Y-%m-%dT%H:%M")
        item(self.folder, "ponto", "doing", "2026-10-11 09:30", title="Ponto na obra", body=(
            "## Context\nMestres anotam em papel.\n\n## Notes\n- 2026-10-10 Dois mestres mandaram foto.\n\n"
            "## Explorations\n"
            "### 2026-10-09 10:00 Como validar?\n<!-- rumo:exploration id=a1 state=done -->\nLigar para 15 empreiteiras.\n\n"
            "<!-- rumo:next -->\n- Quanto cobrar?\n- E no Brasil?\n- Vale seguir?\n\n"
            '### 2026-10-10 11:00 Quem paga?\n<!-- rumo:exploration id=b2 state=failed error="not logged in" -->\n\n'
            f"### 2026-10-11 09:00 E se for a construtora?\n<!-- rumo:exploration id=c3 state=running pid={os.getpid()} "
            f"started={started} limit=600 -->\n"))
        research = self.folder / "research" / "ponto"
        (research / "dig-20261010-1200").mkdir(parents=True)
        (research / "ideas-hub.md").write_text("## Veredito\nTalvez.", encoding="utf-8")
        app = self.app()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            await self.go_to(app, pilot, "ponto")
            body = app.query_one("#body", Markdown).source
            self.assertIn("**Notes**", body)
            self.assertIn("Dois mestres mandaram foto.", body)
            self.assertEqual(self.row_labels(app, "#explorations"), [
                "Oct 11 · E se for a construtora?  asking…",
                "Oct 10 · Quem paga?  failed: not logged in",
                "Oct 09 · Como validar?",
            ])
            shown = str(app.query_one("#research", Static).render())
            self.assertIn("dig-20261010-1200/", shown)
            self.assertIn("ideas-hub.md", shown)
            explorations = app.query_one("#explorations", ListView)
            explorations.focus()
            explorations.index = 2
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ExplorationScreen)
            answer = app.screen.query_one(Markdown).source
            for part in ("Como validar?", "Ligar para 15 empreiteiras.", "Quanto cobrar?"):
                self.assertIn(part, answer)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ExplorationScreen)

    async def test_b_notes_an_idea_that_comes_from_the_item(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("b", *"Logout em todos os devices", "enter")
            await pilot.pause()
            [path] = self.folder.glob("*logout-em-todos-os-devices.md")
            new = items.load(path)
            self.assertEqual((new.status, new.from_slug, new.group, new.tags), ("idea", "logout", "migracao-auth", ["bug"]))
            self.assertEqual((self.current_horizon(app), self.highlighted(app)), ("Later", f"item:{path.stem}"))
            self.assertEqual(self.row_labels(app, "#family"), ["↑ from  ● logout"])
            self.assertNotIn("new", self.row_text(app, f"item:{path.stem}"))

    async def test_b_tab_turns_the_next_step_into_an_item_whose_done_checks_the_step(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("b", "tab")
            await pilot.pause()
            self.assertEqual(app.screen.query_one(Input).value, "Pedir review")
            await pilot.press("enter")
            await pilot.pause()
            [path] = self.folder.glob("*pedir-review.md")
            child = items.load(path)
            self.assertEqual((child.status, child.from_slug, child.group), ("doing", "logout", "migracao-auth"))
            step = items.load(self.logout).steps[1]
            self.assertEqual((step.kind, step.shown, step.target, step.done), ("item", "Pedir review", path.stem, False))
            self.assertEqual((self.current_horizon(app), self.highlighted(app)), ("Now", f"item:{path.stem}"))
            await pilot.press("s", "5")  # the new item is done: the parent's step follows
            await pilot.pause()
            self.assertTrue(items.load(self.logout).steps[1].done)
            await self.go_to(app, pilot, "logout")
            self.assertEqual(self.shown_steps(app)[1], "[x] [item] Pedir review")  # no slug, and no news
            self.assertNotIn("✓", self.row_text(app, "item:logout"))
            app.query_one("#steps", ListView).focus()
            app.query_one("#steps", ListView).index = 1
            await pilot.press("enter")  # Enter on an [item] step goes to that item
            await pilot.pause()
            self.assertEqual((self.current_horizon(app), self.highlighted(app)), ("Past", f"item:{path.stem}"))

    async def test_b_without_an_open_step_only_notes_an_idea(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("down", "b", "tab")  # waiting-infra has no steps
            await pilot.pause()
            self.assertEqual(app.screen.query_one(Input).value, "")
            await pilot.press(*"Outra ideia", "enter")
            await pilot.pause()
            [path] = self.folder.glob("*outra-ideia.md")
            self.assertEqual(items.load(path).status, "idea")

    @staticmethod
    def faded(app: RumoApp, slug: str, word: str) -> bool:
        """Whether the row's title starts dim at that word."""
        console = Console(width=120, color_system="standard")
        with console.capture() as capture:
            console.print(app.query_one("#timeline", OptionList).get_option(f"item:{slug}").prompt)
        return bool(re.search(rf"\x1b\[(?:\d+;)*2(?:;\d+)*m[^\x1b]*{word}", capture.get()))

    async def test_tree_draws_the_families_with_a_member_in_the_horizon(self):
        item(self.folder, "e2e-idea", "idea", "2026-10-10 10:00", extra="from: logout\n", title="Testes e2e")
        item(self.folder, "e2e-ci", "idea", "2026-10-10 11:00", extra="from: e2e-idea\n", title="Rodar e2e no CI")
        item(self.folder, "devices", "done", "2026-10-09 10:00", extra="from: logout\ndone: 2026-10-09 10:00\n",
             title="Logout em devices")
        item(self.folder, "cache-l2", "idea", "2026-10-08 10:00", extra="from: cache-idea\n")  # all of it in Later
        item(self.folder, "a-loop", "paused", "2026-10-08 10:00", extra="from: b-loop\n")  # pointing at each other
        item(self.folder, "b-loop", "paused", "2026-10-08 10:00", extra="from: a-loop\n")
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("4")
            await pilot.pause()
            self.assertEqual(self.current_view(app), "Tree")
            members = lambda: [r for r in self.rows(app) if r.startswith("item:")]
            self.assertEqual(members(), ["item:logout", "item:devices", "item:e2e-idea", "item:e2e-ci"])
            self.assertIn("├ Logout em devices", self.row_text(app, "item:devices", 120))
            self.assertIn("└ Testes e2e", self.row_text(app, "item:e2e-idea", 120))
            self.assertIn("  └ Rodar e2e no CI", self.row_text(app, "item:e2e-ci", 120))
            self.assertFalse(self.faded(app, "logout", "logout"))
            self.assertTrue(self.faded(app, "e2e-idea", "Testes"))  # outside Now
            await pilot.press("h")  # Later
            await pilot.pause()
            later = members()
            for slug in ("logout", "cache-idea", "cache-l2", "a-loop", "b-loop"):
                self.assertEqual(later.count(f"item:{slug}"), 1, slug)
            self.assertNotIn("item:waiting-infra", later)  # no family, not in Tree
            await pilot.press("s")  # item keys work here
            await pilot.pause()
            self.assertIsInstance(app.screen, ModalScreen)

    def asking_app(self, command: str = sys.executable) -> tuple[RumoApp, list[tuple]]:
        rest = [line for line in self.config.read_text(encoding="utf-8").splitlines() if not line.startswith("explore_command")]
        self.config.write_text("\n".join([f"explore_command = {config.toml_str(command)}", *rest]) + "\n", encoding="utf-8")
        spawned: list[tuple] = []
        return RumoApp(config_path=self.config, today=TODAY, spawner=lambda *args: spawned.append(args)), spawned

    @staticmethod
    def rendered(renderable, width: int = 76) -> str:
        console = Console(width=width, color_system=None)
        with console.capture() as capture:
            console.print(renderable)
        return capture.get()

    @staticmethod
    def notices(app: RumoApp) -> str:
        return " | ".join(str(n.message) for n in app._notifications)

    async def test_ask_offers_the_lenses_and_starts_the_exploration_in_the_background(self):
        app, spawned = self.asking_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("?")
            await pilot.pause()
            listed = str(app.screen.query_one(OptionList).get_option_at_index(2).prompt)
            self.assertIn("validate", listed)
            await pilot.press("3")  # validate
            await pilot.pause()
            self.assertEqual(spawned, [(self.config, "logout", "validate", None)])
            self.assertNotIsInstance(app.screen, ModalScreen)
            self.assertIn("Asking", self.notices(app))
            await pilot.press("?", "tab", *"Quem paga?", "enter")
            await pilot.pause()
            self.assertEqual(spawned[1], (self.config, "logout", None, "Quem paga?"))

    async def test_ask_refuses_while_an_answer_is_on_its_way_or_without_claude(self):
        items.start_exploration(items.load(self.logout), "Primeira", "r1", pid=os.getpid(), limit=600)
        app, spawned = self.asking_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("?")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ModalScreen)
            self.assertIn("Still asking", self.notices(app))
        app, spawned = self.asking_app("rumo-no-such-claude")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("down", "?")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ModalScreen)
            self.assertIn("explore_command", self.notices(app))
        self.assertEqual(spawned, [])

    async def test_an_answer_that_lands_is_announced_and_is_news(self):
        items.start_exploration(items.load(self.logout), "Como validar?", "r1", pid=os.getpid(), limit=600)
        app, _ = self.asking_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("down")  # look elsewhere while it runs
            await pilot.pause()
            items.finish_exploration(items.load(self.logout), "r1", "Ligar para 15.", ["Quanto cobrar?"])
            app.check_changes()
            await pilot.pause()
            self.assertIn("Answer ready: “logout”", self.notices(app))
            self.assertIn("1 answer", self.row_text(app, "item:logout", 120))
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(self.row_labels(app, "#explorations"), [f"{short_date(f'{datetime.now():%Y-%m-%d}')} · Como validar?  new"])
            self.assertNotIn("answer", str(app.query_one("#steps-title", Label).render()))
            await pilot.press("?")  # the next questions are offered
            await pilot.pause()
            prompts = [str(o.prompt) for o in app.screen.query_one(OptionList).options]
            self.assertTrue(any("Quanto cobrar?" in p for p in prompts), prompts)

    # ---------- spark ----------

    def spark_app(self, command: str = sys.executable) -> tuple[RumoApp, list[tuple]]:
        app, _ = self.asking_app(command)
        spawned: list[tuple] = []
        app.spark_spawner = lambda *args: spawned.append(args)
        return app, spawned

    def ready(self) -> None:
        item(self.folder, "pix-idea", "idea", "2026-10-01 10:00", extra="group: pagamentos\n", title="Pix na obra")
        spark.save(self.folder, {"state": "ready", "started": "2026-10-11 09:00:00", "direction": "Pagamentos simples.",
                                 "ideas": [{"title": "Recibo por WhatsApp", "summary": "Diarista recebe o recibo do Pix.", "from": ["pix-idea"]},
                                           {"title": "Vale-obra", "summary": "Adiantamento para o pedreiro.", "from": []},
                                           {"title": "Caixa do mestre", "summary": "O mestre controla o caixa da obra.", "from": ["cache-idea"]}]})

    async def test_star_reads_the_ideas_in_the_background(self):
        app, spawned = self.spark_app()
        async with app.run_test(size=(120, 40)) as pilot:
            self.assertIn("* spark", str(app.query_one("#keys", Static).render()))
            await pilot.press("*")
            await pilot.pause()
            self.assertEqual(spawned, [(self.config,)])
            self.assertIn("Reading your ideas…", self.notices(app))
            spark.save(self.folder, {"state": "running", "started": f"{datetime.now():{spark.STAMP}}", "pid": os.getpid(),
                                     "limit": 600})
            app.check_changes()
            await pilot.press("*")
            await pilot.pause()
            self.assertEqual(len(spawned), 1)
            self.assertIn("Still reading your ideas", self.notices(app))

    async def test_star_without_ideas_or_without_claude_code_says_so(self):
        (self.folder / "cache-idea.md").unlink()
        app, spawned = self.spark_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("*")
            await pilot.pause()
            self.assertIn("No ideas to read yet. Press n to note one.", self.notices(app))
        item(self.folder, "cache-idea", "idea", "2026-10-08 10:00")
        app, spawned = self.spark_app("rumo-no-such-claude")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("*")
            await pilot.pause()
            self.assertIn("explore_command", self.notices(app))
        self.assertEqual(spawned, [])

    async def test_questions_come_first_and_the_answers_start_the_ideas(self):
        spark.save(self.folder, {"state": "asking", "started": "2026-10-11 09:00:00", "direction": "Obra e RH.",
                                 "questions": ["Empresas ou pessoas?", "Pode cobrar mensal?"]})
        app, spawned = self.spark_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(self.notices(app).count("rumo has a question before the ideas. Press *."), 1)
            await pilot.press("*")
            await pilot.pause()
            self.assertIsInstance(app.screen, SparkQuestionsScreen)
            self.assertIn("Obra e RH.", " ".join(str(s.render()) for s in app.screen.query(Static)))
            await pilot.press("escape")  # later: they keep waiting
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ModalScreen)
            self.assertEqual(spawned, [])
            await pilot.press("*", *"Empresas", "tab", "enter")
            await pilot.pause()
            self.assertEqual(spark.load(self.folder)["answers"], ["Empresas", ""])
            self.assertEqual(spawned, [(self.config,)])
            app.check_changes()
            await pilot.pause()
            self.assertEqual(self.notices(app).count("rumo has a question"), 1)  # writing the answers is no news

    async def test_ideas_are_thrown_away_with_one_key_and_the_rest_kept(self):
        self.ready()
        app, spawned = self.spark_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertIn("3 new ideas. Press * to see them.", self.notices(app))
            await pilot.press("*")
            await pilot.pause()
            self.assertIsInstance(app.screen, SparkIdeasScreen)
            shown = " ".join(self.rendered(o.prompt) for o in self.dialog_options(app).options)
            for part in ("Pagamentos simples.", "Recibo por WhatsApp", "Diarista recebe o recibo do Pix.", "from Pix na obra"):
                self.assertIn(part, shown + " ".join(str(s.render()) for s in app.screen.query(Static)))
            await pilot.press("2")
            await pilot.pause()
            self.assertIn("✕ Vale-obra", self.rendered(self.dialog_options(app).get_option_at_index(1).prompt))
            first = self.rendered(self.dialog_options(app).get_option_at_index(0).prompt)
            self.assertEqual(len(first.splitlines()), 3)  # no blank line to light up with the highlight
            await pilot.press("escape")  # later: nothing created, the ideas wait
            await pilot.pause()
            self.assertEqual(len(list(self.folder.glob("*.md"))), 5)
            self.assertEqual(spark.load(self.folder)["state"], "ready")
            await pilot.press("*", "down", "space", "enter")  # the second one, thrown away with space
            await pilot.pause()
            new = sorted(set(self.folder.glob("*.md")) - {self.folder / f"{s}.md" for s in
                         ("shipped", "logout", "waiting-infra", "cache-idea", "pix-idea")})
            self.assertEqual(sorted(items.load(p).title for p in new), ["Caixa do mestre", "Recibo por WhatsApp"])
            recibo = next(items.load(p) for p in new if "recibo" in p.stem)
            self.assertEqual((recibo.status, recibo.group, recibo.from_slug, recibo.sections["context"]),
                             ("idea", "pagamentos", "pix-idea", "Diarista recebe o recibo do Pix."))
            self.assertIn("2 ideas added to Later.", self.notices(app))
            self.assertEqual(app.current, recibo.slug)
            self.assertIn("new", self.row_text(app, f"item:{recibo.slug}", 120))
            data = spark.load(self.folder)
            self.assertEqual([d["title"] for d in data["discarded"]], ["Vale-obra"])
            self.assertNotIn("state", data)
            spawned.clear()
            await pilot.press("*")  # idle again: a new spark
            await pilot.pause()
            self.assertEqual(spawned, [(self.config,)])

    async def test_a_spark_that_ends_or_dies_is_announced_once(self):
        spark.save(self.folder, {"state": "running", "started": f"{datetime.now():{spark.STAMP}}", "pid": os.getpid(),
                                 "limit": 600})
        app, _ = self.spark_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertNotIn("idea", self.notices(app))
            self.ready()
            app.check_changes()
            await pilot.pause()
            self.assertEqual(self.notices(app).count("3 new ideas"), 1)
            app.check_changes()
            self.assertEqual(self.notices(app).count("3 new ideas"), 1)
            sleeping = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            spark.save(self.folder, {"state": "running", "started": f"{datetime.now():{spark.STAMP}}",
                                     "pid": sleeping.pid, "limit": 600})
            app.check_changes()
            sleeping.kill()
            sleeping.wait()
            app.check_changes()  # nothing changed on disk: the tick still sees it died
            await pilot.pause()
            self.assertIn("Spark failed: stopped before it answered", self.notices(app))

    async def test_a_spark_that_breaks_before_it_starts_is_announced_once(self):
        app, _ = self.spark_app()
        procs: list[subprocess.Popen] = []

        def broken(*args):  # a broken install: exits before writing spark.json
            procs.append(subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"]))
            return procs[-1]

        def failing(*args):  # fails the usual way: says why in spark.json
            procs.append(subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"]))
            spark.save(self.folder, {"state": "failed", "started": "2026-10-11 09:00:00", "error": "no ideas in the answer"})
            return procs[-1]

        async with app.run_test(size=(120, 40)) as pilot:
            app.spark_spawner = broken
            await pilot.press("*")
            procs[-1].wait()
            app.check_changes()
            app.check_changes()
            await pilot.pause()
            self.assertEqual(self.notices(app).count("Spark failed: it stopped before it started"), 1)
            self.assertIn("explore.log", self.notices(app))
            app.spark_spawner = failing
            await pilot.press("*")
            procs[-1].wait()
            app.check_changes()
            app.check_changes()
            await pilot.pause()
            self.assertEqual(self.notices(app).count("Spark failed"), 2)
            self.assertIn("Spark failed: no ideas in the answer", self.notices(app))

    def shown_steps(self, app: RumoApp) -> list[str]:
        return self.row_labels(app, "#steps")

    async def test_a_long_step_wraps_and_goes_on_under_its_own_text(self):
        long = "Avaliar o api-crm arquivado que ainda é chamado por dialetos antigos do app mobile"
        self.logout.write_text(self.logout.read_text(encoding="utf-8").replace("Pedir review", long), encoding="utf-8")
        app = self.app()
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            row = app.query_one("#steps").query(ListItem).last()
            mark, text = row.query(Label)
            self.assertGreater(row.region.height, 1)  # wrapped, not cut at the edge
            self.assertEqual(text.region.x, mark.region.x + len("[ ] [manual] "))
            self.assertLessEqual(text.region.right, row.region.right)
            self.assertEqual(self.shown_steps(app)[-1], f"[ ] [manual] {long}")

    async def test_x_hides_the_done_steps_of_every_item_and_shows_them_again(self):
        app = self.app()
        async with app.run_test() as pilot:
            keys = lambda: str(app.query_one("#keys", Static).render())
            self.assertIn("x hide done", keys())
            await pilot.press("x")
            await pilot.pause()
            self.assertEqual(self.shown_steps(app), ["[ ] [manual] Pedir review"])
            self.assertIn('1 done hidden · "x" shows them', str(app.query_one("#steps-title", Label).render()))
            self.assertIn("x show done", keys())
            await pilot.press("x")
            await pilot.pause()
            self.assertEqual(len(self.shown_steps(app)), 2)
            self.assertIn('1 done · "x" hides them', str(app.query_one("#steps-title", Label).render()))
            self.assertIn("x hide done", keys())

    async def test_hiding_done_steps_is_remembered_between_runs(self):
        async with self.app().run_test() as pilot:
            await pilot.press("x")
            await pilot.pause()
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.shown_steps(app), ["[ ] [manual] Pedir review"])

    async def test_with_done_steps_hidden_the_step_keys_act_on_the_visible_steps(self):
        self.logout.write_text(
            "---\ntitle: logout\nstatus: doing\nupdated: 2026-10-11 10:00\ngroup: migracao-auth\n---\n## Steps\n"
            "- [ ] [agent] Um\n- [x] [agent] Feito\n- [ ] [agent] Dois\n- [ ] [agent] Tres\n", encoding="utf-8",
        )
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("x")
            app.query_one("#steps").focus()
            await pilot.press("down", "shift+up")  # Dois swaps with Um, skipping the hidden one
            await pilot.pause()
            self.assertEqual(self.steps(), ["  agent Dois", "x agent Feito", "  agent Um", "  agent Tres"])
            self.assertEqual(app.query_one("#steps", ListView).index, 0)
            await pilot.press("down", "space")  # checks Um, which then hides
            await pilot.pause()
            self.assertEqual(self.steps(), ["  agent Dois", "x agent Feito", "x agent Um", "  agent Tres"])
            self.assertEqual(self.shown_steps(app), ["[ ] [agent] Dois", "[ ] [agent] Tres"])
            await pilot.press("a", *"Quatro", "enter")  # below the highlighted Tres
            await pilot.pause()
            self.assertEqual(self.steps()[-1], "  agent Quatro")
            self.assertEqual(app.query_one("#steps", ListView).index, 2)

    async def test_the_steps_grow_with_the_pane_instead_of_scrolling_on_their_own(self):
        body = "## Steps\n" + "".join(f"- [ ] [agent] Passo {n}\n" for n in range(1, 21))
        item(self.folder, "longo", "doing", "2026-10-11 12:00", extra="group: migracao-auth\n", body=body)
        app = self.app()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            self.assertEqual(app.current, "longo")
            steps = app.query_one("#steps")
            self.assertEqual((steps.size.height, steps.max_scroll_y), (20, 0))

    async def test_the_pane_scrolls_to_keep_the_highlighted_step_in_view(self):
        body = "## Steps\n" + "".join(f"- [ ] [agent] Passo {n}\n" for n in range(1, 21))
        item(self.folder, "longo", "doing", "2026-10-11 12:00", extra="group: migracao-auth\n", body=body)
        app = self.app()
        async with app.run_test(size=(120, 24)) as pilot:
            app.query_one("#steps").focus()
            await pilot.press(*["down"] * 19)
            await pilot.pause()
            detail = app.query_one("#detail")
            last = app.query_one("#steps").query(ListItem).last()
            self.assertGreater(detail.scroll_y, 0)
            self.assertTrue(detail.content_region.contains_region(last.region), (detail.content_region, last.region))

    # ---------- pull requests ----------

    def add_prs(self) -> None:
        text = self.logout.read_text(encoding="utf-8")
        self.logout.write_text(text + "\n## PRs\n- [open] https://github.com/acme/web-app/pull/12 Logout novo\n"
                               "- [merged] https://github.com/acme/web-app/pull/3\n", encoding="utf-8")

    def pr_lines(self) -> list[str]:
        return [f"{p.state} {p.name} {p.title}".strip() for p in items.load(self.logout).prs]

    def shown_prs(self, app: RumoApp) -> list[str]:
        return self.row_labels(app, "#prs")

    async def test_the_detail_lists_the_prs_with_their_state(self):
        self.add_prs()
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.shown_prs(app), ["● web-app#12  Logout novo  open", "● web-app#3  merged"])
            self.assertTrue(app.query_one("#prs").display)
            await pilot.press("down")  # waiting-infra has none: the section goes away
            await pilot.pause()
            self.assertFalse(app.query_one("#prs").display)
            self.assertFalse(app.query_one("#prs-title").display)

    async def test_a_row_says_how_many_prs_are_still_open(self):
        self.add_prs()
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertRegex(self.row_text(app, "item:logout").splitlines()[0], r"^● +Migração auth +logout +1 PR +1/2 *$")

    async def test_p_adds_a_pr_and_tab_picks_its_state(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("p")
            hint = str(app.screen.query_one("#state", Static).render())
            self.assertIn("[open]", hint)
            self.assertIn("tab", hint)
            await pilot.press(*"https://github.com/acme/web-app/pull/12/files Logout novo", "tab", "enter")
            await pilot.pause()
            self.assertEqual(self.pr_lines(), ["draft web-app#12 Logout novo"])
            await pilot.press("p", *"nada", "enter")
            await pilot.pause()
            self.assertEqual(len(self.pr_lines()), 1)

    async def test_in_the_prs_enter_opens_space_changes_the_state_e_edits_and_d_deletes(self):
        self.add_prs()
        app = self.app()
        opened = []
        app.open_url = lambda url, **_: opened.append(url)
        async with app.run_test() as pilot:
            app.query_one("#prs").focus()
            await pilot.press("enter")
            self.assertEqual(opened, ["https://github.com/acme/web-app/pull/12"])
            await pilot.press("space")
            await pilot.pause()
            self.assertEqual(self.pr_lines()[0], "draft web-app#12 Logout novo")
            await pilot.press("e", *" do time", "enter")
            await pilot.pause()
            self.assertEqual(self.pr_lines()[0], "draft web-app#12 Logout novo do time")
            await pilot.press("down", "d", "1")
            await pilot.pause()
            self.assertEqual(self.pr_lines(), ["draft web-app#12 Logout novo do time"])
            # found again by gh, a deleted PR stays deleted
            self.assertEqual(prs.dropped(config.load_state(self.config), str(self.logout)), {"https://github.com/acme/web-app/pull/3"})

    async def test_the_footer_shows_the_pr_keys_while_the_prs_have_focus(self):
        self.add_prs()
        app = self.app()
        async with app.run_test() as pilot:
            keys = lambda: str(app.query_one("#keys", Static).render())
            self.assertIn("p add PR", keys())
            app.query_one("#prs").focus()
            await pilot.pause()
            self.assertIn("enter open", keys())
            self.assertIn("space state", keys())
            await pilot.press("escape")
            await pilot.pause()
            self.assertIn("tab steps", keys())

    async def test_gh_keeps_the_states_current_in_the_background(self):
        self.add_prs()

        def gh(args, folder):
            if args[:2] == ["pr", "view"] and args[2].endswith("/pull/12"):
                return '{"url": "https://github.com/acme/web-app/pull/12", "state": "MERGED", "isDraft": false, "title": "x"}'
            return None

        app = self.app(gh=gh)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(self.pr_lines()[0], "merged web-app#12 Logout novo")

    # ---------- what changed since you last looked ----------

    async def test_steps_added_or_done_since_you_last_looked_are_marked_until_you_move_on(self):
        app = self.app()
        async with app.run_test() as pilot:  # the first run takes everything as seen
            await pilot.pause()
            self.assertRegex(self.row_text(app, "item:logout").splitlines()[0], r"^● +Migração auth +logout +1/2 *$")
        self.logout.write_text(self.logout.read_text(encoding="utf-8").replace(
            "- [ ] [manual] Pedir review\n", "- [x] [manual] Pedir review\n- [ ] [agent] Teste e2e\n"), encoding="utf-8")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertRegex(self.row_text(app, "item:logout").splitlines()[0], r"^● +Migração auth +logout +\+1 ✓1 +2/3 *$")
            self.assertEqual(self.shown_steps(app), [
                "[x] [agent] Ajustar handler", "[x] [manual] Pedir review  done since you looked", "[ ] [agent] Teste e2e  new"])
            self.assertIn("+1 ✓1 since you looked", str(app.query_one("#steps-title", Label).render()))
            await pilot.press("down")  # moving on means you saw it
            await pilot.pause()
            self.assertRegex(self.row_text(app, "item:logout").splitlines()[0], r"^● +Migração auth +logout +2/3 *$")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.shown_steps(app)[2], "[ ] [agent] Teste e2e")

    async def test_an_item_that_appeared_since_you_last_looked_is_new_and_your_own_changes_are_not_news(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a", *"Atualizar docs", "enter")
            await pilot.press("n", *"Ideia minha", "enter")
            await pilot.pause()
            self.assertRegex(self.row_text(app, self.rows(app)[0]).splitlines()[0], r"Ideia minha +0d *$")
        item(self.folder, "from-agent", "doing", "2026-10-11 11:00")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertRegex(self.row_text(app, "item:from-agent").splitlines()[0], r"from-agent +new *$")
            self.assertRegex(self.row_text(app, "item:logout").splitlines()[0], r"logout +1/3 *$")

    # ---------- what git has left ----------

    async def test_the_row_and_the_detail_say_what_git_has_left_of_the_work(self):
        repo = self.root / "web-app"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        text = self.logout.read_text(encoding="utf-8")
        self.logout.write_text(text + f"\n## Sessions\n- 2026-10-11 09:00 | web-app (main) | abc | {repo}\n", encoding="utf-8")
        answers = {"remote": "origin\n", "rev-list": "2\n", "status": " M a.py\n?? b.py\n"}
        app = RumoApp(config_path=self.config, today=TODAY, run_git=lambda root, args: answers.get(args[0]))
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            left = "web-app (main): 2 commits not pushed · 2 files not committed"
            lines = [line.strip() for line in self.row_text(app, "item:logout", width=100).splitlines()]
            self.assertEqual(lines[1:], ["↳ manual  Pedir review", f"git     {left}"])
            self.assertIn(f"git  {left}", str(app.query_one("#header", Static).render()))
            self.assertEqual(len(self.row_text(app, "item:waiting-infra").splitlines()), 1)

    async def test_without_git_checks_nothing_is_said(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertNotIn("git", self.row_text(app, "item:logout"))

    async def test_picks_up_changes_made_on_disk(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            item(self.folder, "from-agent", "doing", "2026-10-11 11:00")
            await pilot.pause(2.5)
            self.assertIn("item:from-agent", self.rows(app))

    async def test_first_run_creates_the_config_file(self):
        self.config.unlink()
        os.environ["RUMO_DIR"] = str(self.folder)
        try:
            async with self.app().run_test() as pilot:
                await pilot.pause()
            self.assertTrue(self.config.is_file())
            self.assertEqual(config.load(self.config).tags, config.DEFAULT_TAGS)
        finally:
            os.environ.pop("RUMO_DIR", None)

    async def test_unknown_group_falls_back_to_no_label(self):
        item(self.folder, "orphan", "doing", "2026-10-11 12:00", extra="group: deleted-group\n")
        app = self.app()
        async with app.run_test() as pilot:
            timeline = app.query_one("#timeline", OptionList)
            timeline.highlighted = timeline.get_option_index("item:orphan")
            await pilot.pause()
            self.assertNotIn("group", str(app.query_one("#header", Static).render()))
            self.assertRegex(self.row_text(app, "item:orphan").splitlines()[0], r"^● +orphan *$")

    async def test_malformed_config_is_reported_in_the_footer(self):
        self.config.write_text("tags = [broken", encoding="utf-8")
        os.environ["RUMO_DIR"] = str(self.folder)
        try:
            app = self.app()
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertIn("Config error", str(app.query_one("#legend", Static).render()))
                self.assertIn("item:logout", self.rows(app))
        finally:
            os.environ.pop("RUMO_DIR", None)


if __name__ == "__main__":
    unittest.main()
