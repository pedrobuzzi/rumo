"""User config: config.toml in the place each system expects (see `default_path`), or $RUMO_CONFIG.

    folder = "~/rumo"
    language = "pt-BR"            # language of the content the agent writes
    tags = ["bug", "feature"]     # fixed list the agent picks from

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
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from rumo.items import slugify

# Named colors that read well on dark and light terminals.
PALETTE = {
    "blue": "#5b8cff",
    "green": "#3fbf7f",
    "yellow": "#d9a520",
    "orange": "#ec8a3c",
    "red": "#e05d5d",
    "magenta": "#d561b5",
    "purple": "#a77ee8",
    "cyan": "#2fb8c8",
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


def default_text(folder: Path, language: str) -> str:
    tags = ", ".join(toml_str(t) for t in DEFAULT_TAGS)
    return (
        "# rumo config. Colors: " + ", ".join(PALETTE) + "\n"
        f"folder = {toml_str(str(folder))}\n"
        f"language = {toml_str(language)}\n"
        f"tags = [{tags}]\n"
        "\n"
        "# Groups connect related items; the title takes the group color.\n"
        "# [groups.auth]\n"
        '# name = "Auth migration"\n'
        '# color = "blue"\n'
    )


def toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string
