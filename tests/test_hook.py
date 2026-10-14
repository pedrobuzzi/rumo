import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from rumo import items

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


def ran(call: dict, output: str) -> dict:
    """A tool call and what it returned."""
    return {**call, "_output": output}


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
            call = {k: v for k, v in t.items() if k != "_output"} | {"id": f"toolu_{i}"}
            output = [{"type": "text", "text": t.get("_output", "ok")}]
            lines.append(entry("assistant", [{"type": "text", "text": "ok"}, call], at))
            lines.append(entry("user", [{"type": "tool_result", "tool_use_id": f"toolu_{i}", "content": output}], at))
        path = self.root / f"transcript-{time.monotonic_ns()}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def stop(self, session: str, *tools: dict, active: bool = False, said: str = "Pronto.", cwd: Path | None = None) -> str:
        payload = {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": active,
                   "transcript_path": str(self.transcript(*tools)), "last_assistant_message": said,
                   "cwd": str(cwd or self.root)}
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

    # Steps changed outside the session (the rumo TUI, an editor, another session)

    def prompt(self, session: str) -> str:
        result = subprocess.run([sys.executable, "-m", "rumo.cli", "hook"], input=json.dumps({"session_id": session}),
                                capture_output=True, text=True, encoding="utf-8", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def edit_item(self, old: str, new: str) -> None:
        self.item.write_text(self.item.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def test_the_next_prompt_says_which_steps_changed_since_the_last_answer(self):
        self.stop("open-session")
        self.edit_item("- [ ] [agent] Atualizar a doc\n", "- [ ] [manual] Avisar o suporte\n")
        self.edit_item("- [ ] [manual] Pedir review da PR", "- [x] [manual] Pedir review da PR")
        reminder = self.prompt("open-session")
        self.assertIn("changed outside this session", reminder)
        self.assertIn("Added: [manual] Avisar o suporte", reminder)
        self.assertIn("Removed (do not add them back): [agent] Atualizar a doc", reminder)
        self.assertIn("Checked: [manual] Pedir review da PR", reminder)
        self.assertIn("do not start", reminder)
        self.assertNotIn("changed outside", self.prompt("open-session"))  # said once

    def test_nothing_to_say_before_the_first_answer_or_when_nothing_changed(self):
        self.assertNotIn("changed outside", self.prompt("open-session"))
        self.stop("open-session", tool("Bash", command="make"), active=True)  # a stop after a nudge counts too
        self.assertNotIn("changed outside", self.prompt("open-session"))

    def test_a_step_added_while_the_agent_worked_on_something_else_is_still_pointed_out(self):
        self.stop("open-session")
        self.edit_item("- [ ] [agent] Atualizar a doc\n", "- [ ] [agent] Atualizar a doc\n- [ ] [agent] Medir a home\n")
        self.stop("open-session", tool("Edit", file_path="/repo/app.py"))
        self.assertIn("Added: [agent] Medir a home", self.prompt("open-session"))

    def test_the_agents_own_step_edits_are_not_pointed_out(self):
        self.stop("open-session")
        self.edit_item("- [ ] [manual] Pedir review da PR", "- [x] [manual] Pedir review da PR")
        self.stop("open-session", tool("Edit", file_path=str(self.item)))
        self.assertNotIn("changed outside", self.prompt("open-session"))

    def test_unchecking_a_step_is_reported_too(self):
        self.stop("open-session")
        self.edit_item("- [x] [agent] Ajustar o handler", "- [ ] [agent] Ajustar o handler")
        self.assertIn("Unchecked: [agent] Ajustar o handler", self.prompt("open-session"))

    def test_the_rules_ask_to_name_the_next_step_after_finishing_one(self):
        reminder = self.prompt("open-session")
        self.assertIn("Next in rumo", reminder)
        self.assertIn("(yours)", reminder)

    # Session start: what is in progress, so the agent can answer "what is left?"

    def repo(self, name: str = "web-app") -> Path:
        repo = self.root / name
        (repo / ".git").mkdir(parents=True)
        (repo / "src").mkdir()
        return repo

    def add_item(self, slug: str, status: str, folder: Path | str, steps: str = "", session: str = "old") -> None:
        (self.folder / f"{slug}.md").write_text(
            f"---\ntitle: {slug.title()}\nstatus: {status}\nupdated: 2026-10-12 {len(slug):02d}:00\n---\n\n"
            f"## Steps\n{steps}\n## Sessions\n- 2026-10-11 10:08 | r (main) | {session} | {folder}\n",
            encoding="utf-8",
        )

    def session_start(self, session: str, cwd: Path) -> str:
        payload = {"session_id": session, "hook_event_name": "SessionStart", "source": "startup", "cwd": str(cwd)}
        result = subprocess.run([sys.executable, "-m", "rumo.cli", "hook"], input=json.dumps(payload),  # run where the session is
                                capture_output=True, text=True, encoding="utf-8", env=self.env, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout:
            return ""
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        return output["additionalContext"]

    def test_a_new_session_learns_what_is_in_progress_in_its_repository(self):
        repo = self.repo()
        self.add_item("checkout", "doing", repo / "src", "- [x] [agent] Feito\n- [ ] [manual] Testar no celular\n")
        self.add_item("billing", "waiting", repo, "- [ ] [agent] Trocar a chave\n")
        self.add_item("cache", "idea", repo)
        self.add_item("other-repo", "doing", self.root / "elsewhere")
        message = self.session_start("new-session", repo / "src")
        self.assertIn("Checkout (doing; next: [manual] Testar no celular)", message)
        self.assertIn("Billing (waiting; next: [agent] Trocar a chave)", message)
        self.assertNotIn("Cache", message)
        self.assertNotIn("Other-Repo", message)
        self.assertIn("rumo now", message)
        self.assertIn("/rumo link", message)

    def test_shows_at_most_three_items(self):
        repo = self.repo()
        for slug in ("a1", "a22", "a333", "a4444", "a55555"):
            self.add_item(slug, "doing", repo)
        message = self.session_start("new-session", repo)
        self.assertIn("(+2 more)", message)

    def test_elsewhere_it_only_says_how_many_are_in_progress(self):
        self.add_item("checkout", "doing", self.repo())
        message = self.session_start("new-session", self.repo("api"))
        self.assertIn("items in progress, none from this repository", message)
        self.assertIn("rumo now", message)
        self.assertNotIn("Checkout", message)

    def test_outside_a_repository_only_sessions_started_in_that_folder_count(self):
        home = self.root / "home"
        (home / "notes").mkdir(parents=True)
        self.add_item("notes", "doing", home / "notes")
        self.add_item("home", "doing", home)
        message = self.session_start("new-session", home)
        self.assertIn("Home (doing", message)
        self.assertNotIn("Notes (doing", message)

    def test_a_session_line_without_a_folder_is_from_no_repository(self):
        repo = self.repo()
        self.add_item("blank", "doing", "")
        self.add_item("relative", "doing", "web-app")
        message = self.session_start("new-session", repo)
        self.assertNotIn("Blank", message)
        self.assertNotIn("Relative", message)

    def test_session_start_is_silent_when_linked_or_with_nothing_in_progress(self):
        self.assertEqual(self.session_start("open-session", self.root), "")  # the prompt reminder covers it
        self.item.unlink()
        self.assertEqual(self.session_start("new-session", self.root), "")

    def test_rumo_now_lists_what_is_in_progress_with_the_next_step(self):
        self.add_item("checkout", "doing", self.root, "- [ ] [manual] Testar no celular\n")
        result = subprocess.run([sys.executable, "-m", "rumo.cli", "now"],
                                capture_output=True, text=True, encoding="utf-8", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"Checkout | doing | next: [manual] Testar no celular | {self.folder / 'checkout.md'}")
        self.assertTrue(lines[1].startswith("Migração do logout | waiting | next: [manual] Pedir review da PR | "))
        self.item.unlink()
        (self.folder / "checkout.md").unlink()
        result = subprocess.run([sys.executable, "-m", "rumo.cli", "now"],
                                capture_output=True, text=True, encoding="utf-8", env=self.env)
        self.assertEqual(result.stdout, "Nothing in progress.\n")

    # Pull requests

    def prs(self, path: Path | None = None) -> list[str]:
        return [f"{p.state} {p.name}".strip() for p in items.load(path or self.item).prs]

    def test_a_pr_opened_in_the_turn_is_added_to_the_linked_item(self):
        self.stop("open-session", ran(tool("Bash", command="git push -u origin feat && gh pr create --fill"),
                                      "Creating pull request for feat into main\n\nhttps://github.com/acme/web-app/pull/12\n"))
        self.stop("open-session", ran(tool("Bash", command="gh pr create -d -t x"), "https://github.com/acme/web-app/pull/13"))
        self.stop("open-session", ran(tool("Bash", command="gh pr create --fill"), "https://github.com/acme/web-app/pull/12"))
        self.assertEqual(self.prs(), ["open web-app#12", "draft web-app#13"])

    def test_a_merge_request_gitlab_names_after_a_push_is_added_without_a_state(self):
        self.stop("open-session", ran(tool("Bash", command="git push"),
                                      "remote: View merge request for feat:\nremote:   https://gitlab.com/acme/api/-/merge_requests/34\n"))
        self.assertEqual(self.prs(), ["api!34"])

    def test_a_tool_that_creates_a_pr_counts_too(self):
        output = '{"url": "https://api.github.com/repos/acme/web-app/pulls/9", "html_url": "https://github.com/acme/web-app/pull/9"}'
        self.stop("open-session", ran(tool("mcp__github__create_pull_request", title="x", draft=True), output))
        self.assertEqual(self.prs(), ["draft web-app#9"])

    def test_a_pr_only_looked_at_is_not_added(self):
        self.stop("open-session", ran(tool("Bash", command="gh pr view 3"), "https://github.com/acme/web-app/pull/3"),
                  ran(tool("WebFetch", url="https://github.com/acme/web-app/pull/4"), "https://github.com/acme/web-app/pull/4"))
        self.assertEqual(self.prs(), [])

    def test_with_several_linked_items_the_pr_goes_to_the_one_edited_in_the_turn(self):
        other = self.folder / "outro.md"
        other.write_text("---\ntitle: Outro\nstatus: doing\n---\n\n## Sessions\n- 2026-10-11 10:08 | w (main) | open-session | /w\n", encoding="utf-8")
        create = ran(tool("Bash", command="gh pr create --fill"), "https://github.com/acme/web-app/pull/12")
        self.stop("open-session", tool("Edit", file_path=str(other)), create)
        self.assertEqual((self.prs(), self.prs(other)), ([], ["open web-app#12"]))
        message = self.stop("open-session", ran(tool("Bash", command="gh pr create"), "https://github.com/acme/web-app/pull/14"))
        self.assertIn("https://github.com/acme/web-app/pull/14", message)
        self.assertIn("## PRs", message)
        self.assertEqual(self.prs(), [])
        self.assertEqual(self.stop("open-session", ran(tool("Bash", command="gh pr create"), "https://github.com/acme/web-app/pull/15"),
                                   active=True), "")  # never insist twice in a row

    def test_adding_a_pr_still_asks_for_the_steps_when_the_agent_left_them(self):
        message = self.stop("open-session", ran(tool("Bash", command="gh pr create"), "https://github.com/acme/web-app/pull/12"))
        self.assertIn("no linked item changed", message)
        self.assertIn("rumo already added https://github.com/acme/web-app/pull/12", message)  # so it does not say it is missing

    def test_the_branch_of_each_turn_is_noted_for_the_linked_items(self):
        repo = self.root / "web-app"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/feature/checkout\n", encoding="utf-8")
        self.stop("open-session", tool("Read", file_path="/repo/a.py"), cwd=repo)
        self.assertEqual(self.state()["pr_branches"], {str(self.item): [[str(repo), "feature/checkout"]]})

    def test_the_draft_flag_is_read_per_tool_and_never_inside_a_title(self):
        self.stop("open-session", ran(tool("Bash", command='glab mr create -d "Fixes login -d"'),
                                      "!34 Fixes login\n https://gitlab.com/acme/api/-/merge_requests/34\n"))
        self.stop("open-session", ran(tool("Bash", command='gh pr create --title "Add -d flag" --body "--draft later"'),
                                      "https://github.com/acme/web-app/pull/12"))
        self.stop("open-session", ran(tool("Bash", command="glab mr create --draft --fill"),
                                      "https://gitlab.com/acme/api/-/merge_requests/35"))
        self.assertEqual(self.prs(), ["open api!34", "open web-app#12", "draft api!35"])

    def test_links_printed_by_other_commands_of_the_same_call_are_not_taken(self):
        self.stop("open-session", ran(tool("Bash", command="git push && gh pr list"),
                                      "To github.com:acme/web-app.git\n#7  Old  https://github.com/acme/web-app/pull/7\n"))
        self.stop("open-session", ran(tool("Bash", command="git push && git log -3"),
                                      "commit abc\n\n    Follow-up of\n    https://github.com/acme/web-app/pull/8\n"))
        self.stop("open-session", ran(tool("Bash", command="gh pr view 5 --json url; gh pr create --fill"),
                                      '{"url":"https://github.com/acme/web-app/pull/5"}\nhttps://github.com/acme/web-app/pull/12\n'))
        self.assertEqual(self.prs(), ["open web-app#12"])

    def test_with_several_linked_items_only_the_ones_the_turn_edited_note_the_branch(self):
        repo = self.root / "web-app"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/feat\n", encoding="utf-8")
        other = self.folder / "outro.md"
        other.write_text("---\ntitle: Outro\nstatus: doing\n---\n\n## Sessions\n- 2026-10-11 10:08 | w (main) | open-session | /w\n", encoding="utf-8")
        self.stop("open-session", tool("Read", file_path="/repo/a.py"), cwd=repo)
        self.assertNotIn("pr_branches", self.state())  # which item? none was edited: no guess
        self.stop("open-session", tool("Edit", file_path=str(other)), cwd=repo)
        self.assertEqual(self.state()["pr_branches"], {str(other): [[str(repo), "feat"]]})

    def test_the_agents_own_step_edits_through_the_shell_are_not_pointed_out(self):
        self.stop("open-session")
        self.edit_item("- [ ] [manual] Pedir review da PR", "- [x] [manual] Pedir review da PR")
        self.stop("open-session", tool("Bash", command=f"sed -i '' 's/- \\[ \\] \\[manual\\] Pedir/- [x] [manual] Pedir/' '{self.item}'"))
        self.assertNotIn("changed outside", self.prompt("open-session"))

    def test_the_prompt_names_the_prs_still_open(self):
        self.edit_item("\n## Sessions", "\n## PRs\n- [open] https://github.com/acme/web-app/pull/12 Logout\n"
                       "- [merged] https://github.com/acme/web-app/pull/3\n- https://gitlab.com/acme/api/-/merge_requests/34\n\n## Sessions")
        reminder = self.prompt("open-session")
        self.assertIn("Open PRs: web-app#12 (open); api!34 (state unknown).", reminder)
        self.assertNotIn("web-app#3", reminder)
        self.assertIn("## PRs", reminder)  # the rules say how to keep them

    def test_stays_silent_on_a_missing_or_broken_transcript(self):
        for transcript in ("/nowhere.jsonl", str(self.root)):
            payload = {"session_id": "open-session", "hook_event_name": "Stop", "transcript_path": transcript}
            result = subprocess.run([sys.executable, "-m", "rumo.cli", "hook"], input=json.dumps(payload),
                                    capture_output=True, text=True, encoding="utf-8", env=self.env)
            self.assertEqual((result.returncode, result.stdout), (0, ""), transcript)


if __name__ == "__main__":
    unittest.main()
