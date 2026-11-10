import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from rumo import items

NOW = datetime(2026, 11, 5, 14, 10)


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


IDEA = """---
title: Controle de ponto pra obra
status: idea
group: produtos
tags: [b2b]
created: 2026-09-17 00:00
updated: 2026-09-28 22:10
---

## Context
Mestres de obra anotam o ponto em papel.
"""

CHILD = """---
title: Medição de equipe pra empreiteira faturar
status: idea
from: 2026-09-17-controle-de-ponto-pra-obra
updated: 2026-09-29 08:43
---
"""


class DecidingTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.idea = write(self.folder, "2026-09-17-controle-de-ponto-pra-obra.md", IDEA)
        self.child = write(self.folder, "2026-09-17-medicao-de-equipe.md", CHILD)

    def test_dropping_needs_a_reason_and_writes_it_as_left_off(self):
        item = items.load(self.idea)
        with self.assertRaises(ValueError):
            items.set_status(item, "dropped", now=NOW, reason="  ")
        items.set_status(item, "dropped", now=NOW, reason="Já existe no Gusto")
        dropped = items.load(self.idea)
        self.assertEqual(dropped.status, "dropped")
        self.assertEqual(dropped.meta["dropped"], "2026-11-05 14:10")
        self.assertEqual(dropped.sections["left off"], "Já existe no Gusto")
        self.assertEqual(dropped.done_at, "2026-11-05 14:10")
        self.assertIn("## Context", self.idea.read_text(encoding="utf-8"))

        items.set_status(dropped, "idea", now=NOW)
        reopened = items.load(self.idea)
        self.assertEqual(reopened.status, "idea")
        self.assertNotIn("dropped", reopened.meta)

    def test_a_reason_replaces_an_existing_left_off(self):
        path = write(self.folder, "x.md", "---\ntitle: X\nstatus: idea\n---\n\n## Left off\nantes\n\n## Steps\n- [ ] [agent] a\n")
        items.set_status(items.load(path), "dropped", now=NOW, reason="depois")
        item = items.load(path)
        self.assertEqual(item.sections["left off"], "depois")
        self.assertEqual([s.text for s in item.steps], ["a"])

    def test_dropped_items_go_to_past_for_14_days(self):
        items.set_status(items.load(self.idea), "dropped", now=NOW, reason="não vale")
        tl = items.timeline(items.list_items(self.folder), today=date(2026, 11, 6))
        self.assertEqual([i.title for i in tl.past], ["Controle de ponto pra obra"])
        self.assertNotIn("Controle de ponto pra obra", [i.title for i in tl.later])
        later = items.timeline(items.list_items(self.folder), today=date(2026, 11, 30))
        self.assertEqual((later.past, later.older), ([], 1))

    def test_family_from_the_from_field(self):
        all_items = items.list_items(self.folder)
        child = next(i for i in all_items if i.path == self.child)
        parent = next(i for i in all_items if i.path == self.idea)
        self.assertEqual(child.from_slug, parent.slug)
        self.assertEqual(items.parent_of(all_items, child), parent)
        self.assertEqual(items.children(all_items, parent), [child])
        orphan = items.load(write(self.folder, "o.md", "---\ntitle: O\nstatus: idea\nfrom: gone\n---\n"))
        self.assertIsNone(items.parent_of(all_items, orphan))

    def test_idle_days_count_from_updated_for_ideas_only(self):
        idea = items.load(self.idea)
        self.assertEqual(idea.idle_days(date(2026, 11, 5)), 38)
        doing = items.load(write(self.folder, "d.md", "---\ntitle: D\nstatus: doing\nupdated: 2026-09-28 10:00\n---\n"))
        self.assertIsNone(doing.idle_days(date(2026, 11, 5)))
        undated = items.load(write(self.folder, "u.md", "---\ntitle: U\nstatus: idea\n---\n"))
        self.assertIsNone(undated.idle_days(date(2026, 11, 5)))

    def test_creates_an_idea_with_group_tags_parent_and_context(self):
        path = items.create_idea(self.folder, "Ponto: versão mínima", now=NOW, group="produtos", tags=["b2b"],
                                 parent="2026-09-17-controle-de-ponto-pra-obra", context="Só o mestre usa.")
        item = items.load(path)
        self.assertEqual((item.title, item.status, item.group, item.tags), ("Ponto: versão mínima", "idea", "produtos", ["b2b"]))
        self.assertEqual(item.from_slug, "2026-09-17-controle-de-ponto-pra-obra")
        self.assertEqual(item.sections["context"], "Só o mestre usa.")
        doing = items.load(items.create_idea(self.folder, "Agora", now=NOW, status="doing"))
        self.assertEqual(doing.status, "doing")

    def test_similar_items_share_meaningful_words(self):
        write(self.folder, "z.md", "---\ntitle: Widget da frase do dia\nstatus: idea\n---\n")
        found = items.similar(items.list_items(self.folder), "Ponto eletrônico pra obra")
        self.assertEqual([i.title for i in found], ["Controle de ponto pra obra"])
        self.assertEqual(items.similar(items.list_items(self.folder), "Obra"), [])  # too short a word
        self.assertEqual([i.title for i in items.similar(items.list_items(self.folder), "Medição")],
                         ["Medição de equipe pra empreiteira faturar"])

    def test_index_tells_the_parent_and_how_long_an_idea_sits(self):
        index = items.index(items.list_items(self.folder), today=date(2026, 11, 5))
        line = next(line for line in index.splitlines() if line.startswith("2026-09-17-medicao"))
        self.assertIn("from: 2026-09-17-controle-de-ponto-pra-obra", line)
        self.assertIn("idle: 37d", line)


