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

    def steps(self) -> list[str]:
        return [f"{'x' if s.done else ' '} {s.kind} {s.text}" for s in items.load(self.path).steps]

    def test_adds_a_step_below_the_chosen_one_or_after_the_open_steps(self):
        item = items.load(self.path)
        self.assertEqual(items.add_step(item, "Atualizar o changelog", "manual", after=item.steps[0]), 1)
        self.assertEqual(items.add_step(items.load(self.path), "  Abrir a PR\n de novo ", "agent"), 4)
        self.assertEqual(self.steps(), [
            "x agent Ajustar o handler de logout",
            "  manual Atualizar o changelog",
            "  manual Pedir review da PR",
            "  agent Teste e2e do logout",
            "  agent Abrir a PR de novo",
        ])
        self.assertIn("- [ ] [manual] Atualizar o changelog\n- [ ] [manual] Pedir review", self.path.read_text())
        self.assertIn("\n\n## Sessions", self.path.read_text())

    def test_adds_the_steps_section_when_the_item_has_none(self):
        idea = write(self.folder, "2026-10-11-idea.md", "---\ntitle: Cache\nstatus: idea\n---\n\n## Context\nLento.\n")
        items.add_step(items.load(idea), "Medir o tempo da home", "agent")
        self.assertTrue(idea.read_text().endswith("## Context\nLento.\n\n## Steps\n- [ ] [agent] Medir o tempo da home\n"))

        linked = write(self.folder, "2026-10-11-linked.md", "---\ntitle: X\n---\n\n## Sessions\n- a | b | c | d\n")
        items.add_step(items.load(linked), "Primeiro passo", "manual")
        self.assertIn("---\n\n## Steps\n- [ ] [manual] Primeiro passo\n\n## Sessions\n", linked.read_text())

    def test_edits_moves_and_deletes_steps(self):
        item = items.load(self.path)
        items.edit_step(item, item.steps[0], "Ajustar o handler e o redirect", "manual")
        item = items.load(self.path)
        items.swap_steps(item, item.steps[2], item.steps[1])
        item = items.load(self.path)
        items.delete_step(item, item.steps[2])
        self.assertEqual(self.steps(), [
            "x manual Ajustar o handler e o redirect",
            "  agent Teste e2e do logout",
        ])
        self.assertIn("updated: ", self.path.read_text())
        self.assertNotIn("updated: 2026-10-11 16:30", self.path.read_text())

    def test_swaps_steps_that_are_not_next_to_each_other(self):
        item = items.load(self.path)
        items.swap_steps(item, item.steps[0], item.steps[2])
        self.assertEqual([s.text for s in items.load(self.path).steps],
                         ["Teste e2e do logout", "Pedir review da PR", "Ajustar o handler de logout"])

    def test_a_step_needs_text_and_a_known_kind(self):
        item = items.load(self.path)
        with self.assertRaises(ValueError):
            items.add_step(item, "   ", "agent")
        with self.assertRaises(ValueError):
            items.add_step(item, "Algo", "someone")

    def test_step_edits_refuse_when_the_line_changed(self):
        item = items.load(self.path)
        stale = item.steps[1]
        items.edit_step(item, stale, "Pedir review ao time", "manual")
        with self.assertRaises(items.WriteConflict):
            items.delete_step(items.load(self.path), stale)

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

    def test_a_reader_never_sees_a_half_written_state(self):
        # Hooks of several sessions and the TUI write state.json at the same time: a reader that caught
        # the file truncated would write back only its own keys and wipe the rest.
        import threading

        config.update_state(self.path, keep="me", n=0)
        done = threading.Event()

        def writer() -> None:
            for n in range(60):
                config.update_state(self.path, n=n, padding="x" * 2_000_000)
            done.set()

        threading.Thread(target=writer).start()
        reads = 0
        while not done.is_set():
            self.assertEqual(config.load_state(self.path).get("keep"), "me")
            reads += 1
        self.assertGreater(reads, 0)
        self.assertEqual([p.name for p in self.root.iterdir()], ["state.json"])

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



PR_ITEM = """---
title: Checkout novo
status: doing
updated: 2026-10-11 16:30
---

## Steps
- [ ] [agent] Testar o checkout

## PRs
- [open] https://github.com/acme/web-app/pull/12 Checkout novo
- [merged] https://gitlab.com/acme/pay/api/-/merge_requests/34
- https://bitbucket.org/acme/infra/pull-requests/5 Infra

## Sessions
- 2026-10-11 10:08 | web-app (feature/checkout) | abc-123 | /Users/x/repo/web-app
"""


class PullRequestsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.path = write(self.folder, "checkout.md", PR_ITEM)

    def prs(self) -> list[tuple[str, str, str, str]]:
        return [(p.state, p.name, p.url, p.title) for p in items.load(self.path).prs]

    def test_reads_each_pr_with_its_state_short_name_and_title(self):
        self.assertEqual(self.prs(), [
            ("open", "web-app#12", "https://github.com/acme/web-app/pull/12", "Checkout novo"),
            ("merged", "api!34", "https://gitlab.com/acme/pay/api/-/merge_requests/34", ""),
            ("", "infra#5", "https://bitbucket.org/acme/infra/pull-requests/5", "Infra"),
        ])
        self.assertEqual([p.finished for p in items.load(self.path).prs], [False, True, False])

    def test_finds_pr_links_in_any_text_once_each(self):
        text = (
            "Created https://github.com/acme/web-app/pull/12/files and https://github.com/acme/web-app/pull/12.\n"
            "View merge request: https://gitlab.com/acme/pay/api/-/merge_requests/34?tab=diffs\n"
            "Open one at https://github.com/acme/web-app/pull/new/feature and see https://github.com/acme/web-app/issues/3"
        )
        self.assertEqual(items.pr_links(text), [
            "https://github.com/acme/web-app/pull/12", "https://gitlab.com/acme/pay/api/-/merge_requests/34",
        ])

    def test_adds_a_pr_once_at_the_end_of_the_section(self):
        self.assertTrue(items.add_pr(items.load(self.path), "https://github.com/acme/web-app/pull/13#top", "draft", "Frete"))
        self.assertFalse(items.add_pr(items.load(self.path), "https://github.com/acme/web-app/pull/12/commits"))
        self.assertEqual(self.prs()[-1], ("draft", "web-app#13", "https://github.com/acme/web-app/pull/13", "Frete"))
        self.assertEqual(len(self.prs()), 4)
        self.assertIn("- [draft] https://github.com/acme/web-app/pull/13 Frete\n\n## Sessions", self.path.read_text())

    def test_adds_the_prs_section_before_sessions_or_at_the_end(self):
        path = write(self.folder, "a.md", "---\ntitle: A\n---\n\n## Steps\n- [ ] [agent] X\n\n## Sessions\n- s\n")
        items.add_pr(items.load(path), "https://github.com/acme/a/pull/1", "open")
        self.assertIn("- [ ] [agent] X\n\n## PRs\n- [open] https://github.com/acme/a/pull/1\n\n## Sessions", path.read_text())
        path = write(self.folder, "b.md", "---\ntitle: B\n---\n\n## Steps\n- [ ] [agent] X\n")
        items.add_pr(items.load(path), "https://github.com/acme/b/pull/2")
        self.assertTrue(path.read_text().endswith("- [ ] [agent] X\n\n## PRs\n- https://github.com/acme/b/pull/2\n"))

    def test_any_link_can_be_added_but_not_other_text(self):
        items.add_pr(items.load(self.path), "https://review.example.com/c/123", "open")
        self.assertEqual(self.prs()[-1][1:3], ("review.example.com/c/123", "https://review.example.com/c/123"))
        with self.assertRaises(ValueError):
            items.add_pr(items.load(self.path), "not a link")
        with self.assertRaises(ValueError):
            items.add_pr(items.load(self.path), "https://github.com/acme/web-app/pull/14", "shipped")

    def test_changes_the_state_and_title_and_deletes_a_pr(self):
        pr = items.load(self.path).prs[0]
        items.update_pr(items.load(self.path), pr, state="merged")
        self.assertEqual(self.prs()[0][0::3], ("merged", "Checkout novo"))
        items.update_pr(items.load(self.path), items.load(self.path).prs[2], title="Infra nova", state="")
        self.assertEqual(self.prs()[2][0::3], ("", "Infra nova"))
        items.delete_pr(items.load(self.path), items.load(self.path).prs[1])
        self.assertEqual([p[1] for p in self.prs()], ["web-app#12", "infra#5"])
        items.update_pr(items.load(self.path), items.load(self.path).prs[0], url="https://github.com/acme/web-app/pull/21/files")
        self.assertEqual(self.prs()[0], ("merged", "web-app#21", "https://github.com/acme/web-app/pull/21", "Checkout novo"))
        with self.assertRaises(ValueError):
            items.update_pr(items.load(self.path), items.load(self.path).prs[0], url="nada")

    def test_pr_edits_refuse_when_the_line_changed(self):
        item = items.load(self.path)
        self.path.write_text(PR_ITEM.replace("pull/12", "pull/99"), encoding="utf-8")
        os.utime(self.path, (item.mtime, item.mtime))
        with self.assertRaises(items.WriteConflict):
            items.update_pr(item, item.prs[0], state="closed")


if __name__ == "__main__":
    unittest.main()
