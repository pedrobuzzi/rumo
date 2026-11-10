"""New ideas from the ones the user already has: `*` in the Timeline.

`rumo spark` runs one step here: it reads the ideas, paused and dropped items (oldest first), asks Claude
Code for the direction they show and three new ideas (or, the first time, one or two questions), and
writes the result to `spark.json` in the items folder. Nothing becomes an item until the user keeps it
(`keep`). The Timeline starts it with `spawn`, detached, like `rumo explore`. Stdlib only, like `items`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from rumo import explore, items
from rumo.config import Config

STATE_FILE = "spark.json"  # not `.md`: never read as an item
READS = ("idea", "paused", "dropped")  # what the direction is read from
ITEM_CHARS = 1500  # each idea in the prompt
PROMPT_CHARS = 40000  # all the ideas in the prompt
ANSWER_CHARS = 300  # the start of an idea's latest answer
KEEP_DISCARDED = 100  # suggestions thrown away, remembered so they never come back
MAX_IDEAS = 3
MAX_QUESTIONS = 2
STAMP = "%Y-%m-%d %H:%M:%S"
STATES = ("running", "asking", "ready", "failed")  # no `state`: idle

PROMPT = """You help someone find their next idea. They keep their ideas and their work in rumo, a notebook of Markdown files. Below are their ideas, oldest first: ideas not started yet, paused work, and ideas they dropped, with the reason.

{ideas}

## Their other work (titles only)

{work}

## Suggestions they already threw away

{discarded}
{answers}
Read the ideas from oldest to newest and find what they keep coming back to: the people they build for, the problems, the kind of product, the way of charging. Notice what they dropped and why. Write that direction in at most 40 words.

Then suggest 3 ideas they have not had:
1. one close to that direction;
2. one that crosses two of their ideas;
3. one that keeps the core of the direction but takes it somewhere else (another audience, market or format).

None may repeat or reword one of their ideas or a suggestion they threw away, and none may bring back a dropped idea unless it says what changed. Do a quick web search to check that each one answers a real need today; do not include links.

Each idea has a title of at most 8 words and a summary of one short sentence, at most 20 words (count them), saying what it is and for whom. "from" lists the slugs of their ideas it builds on, the closest first.
{ask}
Write the direction, titles and summaries in {language}. Reply with JSON only, nothing before or after it:

