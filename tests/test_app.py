import os
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rich.console import Console
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, OptionList, Static

from rumo import config, items
from rumo.app import GroupScreen, RumoApp

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

    def app(self) -> RumoApp:
        return RumoApp(config_path=self.config, today=TODAY)

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
        m = re.search(r"\[bold\]\s*(?:[pl1-3] )?([A-Z]\w*)", str(title))
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

    async def test_a_row_is_symbol_label_title_progress_and_next_step(self):
        item(self.folder, "pix", "doing", "2026-10-11 09:00", title="Pagamentos: Pix no checkout",
             extra="group: pagamentos\n", body="## Steps\n- [ ] [agent] Gerar o QR code\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            logout = self.row_text(app, "item:logout").splitlines()
            self.assertEqual(len(logout), 2)
            self.assertRegex(logout[0], r"^● +Migração auth +logout +1/2 *$")
            self.assertRegex(logout[1], r"^ +↳ Pedir review *$")
            pix = self.row_text(app, "item:pix").splitlines()[0]
            self.assertRegex(pix, r"^● +Pagamentos +Pix no checkout +0/1 *$")
            self.assertRegex(self.row_text(app, "item:waiting-infra").splitlines()[0], r"^◐ +waiting-infra *$")

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
                ("past", "done in the last 14 days", "h ▸ Now"),
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
            for key, expected, name in [("v", projects, "Projects"), ("v", repos, "Repos"), ("v", timeline, "Timeline"),
                                        ("3", repos, "Repos"), ("2", projects, "Projects"), ("1", timeline, "Timeline")]:
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
            for key in ("v", "v", "v"):
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

    async def test_a_adds_a_step_after_the_open_ones_and_tab_switches_agent_and_manual(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("a")
            hint = str(app.screen.query_one("#kind", Static).render())
            self.assertIn("tab", hint)
            self.assertIn("[agent]", hint)
            await pilot.press(*"Atualizar docs", "tab", "enter")
            await pilot.pause()
            self.assertEqual(self.steps()[-1], "  manual Atualizar docs")
            self.assertEqual(app.query_one("#steps").index, 2)

    async def test_in_the_steps_a_adds_below_and_enter_edits_the_highlighted_step(self):
        app = self.app()
        async with app.run_test() as pilot:
            app.query_one("#steps").focus()
            await pilot.press("a", *"Revisar o fluxo", "enter")
            await pilot.pause()
            self.assertEqual(self.steps(), ["x agent Ajustar handler", "  agent Revisar o fluxo", "  manual Pedir review"])
            await pilot.press("down", "enter", *" do time", "tab", "enter")
            await pilot.pause()
            self.assertEqual(self.steps()[2], "  agent Pedir review do time")

    async def test_shift_arrows_move_a_step_and_d_deletes_it_after_confirming(self):
        app = self.app()
        async with app.run_test() as pilot:
            app.query_one("#steps").focus()
            await pilot.press("down", "shift+up")
            await pilot.pause()
            self.assertEqual(self.steps(), ["  manual Pedir review", "x agent Ajustar handler"])
            self.assertEqual(app.query_one("#steps").index, 0)
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

    def shown_steps(self, app: RumoApp) -> list[str]:
        return [str(row.query_one(Label).render()) for row in app.query_one("#steps").query(ListItem)]

    async def test_x_hides_the_done_steps_of_every_item_and_shows_them_again(self):
        app = self.app()
        async with app.run_test() as pilot:
            keys = lambda: str(app.query_one("#keys", Static).render())
            self.assertIn("x hide done", keys())
            await pilot.press("x")
            await pilot.pause()
            self.assertEqual(self.shown_steps(app), ["[ ] [manual] Pedir review"])
            self.assertIn("1 done hidden", str(app.query_one("#steps-title", Label).render()))
            self.assertIn("x show done", keys())
            await pilot.press("x")
            await pilot.pause()
            self.assertEqual(len(self.shown_steps(app)), 2)
            self.assertNotIn("hidden", str(app.query_one("#steps-title", Label).render()))
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
            self.assertEqual(app.query_one("#steps").index, 0)
            await pilot.press("down", "space")  # checks Um, which then hides
            await pilot.pause()
            self.assertEqual(self.steps(), ["  agent Dois", "x agent Feito", "x agent Um", "  agent Tres"])
            self.assertEqual(self.shown_steps(app), ["[ ] [agent] Dois", "[ ] [agent] Tres"])
            await pilot.press("a", *"Quatro", "enter")  # below the highlighted Tres
            await pilot.pause()
            self.assertEqual(self.steps()[-1], "  agent Quatro")
            self.assertEqual(app.query_one("#steps").index, 2)

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
