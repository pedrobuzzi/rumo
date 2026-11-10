"""The `rumo` command: opens the Timeline; `rumo idea` notes an idea from any shell.

Other subcommands exist for the agent and the hook, and are kept out of
the help on purpose: `hook`, `index`, `now`, `sessions`, `path`, `group-add`, `ask-to-track`, `explore`, `spark`, `--version`.
`index` reads the session id itself: Claude Code refuses to run a command that expands a variable
without asking, so the skill only ever runs `rumo ...`.
"""

from __future__ import annotations

import io
import os
import sys

USAGE = """rumo: your agent forgets, rumo doesn't.

  rumo                  open the Timeline
  rumo idea "<title>"   note an idea, and see similar items (--group <key> to file it)

Inside an AI agent session, type /rumo for the agent commands.
"""


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else ""

    if command and isinstance(sys.stdout, io.TextIOWrapper):
        # Agents read the output as UTF-8; Windows pipes default to the locale's code page.
        sys.stdout.reconfigure(encoding="utf-8")

    # Late imports: `rumo hook` runs on every prompt and must not pay for Textual.
    if command == "":
        from rumo import git, prs
        from rumo.app import RumoApp

        RumoApp(gh=prs.gh(), run_git=git.run).run()
    elif command == "hook":
        from rumo.hook import main as hook

        hook()
    elif command == "path":
        from rumo import config

        print(config.load().folder)
    elif command == "index":
        _index(args[1:])
    elif command == "now":
        _now()
    elif command == "sessions":
        _sessions(args[1:])
    elif command == "group-add":
        _group_add(args[1:])
    elif command == "ask-to-track":
        _ask_to_track(args[1:])
    elif command == "explore":
        _explore(args[1:])
    elif command == "spark":
        _spark(args[1:])
    elif command == "idea":
        _idea(args[1:])
    elif command in ("-V", "--version"):
        from importlib.metadata import version

        print(f"rumo {version('rumo-cli')}")
    elif command in ("-h", "--help", "help"):
        print(USAGE, end="")
    else:
        print(USAGE, end="", file=sys.stderr)
        raise SystemExit(2)


def _index(rest: list[str]) -> None:
    """`rumo index [<folder>]`: everything the agent needs in one call. Where items live, the content
    language, tags, groups, the time, this session, the items it keeps updated, what git has left of their
    work and the line that links it to another one, then the items.

    The folder is where the session started (by default the current one): `claude --resume` only finds
    the session from there.
    """
    from datetime import datetime

    from rumo import config, git, items

    cfg = config.load()
    all_items = items.list_items(cfg.folder)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    print(f"# folder: {cfg.folder}")
    print(f"# config: {cfg.path}")
    print(f"# language: {cfg.language}")
    print(f"# tags: {', '.join(cfg.tags)}")
    print(f"# groups: {', '.join(f'{g.key} ({g.name}, {g.color})' for g in cfg.groups.values()) or '-'}")
    print(f"# colors: {', '.join(config.PALETTE)}")
    print(f"# now: {datetime.now():%Y-%m-%d %H:%M}")
    if session:
        print(f"# session: {session}")
        linked = [i for i in all_items if i.linked_to(session)]
        print(f"# linked: {', '.join(i.slug for i in linked) or '-'}")
        left = git.pending(linked, config.load_state(cfg.path)) if linked else {}
        print(f"# git-left: {'; '.join(p.label for found in left.values() for p in found) or '-'}")
        print(f"# session-line: {_session_line(session, rest[0] if rest else os.getcwd())}")
    else:
        print("# session: none (simple mode: sessions can't be linked)")
    if listing := items.index(all_items):
        print(listing)


def _now() -> None:
    """What is in progress (doing, then waiting), one line each: title, status, next step and file."""
    from rumo import config, items

    in_progress = items.timeline(items.list_items(config.load().folder)).now
    for item in in_progress:
        step = item.next_step
        print(f"{item.title} | {item.status} | next: {step.label if step else 'no open steps'} | {item.path}")
    if not in_progress:
        print("Nothing in progress.")


def _sessions(rest: list[str]) -> None:
    """`rumo sessions [--since <N>d|<N>h]`: the Claude Code sessions active since the last review (3 days
    back the first time), digested for `/rumo sessions`. `rumo sessions --reviewed "<time>"` saves the
    `# now:` of the listing that was reviewed as where the next review starts.
    """
    import re
    from datetime import datetime, timedelta

    from rumo import config, items, sessions

    usage = 'usage: rumo sessions [--since <N>d] | rumo sessions --reviewed "<YYYY-MM-DD HH:MM>"'
    cfg = config.load()
    if rest[:1] == ["--reviewed"]:
        when = " ".join(rest[1:]).replace("T", " ")
        try:
            datetime.strptime(when, items.TIME_FORMAT)
        except ValueError:
            print(usage, file=sys.stderr)
            raise SystemExit(2) from None
        config.update_state(cfg.path, sessions_reviewed_at=when)
        return
    now = datetime.now().replace(second=0, microsecond=0)
    if rest[:1] == ["--since"] and len(rest) == 2 and (m := re.fullmatch(r"(\d+)([dh])", rest[1])):
        since, why = now - timedelta(**{"days" if m[2] == "d" else "hours": int(m[1])}), f"--since {rest[1]}"
    elif rest:
        print(usage, file=sys.stderr)
        raise SystemExit(2)
    else:
        try:
            since, why = datetime.strptime(str(config.load_state(cfg.path).get("sessions_reviewed_at")), items.TIME_FORMAT), "the last review"
        except ValueError:
            since, why = now - timedelta(days=sessions.FIRST_REVIEW_DAYS), f"{sessions.FIRST_REVIEW_DAYS} days ago: the first review"
    claude = sessions.claude_dir()
    found = sessions.recent(claude, since)
    if found is None:
        print(f"No Claude Code sessions found in {claude}.")
        return
    print(f"# since: {since:%Y-%m-%d %H:%M} ({why})")
    print(f"# now: {now:%Y-%m-%d %H:%M}")
    print("\n".join(sessions.digest(found, items.list_items(cfg.folder), config.load_state(cfg.path), now, cfg.folder)))


