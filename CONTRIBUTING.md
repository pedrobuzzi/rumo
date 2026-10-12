# Contributing to rumo

## Layout

```
src/rumo/items.py        reading and writing items, the Timeline, the agent index
src/rumo/config.py       config file, per-OS location, system language
src/rumo/app.py          the Timeline TUI (Textual)
src/rumo/hook.py         Claude Code UserPromptSubmit hook (stdlib only, runs on every prompt)
src/rumo/cli.py          the `rumo` command
skills/rumo/SKILL.md     the /rumo skill (Claude Code plugin and `npx skills add`)
hooks/hooks.json         the plugin hook
.claude-plugin/          plugin manifest and the marketplace that serves this repo
scripts/                 version bump, Homebrew formula generator and tap updater
docs/specs/              specs: rumo-v1.md (the tool), rumo-distribution.md (how it ships)
```

## Develop

```bash
uv sync
uv run rumo                                     # the TUI, against your real ~/rumo
RUMO_DIR=/tmp/rumo-items uv run rumo            # or against a scratch folder
uv run python -m unittest discover -s tests     # tests (CI runs them on macOS, Linux and Windows)
uvx pyright --pythonpath .venv/bin/python       # type check
```

## Try the plugin from your checkout

```bash
uv tool install --force --editable .            # `rumo` on PATH, following your checkout
claude plugin validate .
```

Then, in Claude Code:

```
/plugin marketplace add /absolute/path/to/your/checkout
/plugin install rumo@rumo
```

Remove it afterwards with `/plugin uninstall rumo@rumo` and `/plugin marketplace remove rumo`.

## Release

One-time setup (already done for pedrobuzzi/rumo):

1. PyPI: a trusted publisher for project `rumo-cli`, repository `pedrobuzzi/rumo`, workflow
   `release.yml`, environment `pypi`.
2. GitHub: an environment `pypi` in this repo.
3. GitHub: the repo `pedrobuzzi/homebrew-tap`, and a fine-grained token with *Contents: read and
   write* on it, stored in this repo as the Actions secret `HOMEBREW_TAP_TOKEN`.

Every release:

```bash
uv run python scripts/bump_version.py 0.2.0     # pyproject.toml and .claude-plugin/plugin.json
git commit -am "Release 0.2.0"
git tag v0.2.0
git push origin main v0.2.0
```

The `release` workflow then runs the tests, checks that the tag matches both versions, publishes
to PyPI, creates the GitHub Release and updates the Homebrew formula. Last, `install-check` installs
the new version the way users do (Homebrew from the tap, uv, pipx and the Claude Code plugin from
the GitHub marketplace) on clean macOS, Linux and Windows machines and runs
`scripts/smoke_test.sh`. It also runs every Monday, so a change in Homebrew, a dependency or Claude
Code that breaks installing shows up even between releases. Claude Code users get the new
plugin version from `/plugin marketplace update rumo`.

To test the Homebrew formula before a release (macOS, does not push and leaves Homebrew as it was):

```bash
uv build
scripts/update_homebrew_tap.sh 0.2.0 "file://$PWD/dist/rumo_cli-0.2.0.tar.gz" \
  "$(shasum -a 256 dist/rumo_cli-0.2.0.tar.gz | cut -d' ' -f1)" --dry-run
```
