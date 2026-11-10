"""Exploring an item: a question about it, answered by Claude Code (or keep-digging) in the background.

`rumo explore` runs here: it writes a running entry under `## Explorations`, runs the engine, then
replaces the entry with the answer or the reason it failed. The Timeline starts it with `spawn`, detached,
so closing the Timeline does not stop it. Stdlib only, like `items`.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from rumo import items
from rumo.config import Config

LIMIT = 600  # seconds Claude Code gets to answer
DEEP_LIMIT = 5400  # keep-digging runs for a long time
DEEP = "deep"  # the lens that runs keep-digging
DEEP_QUESTION = "Is this worth doing, for whom, and what would make it fail?"
CLAUDE_FLAGS = [
    "-p", "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence",
    "--allowedTools", "WebSearch,WebFetch",
]
HISTORY_CHARS = 6000  # earlier explorations in the prompt
RESEARCH_CHARS = 4000  # start of the research files in the prompt
ATTEMPTS = 5  # writes retried when the user edits the item meanwhile

PROMPT = """You help someone think about an idea or a piece of work they keep in rumo, a notebook of Markdown files. Below is everything known about it.

{history}

The question to explore now:

> {question}

Answer the question directly, in Markdown, in at most about 350 words. Write in {language}. Search the web only when the question asks for real-world facts (prices, competitors, rules, market data), and link the source. Be concrete and honest: if the answer weakens the idea, say so. The notes are facts the owner checked personally; they outweigh any research.

Do not repeat what is above. Do not use `#` or `##` headings. Start straight with the content, with no opening line such as "Here is".

Always end with this marker on its own line, followed by exactly three short questions:

<!-- rumo:next -->
- <a question that digs into the most uncertain point of your answer>
- <a question that opens a different scenario>
- <a question that would help decide whether to go on>
"""

_HEADING_RE = re.compile(r"^#{1,3} ")
_QUESTION_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+(.*)$")

Runner = Callable[[str, str, Path, int], str]


class ExploreError(Exception):
    """Why an exploration could not start or did not answer, in a line the user can act on."""


def available(command: str) -> bool:
    """Whether the command's executable can be found."""
    try:
        return bool(_resolve(command))
    except ExploreError:
        return False


def explore(
    cfg: Config, path: Path, lens: str | None = None, question: str | None = None,
    now: datetime | None = None, run: Runner | None = None,
) -> str:
    """Ask about the item with a lens from the config (or `DEEP`) or the user's own question, and write the
    answer into it. Returns "done" or "failed: <reason>"; raises ExploreError, writing nothing, when the
    lens is unknown or the item already has an exploration running."""
    deep = lens == DEEP
    if lens and not deep:
        if lens not in cfg.lenses:
            raise ExploreError(f"unknown lens: {lens} (lenses: {', '.join([*cfg.lenses, DEEP])})")
        question = cfg.lenses[lens]
    question = " ".join((question or (DEEP_QUESTION if deep else "")).split())
    if not question:
        raise ExploreError("a lens or a question is needed")

    item = items.load(path)
    stale = []
    for e in item.explorations:
        if e.state == "running":
            if items.exploration_state(e, datetime.now(), pid_alive) == "running":
                raise ExploreError(f"already exploring: {e.question}")
            stale.append(e.id)
    taken = {e.id for e in item.explorations}
    id = secrets.token_hex(2)
    while id in taken:
        id = secrets.token_hex(2)
    for old in stale:  # a run that died without a word: say so in the file too
        _write(path, lambda it, old=old: items.fail_exploration(it, old, "stopped before it answered", now))
    _write(path, lambda it: items.start_exploration(it, question, id, os.getpid(), DEEP_LIMIT if deep else LIMIT, now))

    try:
        item = items.load(path)
        if deep:
            if not cfg.keep_digging:
                raise ExploreError("keep_digging is not set in the config")
            out = cfg.folder / "research" / item.slug / f"dig-{now or datetime.now():%Y%m%d-%H%M%S}"
            text = run_deep(cfg.keep_digging, _brief(item, question), out, cfg.deep_iterations, DEEP_LIMIT)
        else:
            prompt = build_prompt(item, question, cfg.language, research_text(cfg.folder, item.slug))
            text = (run or run_claude)(cfg.explore_command, prompt, cfg.folder, LIMIT)
        answer, next_questions = parse_answer(text)
        if not answer:
            raise ExploreError("empty answer")
    except ExploreError as e:
        _write(path, lambda it: items.fail_exploration(it, id, str(e), now))
        return f"failed: {e}"
    _write(path, lambda it: items.finish_exploration(it, id, answer, next_questions, now))
    return "done"


def _write(path: Path, edit: Callable[[items.Item], None]) -> None:
    """Re-read the item and apply `edit`, again when the user saved it in between."""
    for attempt in range(ATTEMPTS):
        try:
            edit(items.load(path))
            return
        except items.WriteConflict:
            if attempt == ATTEMPTS - 1:
                raise


def build_prompt(item: items.Item, question: str, language: str, research: str) -> str:
    return PROMPT.format(history=_history(item, research), question=question, language=language)


def _history(item: items.Item, research: str = "") -> str:
    """What is known about the item: title, Context, Left off, Notes, earlier answers (newest first) and research."""
    parts = [f"# {item.title}", f"Status: {item.status}"]
    for name, heading in (("context", "Context"), ("left off", "Left off"), ("notes", "Notes (checked by the owner)")):
        if text := item.sections.get(name, ""):
            parts.append(f"## {heading}\n{text}")
    earlier = [f"### {e.when} {e.question}\n{e.answer}" for e in reversed(item.explorations) if e.state == "done" and e.answer]
    if earlier:
        parts.append("## Earlier explorations (newest first)\n" + _cut("\n\n".join(earlier), HISTORY_CHARS))
    if research:
        parts.append(f"## Research (start of the files)\n{research}")
    return "\n\n".join(parts)