EXPLORED = """---
title: Medição de equipe
status: idea
updated: 2026-09-29 08:43
---

## Context
Canhoto de medição.

## Explorations
### 2026-09-29 10:00 Como validar em uma semana?
Antigo, sem marcador.

### 2026-11-05 14:10 Quem já faz?
<!-- rumo:exploration id=a1 state=running pid=4242 started=2026-11-05T14:10 limit=600 -->

### 2026-11-05 14:20 E se for a construtora?
<!-- rumo:exploration id=b2 state=failed error="claude saiu com código 1" -->

## Sessions
- 2026-10-11 10:08 | web-app (main) | abc | /x
"""


class ExplorationsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.path = write(self.folder, "m.md", EXPLORED)

    def test_reads_entries_with_and_without_markers(self):
        item = items.load(self.path)
        self.assertEqual([(e.when, e.question, e.state, e.id) for e in item.explorations], [
            ("2026-09-29 10:00", "Como validar em uma semana?", "done", ""),
            ("2026-11-05 14:10", "Quem já faz?", "running", "a1"),
            ("2026-11-05 14:20", "E se for a construtora?", "failed", "b2"),
        ])
        self.assertEqual(item.explorations[0].answer, "Antigo, sem marcador.")
        running = item.explorations[1]
        self.assertEqual((running.pid, running.started, running.limit), (4242, "2026-11-05T14:10", 600))
        self.assertEqual(item.explorations[2].error, "claude saiu com código 1")
        self.assertEqual(len(item.sessions), 1)

    def test_a_running_entry_whose_process_is_gone_or_too_old_reads_as_failed(self):
        running = items.load(self.path).explorations[1]
        alive, dead = (lambda pid: True), (lambda pid: False)
        self.assertEqual(items.exploration_state(running, datetime(2026, 11, 5, 14, 12), alive), "running")
        self.assertEqual(items.exploration_state(running, datetime(2026, 11, 5, 14, 12), dead), "failed")
        self.assertEqual(items.exploration_state(running, datetime(2026, 11, 5, 14, 26), alive), "failed")
        done = items.load(self.path).explorations[0]
        self.assertEqual(items.exploration_state(done, datetime(2026, 11, 5, 14, 12), dead), "done")

    def test_an_exploration_starts_running_then_finishes_with_its_next_questions(self):
        path = write(self.folder, "n.md", "---\ntitle: N\nstatus: idea\n---\n\n## Context\nc\n\n## Sessions\n- s\n")
        items.start_exploration(items.load(path), "Quem já faz?", "c3", pid=99, limit=600, now=NOW)
        entry = items.load(path).explorations[0]
        self.assertEqual((entry.question, entry.state, entry.pid, entry.started, entry.when),
                         ("Quem já faz?", "running", 99, "2026-11-05T14:10", "2026-11-05 14:10"))
        items.finish_exploration(items.load(path), "c3", "#### Diretos\nHeym.", ["Quanto cobram?", "E no Brasil?", "Vale seguir?"])
        entry = items.load(path).explorations[0]
        self.assertEqual((entry.state, entry.answer), ("done", "#### Diretos\nHeym."))
        self.assertEqual(entry.next_questions, ["Quanto cobram?", "E no Brasil?", "Vale seguir?"])
        text = path.read_text(encoding="utf-8")
        self.assertLess(text.index("## Explorations"), text.index("## Sessions"))
        self.assertEqual(items.load(path).sections["context"], "c")

    def test_a_failure_replaces_the_running_entry_and_keeps_the_others(self):
        items.fail_exploration(items.load(self.path), "a1", 'tempo esgotado "10 min" -->')
        item = items.load(self.path)
        self.assertEqual([e.state for e in item.explorations], ["done", "failed", "failed"])
        self.assertEqual(item.explorations[1].error, "tempo esgotado '10 min'")
        self.assertEqual(item.explorations[1].question, "Quem já faz?")

    def test_an_unknown_entry_is_a_conflict(self):
        with self.assertRaises(items.WriteConflict):
            items.finish_exploration(items.load(self.path), "zz", "x", [])

    def test_notes_go_after_context_one_dated_line_each(self):
        items.add_note(items.load(self.path), "Liguei para 3 empreiteiras", now=NOW)
        items.add_note(items.load(self.path), "2 cobram por diária", now=NOW)
        item = items.load(self.path)
        self.assertEqual(item.sections["notes"], "- 2026-11-05 Liguei para 3 empreiteiras\n- 2026-11-05 2 cobram por diária")
        text = self.path.read_text(encoding="utf-8")
        self.assertLess(text.index("## Context"), text.index("## Notes"))
        self.assertLess(text.index("## Notes"), text.index("## Explorations"))
        bare = write(self.folder, "b.md", "---\ntitle: B\nstatus: idea\n---\n")
        items.add_note(items.load(bare), "x", now=NOW)
        self.assertEqual(items.load(bare).sections["notes"], "- 2026-11-05 x")


