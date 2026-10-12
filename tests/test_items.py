import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from rumo import config, items

ITEM = """---
title: Redirect depois do logout
status: doing
group: auth
tags: [bug, frontend]
created: 2026-10-11 10:08
updated: 2026-10-11 16:30
---

## Left off
Ajustar o redirect para a home.

## Context
Usuário volta para a página protegida.

## Steps
- [x] [agent] Ajustar o handler de logout
- [ ] [manual] Pedir review da PR
- [ ] [agent] Teste e2e do logout

## Sessions
- 2026-10-11 10:08 | web-app (main) | abc-123 | /Users/x/repo/web-app
- 2026-10-11 12:00 | web-app (main) | def-456 | /Users/x/repo/web-app | closed
"""


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


class ItemsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.path = write(self.folder, "2026-10-11-redirect-depois-do-logout.md", ITEM)

    def test_reads_fields_sections_steps_and_sessions(self):
        item = items.load(self.path)
        self.assertEqual(item.slug, "2026-10-11-redirect-depois-do-logout")
        self.assertEqual(item.title, "Redirect depois do logout")
        self.assertEqual(item.status, "doing")
        self.assertEqual(item.group, "auth")
        self.assertEqual(item.tags, ["bug", "frontend"])
        self.assertEqual(item.sections["left off"], "Ajustar o redirect para a home.")
        self.assertEqual(item.sections["context"], "Usuário volta para a página protegida.")
        self.assertEqual([(s.done, s.kind, s.text) for s in item.steps], [
            (True, "agent", "Ajustar o handler de logout"),
            (False, "manual", "Pedir review da PR"),
            (False, "agent", "Teste e2e do logout"),
        ])
        next_step = item.next_step
        assert next_step is not None
        self.assertEqual(next_step.text, "Pedir review da PR")
        self.assertEqual([(s.id, s.closed) for s in item.sessions], [("abc-123", False), ("def-456", True)])
        self.assertEqual(item.sessions[0].resume_command_for(windows=False), "cd /Users/x/repo/web-app && claude --resume abc-123")
        self.assertEqual(item.sessions[0].resume_command_for(windows=True), 'cd "/Users/x/repo/web-app"; claude --resume abc-123')

    def test_tolerates_minimal_and_unknown_content(self):
        idea = items.load(write(self.folder, "2026-10-11-cache.md", "---\ntitle: Cache\nstatus: idea\n---\n"))
        self.assertEqual((idea.title, idea.status, idea.tags, idea.steps, idea.next_step), ("Cache", "idea", [], [], None))

        odd = items.load(write(self.folder, "2026-10-11-odd.md", "---\nstatus: whatever\n---\n## Random\ntext\n"))
        self.assertEqual(odd.status, "doing")
        self.assertEqual(odd.title, "2026-10-11-odd")

        bare = items.load(write(self.folder, "2026-10-11-bare.md", "just text\n"))
        self.assertEqual((bare.title, bare.status), ("2026-10-11-bare", "doing"))

    def test_done_records_completion_and_reopening_clears_it(self):
        items.set_status(items.load(self.path), "done", now=datetime(2026, 10, 12, 9, 0))
        item = items.load(self.path)
        self.assertEqual(item.status, "done")
        self.assertEqual(item.meta["done"], "2026-10-12 09:00")
        self.assertEqual(item.meta["updated"], "2026-10-12 09:00")
        self.assertIn("Ajustar o redirect para a home.", self.path.read_text())

        items.set_status(item, "doing", now=datetime(2026, 10, 13, 8, 30))
        item = items.load(self.path)
        self.assertEqual(item.status, "doing")
        self.assertNotIn("done", item.meta)
        self.assertEqual(item.meta["updated"], "2026-10-13 08:30")

    def test_toggles_steps_and_edits_group_and_tags(self):
        item = items.load(self.path)
        items.toggle_step(item, item.steps[1])
        item = items.load(self.path)
        items.toggle_step(item, item.steps[0])
        item = items.load(self.path)
        self.assertEqual([s.done for s in item.steps], [False, True, False])
        self.assertIn("- [x] [manual] Pedir review da PR", self.path.read_text())

        items.set_group(item, "billing")
        items.set_tags(items.load(self.path), ["feature", "review"])
        item = items.load(self.path)
        self.assertEqual((item.group, item.tags), ("billing", ["feature", "review"]))
        self.assertIn("tags: [feature, review]", self.path.read_text())

        items.set_group(item, None)
        items.set_tags(items.load(self.path), [])
        item = items.load(self.path)
        self.assertEqual((item.group, item.tags), ("", []))
        self.assertNotIn("group:", self.path.read_text())

    def test_edits_a_file_written_without_frontmatter(self):
        path = write(self.folder, "2026-10-11-bare.md", "## Steps\n- [ ] [manual] Ligar para o suporte\n")
        item = items.load(path)
        items.toggle_step(item, item.steps[0])
        item = items.load(path)
        self.assertTrue(item.steps[0].done)
        self.assertIn("updated:", path.read_text())

    def test_creates_ideas_with_unique_ascii_slugs(self):
        now = datetime(2026, 10, 14, 17, 2)
        first = items.create_idea(self.folder, "Cache nas respostas da API", now=now)
        second = items.create_idea(self.folder, "Cache nas respostas da API!", now=now)
        accented = items.create_idea(self.folder, "Revisão de migração", now=now)
        self.assertEqual(first.name, "2026-10-14-cache-nas-respostas-da-api.md")
        self.assertEqual(second.name, "2026-10-14-cache-nas-respostas-da-api-2.md")
        self.assertEqual(accented.name, "2026-10-14-revisao-de-migracao.md")

        idea = items.load(first)
        self.assertEqual((idea.title, idea.status, idea.tags), ("Cache nas respostas da API", "idea", []))
        self.assertEqual((idea.meta["created"], idea.meta["updated"]), ("2026-10-14 17:02", "2026-10-14 17:02"))

    def test_lists_only_item_files(self):
        items.create_idea(self.folder, "Outra ideia")
        write(self.folder, "notes.txt", "x")
        write(self.folder, "half-written.md.tmp", "x")
        self.assertEqual(sorted(i.title for i in items.list_items(self.folder)), ["Outra ideia", "Redirect depois do logout"])
        self.assertEqual(items.list_items(self.folder / "missing"), [])

    def test_groups_items_into_past_now_and_later(self):
        def item(slug, status, updated, done=""):
            extra = f"done: {done}\n" if done else ""
            write(self.folder, f"{slug}.md", f"---\ntitle: {slug}\nstatus: {status}\nupdated: {updated}\n{extra}---\n")

        self.path.unlink()
        item("old-done", "done", "2026-09-01 10:00", done="2026-09-01 10:00")
        item("done-tue", "done", "2026-10-07 10:00", done="2026-10-07 10:00")
        item("done-fri", "done", "2026-10-10 18:00", done="2026-10-10 18:00")
        item("waiting", "waiting", "2026-10-11 12:00")
        item("doing-old", "doing", "2026-10-09 09:00")
        item("doing-new", "doing", "2026-10-11 09:00")
        item("idea", "idea", "2026-10-11 08:00")
        item("paused", "paused", "2026-10-01 08:00")

        all_items = items.list_items(self.folder)
        tl = items.timeline(all_items, today=datetime(2026, 10, 11).date())
        self.assertEqual([i.slug for i in tl.past], ["done-tue", "done-fri"])  # oldest first, closest to Now last
        self.assertEqual(tl.older, 1)
        self.assertEqual([i.slug for i in tl.now], ["doing-new", "doing-old", "waiting"])
        self.assertEqual([i.slug for i in tl.later], ["paused", "idea"])

        everything = items.timeline(all_items, today=datetime(2026, 10, 11).date(), show_older=True)
        self.assertEqual([i.slug for i in everything.past], ["old-done", "done-tue", "done-fri"])
        self.assertEqual(everything.older, 0)

    def test_index_has_one_compact_line_per_item(self):
        items.create_idea(self.folder, "Cache nas respostas", now=datetime(2026, 10, 14, 9, 0))
        lines = items.index(items.list_items(self.folder)).splitlines()
        self.assertEqual(lines, [
            "2026-10-11-redirect-depois-do-logout | doing | Redirect depois do logout | group: auth | tags: bug, frontend"
            " | left off: Ajustar o redirect para a home.",
            "2026-10-14-cache-nas-respostas | idea | Cache nas respostas",
        ])

    def test_quoted_values_are_read_without_the_quotes(self):
        write(self.folder, "a.md", '---\ntitle: "PhraseDay: ASO e conversão"\nstatus: doing\n---\n')
        write(self.folder, "b.md", "---\ntitle: 'Word Fall: iPhone Duo'\nstatus: \"waiting\"\n---\n")
        write(self.folder, "c.md", '---\ntitle: "O \\"ok\\" do Pedro \\\\ hoje"\n---\n')
        write(self.folder, "d.md", '---\ntitle: "meio aberto\n---\n')
        loaded = {i.slug: i for i in items.list_items(self.folder)}
        self.assertEqual(loaded["a"].title, "PhraseDay: ASO e conversão")
        self.assertEqual((loaded["b"].title, loaded["b"].status), ("Word Fall: iPhone Duo", "waiting"))
        self.assertEqual(loaded["c"].title, 'O "ok" do Pedro \\ hoje')
        self.assertEqual(loaded["d"].title, '"meio aberto')
        self.assertIn("| doing | PhraseDay: ASO e conversão", items.index([loaded["a"]]))

    def test_edits_a_file_whose_frontmatter_was_left_unclosed(self):
        path = write(self.folder, "2026-10-11-broken.md", "---\ntitle: Quebrado\n\n## Left off\nAlgo\n")
        items.set_status(items.load(path), "paused")
        item = items.load(path)
        self.assertEqual(item.status, "paused")
        self.assertIn("title: Quebrado", path.read_text())

    def test_refuses_to_write_when_file_changed_after_reading(self):
        item = items.load(self.path)
        time.sleep(0.01)
        self.path.write_text(ITEM.replace("home", "landing"), encoding="utf-8")
        with self.assertRaises(items.WriteConflict):
            items.set_status(item, "paused")
        self.assertIn("landing", self.path.read_text())


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.path = self.root / "config.toml"
        self.env = {k: os.environ.pop(k, None) for k in ("RUMO_DIR", "LANG", "LC_ALL")}

    def tearDown(self):
        for k, v in self.env.items():
            if v is not None:
                os.environ[k] = v

    def test_missing_file_gives_defaults_with_the_system_language(self):
        cfg = config.load(self.path)
        self.assertEqual(cfg.language, "en")
        os.environ["LANG"] = "pt_BR.UTF-8"
        self.assertEqual(config.load(self.path).language, "pt-BR")
        os.environ.pop("LANG")
        self.assertIn("tech-debt", cfg.tags)
        self.assertEqual(cfg.groups, {})
        self.assertIsNone(cfg.error)
        self.assertEqual(cfg.folder, Path.home() / "rumo")

    def test_reads_folder_language_tags_and_groups(self):
        self.path.write_text(config.default_text(self.root / "items", "pt-BR"), encoding="utf-8")
        config.add_group(self.path, "Migração auth", "blue")
        config.add_group(self.path, "Migração auth", "green")
        cfg = config.load(self.path)
        self.assertEqual((cfg.folder, cfg.language), (self.root / "items", "pt-BR"))
        self.assertEqual(cfg.tags, config.DEFAULT_TAGS)
        self.assertEqual([(g.key, g.name, g.color) for g in cfg.groups.values()], [
            ("migracao-auth", "Migração auth", "blue"),
            ("migracao-auth-2", "Migração auth", "green"),
        ])

    def test_updates_a_group_and_keeps_the_rest_of_the_file(self):
        original = (
            "# my notes\nfolder = \"~/rumo\"\n\n"
            "[groups.auth]\nname = \"Auth\"  # login work\ncolor = \"blue\"\n\n"
            "# payments below\n[ groups . \"pay\" ]\nname = \"Pagamentos\"\n\n"
            "[groups.ops]\nname = \"Ops\"\ncolor = \"red\"\n"
        )
        self.path.write_text(original, encoding="utf-8")
        config.update_group(self.path, "auth", name="Migração auth")
        config.update_group(self.path, "pay", color="green")
        config.update_group(self.path, "ops", name="Infra", color="cyan")
        self.assertEqual(self.path.read_text(encoding="utf-8"), (
            "# my notes\nfolder = \"~/rumo\"\n\n"
            "[groups.auth]\nname = \"Migração auth\"\ncolor = \"blue\"\n\n"
            "# payments below\n[ groups . \"pay\" ]\nname = \"Pagamentos\"\ncolor = \"green\"\n\n"
            "[groups.ops]\nname = \"Infra\"\ncolor = \"cyan\"\n"
        ))

    def test_deletes_a_group_and_keeps_the_rest_of_the_file(self):
        self.path.write_text(
            "folder = \"~/rumo\"\n\n[groups.auth]\nname = \"Auth\"\ncolor = \"blue\"\n\n"
            "[groups.pay]\nname = \"Pay\"\ncolor = \"green\"\n\n[other]\nx = 1\n",
            encoding="utf-8",
        )
        config.delete_group(self.path, "auth")
        config.delete_group(self.path, "pay")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "folder = \"~/rumo\"\n\n[other]\nx = 1\n")

    def test_group_that_cannot_be_edited_as_text_raises(self):
        self.path.write_text("[groups]\nauth = { name = \"Auth\", color = \"blue\" }\n", encoding="utf-8")
        self.assertEqual(config.load(self.path).groups["auth"].name, "Auth")
        with self.assertRaises(config.GroupNotEditable):
            config.update_group(self.path, "auth", name="X")
        with self.assertRaises(config.GroupNotEditable):
            config.delete_group(self.path, "missing")

    def test_ask_to_track_is_unset_until_answered_then_a_top_level_key(self):
        self.assertIsNone(config.load(self.path).ask_to_track)
        # No file yet: only the key, so the language still comes from the system (the hook's
        # environment may not have it) and the first `rumo` run can still write its defaults.
        config.set_ask_to_track(self.path, False)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "ask_to_track = false\n")
        self.assertIs(config.load(self.path).ask_to_track, False)

        self.path.write_text(
            "folder = \"~/rumo\"\n\n# groups\n[groups.auth]\nname = \"Auth\"\ncolor = \"blue\"\n", encoding="utf-8")
        config.set_ask_to_track(self.path, True)
        config.set_ask_to_track(self.path, False)
        self.assertEqual(self.path.read_text(encoding="utf-8"), (
            "folder = \"~/rumo\"\nask_to_track = false\n\n# groups\n[groups.auth]\nname = \"Auth\"\ncolor = \"blue\"\n"))
        cfg = config.load(self.path)
        self.assertEqual((cfg.ask_to_track, cfg.groups["auth"].name), (False, "Auth"))

    def test_language_from_env(self):
        self.assertEqual(config.language_from_env({"LANG": "pt_BR.UTF-8"}), "pt-BR")
        self.assertEqual(config.language_from_env({"LC_ALL": "de_DE.UTF-8", "LANG": "en_US"}), "de-DE")
        self.assertEqual(config.language_from_env({"LANG": "C"}), "en")
        self.assertEqual(config.language_from_env({}), "en")

    def test_ensure_file_writes_defaults_once(self):
        config.ensure_file(self.path, self.root / "items", "es")
        config.ensure_file(self.path, self.root / "other", "fr")
        cfg = config.load(self.path)
        self.assertEqual((cfg.folder, cfg.language), (self.root / "items", "es"))

    def test_config_lives_where_each_system_expects(self):
        home = Path.home()
        self.assertEqual(config.default_path(env={}, windows=False), home / ".config" / "rumo" / "config.toml")
        self.assertEqual(config.default_path(env={"XDG_CONFIG_HOME": "/xdg"}, windows=False), Path("/xdg/rumo/config.toml"))
        self.assertEqual(config.default_path(env={"APPDATA": "/appdata"}, windows=True), Path("/appdata/rumo/config.toml"))
        self.assertEqual(config.default_path(env={"RUMO_CONFIG": "/x/c.toml", "APPDATA": "/a"}, windows=True), Path("/x/c.toml"))

    def test_env_overrides_folder_and_malformed_file_falls_back_to_defaults(self):
        self.path.write_text("language = [broken", encoding="utf-8")
        os.environ["RUMO_DIR"] = str(self.root / "elsewhere")
        cfg = config.load(self.path)
        self.assertEqual(cfg.folder, self.root / "elsewhere")
        self.assertEqual(cfg.language, "en")
        self.assertIsNotNone(cfg.error)


if __name__ == "__main__":
    unittest.main()
