import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rumo import config

NOW = datetime.now(timezone.utc)


def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


def local(at: datetime) -> str:
    return at.astimezone().strftime("%Y-%m-%d %H:%M")


def prompt(text: str, at: datetime, **extra) -> dict:
    return {"type": "user", "timestamp": at, "origin": {"kind": "human"}, "promptSource": "typed",
            "message": {"role": "user", "content": text}, **extra}


def answer(text: str, at: datetime) -> dict:
    return {"type": "assistant", "timestamp": at, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def ran(name: str, tool_input: dict, output: str, at: datetime, n: int = 0) -> list[dict]:
    """A tool call and its result."""
    call = {"type": "tool_use", "id": f"toolu_{name}_{n}", "name": name, "input": tool_input}
    result = {"type": "tool_result", "tool_use_id": call["id"], "content": output}
    return [{"type": "assistant", "timestamp": at, "message": {"role": "assistant", "content": [call]}},
            {"type": "user", "timestamp": at, "message": {"role": "user", "content": [result]}}]


def work(start: float, prompts: int = 3, cwd_prompt: str = "faz a parte") -> list[dict]:
    """A few prompts and answers, starting `start` minutes ago, one minute apart."""
    entries = []
    for n in range(prompts):
        entries += [prompt(f"{cwd_prompt} {n + 1}", ago(start - n)), answer(f"feito {n + 1}", ago(start - n))]
    return entries


class SessionsTest(unittest.TestCase):
    """`rumo sessions` against a fake Claude Code folder."""

    def setUp(self):
        # Not in the temporary directory: sessions there are left out on purpose
        self.root = Path(tempfile.mkdtemp(dir=Path.home(), prefix=".rumo-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.claude = self.root / "claude"
        (self.claude / "projects").mkdir(parents=True)
        self.folder = self.root / "items"
        self.folder.mkdir()
        self.config = self.root / "config.toml"
        self.config.write_text(config.default_text(self.folder, "pt-BR"), encoding="utf-8")
        self.repo = self.root / "web-app"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "src").mkdir()
        self.env = {**os.environ, "RUMO_DIR": str(self.folder), "RUMO_CONFIG": str(self.config),
                    "CLAUDE_CONFIG_DIR": str(self.claude)}

    def transcript(self, session: str, entries: list[dict], cwd: Path | str | None = None, branch: str = "main",
                   entrypoint: str = "cli", extra: tuple[str, ...] = ()) -> Path:
        cwd = str(cwd or self.repo)
        project = self.claude / "projects" / re.sub(r"[^\w]", "-", cwd)
        project.mkdir(parents=True, exist_ok=True)
        lines = []
        for e in entries:
            e = dict(e)
            e["timestamp"] = e["timestamp"].isoformat(timespec="milliseconds").replace("+00:00", "Z")
            lines.append(json.dumps({"sessionId": session, "cwd": cwd, "gitBranch": branch, "entrypoint": entrypoint, **e},
                                    ensure_ascii=False))
        lines += list(extra)
        path = project / f"{session}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        last = max(e["timestamp"] for e in entries)
        os.utime(path, (last.timestamp(), last.timestamp()))
        return path

    def rumo(self, *args: str) -> str:
        done = subprocess.run([sys.executable, "-m", "rumo.cli", "sessions", *args], capture_output=True, text=True,
                              encoding="utf-8", env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def block(self, out: str, session: str) -> str:
        m = re.search(rf"^## {session}\b.*?(?=^## |\Z)", out, re.M | re.S)
        self.assertIsNotNone(m, out)
        return m.group(0) if m else ""

    def item(self, slug: str, status: str = "doing", updated: str = "2026-10-01 10:00", sessions: str = "") -> Path:
        path = self.folder / f"{slug}.md"
        path.write_text(f"---\ntitle: {slug}\nstatus: {status}\nupdated: {updated}\n---\n\n## Sessions\n{sessions}",
                        encoding="utf-8")
        return path

    # ---------- what one session says ----------

    def test_a_session_says_where_it_ran_what_was_asked_answered_and_edited_and_how_to_link_it(self):
        entries = [
            prompt("arruma o logout", ago(60)),
            {"type": "ai-title", "aiTitle": "Logout que não redireciona", "timestamp": ago(60)},
            *ran("Edit", {"file_path": str(self.repo / "src" / "logout.ts")}, "ok", ago(59)),
            *ran("Write", {"file_path": "/elsewhere/notes.md"}, "ok", ago(59), 1),
            answer("Ajustei o handler.", ago(59)),
            prompt("agora os testes", ago(30)),
            answer("Testes passando.", ago(29)),
            prompt("abre a PR", ago(10)),
            *ran("Bash", {"command": "gh pr create --fill"}, "Creating pull request\n\nhttps://github.com/acme/web-app/pull/12\n", ago(9)),
            answer("PR aberta: https://github.com/acme/web-app/pull/12", ago(8)),
        ]
        self.transcript("s-logout", entries, cwd=self.repo / "src", branch="feature/logout")
        out = self.rumo()
        block = self.block(out, "s-logout")
        self.assertIn(f"- last: {local(ago(8))} · started {local(ago(60))}\n", block)
        self.assertIn(f"- where: web-app (feature/logout) · {self.repo / 'src'}\n", block)
        self.assertIn("- title: Logout que não redireciona\n", block)
        self.assertIn('- prompts (3): first "arruma o logout" · last: "agora os testes" / "abre a PR"\n', block)
        self.assertIn('- last answer: "PR aberta: https://github.com/acme/web-app/pull/12"\n', block)
        self.assertIn(f"- edited: /elsewhere/notes.md, {Path('src') / 'logout.ts'}\n", block)  # the latest first
        self.assertIn("- prs: https://github.com/acme/web-app/pull/12\n", block)
        self.assertRegex(block, rf"- line: - \d{{4}}-\d\d-\d\d \d\d:\d\d \| web-app \(feature/logout\) \| s-logout \| "
                         + re.escape(str(self.repo / "src")) + "\n")
        self.assertNotIn("linked:", block)

    def test_only_what_the_user_typed_counts_as_a_prompt(self):
        entries = [
            prompt("<command-message>rumo:rumo</command-message>\n<command-name>/rumo:rumo</command-name>\n"
                   "<command-args>new</command-args>", ago(50)),
            {**prompt("texto da skill", ago(50)), "isMeta": True},
            {"type": "user", "timestamp": ago(49), "origin": {"kind": "task-notification"},
             "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
            {"type": "user", "timestamp": ago(49), "isCompactSummary": True,
             "message": {"role": "user", "content": "This session is being continued..."}},
            {"type": "user", "timestamp": ago(48), "message": {"role": "user", "content": "<local-command-stdout>x</local-command-stdout>"}},
            {"type": "user", "timestamp": ago(48), "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
            prompt('olha isso <pasted_content id="ab12">linha 1\nlinha 2</pasted_content id="ab12"> e corrige', ago(47)),
            {"type": "user", "timestamp": ago(46), "origin": {"kind": "human"}, "promptSource": "typed",
             "message": {"role": "user", "content": [{"type": "image", "source": {}}, {"type": "text", "text": "[Image #1] e essa tela"}]}},
            prompt("x" * 300, ago(45)),
        ]
        self.transcript("s-prompts", entries)
        block = self.block(self.rumo(), "s-prompts")
        self.assertIn('- prompts (4): first "/rumo:rumo new" · last: "olha isso [pasted text] e corrige" / '
                      f'"[Image #1] e essa tela" / "{"x" * 199}…"\n', block)

    def test_the_last_answer_is_cut_and_kept_on_one_line(self):
        self.transcript("s-long", [*work(30), answer("começo\n\n" + "y" * 500, ago(20))])
        self.assertIn(f'- last answer: "começo {"y" * 392}…"\n', self.block(self.rumo(), "s-long"))

    def test_a_session_that_edited_many_files_lists_the_latest_eight(self):
        edits = [e for n in range(11) for e in ran("Edit", {"file_path": str(self.repo / f"f{n}.py")}, "ok", ago(20), n)]
        self.transcript("s-many", [*work(30), *edits])
        self.assertIn("- edited: f10.py, f9.py, f8.py, f7.py, f6.py, f5.py, f4.py, f3.py (+3 more)\n",
                      self.block(self.rumo(), "s-many"))

    def test_notes_kept_by_the_agent_are_not_work_on_the_project(self):
        memory = self.claude / "projects" / "x" / "memory" / "notes.md"
        scratch = Path(tempfile.gettempdir()) / "claude-1" / "scratchpad" / "page.html"
        edits = [*ran("Write", {"file_path": str(memory)}, "ok", ago(20)), *ran("Write", {"file_path": str(scratch)}, "ok", ago(20), 1),
                 *ran("Edit", {"file_path": str(self.repo / "app.py")}, "ok", ago(20), 2)]
        self.transcript("s-notes", [*work(30), *edits])
        self.assertIn("- edited: app.py\n", self.block(self.rumo(), "s-notes"))

    def test_items_written_by_rumo_are_not_work_on_the_project(self):
        item = self.item("logout")
        edits = [*ran("Edit", {"file_path": str(item)}, "ok", ago(20)), *ran("Edit", {"file_path": str(self.repo / "app.py")}, "ok", ago(20), 1)]
        self.transcript("s-item", [*work(30), *edits])
        self.assertIn("- edited: app.py\n", self.block(self.rumo(), "s-item"))

    def test_files_are_named_from_the_repository_the_session_ended_in(self):
        gone = self.root / "old-name"  # a folder renamed or deleted since the session started
        entries = [{**e, "cwd": str(gone)} for e in work(40)]
        entries += [{**e, "cwd": str(self.repo)} for e in ran("Edit", {"file_path": str(self.repo / "src" / "a.py")}, "ok", ago(20))]
        self.transcript("s-moved", entries, cwd=gone)
        self.assertIn(f"- edited: {Path('src') / 'a.py'}\n", self.block(self.rumo(), "s-moved"))

    def test_housekeeping_commands_are_not_prompts(self):
        self.transcript("s-compact", [*work(30), prompt("<command-name>/compact</command-name>\n<command-args></command-args>", ago(20)),
                                      prompt("<command-name>/rumo:rumo</command-name>\n<command-args>status</command-args>", ago(19))])
        self.assertIn('- prompts (4): first "faz a parte 1" · last: "faz a parte 2" / "faz a parte 3" / "/rumo:rumo status"\n',
                      self.block(self.rumo(), "s-compact"))

    def test_one_long_prompt_that_kept_the_agent_busy_is_work(self):
        looks = [e for n in range(10) for e in ran("Read", {"file_path": f"/x/{n}"}, "ok", ago(20), n)]
        self.transcript("s-explore", [prompt("explore como monetizar o app", ago(30)), *looks, answer("Três caminhos.", ago(19))])
        self.transcript("s-question", [prompt("o que é um rebase?", ago(30)), answer("É reescrever...", ago(29))])
        self.assertEqual(re.findall(r"^## (\S+)", self.rumo(), re.M), ["s-explore"])

    def test_a_folder_without_a_branch_says_so(self):
        self.transcript("s-nobranch", work(30), branch="HEAD")
        self.assertIn("- where: web-app (-) · ", self.block(self.rumo(), "s-nobranch"))

    # ---------- which sessions ----------

    def test_scripts_temporary_folders_short_chats_and_subagents_are_left_out(self):
        self.transcript("s-real", work(30))
        self.transcript("s-script", work(30), entrypoint="sdk-cli")
        self.transcript("s-tmp", work(30), cwd=Path(tempfile.gettempdir()) / "eval-work")
        self.transcript("s-short", work(30, prompts=2))
        self.transcript("s-short-edit", [*work(30, prompts=1), *ran("Edit", {"file_path": str(self.repo / "a.py")}, "ok", ago(29))])
        nested = self.claude / "projects" / "x" / "s-real" / "subagents"
        nested.mkdir(parents=True)
        (nested / "agent-1.jsonl").write_text((self.claude / "projects").joinpath(
            re.sub(r"[^\w]", "-", str(self.repo)), "s-real.jsonl").read_text(encoding="utf-8").replace("s-real", "agent-1"),
            encoding="utf-8")
        out = self.rumo()
        self.assertEqual(re.findall(r"^## (\S+)", out, re.M), ["s-real", "s-short-edit"])

    def test_the_newest_come_first_and_the_header_counts_them(self):
        self.transcript("s-old", work(120))
        self.transcript("s-new", work(10))
        out = self.rumo()
        self.assertEqual(re.findall(r"^## (\S+)", out, re.M), ["s-new", "s-old"])
        self.assertIn("# sessions: 2 (0 open)\n", out)

    def test_the_first_review_looks_three_days_back(self):
        self.transcript("s-two-days", work(2 * 24 * 60))
        self.transcript("s-four-days", work(4 * 24 * 60))
        out = self.rumo()
        self.assertEqual(re.findall(r"^## (\S+)", out, re.M), ["s-two-days"])
        self.assertRegex(out, r"^# since: \d{4}-\d\d-\d\d \d\d:\d\d \(3 days ago: the first review\)\n")

    def test_a_review_saves_where_the_next_one_starts(self):
        self.transcript("s-before", work(60))
        out = self.rumo()
        now = re.search(r"^# now: (.+)$", out, re.M)
        assert now is not None
        self.assertEqual(self.rumo("--reviewed", now[1]), "")
        self.assertEqual(config.load_state(self.config)["sessions_reviewed_at"], now[1])
        self.transcript("s-after", work(-1))  # active after the review
        out = self.rumo()
        self.assertEqual(re.findall(r"^## (\S+)", out, re.M), ["s-after"])
        self.assertIn(f"# since: {now[1]} (the last review)\n", out)

    def test_since_widens_the_review(self):
        config.update_state(self.config, sessions_reviewed_at=local(ago(5)))
        self.transcript("s-week", work(6 * 24 * 60))
        self.assertEqual(self.rumo().count("## "), 0)
        out = self.rumo("--since", "7d")
        self.assertEqual(re.findall(r"^## (\S+)", out, re.M), ["s-week"])
        self.assertIn("(--since 7d)", out)

    def test_after_twenty_sessions_the_older_ones_come_without_prompts_answer_or_edits(self):
        for n in range(22):
            self.transcript(f"s{n:02}", work(100 + n * 10))
        out = self.rumo()
        self.assertIn("# sessions: 22 (0 open); the 2 oldest without prompts, last answer and edits\n", out)
        self.assertIn("- prompts (3)", self.block(out, "s19"))
        self.assertNotIn("- prompts", self.block(out, "s20"))
        self.assertIn("- line: ", self.block(out, "s21"))

    def test_a_broken_line_is_skipped_and_an_unreadable_transcript_left_out(self):
        self.transcript("s-ok", work(30), extra=("{not json", ""))
        (self.claude / "projects" / "bad").mkdir()
        (self.claude / "projects" / "bad" / "s-bad.jsonl").write_bytes(b"\xff\xfe garbage")
        out = self.rumo()
        self.assertEqual(re.findall(r"^## (\S+)", out, re.M), ["s-ok"])

    def test_without_claude_code_it_says_so(self):
        self.env["CLAUDE_CONFIG_DIR"] = str(self.root / "nowhere")
        self.assertIn("No Claude Code sessions found", self.rumo())

    # ---------- open sessions ----------

    def registry(self, pid: int, session: str) -> None:
        (self.claude / "sessions").mkdir(exist_ok=True)
        (self.claude / "sessions" / f"{pid}.json").write_text(
            json.dumps({"pid": pid, "sessionId": session, "cwd": str(self.repo), "status": "idle"}), encoding="utf-8")

    def test_a_session_whose_process_is_alive_is_open(self):
        self.transcript("s-open", work(30))
        self.transcript("s-gone", work(40))
        self.registry(os.getpid(), "s-open")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.registry(dead.pid, "s-gone")
        (self.claude / "sessions" / "123.json").write_text("{broken", encoding="utf-8")
        out = self.rumo()
        self.assertRegex(out, r"(?m)^## s-open  open\n")
        self.assertRegex(out, r"(?m)^## s-gone\n")
        self.assertIn("# sessions: 2 (1 open)\n", out)

    # ---------- items ----------

    def test_a_linked_session_names_its_item_and_whether_it_is_up_to_date(self):
        self.transcript("s-linked", work(30))
        self.transcript("s-fresh", work(30))
        self.item("logout", sessions=f"- 2026-10-01 10:00 | web-app (main) | s-linked | {self.repo}\n")
        self.item("cache", updated=local(ago(1)), sessions=f"- 2026-10-01 10:00 | web-app (main) | s-fresh | {self.repo}\n")
        out = self.rumo()
        self.assertIn("- linked: logout\n", self.block(out, "s-linked"))
        self.assertIn("- linked: cache (up to date)\n", self.block(out, "s-fresh"))

    def test_an_item_the_session_closed_is_named_apart(self):
        self.transcript("s-parked", work(30))
        self.item("logout", status="paused", sessions=f"- 2026-10-01 10:00 | web-app (main) | s-parked | {self.repo} | closed\n")
        block = self.block(self.rumo(), "s-parked")
        self.assertIn("- closed: logout\n", block)
        self.assertNotIn("- linked:", block)

    def test_items_that_worked_on_the_same_branch_are_named(self):
        self.transcript("s-pix", work(30), cwd=self.repo / "src", branch="feature/pix")
        self.transcript("s-main", work(30), branch="main")
        self.item("pix", status="paused", sessions=f"- 2026-10-01 10:00 | web-app (feature/pix) | other | {self.repo}\n")
        self.item("pix-done", status="done", sessions=f"- 2026-10-01 10:00 | web-app (feature/pix) | other2 | {self.repo}\n")
        self.item("on-main", sessions=f"- 2026-10-01 10:00 | web-app (main) | other3 | {self.repo}\n")
        noted = self.item("noted")
        config.update_state(self.config, pr_branches={str(noted): [[str(self.repo), "feature/pix"]]})
        out = self.rumo()
        self.assertIn("- same branch: noted, pix (feature/pix)\n", self.block(out, "s-pix"))
        self.assertNotIn("same branch", self.block(out, "s-main"))


if __name__ == "__main__":
    unittest.main()
