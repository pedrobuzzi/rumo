"""The `rumo` command: opens the Timeline.

Other subcommands exist for the agent and the hook, and are kept out of
the help on purpose: `hook`, `index`, `path`, `group-add`, `--version`.
"""

from __future__ import annotations

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
        _index()
    elif command == "group-add":
        _group_add(args[1:])
    elif command in ("-V", "--version"):
        from importlib.metadata import version

        print(f"rumo {version('rumo-cli')}")
    elif command in ("-h", "--help", "help"):
        print(USAGE, end="")
    else:
        print(USAGE, end="", file=sys.stderr)
        raise SystemExit(2)


def _index() -> None:
    """Everything the agent needs in one call: where items live, the content language, tags, groups and items."""
    from rumo import config, items

    cfg = config.load()
    print(f"# folder: {cfg.folder}")
    print(f"# config: {cfg.path}")
    print(f"# language: {cfg.language}")
    print(f"# tags: {', '.join(cfg.tags)}")
    print(f"# groups: {', '.join(f'{g.key} ({g.name}, {g.color})' for g in cfg.groups.values()) or '-'}")
    print(f"# colors: {', '.join(config.PALETTE)}")
    if listing := items.index(items.list_items(cfg.folder)):
        print(listing)


def _group_add(rest: list[str]) -> None:
    """`rumo group-add "<name>" <color>`: append a group to the config and print its key."""
    from rumo import config

    if len(rest) != 2 or not rest[0].strip() or rest[1] not in config.PALETTE:
        print(f'usage: rumo group-add "<name>" <{"|".join(config.PALETTE)}>', file=sys.stderr)
        raise SystemExit(2)
    print(config.add_group(config.load().path, rest[0].strip(), rest[1]))


if __name__ == "__main__":
    main()
