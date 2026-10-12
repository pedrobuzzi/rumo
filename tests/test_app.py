import os
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from textual.widgets import Input, OptionList, Static

from rumo import config, items
from rumo.app import GroupScreen, RumoApp

TODAY = date(2026, 10, 11)


def item(folder: Path, slug: str, status: str, updated: str, extra: str = "", body: str = "") -> Path:
    path = folder / f"{slug}.md"
    path.write_text(f"---\ntitle: {slug}\nstatus: {status}\nupdated: {updated}\n{extra}---\n{body}", encoding="utf-8")
    return path


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
        timeline = app.query_one("#timeline", OptionList)
        return [str(timeline.get_option_at_index(i).id) for i in range(timeline.option_count)]

    @staticmethod
    def highlighted(app: RumoApp) -> str:
        timeline = app.query_one("#timeline", OptionList)
        assert timeline.highlighted is not None
        return str(timeline.get_option_at_index(timeline.highlighted).id)

    async def test_opens_on_now_with_past_now_later_and_a_group_legend(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.rows(app), [
                "header:past", "item:shipped",
                "header:now", "item:logout", "item:waiting-infra",
                "header:later", "item:cache-idea",
            ])
            self.assertEqual(self.highlighted(app), "item:logout")
            legend = str(app.query_one("#legend", Static).render())
            self.assertIn("Migração auth", legend)
            self.assertIn("Pagamentos", legend)

    @staticmethod
    def current_view(app: RumoApp) -> str:
        """The view name highlighted in the list's title."""
        m = re.search(r"\[bold\]\s*\d (\w+)", str(app.query_one("#timeline", OptionList).border_title))
        return m.group(1) if m else ""

    @staticmethod
    def row_text(app: RumoApp, option_id: str) -> str:
        return str(app.query_one("#timeline", OptionList).get_option(option_id).prompt)

    async def test_status_is_a_shape_and_a_word(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertIn("● doing", self.row_text(app, "item:logout"))
            self.assertIn("◐ waiting", self.row_text(app, "item:waiting-infra"))
            self.assertIn("○ idea", self.row_text(app, "item:cache-idea"))
            self.assertIn("✓ Oct 09", self.row_text(app, "item:shipped"))

    async def test_v_cycles_the_three_views_and_digits_jump_to_one(self):
        item(self.folder, "pix", "doing", "2026-10-11 11:00", extra="group: pagamentos\n")
        timeline_rows = [
            "header:past", "item:shipped",
            "header:now", "item:pix", "item:logout", "item:waiting-infra",
            "header:later", "item:cache-idea",
        ]
        grouped_rows = [
            "header:past", "item:shipped",
            "header:now", "group:now:migracao-auth", "item:logout", "group:now:pagamentos", "item:pix",
            "group:now:-", "item:waiting-infra",
            "header:later", "group:later:-", "item:cache-idea",
        ]
        project_rows = [
            "group:migracao-auth", "item:logout",
            "group:pagamentos", "item:pix",
            "group:-", "item:shipped", "item:waiting-infra", "item:cache-idea",
        ]
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.rows(app), timeline_rows)
            self.assertEqual(self.current_view(app), "Timeline")
            for key, expected, name in [("v", grouped_rows, "Grouped"), ("v", project_rows, "Projects"),
                                        ("v", timeline_rows, "Timeline"), ("3", project_rows, "Projects"),
                                        ("2", grouped_rows, "Grouped"), ("1", timeline_rows, "Timeline")]:
                await pilot.press(key)
                await pilot.pause()
                self.assertEqual(self.rows(app), expected, key)
                self.assertEqual(self.current_view(app), name)

    async def test_the_selected_item_stays_selected_across_views(self):
        app = self.app()
        async with app.run_test() as pilot:
            app.query_one("#timeline", OptionList).highlighted = self.rows(app).index("item:cache-idea")
            await pilot.press("v")
            await pilot.pause()
            self.assertEqual(self.highlighted(app), "item:cache-idea")
            await pilot.press("v")
            await pilot.pause()
            self.assertEqual(self.highlighted(app), "item:cache-idea")

    async def test_p_and_l_collapse_past_and_later_in_every_view(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("p", "l")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["header:past", "header:now", "item:logout", "item:waiting-infra", "header:later"])
            self.assertIn("1 hidden · p opens", self.row_text(app, "header:past"))
            self.assertIn("1 hidden · l opens", self.row_text(app, "header:later"))
            await pilot.press("v")
            await pilot.pause()
            self.assertEqual(self.rows(app), [
                "header:past", "header:now", "group:now:migracao-auth", "item:logout",
                "group:now:-", "item:waiting-infra", "header:later",
            ])
            await pilot.press("v")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["group:migracao-auth", "item:logout", "group:-", "item:waiting-infra"])
            self.assertIn("1 past ▸ · 1 now · 1 later ▸", self.row_text(app, "group:-"))
            await pilot.press("p", "l")
            await pilot.pause()
            self.assertEqual(self.rows(app), [
                "group:migracao-auth", "item:logout", "group:-", "item:shipped", "item:waiting-infra", "item:cache-idea",
            ])

    async def test_a_filter_shows_matches_in_collapsed_sections(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("p", "l", "slash", *"cache")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["header:later", "item:cache-idea"])
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIn("item:cache-idea", self.rows(app))

    async def test_collapsing_the_selected_item_moves_the_selection_to_now(self):
        app = self.app()
        async with app.run_test() as pilot:
            app.query_one("#timeline", OptionList).highlighted = self.rows(app).index("item:cache-idea")
            await pilot.press("l")
            await pilot.pause()
            self.assertEqual(self.highlighted(app), "item:logout")

    async def test_remembers_the_view_and_collapsed_sections_between_runs(self):
        async with self.app().run_test() as pilot:
            await pilot.press("2", "p")
            await pilot.pause()
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(self.current_view(app), "Grouped")
            self.assertEqual(self.rows(app)[:3], ["header:past", "header:now", "group:now:migracao-auth"])
            self.assertIn("item:cache-idea", self.rows(app))

    async def test_a_broken_remembered_state_falls_back_to_the_timeline(self):
        for text in ("{broken", '{"view": "nope", "past_open": "yes"}', "[]"):
            (self.root / "state.json").write_text(text, encoding="utf-8")
            app = self.app()
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertEqual(self.rows(app), [
                    "header:past", "item:shipped",
                    "header:now", "item:logout", "item:waiting-infra",
                    "header:later", "item:cache-idea",
                ], text)

    async def test_s_marks_done_and_the_item_moves_to_past(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("s", "5")  # 5 = done
            await pilot.pause()
            item = items.load(self.logout)
            self.assertEqual(item.status, "done")
            self.assertIn("done", item.meta)
            rows = self.rows(app)
            self.assertLess(rows.index("item:logout"), rows.index("header:now"))

    async def test_n_captures_an_idea_into_later(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("n", *"Cache na API", "enter")
            await pilot.pause()
            [path] = [p for p in self.folder.glob("*cache-na-api.md")]
            self.assertEqual(items.load(path).status, "idea")
            rows = self.rows(app)
            self.assertGreater(rows.index(f"item:{path.stem}"), rows.index("header:later"))
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
            self.assertIn("Plataforma", str(app.query_one("#legend", Static).render()))

            await pilot.press("g", "4")  # no group (now after three groups)
            await pilot.pause()
            self.assertEqual(items.load(self.logout).group, "")

    @staticmethod
    def dialog_options(app: RumoApp) -> OptionList:
        return app.screen.query_one(OptionList)

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
            legend = str(app.query_one("#legend", Static).render())
            self.assertIn("Auth nova", legend)
            self.assertNotIn("Pagamentos", legend)
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

    async def test_filter_narrows_the_timeline(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.press("slash", *"migração")
            await pilot.pause()
            self.assertEqual(self.rows(app), ["header:now", "item:logout"])
            await pilot.press("escape")
            await pilot.pause()
            self.assertIn("item:cache-idea", self.rows(app))

    async def test_older_done_items_are_hidden_until_asked(self):
        item(self.folder, "ancient", "done", "2026-08-01 10:00", extra="done: 2026-08-01 10:00\n")
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertNotIn("item:ancient", self.rows(app))
            timeline = app.query_one("#timeline", OptionList)
            timeline.highlighted = self.rows(app).index("older")
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(self.rows(app)[:3], ["header:past", "item:ancient", "item:shipped"])

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

    async def test_unknown_group_falls_back_to_plain_title(self):
        item(self.folder, "orphan", "doing", "2026-10-11 12:00", extra="group: deleted-group\n")
        app = self.app()
        async with app.run_test() as pilot:
            timeline = app.query_one("#timeline", OptionList)
            timeline.highlighted = self.rows(app).index("item:orphan")
            await pilot.pause()
            self.assertNotIn("group", str(app.query_one("#header", Static).render()))

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