PARENT = """---
title: Evoluir o rumo
status: doing
group: rumo
tags: [feature]
updated: 2026-10-29 09:03
---

## Steps
- [x] [agent] Escrever a spec
- [ ] [agent] Distribuição: PyPI e CI
- [ ] [manual] Testar a TUI
"""


class ItemStepsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.parent = write(self.folder, "2026-10-11-evoluir-o-rumo.md", PARENT)

    def test_a_step_becomes_an_item_and_points_to_it(self):
        parent = items.load(self.parent)
        child_path = items.step_to_item(parent, parent.steps[1], "Distribuição no PyPI", now=NOW)
        child = items.load(child_path)
        self.assertEqual((child.title, child.status, child.group, child.tags, child.from_slug),
                         ("Distribuição no PyPI", "doing", "rumo", ["feature"], "2026-10-11-evoluir-o-rumo"))
        step = items.load(self.parent).steps[1]
        self.assertEqual((step.done, step.kind, step.shown, step.target), (False, "item", "Distribuição no PyPI", child.slug))
        self.assertEqual(self.parent.read_text(encoding="utf-8").splitlines()[10],
                         f"- [ ] [item] Distribuição no PyPI → {child.slug}")

    def test_the_step_checks_itself_when_the_child_is_done_and_reopens_with_it(self):
        parent = items.load(self.parent)
        child_path = items.step_to_item(parent, parent.steps[1], "Distribuição", now=NOW)
        self.assertEqual(items.sync_item_steps(items.list_items(self.folder)), [])
        items.set_status(items.load(child_path), "done", now=NOW)
        updated_before = items.load(self.parent).meta["updated"]
        self.assertEqual(items.sync_item_steps(items.list_items(self.folder)), [self.parent])
        self.assertTrue(items.load(self.parent).steps[1].done)
        self.assertEqual(items.load(self.parent).meta["updated"], updated_before)
        self.assertEqual(items.sync_item_steps(items.list_items(self.folder)), [])
        items.set_status(items.load(child_path), "doing", now=NOW)
        items.sync_item_steps(items.list_items(self.folder))
        self.assertFalse(items.load(self.parent).steps[1].done)

    def test_an_item_step_to_a_missing_item_stays_as_it_is(self):
        write(self.folder, "p.md", "---\ntitle: P\nstatus: doing\n---\n## Steps\n- [ ] [item] Algo → sumiu\n")
        self.assertEqual(items.sync_item_steps(items.list_items(self.folder)), [])

    def test_an_older_rumo_reads_an_item_step_as_plain_text(self):
        import re
        old = re.compile(r"^- \[( |x|X)\] (?:\[(agent|manual|waiting)\] )?(.*)$")
        m = old.match("- [ ] [item] Distribuição → 2026-11-05-distribuicao")
        assert m is not None
        self.assertEqual((m.group(2), m.group(3)), (None, "[item] Distribuição → 2026-11-05-distribuicao"))


if __name__ == "__main__":
    unittest.main()
