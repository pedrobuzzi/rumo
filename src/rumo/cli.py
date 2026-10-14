"""The `rumo` command: opens the Timeline.

Other subcommands exist for the agent and the hook, and are kept out of
the help on purpose: `hook`, `index`, `now`, `path`, `group-add`, `ask-to-track`, `--version`.
`index` reads the session id itself: Claude Code refuses to run a command that expands a variable
without asking, so the skill only ever runs `rumo ...`.
"""

from __future__ import annotations

import os
import sys

USAGE = """rumo: your work, past, now and later.

  rumo        open the Timeline

Inside an AI agent session, type /rumo for the agent commands.
"""


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else ""

    # Late imports: `rumo hook` runs on every prompt and must not pay for Textual.
    if command == "":
        from rumo.app import RumoApp

        RumoApp().run()
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
    elif command == "group-add":
        _group_add(args[1:])
    elif command == "ask-to-track":
        _ask_to_track(args[1:])
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
    language, tags, groups, the time, this session, the items it keeps updated and the line that links
    it to another one, then the items.

    The folder is where the session started (by default the current one): `claude --resume` only finds
    the session from there.
    """
    from datetime import datetime

    from rumo import config, items

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
        print(f"# linked: {', '.join(i.slug for i in all_items if i.linked_to(session)) or '-'}")
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


if __name__ == "__main__":
    main()
