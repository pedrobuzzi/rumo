# rumo distribution — spec

Status: agreed with the user on 2026-10-11 (grilling session). Builds on [rumo-v1.md](rumo-v1.md).

## Problem

rumo works, but installing it means cloning the repo and running a script that edits global
Claude Code files and creates a symlink (which fails on Windows without developer mode). Only
Claude Code users get the agent side. There is no CI, and no repeatable release.

## Decisions

1. **CLI channel**: PyPI package `rumo-cli` (command `rumo`). Official install:
   `uv tool install rumo-cli` or `pipx install rumo-cli` on macOS, Linux and Windows.
2. **Homebrew**: own tap `pedrobuzzi/homebrew-tap`, formula `rumo`
   (`brew install pedrobuzzi/tap/rumo`). homebrew-core later, once there is traction.
3. **Claude Code**: the repo is a plugin and its own marketplace
   (`/plugin marketplace add pedrobuzzi/rumo`, `/plugin install rumo@rumo`). The plugin ships the
   `/rumo` skill and the `UserPromptSubmit` hook. `install.sh`, `rumo install`/`uninstall` and every
   edit to `settings.json` and `CLAUDE.md` are removed.
4. **Other agents**: `npx skills add pedrobuzzi/rumo` installs the same `SKILL.md`. Without
   `$CLAUDE_CODE_SESSION_ID` the skill runs in **simple mode**: `new`, `idea`, `now`, `find`, `done`
   work without session lines; `link`, `park`, `unlink` explain they need Claude Code.
5. **Hook depends on the CLI**: the plugin hook runs `rumo hook` only if `rumo` is on `PATH`, and is
   silent otherwise. The skill detects a missing CLI and shows the install commands.
6. **Rules**: no always-on rules. The "keep the item updated" rules travel inside the hook reminder
   (shown only while a session is linked); everything else lives in the skill.
7. **Windows is supported**: default editor `notepad`; resume command that works in PowerShell
   (`cd "<dir>"; claude --resume <id>`); hook reads stdin and writes stdout as UTF-8.
8. **Config location**: `$XDG_CONFIG_HOME/rumo/config.toml` (default `~/.config/rumo`) on macOS and
   Linux, `%APPDATA%\rumo\config.toml` on Windows. `RUMO_CONFIG` still overrides. Items stay in
   `~/rumo` by default.
9. **Versions**: first public version `0.1.0`. The version lives in `pyproject.toml` and
   `.claude-plugin/plugin.json`; a bump script updates both and a test fails if they differ. The
   release workflow refuses a tag that doesn't match.
10. **CI**: tests on macOS, Linux and Windows with Python 3.11 and 3.13; pyright once.
11. **Release**: pushing tag `vX.Y.Z` runs: tests → version check → build → publish to PyPI with
    trusted publishing (environment `pypi`) → GitHub Release with the built files → regenerate the
    tap formula (`brew update-python-resources`), test it, push it with `HOMEBREW_TAP_TOKEN`.
12. **Nothing is published before the user tests everything locally.**

## Out of scope

homebrew-core, winget/scoop, prebuilt binaries, full hook support in Codex/Gemini (simple mode only).
