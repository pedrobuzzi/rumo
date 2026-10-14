"""User config: config.toml in the place each system expects (see `default_path`), or $RUMO_CONFIG.

    folder = "~/rumo"
    language = "pt-BR"            # language of the content the agent writes
    tags = ["bug", "feature"]     # fixed list the agent picks from
    ask_to_track = true           # in agent sessions without an item, offer to track them (unset: ask once)

    [groups.auth]
    name = "Migração auth"        # content: any language
    color = "blue"                # one of PALETTE

A missing file means defaults; a malformed one means defaults plus `error`, never a crash.
Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from rumo.items import slugify

# Named colors that read well on dark and light terminals, spread so that no two look alike
# (yellow leans lime and red leans pink, away from orange; purple leans blue, away from magenta).
PALETTE = {
    "blue": "#5b8cff",
    "green": "#4fc26b",
    "yellow": "#d2c43c",
    "orange": "#f08a3b",
    "red": "#ee5f74",
    "magenta": "#e070c8",
    "purple": "#9d7cf2",
    "cyan": "#2ab7c9",
}

DEFAULT_TAGS = [
    "bug", "feature", "refactor", "research", "spike", "review",
    "infra", "deploy", "meeting", "docs", "support", "tech-debt",
]


@dataclass
class Group:
    key: str
    name: str
    color: str  # a PALETTE name; anything else renders in the default color


@dataclass
class Config:
    path: Path
    folder: Path
    language: str = "en"
    tags: list[str] = field(default_factory=lambda: list(DEFAULT_TAGS))
    groups: dict[str, Group] = field(default_factory=dict)
    ask_to_track: bool | None = None  # None: the user was never asked
    error: str | None = None


def default_path(env: Mapping[str, str] | None = None, windows: bool | None = None) -> Path:
    """$RUMO_CONFIG, else %APPDATA%\\rumo on Windows, else $XDG_CONFIG_HOME/rumo (default ~/.config/rumo)."""
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    if override := env.get("RUMO_CONFIG"):
        return Path(override).expanduser()
    if windows:
        base = Path(env["APPDATA"]) if env.get("APPDATA") else Path.home() / "AppData" / "Roaming"
    else:
        base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "rumo" / "config.toml"


def load(path: Path | None = None) -> Config:
    path = path or default_path()
    data: dict = {}
    error = None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        error = f"{path}: {e}"

    folder = os.environ.get("RUMO_DIR") or data.get("folder") or "~/rumo"
    cfg = Config(path=path, folder=Path(str(folder)).expanduser(), language=language_from_env(), error=error)
    if isinstance(language := data.get("language"), str) and language.strip():
        cfg.language = language.strip()
    if isinstance(tags := data.get("tags"), list):
        cfg.tags = [str(t) for t in tags]
    if isinstance(ask := data.get("ask_to_track"), bool):
        cfg.ask_to_track = ask
    groups = data.get("groups")
    for key, value in (groups.items() if isinstance(groups, dict) else []):
        if isinstance(value, dict):
            cfg.groups[key] = Group(key, str(value.get("name") or key), str(value.get("color", "")))
    return cfg


def language_from_env(env: Mapping[str, str] | None = None) -> str:
    """The system language as a tag: `pt_BR.UTF-8` -> `pt-BR`; anything unusable -> `en`."""
    env = os.environ if env is None else env
    value = env.get("LC_ALL") or env.get("LANG") or ""
    m = re.match(r"^([a-z]{2})(?:_([A-Z]{2}))?", value)
    if not m:
        return "en"
    return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)


def ensure_file(path: Path, folder: Path, language: str) -> None:
    """Write a default config the first time, so there is a file to edit. Never overwrites."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_text(folder, language), encoding="utf-8")


def add_group(path: Path, name: str, color: str) -> str:
    """Append a [groups.<key>] block as text (no TOML writer needed) and return the new key."""
    existing = load(path).groups
    base = slugify(name)
    key, n = base, 2
    while key in existing:
        key, n = f"{base}-{n}", n + 1
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = f'\n[groups.{key}]\nname = {toml_str(name)}\ncolor = {toml_str(color)}\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n" + block if text.strip() else block.lstrip("\n"), encoding="utf-8")
    return key


