import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from textual.widgets import OptionList, Static

from rumo import config, items
from rumo.app import RumoApp

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