{{"direction": "...", "ideas": [{{"title": "...", "summary": "...", "from": ["<slug>"]}}]}}
"""

ASK = """
Only if an answer would really change which ideas you suggest, you may instead ask one or two short questions first, and nothing you can tell from the ideas. Then reply with {"direction": "...", "questions": ["..."]} instead.
"""

Runner = Callable[[str, str, Path, int], str]


class SparkError(Exception):
    """Why a spark could not start or did not bring ideas, in a line the user can act on."""


@dataclass
class Idea:
    title: str
    summary: str
    sources: list[str]  # slugs of the user's ideas it builds on, the closest first


def state_path(folder: Path) -> Path:
    return folder / STATE_FILE


def load(folder: Path) -> dict:
    """spark.json as a dict; {} when missing or unreadable (idle)."""
    for _ in range(50):
        try:
            data = json.loads(state_path(folder).read_text(encoding="utf-8"))
        except PermissionError:  # Windows: being replaced right now
            time.sleep(0.01)
            continue
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def save(folder: Path, data: dict) -> None:
    """Write spark.json whole. A file that could not be read is kept as spark.json.bad first."""
    path = state_path(folder)
    if _unreadable(path):
        os.replace(path, path.with_name(f"{STATE_FILE}.bad"))
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{STATE_FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: someone is reading it right now
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


def _unreadable(path: Path) -> bool:
    try:
        return not isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (FileNotFoundError, PermissionError):  # missing, or being replaced right now
        return False
    except (OSError, ValueError):
        return True


def current(data: dict, now: datetime, alive: Callable[[int], bool]) -> tuple[str, str]:
    """(state, error): idle, running, asking, ready or failed. A run whose process is gone, or that went
    past its time, is failed: it stopped before it answered."""
    state = data.get("state")
    if state not in STATES:
        return "idle", ""
    if state == "running":
        try:
            started = datetime.strptime(str(data.get("started", "")), STAMP)
            limit = int(data.get("limit", 0))
            pid = int(data.get("pid", 0))
        except (TypeError, ValueError):
            return "failed", "stopped before it answered"
        if not alive(pid) or now > started + timedelta(seconds=limit + items.STALE_GRACE_SECONDS):
            return "failed", "stopped before it answered"
        return "running", ""
    if state == "asking" and not questions_of(data):
        return "idle", ""
    if state == "ready" and not ideas_of(data):
        return "idle", ""
    return state, str(data.get("error") or "stopped before it answered") if state == "failed" else ""


def ideas_of(data: dict) -> list[Idea]:
    found = []
    for raw in data.get("ideas") or []:
        if isinstance(raw, dict) and (title := _line(raw.get("title"))):
            sources = raw.get("from")
            found.append(Idea(title, _line(raw.get("summary")),
                              [s for s in sources if isinstance(s, str) and s] if isinstance(sources, list) else []))
    return found[:MAX_IDEAS]


def questions_of(data: dict) -> list[str]:
    raw = data.get("questions")
    return [q for q in (_line(x) for x in raw) if q][:MAX_QUESTIONS] if isinstance(raw, list) else []


def _line(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def readable(all_items: list[items.Item]) -> list[items.Item]:
    """The ideas, paused and dropped items, oldest first (by `created`, else `updated`)."""
    found = [i for i in all_items if i.status in READS]
    return sorted(found, key=lambda i: (i.meta.get("created") or i.meta.get("updated") or "", i.slug))


def build_prompt(cfg: Config, all_items: list[items.Item], data: dict) -> str:
    ideas = readable(all_items)
    per_item = min(ITEM_CHARS, PROMPT_CHARS // max(len(ideas), 1))
    blocks = [explore._cut(_describe(cfg, i), per_item) for i in ideas]
    work = [f"- {i.title} ({_group(cfg, i)}{i.status})" for i in all_items if i.status not in READS]
    discarded = [f"- {d['title']}: {d.get('summary') or ''}" for d in data.get("discarded") or []
                 if isinstance(d, dict) and isinstance(d.get("title"), str)]
    answers = ""
    if questions := questions_of(data):
        given = [a if isinstance(a, str) else "" for a in data.get("answers") or []]
        pairs = [f"- {q}\n  {(given[n] if n < len(given) else '').strip() or '(no answer)'}" for n, q in enumerate(questions)]
        answers = "\n## Your questions, with their answers (no answer: decide yourself)\n\n" + "\n".join(pairs) + "\n"
    return PROMPT.format(
        ideas="\n\n".join(blocks), work="\n".join(work) or "-", discarded="\n".join(discarded[:KEEP_DISCARDED]) or "-",
        answers=answers, ask="" if answers else ASK, language=cfg.language,
    )


def _group(cfg: Config, item: items.Item) -> str:
    group = cfg.groups.get(item.group)
    return f"{group.name}, " if group else f"{item.group}, " if item.group else ""


def _describe(cfg: Config, item: items.Item) -> str:
    created = (item.meta.get("created") or item.meta.get("updated") or "")[:10]
    facts = [f"slug: {item.slug}", item.status, f"created {created}" if created else "",
             f"group: {_group(cfg, item).rstrip(', ')}" if item.group else "",
             f"from: {item.from_slug}" if item.from_slug else ""]
    parts = [f"### {item.title}", " · ".join(f for f in facts if f)]
    left_off = "Why dropped" if item.status == "dropped" else "Left off"
    for name, heading in (("context", "Context"), ("left off", left_off), ("notes", "Notes")):
        if text := item.sections.get(name, "").strip():
            parts.append(f"{heading}: {text}")
    asked = [e for e in item.explorations if e.question]
    if asked:
        parts.append("Explored: " + "; ".join(e.question for e in asked))
        if latest := next((e for e in reversed(asked) if e.state == "done" and e.answer), None):
            start = " ".join(line for line in latest.answer.splitlines()[:3] if line.strip())
            parts.append(f"Latest answer, start: {explore._cut(start, ANSWER_CHARS)}")
    return "\n".join(parts)


def parse_reply(text: str, slugs: set[str], second: bool) -> dict:
    """The JSON in Claude Code's reply (a code fence or prose around it is fine), checked: at most three ideas
    with a title, `from` only naming items that exist; or, on the first run only, at most two questions."""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise SparkError("the answer was not JSON")
    direction = _line(data.get("direction"))
    if ideas := ideas_of(data):
        return {"direction": direction, "ideas": [
            {"title": i.title, "summary": i.summary, "from": [s for s in i.sources if s in slugs]} for i in ideas]}
    if questions := questions_of(data):
        if second:
            raise SparkError("asked again instead of answering")
        return {"direction": direction, "questions": questions}
    raise SparkError("no ideas in the answer")


def spark(cfg: Config, now: datetime | None = None, run: Runner | None = None) -> str:
    """One step: the ideas, or the questions to ask first, written to spark.json. Returns "asking", "ready" or
    "failed: <reason>"; raises SparkError, writing nothing, when there is nothing to read or another run is on."""
    folder = cfg.folder
    data = load(folder)
    state, _ = current(data, datetime.now(), explore.pid_alive)
    if state == "running" and data.get("pid") != os.getpid():
        raise SparkError("already reading your ideas")
    all_items = items.list_items(folder)
    if not readable(all_items):
        raise SparkError("no ideas to read yet")
    second = state == "asking" and isinstance(data.get("answers"), list)
    base = {"discarded": data.get("discarded") or []}
    if second:
        base |= {k: data[k] for k in ("direction", "questions", "answers") if k in data}
    started = f"{now or datetime.now():{STAMP}}"
    save(folder, base | {"state": "running", "started": started, "pid": os.getpid(), "limit": explore.LIMIT})
    try:
        prompt = build_prompt(cfg, all_items, base)
        text = (run or explore.run_claude)(cfg.explore_command, prompt, folder, explore.LIMIT)
        reply = parse_reply(text, {i.slug for i in all_items}, second)
    except (SparkError, explore.ExploreError) as e:
        save(folder, {"discarded": load(folder).get("discarded") or base["discarded"], "state": "failed",
                      "started": started, "error": str(e)})
        return f"failed: {e}"
    result = "asking" if "questions" in reply else "ready"
    save(folder, {"discarded": load(folder).get("discarded") or base["discarded"], "state": result, "started": started,
                  **reply})
    return result


def answer(folder: Path, answers: list[str]) -> None:
    """The user's answers to the questions; the next run reads them and brings the ideas."""
    data = load(folder)
    data["answers"] = [" ".join(a.split()) for a in answers]
    save(folder, data)