def set_ask_to_track(path: Path, value: bool) -> None:
    """Write `ask_to_track` as a top-level key: replace it in place, or add it before the first table.

    Without a config file, write only this key: the hook that records the answer may not see the
    system language, so the defaults are left to the first `rumo` run or to `load`.
    """
    new_line = f"ask_to_track = {'true' if value else 'false'}\n"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_line, encoding="utf-8")
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    first_table = next((i for i, line in enumerate(lines) if _TABLE_RE.match(line)), len(lines))
    found = next((i for i in range(first_table) if re.match(r"^\s*ask_to_track\s*=", lines[i])), None)
    if found is not None:
        lines[found] = new_line
    else:
        lines.insert(_content_end(lines, -1, first_table), new_line)
    path.write_text("".join(lines), encoding="utf-8")


class GroupNotEditable(Exception):
    """The group has no `[groups.<key>]` table in the plain form these text edits understand."""


_GROUP_TABLE_RE = re.compile(r"""^\s*\[\s*groups\s*\.\s*(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*\]\s*(?:#.*)?$""")
_TABLE_RE = re.compile(r"^\s*\[")


def update_group(path: Path, key: str, name: str | None = None, color: str | None = None) -> None:
    """Rewrite the `name` and/or `color` line of one group, keeping every other line as it is."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for field_name, value in (("name", name), ("color", color)):
        if value is None:
            continue
        start, end = _group_table(lines, key)
        new_line = f"{field_name} = {toml_str(value)}\n"
        found = next((i for i in range(start + 1, end) if re.match(rf"^\s*{field_name}\s*=", lines[i])), None)
        if found is not None:
            lines[found] = new_line
        else:
            lines.insert(_content_end(lines, start, end), new_line)
    path.write_text("".join(lines), encoding="utf-8")


def delete_group(path: Path, key: str) -> None:
    """Remove one group's table; comments after it belong to whatever follows, so they stay."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start, end = _group_table(lines, key)
    del lines[start:_content_end(lines, start, end)]
    if start > 0 and not lines[start - 1].strip() and (start == len(lines) or not lines[start].strip()):
        del lines[start - 1]
    path.write_text("".join(lines), encoding="utf-8")


def _group_table(lines: list[str], key: str) -> tuple[int, int]:
    """Index of the group's table header and of the line where the next table starts."""
    for i, line in enumerate(lines):
        m = _GROUP_TABLE_RE.match(line)
        if m and next(g for g in m.groups() if g is not None) == key:
            end = next((j for j in range(i + 1, len(lines)) if _TABLE_RE.match(lines[j])), len(lines))
            return i, end
    raise GroupNotEditable(key)


def _content_end(lines: list[str], start: int, end: int) -> int:
    """One past the table's last key line, leaving out the blank and comment lines before the next table."""
    last = start
    for i in range(start + 1, end):
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            last = i
    return last + 1


def default_text(folder: Path, language: str) -> str:
    tags = ", ".join(toml_str(t) for t in DEFAULT_TAGS)
    return (
        "# rumo config. Colors: " + ", ".join(PALETTE) + "\n"
        f"folder = {toml_str(str(folder))}\n"
        f"language = {toml_str(language)}\n"
        f"tags = [{tags}]\n"
        "\n"
        "# Groups connect related items; each group shows in its own color.\n"
        "# [groups.auth]\n"
        '# name = "Auth migration"\n'
        '# color = "blue"\n'
    )


def state_path(config_path: Path) -> Path:
    """Small state rumo writes for itself (the TUI's view, the hook's counters), apart from the user's config."""
    return config_path.parent / "state.json"


def load_state(config_path: Path) -> dict:
    return _read_state(config_path) or {}


def _read_state(config_path: Path) -> dict | None:
    """state.json as a dict ({} when missing or broken), or None while it cannot be read."""
    for _ in range(50):
        try:
            data = json.loads(state_path(config_path).read_text(encoding="utf-8"))
        except PermissionError:  # Windows: another process is replacing it right now
            time.sleep(0.01)
            continue
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return None


def update_state(config_path: Path, **changes: object) -> None:
    """Merge `changes` into state.json. Never fails: remembering is a convenience.

    The hooks of several sessions and the TUI write it at once, so the new file replaces the old one
    whole: a reader that caught it half written would write back only its own keys.
    """
    path = state_path(config_path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        state = _read_state(config_path)
        if state is None:
            return  # never write only these keys over a state that could not be read
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state | changes) + "\n", encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows: someone is reading it right now
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string
