import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from rumo import config, explore, items

NOW = datetime(2026, 11, 5, 14, 10)

FAKE_CLAUDE = r'''
import json, os, sys, time
prompt = sys.stdin.read()
if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "prompt": prompt, "mds": os.environ.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS"),
                   "cwd": os.getcwd()}, f)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "ok":
    print("## Quem já faz\nHeym e Notion.\n\n<!-- rumo:next -->\n- Quanto cobram?\n- E no Brasil?\n- Vale seguir?\n- Uma a mais")
elif mode == "fail":
    print("not logged in", file=sys.stderr)
    sys.exit(3)
elif mode == "slow":
    time.sleep(5)
'''

FAKE_DIGGING = r'''
import os, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
question = open(args[args.index("-q") + 1], encoding="utf-8").read()
os.makedirs(os.path.join(out, "synthesis"))
with open(os.path.join(out, "synthesis", "final_answer.md"), "w", encoding="utf-8") as f:
    f.write("## Resposta curta\nNão vale como produto isolado. Rodadas: " + args[args.index("-n") + 1] + "\nPergunta: " + question)
with open(os.path.join(out, "synthesis", "key_findings.md"), "w", encoding="utf-8") as f:
    f.write("- [HIGH] Já existe.")
'''

ITEM = """---
title: Controle de ponto pra obra
status: idea
updated: 2026-09-28 22:10
---

## Context
Mestres de obra anotam o ponto em papel.

## Notes
- 2026-11-01 Dois mestres mandaram foto da folha.

## Explorations
### 2026-09-29 10:00 Como validar?
Ligar para 15 empreiteiras.
"""


def command(script: Path) -> str:
    return f'"{sys.executable}" "{script}"'


class ExploreTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.folder = self.root / "items"
        self.folder.mkdir()
        self.path = self.folder / "2026-09-17-controle-de-ponto-pra-obra.md"
        self.path.write_text(ITEM, encoding="utf-8")
        self.fake = self.root / "fake_claude.py"
        self.fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.digging = self.root / "fake_digging.py"
        self.digging.write_text(FAKE_DIGGING, encoding="utf-8")
        self.config = self.root / "config.toml"
        self.config.write_text(
            f"folder = {config.toml_str(str(self.folder))}\nlanguage = \"pt-BR\"\n"
            f"explore_command = {config.toml_str(command(self.fake))}\n"
            f"keep_digging = {config.toml_str(command(self.digging))}\ndeep_iterations = 2\n"
            '[lenses]\nvalidar = "Como validar em uma semana?"\n', encoding="utf-8")
        self.cfg = config.load(self.config)
        self.log = self.root / "fake.json"
        os.environ["FAKE_LOG"] = str(self.log)
        os.environ["FAKE_MODE"] = "ok"

    def tearDown(self):
        for key in ("FAKE_LOG", "FAKE_MODE"):
            os.environ.pop(key, None)

    def entry(self) -> items.Exploration:
        return items.load(self.path).explorations[-1]

    def test_a_lens_question_is_answered_and_saved_with_three_next_questions(self):
        self.assertEqual(explore.explore(self.cfg, self.path, lens="validar", now=NOW), "done")
        entry = self.entry()
        self.assertEqual((entry.question, entry.state, entry.when), ("Como validar em uma semana?", "done", "2026-11-05 14:10"))
        self.assertEqual(entry.answer, "#### Quem já faz\nHeym e Notion.")
        self.assertEqual(entry.next_questions, ["Quanto cobram?", "E no Brasil?", "Vale seguir?"])
        ran = json.loads(self.log.read_text(encoding="utf-8"))
        for flag in ("-p", "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence",
                     "--allowedTools", "WebSearch,WebFetch"):
            self.assertIn(flag, ran["argv"])
        self.assertEqual(ran["mds"], "1")
        self.assertEqual(Path(ran["cwd"]).resolve(), self.folder.resolve())
        for part in ("Controle de ponto pra obra", "Mestres de obra", "Dois mestres mandaram foto", "Ligar para 15",
                     "Como validar em uma semana?", "pt-BR", "<!-- rumo:next -->"):
            self.assertIn(part, ran["prompt"])

    def test_an_own_question_is_asked_as_written(self):
        self.assertEqual(explore.explore(self.cfg, self.path, question="E se o comprador for a construtora?", now=NOW), "done")
        self.assertEqual(self.entry().question, "E se o comprador for a construtora?")

    def test_a_failure_is_saved_with_its_reason(self):
        os.environ["FAKE_MODE"] = "fail"
        result = explore.explore(self.cfg, self.path, lens="validar", now=NOW)
        self.assertTrue(result.startswith("failed: "), result)
        self.assertEqual(self.entry().state, "failed")
        self.assertIn("not logged in", self.entry().error)
        os.environ["FAKE_MODE"] = "empty"
        explore.explore(self.cfg, self.path, lens="validar", now=NOW)
        self.assertEqual((self.entry().state, self.entry().error), ("failed", "empty answer"))

    def test_a_missing_command_or_lens_is_said_plainly(self):
        self.cfg.explore_command = "rumo-no-such-claude"
        self.assertEqual(explore.explore(self.cfg, self.path, lens="validar", now=NOW), "failed: rumo-no-such-claude not found")
        self.assertFalse(explore.available(self.cfg.explore_command))
        self.assertTrue(explore.available(command(self.fake)))
        with self.assertRaises(explore.ExploreError):
            explore.explore(self.cfg, self.path, lens="nope", now=NOW)

    def test_a_slow_answer_times_out(self):
        os.environ["FAKE_MODE"] = "slow"
        with self.assertRaises(explore.ExploreError) as caught:
            explore.run_claude(command(self.fake), "x", self.folder, timeout=1)
        self.assertIn("no answer after", str(caught.exception))

    def test_refuses_a_second_question_while_one_runs(self):
        items.start_exploration(items.load(self.path), "Primeira", "r1", pid=os.getpid(), limit=600, now=datetime.now())
        with self.assertRaises(explore.ExploreError):
            explore.explore(self.cfg, self.path, lens="validar")
        stale = self.root / "stale.md"
        stale.write_text(ITEM, encoding="utf-8")
        items.start_exploration(items.load(stale), "Morta", "r2", pid=explore_dead_pid(), limit=600, now=datetime.now())
        self.assertEqual(explore.explore(self.cfg, stale, lens="validar"), "done")  # a dead run does not block

    def test_an_edit_made_while_it_runs_survives(self):
        def run(command: str, prompt: str, cwd: Path, timeout: int) -> str:
            items.add_step(items.load(self.path), "Ligar para o primeiro mestre", "manual")
            return "Resposta.\n<!-- rumo:next -->\n- A?\n"

        self.assertEqual(explore.explore(self.cfg, self.path, lens="validar", now=NOW, run=run), "done")
        item = items.load(self.path)
        self.assertEqual([s.text for s in item.steps], ["Ligar para o primeiro mestre"])
        self.assertEqual((item.explorations[-1].answer, item.explorations[-1].next_questions), ("Resposta.", ["A?"]))

    def test_the_deep_lens_runs_keep_digging_and_keeps_its_research(self):
        self.assertEqual(explore.explore(self.cfg, self.path, lens=explore.DEEP, question="Vale seguir?", now=NOW), "done")
        entry = self.entry()
        self.assertEqual(entry.question, "Vale seguir?")
        self.assertIn("#### Resposta curta", entry.answer)
        self.assertIn("Rodadas: 2", entry.answer)
        self.assertIn("[HIGH] Já existe.", entry.answer)
        [dig] = list((self.folder / "research" / self.path.stem).glob("dig-*"))
        self.assertIn(str(dig), entry.answer)
        self.cfg.keep_digging = ""
        self.assertEqual(explore.explore(self.cfg, self.path, lens=explore.DEEP, now=NOW), "failed: keep_digging is not set in the config")

    def test_parse_answer_demotes_headings_and_keeps_three_questions(self):
        answer, questions = explore.parse_answer("# A\n## B\n### C\ntexto\n\n<!-- rumo:next -->\n1. Um?\n- Dois?\n* Três?\n- Quatro?")
        self.assertEqual(answer, "#### A\n#### B\n#### C\ntexto")
        self.assertEqual(questions, ["Um?", "Dois?", "Três?"])
        self.assertEqual(explore.parse_answer("Só texto."), ("Só texto.", []))

    def test_research_files_join_the_prompt(self):
        research = self.folder / "research" / self.path.stem
        research.mkdir(parents=True)
        (research / "ideas-hub.md").write_text("## Veredito\nNão seguir.", encoding="utf-8")
        self.assertIn("Não seguir.", explore.research_text(self.folder, self.path.stem))
        explore.explore(self.cfg, self.path, lens="validar", now=NOW)
        self.assertIn("Não seguir.", json.loads(self.log.read_text(encoding="utf-8"))["prompt"])

    def test_a_running_pid_is_alive_and_a_finished_one_is_not(self):
        self.assertTrue(explore.pid_alive(os.getpid()))
        self.assertFalse(explore.pid_alive(explore_dead_pid()))

    def test_the_cli_answers_and_exits_with_the_result(self):
        env = {**os.environ, "RUMO_CONFIG": str(self.config)}
        env.pop("RUMO_DIR", None)

        def rumo(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-m", "rumo.cli", "explore", *args], capture_output=True, text=True,
                                  encoding="utf-8", env=env)

        done = rumo(self.path.stem, "validar")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "done"))
        asked = rumo(self.path.stem, "--ask", "Quem paga?")
        self.assertEqual(asked.returncode, 0)
        self.assertEqual(self.entry().question, "Quem paga?")
        env["FAKE_MODE"] = "fail"
        failed = rumo(self.path.stem, "validar")
        self.assertEqual(failed.returncode, 1)
        self.assertTrue(failed.stdout.startswith("failed: "))
        self.assertEqual(rumo("no-such-item", "validar").returncode, 2)
        self.assertEqual(rumo(self.path.stem).returncode, 2)

    def test_spawn_runs_the_cli_detached_and_logs_next_to_the_config(self):
        proc = explore.spawn(self.config, self.path.stem, lens="validar")
        self.assertEqual(proc.wait(30), 0)
        self.assertEqual(self.entry().state, "done")
        self.assertTrue((self.root / "explore.log").exists())


def explore_dead_pid() -> int:
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    return int(done.stdout)


if __name__ == "__main__":
    unittest.main()