def _session_line(session: str, folder: str) -> str:
    """The Sessions line of an item: when, the repository and branch of the folder, the session and the folder."""
    import subprocess
    from datetime import datetime

    folder = os.path.abspath(os.path.expanduser(folder))

    def git(*args: str) -> str:
        try:
            done = subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    repo = os.path.basename(git("rev-parse", "--show-toplevel") or folder)
    branch = git("symbolic-ref", "--short", "-q", "HEAD") or "-"
    return f"- {datetime.now():%Y-%m-%d %H:%M} | {repo} ({branch}) | {session} | {folder}"


def _group_add(rest: list[str]) -> None:
    """`rumo group-add "<name>" <color>`: append a group to the config and print its key."""
    from rumo import config

    if len(rest) != 2 or not rest[0].strip() or rest[1] not in config.PALETTE:
        print(f'usage: rumo group-add "<name>" <{"|".join(config.PALETTE)}>', file=sys.stderr)
        raise SystemExit(2)
    print(config.add_group(config.load().path, rest[0].strip(), rest[1]))


def _ask_to_track(rest: list[str]) -> None:
    """`rumo ask-to-track true|false`: whether the hook offers rumo in agent sessions without an item."""
    from rumo import config

    if len(rest) != 1 or rest[0] not in ("true", "false"):
        print("usage: rumo ask-to-track <true|false>", file=sys.stderr)
        raise SystemExit(2)
    path = config.load().path
    config.set_ask_to_track(path, rest[0] == "true")
    print(f"ask_to_track = {rest[0]} in {path}")


def _idea(rest: list[str]) -> None:
    """`rumo idea "<title>" [--group <key>]`: note an idea from any shell, then name up to three similar
    items, with the reason for the dropped ones, so an idea already turned down is not rediscovered.
    """
    from datetime import datetime

    from rumo import config, items

    cfg = config.load()
    group = ""
    if rest[:1] in (["-h"], ["--help"]):
        print('usage: rumo idea "<title>" [--group <key>]')
        return
    if rest[:1] and rest[0].startswith("-"):  # a flag, never a title
        rest = []
    if len(rest) == 3 and rest[1] == "--group" and rest[2] in cfg.groups:
        group = rest[2]
    elif len(rest) != 1:
        groups = f" (groups: {', '.join(cfg.groups)})" if cfg.groups else ""
        print(f'usage: rumo idea "<title>" [--group <key>]{groups}', file=sys.stderr)
        raise SystemExit(2)
    title = " ".join(rest[0].split())
    if not title:
        print('usage: rumo idea "<title>" [--group <key>]', file=sys.stderr)
        raise SystemExit(2)
    found = items.similar(items.list_items(cfg.folder), title)
    print(items.create_idea(cfg.folder, title, now=datetime.now(), group=group))
    for item in found:
        reason = item.sections.get("left off", "") if item.status == "dropped" else ""
        print(f"similar: {item.title} ({item.status}{': ' + ' '.join(reason.split()) if reason else ''})")


def _explore(rest: list[str]) -> None:
    """`rumo explore <slug> <lens>` or `rumo explore <slug> [deep] --ask "<question>"`: ask about an item and
    write the answer into it. The Timeline runs it in the background on `?`. Prints `done` or `failed: <reason>`.
    """
    from rumo import config, explore, items

    cfg = config.load()
    lens = question = None
    if len(rest) == 2 and rest[1] != "--ask":
        lens = rest[1]
    elif len(rest) in (3, 4) and rest[-2] == "--ask" and rest[-1].strip() and (len(rest) == 3 or rest[1] == explore.DEEP):
        lens, question = (rest[1] if len(rest) == 4 else None), rest[-1]
    else:
        print('usage: rumo explore <slug> <lens> | rumo explore <slug> [deep] --ask "<question>"', file=sys.stderr)
        raise SystemExit(2)
    path = cfg.folder / f"{rest[0]}.md"
    if not path.is_file():
        print(f"no item {rest[0]} in {cfg.folder}", file=sys.stderr)
        raise SystemExit(2)
    if lens and lens != explore.DEEP and lens not in cfg.lenses:
        print(f"unknown lens: {lens} (lenses: {', '.join([*cfg.lenses, explore.DEEP])})", file=sys.stderr)
        raise SystemExit(2)
    try:
        result = explore.explore(cfg, path, lens=lens, question=question)
    except (explore.ExploreError, items.WriteConflict) as e:
        print(f"failed: {e}")
        raise SystemExit(1) from None
    print(result)
    if result != "done":
        raise SystemExit(1)


def _spark(rest: list[str]) -> None:
    """`rumo spark`: read the ideas and write three new ones, or the questions to ask first, to spark.json.
    The Timeline runs it in the background on `*`. Prints `asking`, `ready` or `failed: <reason>`.
    """
    from rumo import config, spark

    if rest:
        print("usage: rumo spark", file=sys.stderr)
        raise SystemExit(2)
    try:
        result = spark.spark(config.load())
    except spark.SparkError as e:
        print(f"failed: {e}")
        raise SystemExit(1) from None
    print(result)
    if result.startswith("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