def keep(folder: Path, all_items: list[items.Item], ideas: list[Idea], thrown: set[int],
         now: datetime | None = None) -> list[Path]:
    """Create the ideas not thrown away (`from:` and group of the first idea each builds on, the summary as
    Context), remember the thrown ones, newest first, and leave the spark idle. Returns the new items' paths."""
    now = now or datetime.now()
    by_slug = {i.slug: i for i in all_items}
    created = []
    for n, idea in enumerate(ideas):
        if n in thrown:
            continue
        parent = next((by_slug[s] for s in idea.sources if s in by_slug), None)
        created.append(items.create_idea(folder, idea.title, now=now, group=parent.group if parent else "",
                                         parent=parent.slug if parent else "", context=idea.summary))
    data = load(folder)
    gone = [{"title": i.title, "summary": i.summary, "date": f"{now:%Y-%m-%d}"} for n, i in enumerate(ideas) if n in thrown]
    save(folder, {"discarded": (gone + list(data.get("discarded") or []))[:KEEP_DISCARDED]})
    return created


def spawn(config_path: Path) -> subprocess.Popen:
    """Start `rumo spark` detached from the Timeline, logging to `explore.log` next to the config, the way
    `explore.spawn` does. The caller polls the result to reap it."""
    args = [sys.executable, "-m", "rumo.cli", "spark"]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path.parent / "explore.log", "a", encoding="utf-8") as log:
        log.write(f"{datetime.now():%Y-%m-%d %H:%M} spark\n")
        log.flush()
        return subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "RUMO_CONFIG": str(config_path)}, start_new_session=sys.platform != "win32",
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
