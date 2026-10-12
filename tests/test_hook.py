import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ITEM = """---
title: Migração do logout
status: waiting
---

## Steps
- [x] [agent] Ajustar o handler
- [ ] [manual] Pedir review da PR
- [ ] [agent] Teste e2e do logout
- [ ] [agent] Remover o cookie antigo
- [ ] [agent] Atualizar a doc

## Sessions
- 2026-10-11 10:08 | web-app (main) | open-session | /Users/x/repo/web-app
- 2026-10-11 12:00 | web-app (main) | parked-session | /Users/x/repo/web-app | closed
"""


class HookTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "2026-10-11-migração.md").write_text(ITEM, encoding="utf-8")
        self.env = {**os.environ, "RUMO_DIR": str(self.folder), "RUMO_CONFIG": str(self.folder / "none.toml")}

    def run_hook(self, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "rumo.cli", "hook"], input=stdin, capture_output=True, text=True,
            encoding="utf-8", env=self.env,
        )

    def test_reminds_a_linked_session_to_update_its_item(self):
        result = self.run_hook(json.dumps({"session_id": "open-session"}))
        self.assertEqual(result.returncode, 0)
        self.assertIn("2026-10-11-migração.md (status: waiting)", result.stdout)
        for rule in ("Left off", "Steps", "[manual]", "| closed", "language"):
            self.assertIn(rule, result.stdout)

    def test_shows_the_next_open_steps_so_the_agent_can_keep_them_current(self):
        result = self.run_hook(json.dumps({"session_id": "open-session"}))
        self.assertIn("Open steps: [manual] Pedir review da PR; [agent] Teste e2e do logout; "
                      "[agent] Remover o cookie antigo; (+1 more)", result.stdout)
        self.assertNotIn("Ajustar o handler", result.stdout)

    def test_speaks_utf8_even_on_a_non_utf8_console(self):
        self.env["PYTHONIOENCODING"] = "cp1252"
        self.env["PYTHONUTF8"] = "0"
        result = subprocess.run(
            [sys.executable, "-m", "rumo.cli", "hook"], input=json.dumps({"session_id": "open-session"}).encode(),
            capture_output=True, env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2026-10-11-migração.md", result.stdout.decode("utf-8"))

    def test_stays_silent_for_unknown_or_closed_sessions_and_bad_input(self):
        for stdin in (json.dumps({"session_id": "other"}), json.dumps({"session_id": "parked-session"}), "not json", "{}"):
            result = self.run_hook(stdin)
            self.assertEqual((result.returncode, result.stdout), (0, ""), stdin)

    def test_tells_the_agent_never_to_drop_open_steps_and_to_ask_about_new_topics(self):
        result = self.run_hook(json.dumps({"session_id": "open-session"}))
        self.assertIn("Never delete an open step", result.stdout)
        self.assertIn("belongs to none of", result.stdout)


PROMPT_TIME = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


def entry(kind: str, content, at: datetime) -> str:
    return json.dumps({"type": kind, "timestamp": at.isoformat().replace("+00:00", "Z"),
                       "message": {"role": kind, "content": content}})


def tool(name: str, **tool_input) -> dict:
    return {"type": "tool_use", "id": "toolu_x", "name": name, "input": tool_input}


class StopHookTest(unittest.TestCase):
    """At the end of each turn: work must reach the linked items, and untracked work may be offered to rumo."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.folder = self.root / "items"
        self.folder.mkdir()
        self.item = self.folder / "2026-10-11-migração.md"
        self.item.write_text(ITEM, encoding="utf-8")
        self.set_item_changed_at(PROMPT_TIME - timedelta(hours=1))
        self.config = self.root / "config.toml"
        self.env = {**os.environ, "RUMO_DIR": str(self.folder), "RUMO_CONFIG": str(self.config)}
        self.env.pop("CLAUDE_PLUGIN_ROOT", None)

    def set_item_changed_at(self, at: datetime) -> None:
        os.utime(self.item, (at.timestamp(), at.timestamp()))

    def transcript(self, *tools: dict) -> Path:
        """An earlier turn that edited a file, then this turn's prompt and the tools it used."""
        earlier = PROMPT_TIME - timedelta(hours=2)
        lines = [
            entry("user", "turno anterior", earlier),
            entry("assistant", [tool("Edit", file_path="/repo/antigo.py")], earlier),
            entry("user", [{"type": "tool_result", "tool_use_id": "toolu_x", "content": "ok"}], earlier),
            entry("user", "faz a próxima parte", PROMPT_TIME),
            entry("user", [{"type": "text", "text": "skill text"}], PROMPT_TIME) [:-1] + ', "isMeta": true}',
        ]
        for i, t in enumerate(tools, 1):
            at = PROMPT_TIME + timedelta(seconds=i)
            lines.append(entry("assistant", [{"type": "text", "text": "ok"}, t], at))
            lines.append(entry("user", [{"type": "tool_result", "tool_use_id": "toolu_x", "content": "ok"}], at))
        path = self.root / f"transcript-{time.monotonic_ns()}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def stop(self, session: str, *tools: dict, active: bool = False, said: str = "Pronto.") -> str:
        payload = {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": active,
                   "transcript_path": str(self.transcript(*tools)), "last_assistant_message": said}
        result = subprocess.run([sys.executable, "-m", "rumo.cli", "hook"], input=json.dumps(payload),
                                capture_output=True, text=True, encoding="utf-8", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout:
            return ""
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "Stop")
        return output["additionalContext"]

    def state(self) -> dict:
        return json.loads((self.root / "state.json").read_text(encoding="utf-8"))

    def write_state(self, **data) -> None:
        (self.root / "state.json").write_text(json.dumps({"view": "repos", **data}), encoding="utf-8")

    # Linked sessions

    def test_reminds_a_linked_session_when_work_did_not_reach_its_items(self):
        message = self.stop("open-session", tool("Bash", command="npm test"))
        self.assertIn(str(self.item), message)
        self.assertIn("Steps", message)
        self.assertIn("never delete open steps", message)
        self.assertIn("belongs to none of", message)

    def test_is_satisfied_once_any_linked_item_changed_in_this_turn(self):
        self.set_item_changed_at(PROMPT_TIME + timedelta(minutes=1))
        self.assertEqual(self.stop("open-session", tool("Edit", file_path="/repo/app.py")), "")

    def test_reading_or_talking_is_not_work_to_record(self):
        self.assertEqual(self.stop("open-session", tool("Read", file_path="/repo/app.py"), tool("Grep", pattern="x")), "")
        self.assertEqual(self.stop("open-session"), "")

    def test_waits_for_the_user_when_the_answer_asks_them_something(self):
        # e.g. "track it as a new item, a step here, or not at all?": the next prompt's reminder follows up.
        said = "Criei o hello.py. Quer acompanhar isso como item novo, como passo deste item ou não acompanhar?"
        self.assertEqual(self.stop("open-session", tool("Write", file_path="/repo/hello.py"), said=said), "")
        self.assertEqual(self.stop("open-session", tool("Write", file_path="/repo/hello.py"), said="どうしますか？"), "")

    def test_the_nudge_never_makes_the_agent_repeat_its_answer(self):
        self.assertIn("never repeat", self.stop("open-session", tool("Bash", command="make")))

    def test_commands_that_only_look_around_are_not_work(self):
        for command in ("ls -la work", "git status && git diff --stat", "cat a.txt | grep oi", "rumo index",
                        "find . -name '*.py' 2>/dev/null", "FOO=1 head -5 notas.txt"):
            self.assertEqual(self.stop("open-session", tool("Bash", command=command)), "", command)
        for command in ("npm test", "echo oi > b.txt", "git commit -m x", "ls && touch b.txt", "find . -delete"):
            self.assertNotEqual(self.stop("open-session", tool("Bash", command=command)), "", command)

    def test_never_insists_twice_in_a_row(self):
        self.assertEqual(self.stop("open-session", tool("Bash", command="make"), active=True), "")

    # Sessions without an item

    def test_first_time_asks_whether_to_track_and_whether_to_keep_asking(self):
        message = self.stop("new-session", tool("Write", file_path="/repo/novo.py"))
        self.assertIn("/rumo new", message)
        self.assertIn("future sessions", message)
        self.assertIn("rumo ask-to-track true", message)
        self.assertIn("rumo ask-to-track false", message)
        self.assertEqual(self.state()["ask_to_track"]["last_asked"], date.today().isoformat())

    def test_asks_at_most_once_per_session(self):
        self.assertNotEqual(self.stop("new-session", tool("Edit", file_path="/repo/a.py")), "")
        self.assertEqual(self.stop("new-session", tool("Edit", file_path="/repo/b.py")), "")

    def test_only_file_edits_outside_the_items_folder_count(self):
        self.assertEqual(self.stop("s1", tool("Bash", command="git status")), "")
        self.assertEqual(self.stop("s2", tool("Write", file_path=str(self.folder / "2026-10-12-ideia.md"))), "")
        self.assertNotEqual(self.stop("s3", tool("NotebookEdit", notebook_path="/repo/n.ipynb")), "")

    def test_keeps_the_view_the_timeline_remembered(self):
        self.write_state()
        self.stop("new-session", tool("Edit", file_path="/repo/a.py"))
        self.assertEqual(self.state()["view"], "repos")

    def test_asks_every_session_when_the_user_wants_it(self):
        self.config.write_text("ask_to_track = true\n", encoding="utf-8")
        for session in ("s1", "s2"):
            message = self.stop(session, tool("Edit", file_path="/repo/a.py"))
            self.assertIn("/rumo new", message)
            self.assertIn("rumo ask-to-track false", message)
            self.assertNotIn("future sessions", message)

    def test_after_a_no_stays_quiet_until_15_sessions_then_offers_again(self):
        self.config.write_text("ask_to_track = false\n", encoding="utf-8")
        self.write_state(ask_to_track={"last_asked": date.today().isoformat(), "sessions": 0})
        for n in range(1, 15):
            self.assertEqual(self.stop(f"s{n}", tool("Edit", file_path="/repo/a.py")), "", n)
        message = self.stop("s15", tool("Edit", file_path="/repo/a.py"))
        self.assertIn("rumo ask-to-track true", message)
        self.assertEqual(self.state()["ask_to_track"]["sessions"], 0)
        self.assertEqual(self.stop("s16", tool("Edit", file_path="/repo/a.py")), "")

    def test_after_a_no_offers_again_after_15_days(self):
        self.config.write_text("ask_to_track = false\n", encoding="utf-8")
        self.write_state(ask_to_track={"last_asked": (date.today() - timedelta(days=15)).isoformat(), "sessions": 2})
        self.assertIn("rumo ask-to-track true", self.stop("s1", tool("Edit", file_path="/repo/a.py")))

    def test_the_agent_records_the_answer_with_a_command(self):
        for value in ("false", "true"):
            result = subprocess.run([sys.executable, "-m", "rumo.cli", "ask-to-track", value],
                                    capture_output=True, text=True, encoding="utf-8", env=self.env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"ask_to_track = {value}", self.config.read_text(encoding="utf-8"))
        result = subprocess.run([sys.executable, "-m", "rumo.cli", "ask-to-track", "maybe"],
                                capture_output=True, text=True, encoding="utf-8", env=self.env)
        self.assertEqual(result.returncode, 2)

    def test_stays_silent_on_a_missing_or_broken_transcript(self):
        for transcript in ("/nowhere.jsonl", str(self.root)):
            payload = {"session_id": "open-session", "hook_event_name": "Stop", "transcript_path": transcript}
            result = subprocess.run([sys.executable, "-m", "rumo.cli", "hook"], input=json.dumps(payload),
                                    capture_output=True, text=True, encoding="utf-8", env=self.env)
            self.assertEqual((result.returncode, result.stdout), (0, ""), transcript)


if __name__ == "__main__":
    unittest.main()