def _brief(item: items.Item, question: str) -> str:
    """The question file for keep-digging, which knows nothing about the item."""
    return f"{question}\n\nAbout:\n\n{_history(item)}\n"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n[…]"


def parse_answer(text: str) -> tuple[str, list[str]]:
    """Split the engine's output into the answer and at most three next questions. Headings `#` to `###`
    become `####`: in the item file they would end the entry or the section."""
    answer, _, rest = text.replace("\r\n", "\n").partition(items.NEXT_MARKER)
    lines = [("####" + line.lstrip("#")) if _HEADING_RE.match(line) else line for line in answer.splitlines()]
    questions = [m.group(1).strip() for line in rest.splitlines() if (m := _QUESTION_RE.match(line.strip()))]
    return "\n".join(lines).strip(), [q for q in questions if q][:3]


def run_claude(command: str, prompt: str, cwd: Path, timeout: int) -> str:
    """Claude Code with the prompt on stdin, kept away from the user's CLAUDE.md, plugins, hooks and sessions."""
    env = {**os.environ, "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
    return _run([*_resolve(command), *CLAUDE_FLAGS], prompt, cwd, env, timeout, _name(command))


def run_deep(bin: str, question: str, out_dir: Path, iterations: int, timeout: int) -> str:
    """keep-digging into `out_dir`; its synthesis and key findings, with where the rest is."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    question_file = out_dir.parent / f".question-{out_dir.name}.txt"
    question_file.write_text(question, encoding="utf-8")
    try:
        args = [*_resolve(bin), "-q", str(question_file), "-o", str(out_dir), "-n", str(iterations)]
        _run(args, "", out_dir.parent, dict(os.environ), timeout, "keep-digging")
    finally:
        question_file.unlink(missing_ok=True)
    try:
        answer = (out_dir / "synthesis" / "final_answer.md").read_text(encoding="utf-8").strip()
    except OSError:
        raise ExploreError(f"keep-digging wrote no synthesis/final_answer.md in {out_dir}") from None
    try:
        findings = (out_dir / "synthesis" / "key_findings.md").read_text(encoding="utf-8").strip()
    except OSError:
        findings = ""
    if findings:
        answer += f"\n\n#### Key findings\n\n{findings}"
    return f"{answer}\n\nFull research: `{out_dir}`"


def _run(args: list[str], stdin: str, cwd: Path, env: dict[str, str], timeout: int, name: str) -> str:
    """Run in a group of its own, so a timeout also stops what the command started."""
    windows = sys.platform == "win32"
    try:
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
            text=True, encoding="utf-8", errors="replace", start_new_session=not windows,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    except OSError as e:
        raise ExploreError(f"{name} could not start: {e.strerror or e}") from None
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        proc.communicate()
        raise ExploreError(f"no answer after {_duration(timeout)}") from None
    if proc.returncode != 0:
        tail = " ".join((err.strip() or out.strip()).splitlines()[-3:])
        raise ExploreError(f"{name} exited with {proc.returncode}: {tail[-150:] or 'no message'}")
    return out


def _kill(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def _duration(seconds: int) -> str:
    return f"{seconds // 60} minutes" if seconds >= 120 else f"{seconds} seconds"


def _split(command: str) -> list[str]:
    windows = sys.platform == "win32"
    try:
        parts = shlex.split(command, posix=not windows)
    except ValueError:
        raise ExploreError(f"cannot read the command: {command}") from None
    return [p.strip('"') for p in parts] if windows else parts


def _name(command: str) -> str:
    parts = _split(command)
    return parts[0] if parts else command


def _resolve(command: str) -> list[str]:
    """The command line with its executable found on PATH (where `claude.cmd` lives on Windows)."""
    parts = _split(command)
    if not parts:
        raise ExploreError("no command set")
    found = shutil.which(parts[0])
    if not found:
        raise ExploreError(f"{parts[0]} not found")
    return [found, *parts[1:]]


def research_text(folder: Path, slug: str, limit: int = RESEARCH_CHARS) -> str:
    """The start of the files in `<folder>/research/<slug>/` (not its subfolders), "" when there are none."""
    try:
        files = sorted(p for p in (folder / "research" / slug).iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError:
        return ""
    texts = []
    for p in files:
        try:
            texts.append(f"### {p.name}\n{p.read_text(encoding='utf-8', errors='replace').strip()}")
        except OSError:
            continue
    return _cut("\n\n".join(texts), limit) if texts else ""


def spawn(config_path: Path, slug: str, lens: str | None = None, question: str | None = None) -> subprocess.Popen:
    """Start `rumo explore` detached from the Timeline (its own session; a detached process on Windows),
    logging to `explore.log` next to the config. The caller polls the result to reap it."""
    args = [sys.executable, "-m", "rumo.cli", "explore", slug, *([lens] if lens else []), *(["--ask", question] if question else [])]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path.parent / "explore.log", "a", encoding="utf-8") as log:
        log.write(" ".join(filter(None, [f"{datetime.now():%Y-%m-%d %H:%M}", slug, lens, question])) + "\n")
        log.flush()
        return subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "RUMO_CONFIG": str(config_path)}, start_new_session=sys.platform != "win32",
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )


def pid_alive(pid: int) -> bool:
    """Whether a process with this id runs. On Windows `os.kill(pid, 0)` would end it, so it asks the system."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True
