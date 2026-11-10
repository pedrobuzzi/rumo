import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from rumo import config, explore, items, spark

NOW = datetime(2026, 11, 9, 9, 30)

FAKE_CLAUDE = r'''
import json, os, sys, time
prompt = sys.stdin.read()
if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "w", encoding="utf-8") as f:
        json.dump({"prompt": prompt}, f)
mode = os.environ.get("FAKE_MODE", "ideas")
if mode == "ideas":
    print(json.dumps({"direction": "Ferramentas simples para quem trabalha em obra.", "ideas": [
        {"title": "Diário de obra por áudio", "summary": "Mestre grava o dia e o engenheiro recebe o resumo.",
         "from": ["2026-09-17-controle-de-ponto-pra-obra"]},
        {"title": "Medição com foto", "summary": "Empreiteiro fotografa o serviço e a medição sai pronta.",
         "from": ["no-such-item", "2026-10-02-medicao-de-servicos"]},
        {"title": "Ponto para diaristas", "summary": "Diarista marca o dia pelo WhatsApp e recebe no Pix.", "from": []},
        {"title": "Uma quarta", "summary": "Sobra.", "from": []}]}))
elif mode == "fenced":
    print('Aqui estão:\n```json\n{"direction": "Obra.", "ideas": [{"title": "A", "summary": "B {chave}", "from": []}]}\n```\nBoa sorte.')
elif mode == "questions":
    print(json.dumps({"direction": "Obra e RH.", "questions": ["Quer vender para empresas ou pessoas?", "Pode cobrar mensal?", "Uma terceira?"]}))
elif mode == "none":
    print(json.dumps({"direction": "Obra.", "ideas": [{"title": "", "summary": "sem título"}]}))
elif mode == "garbage":
    print("Não consegui pensar em nada.")
elif mode == "fail":
    print("not logged in", file=sys.stderr)
    sys.exit(3)
'''


def write(folder: Path, slug: str, front: str, body: str = "") -> Path:
    path = folder / f"{slug}.md"
    path.write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")
    return path


class SparkTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("RUMO_DIR", None)  # another test may leave it set
        self.root = Path(tempfile.mkdtemp())
        self.folder = self.root / "items"
        self.folder.mkdir()
        write(self.folder, "2026-10-02-medicao-de-servicos", "title: Medição de serviços\nstatus: idea\ngroup: produtos\n"
              "created: 2026-10-02 10:00\nupdated: 2026-10-02 10:00", "## Context\nMedir o que o empreiteiro fez.\n")
        write(self.folder, "2026-09-17-controle-de-ponto-pra-obra", "title: Controle de ponto pra obra\nstatus: idea\n"
              "group: produtos\ncreated: 2026-09-17 08:00\nupdated: 2026-09-28 22:10",
              "## Context\nMestres de obra anotam o ponto em papel.\n\n## Notes\n- 2026-11-01 Dois mestres mandaram foto.\n\n"
              "## Explorations\n### 2026-09-29 10:00 Como validar?\nLigar para 15 empreiteiras.\nDepois cobrar.\n")
        write(self.folder, "2026-08-01-app-de-receitas", "title: App de receitas\nstatus: dropped\ncreated: 2026-08-01 09:00\n"
              "updated: 2026-08-20 09:00\ndropped: 2026-08-20 09:00", "## Left off\nMercado lotado, ninguém paga.\n")
        write(self.folder, "hand-written", "title: Pausada à mão\nstatus: paused\nupdated: 2026-07-01 09:00")
        write(self.folder, "2026-10-20-phraseday", "title: PhraseDay\nstatus: doing\ngroup: apps\ncreated: 2026-10-20 09:00\n"
              "updated: 2026-11-08 09:00", "## Context\nSEGREDO do trabalho em andamento.\n")
        self.fake = self.root / "fake_claude.py"
        self.fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.config = self.root / "config.toml"
        self.config.write_text(
            f"folder = {config.toml_str(str(self.folder))}\nlanguage = \"pt-BR\"\n"
            f"explore_command = {config.toml_str(command(self.fake))}\n"
            '[groups.produtos]\nname = "Produtos"\ncolor = "green"\n', encoding="utf-8")
        self.cfg = config.load(self.config)
        self.log = self.root / "fake.json"
        os.environ["FAKE_LOG"] = str(self.log)
        os.environ["FAKE_MODE"] = "ideas"

    def tearDown(self):
        for key in ("FAKE_LOG", "FAKE_MODE"):
            os.environ.pop(key, None)

    def prompt(self) -> str:
        return json.loads(self.log.read_text(encoding="utf-8"))["prompt"]

    def state(self) -> tuple[str, str]:
        return spark.current(spark.load(self.folder), datetime.now(), explore.pid_alive)

    def test_three_ideas_come_back_with_the_direction(self):
        self.assertEqual(spark.spark(self.cfg, now=NOW), "ready")
        data = spark.load(self.folder)
        self.assertEqual(self.state(), ("ready", ""))
        self.assertEqual(data["direction"], "Ferramentas simples para quem trabalha em obra.")
        ideas = spark.ideas_of(data)
        self.assertEqual([i.title for i in ideas], ["Diário de obra por áudio", "Medição com foto", "Ponto para diaristas"])
        self.assertEqual(ideas[1].sources, ["2026-10-02-medicao-de-servicos"])  # unknown slugs dropped
        self.assertEqual(ideas[0].summary, "Mestre grava o dia e o engenheiro recebe o resumo.")
        self.assertEqual(data["started"], "2026-11-09 09:30:00")

    def test_the_prompt_reads_the_ideas_oldest_first_and_the_work_as_titles(self):
        spark.save(self.folder, {"discarded": [{"title": "Marketplace de pedreiros", "summary": "x", "date": "2026-11-01"}]})
        spark.spark(self.cfg, now=NOW)
        prompt = self.prompt()
        order = [prompt.index(t) for t in ("Pausada à mão", "App de receitas", "Controle de ponto pra obra", "Medição de serviços")]
        self.assertEqual(order, sorted(order))  # no `created:` falls back to `updated`
        for part in ("Mercado lotado, ninguém paga.", "Dois mestres mandaram foto.", "Como validar?", "Ligar para 15",
                     "slug: 2026-09-17-controle-de-ponto-pra-obra", "Produtos", "Marketplace de pedreiros", "pt-BR",
                     "PhraseDay"):
            self.assertIn(part, prompt)
        self.assertNotIn("SEGREDO", prompt)  # current work: titles only
        self.assertIn('"questions"', prompt)  # the first run may ask

    def test_a_reply_wrapped_in_prose_or_a_code_fence_is_read(self):
        os.environ["FAKE_MODE"] = "fenced"
        self.assertEqual(spark.spark(self.cfg, now=NOW), "ready")
        [idea] = spark.ideas_of(spark.load(self.folder))
        self.assertEqual((idea.title, idea.summary, idea.sources), ("A", "B {chave}", []))

    def test_questions_first_then_a_second_run_with_the_answers(self):
        os.environ["FAKE_MODE"] = "questions"
        self.assertEqual(spark.spark(self.cfg, now=NOW), "asking")
        data = spark.load(self.folder)
        self.assertEqual(spark.questions_of(data), ["Quer vender para empresas ou pessoas?", "Pode cobrar mensal?"])
        self.assertEqual(self.state(), ("asking", ""))
        spark.answer(self.folder, ["Empresas pequenas", ""])
        self.assertEqual(self.state(), ("asking", ""))
        os.environ["FAKE_MODE"] = "ideas"
        self.assertEqual(spark.spark(self.cfg, now=NOW + timedelta(minutes=1)), "ready")
        prompt = self.prompt()
        self.assertIn("Quer vender para empresas ou pessoas?", prompt)
        self.assertIn("Empresas pequenas", prompt)
        self.assertIn("(no answer)", prompt)
        self.assertNotIn('"questions"', prompt)  # no more asking
        data = spark.load(self.folder)
        self.assertNotIn("questions", data)
        self.assertNotIn("answers", data)

    def test_asking_again_on_the_second_run_fails(self):
        spark.save(self.folder, {"state": "asking", "started": "2026-11-09 09:00:00", "direction": "Obra.",
                                 "questions": ["Empresas ou pessoas?"]})
        spark.answer(self.folder, ["Empresas"])
        os.environ["FAKE_MODE"] = "questions"
        self.assertEqual(spark.spark(self.cfg, now=NOW), "failed: asked again instead of answering")
        self.assertEqual(self.state(), ("failed", "asked again instead of answering"))
        self.assertNotIn("answers", spark.load(self.folder))  # the next `*` starts over

    def test_each_failure_is_saved_with_its_reason(self):
        for mode, reason in (("none", "no ideas in the answer"), ("garbage", "the answer was not JSON"),
                             ("fail", "exited with 3: not logged in")):
            os.environ["FAKE_MODE"] = mode
            result = spark.spark(self.cfg, now=NOW)
            self.assertTrue(result.startswith("failed: ") and reason in result, (mode, result))
            state, error = self.state()
            self.assertEqual(state, "failed")
            self.assertIn(reason, error)
        self.cfg.explore_command = "rumo-no-such-claude"
        self.assertEqual(spark.spark(self.cfg, now=NOW), "failed: rumo-no-such-claude not found")

    def test_a_slow_answer_times_out(self):
        def slow(command: str, prompt: str, cwd: Path, timeout: int) -> str:
            raise explore.ExploreError("no answer after 10 minutes")

        self.assertEqual(spark.spark(self.cfg, now=NOW, run=slow), "failed: no answer after 10 minutes")

    def test_nothing_to_read_or_a_run_alive_refuses_and_writes_nothing(self):
        spark.save(self.folder, {"state": "running", "started": f"{datetime.now():{spark.STAMP}}", "pid": dead_pid(),
                                 "limit": 600})
        self.assertEqual(self.state(), ("failed", "stopped before it answered"))
        spark.save(self.folder, {"state": "running", "started": f"{datetime.now():{spark.STAMP}}",
                                 "pid": sleeper(self), "limit": 600})
        with self.assertRaises(spark.SparkError):
            spark.spark(self.cfg)
        self.assertEqual(self.state()[0], "running")
        spark.save(self.folder, {"state": "running", "started": "2026-11-01 09:00:00", "pid": os.getpid(), "limit": 600})
        self.assertEqual(self.state(), ("failed", "stopped before it answered"))  # its time ran out
        for path in self.folder.glob("*.md"):
            if items.load(path).status != "doing":
                path.unlink()
        with self.assertRaises(spark.SparkError):
            spark.spark(self.cfg)

    def test_many_ideas_stay_under_the_limit_and_keep_the_newest(self):
        for n in range(60):
            write(self.folder, f"2026-10-{n % 28 + 1:02d}-ideia-{n}", f"title: Ideia {n}\nstatus: idea\n"
                  f"created: 2026-10-{n % 28 + 1:02d} 10:{n:02d}\nupdated: 2026-10-30 10:00",
                  "## Context\n" + "Texto longo. " * 300 + "\n")
        write(self.folder, "2026-11-08-a-mais-nova", "title: A mais nova\nstatus: idea\ncreated: 2026-11-08 10:00\n"
              "updated: 2026-11-08 10:00")
        prompt = spark.build_prompt(self.cfg, items.list_items(self.folder), {})
        self.assertLess(len(prompt), spark.PROMPT_CHARS + 5000)
        self.assertIn("A mais nova", prompt)
        self.assertIn("Ideia 0", prompt)

    def test_an_unreadable_file_reads_as_idle_and_is_kept_aside(self):
        (self.folder / spark.STATE_FILE).write_text("{quebrado", encoding="utf-8")
        self.assertEqual(spark.load(self.folder), {})
        self.assertEqual(self.state(), ("idle", ""))
        spark.spark(self.cfg, now=NOW)
        self.assertEqual((self.folder / "spark.json.bad").read_text(encoding="utf-8"), "{quebrado")
        self.assertEqual(self.state()[0], "ready")

    def test_keeping_creates_the_ideas_and_remembers_the_ones_thrown_away(self):
        spark.spark(self.cfg, now=NOW)
        data = spark.load(self.folder)
        data["discarded"] = [{"title": f"Velha {n}", "summary": "", "date": "2026-10-01"} for n in range(100)]
        spark.save(self.folder, data)
        all_items = items.list_items(self.folder)
        created = spark.keep(self.folder, all_items, spark.ideas_of(data), {1}, now=NOW)
        self.assertEqual(len(created), 2)
        first, third = (items.load(p) for p in created)
        self.assertEqual((first.title, first.status, first.group, first.from_slug),
                         ("Diário de obra por áudio", "idea", "produtos", "2026-09-17-controle-de-ponto-pra-obra"))
        self.assertEqual(first.sections["context"], "Mestre grava o dia e o engenheiro recebe o resumo.")
        self.assertEqual((third.title, third.group, third.from_slug), ("Ponto para diaristas", "", ""))
        data = spark.load(self.folder)
        self.assertEqual(self.state(), ("idle", ""))
        self.assertEqual(len(data["discarded"]), spark.KEEP_DISCARDED)
        self.assertEqual(data["discarded"][0], {"title": "Medição com foto",
                                                "summary": "Empreiteiro fotografa o serviço e a medição sai pronta.",
                                                "date": "2026-11-09"})
        self.assertNotIn("ideas", data)

    def test_the_cli_runs_one_step_and_exits_with_the_result(self):
        env = {**os.environ, "RUMO_CONFIG": str(self.config)}
        env.pop("RUMO_DIR", None)

        def rumo(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-m", "rumo.cli", "spark", *args], capture_output=True, text=True,
                                  encoding="utf-8", env=env)

        done = rumo()
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ready"))
        env["FAKE_MODE"] = "fail"
        failed = rumo()
        self.assertEqual(failed.returncode, 1)
        self.assertTrue(failed.stdout.startswith("failed: "))
        self.assertEqual(rumo("extra").returncode, 2)

    def test_spawn_runs_the_cli_detached_and_logs_next_to_the_config(self):
        proc = spark.spawn(self.config)
        self.assertEqual(proc.wait(30), 0)
        self.assertEqual(self.state()[0], "ready")
        self.assertIn("spark", (self.root / "explore.log").read_text(encoding="utf-8"))


def command(script: Path) -> str:
    return f'"{sys.executable}" "{script}"'


def dead_pid() -> int:
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    return int(done.stdout)


def sleeper(test: unittest.TestCase) -> int:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    test.addCleanup(proc.wait)
    test.addCleanup(proc.kill)
    return proc.pid


if __name__ == "__main__":
    unittest.main()
