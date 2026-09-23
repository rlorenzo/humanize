> **Engineering Constitution**: `~/.claude/memory/engineering-constitution.md` — global Spec-Driven PDVA, context-engineering rules (token budgets, just-in-time loading), evidence-first verification, anti-patterns. Read on demand at the start of any non-trivial session. Project-specific guidance below; constitution wins on conflicts.

# humanize — Project conventions

## What this project is
A Claude Code plugin that flags AI-writing patterns in prose: a skill (`SKILL.md` +
`patterns/core.md`), a reviewer subagent, a PostToolUse hook, an optional always-on
rule, and two zero-dependency Python CLIs (`humanize-score`, `burstiness-check`).
Distributed through the plugin marketplace in `.claude-plugin/`, or by `install.sh`.

## Stack
- Language / runtime: Python 3.9+, stdlib only (no runtime dependencies); bash for the hook and installer
- Packaging: hatchling, installed with pip
- Tooling: pytest + pytest-cov, ruff (lint + format), vulture, pylint (duplicate-code only), shellcheck

## Layout
- `humanize_anti_slop/humanize_score.py` — pattern scorer; also the hook's `--hook` mode
- `humanize_anti_slop/burstiness_check.py` — sentence-length statistics
- `hooks/` — `hooks.json` (auto-loaded by the plugin) and the bash wrapper
- `rules/10-anti-slop.md` — always-on rule, copied by `install.sh` only
- `scripts/calibration/` — one-off corpus validation behind the burstiness thresholds; not shipped

## Commands
```bash
# install
pip install -e '.[dev]' pytest-cov

# test (what CI runs)
python -m pytest --cov --cov-report=term-missing --cov-fail-under=85

# lint + format
ruff check . && ruff format --check .
vulture humanize_anti_slop tests --min-confidence 80
shellcheck hooks/humanize-post-write.sh install.sh .github/smoke-console-scripts.sh

# build
python -m build --wheel
```

## Conventions
- Branch names: `feat/…`, `fix/…`, `chore/…`, `refactor/…`
- Commits: conventional commits with structured trailers (see global Commit Protocol)
- PRs: small, single-concern; include a "Test plan" section.
- Every user-visible change gets a `CHANGELOG.md` entry under `[Unreleased]`.

## Hidden gotchas
- The version lives in `pyproject.toml`, `.claude-plugin/plugin.json` and the `SKILL.md`
  frontmatter; `tests/test_metadata.py` fails if they drift.
- Do not add a `hooks` key to `plugin.json`: Claude Code already loads
  `hooks/hooks.json`, and declaring it again makes the plugin uninstallable.
- The hook must always exit 0 and print nothing but the JSON contract on stdout. Set
  `HUMANIZE_DEBUG=1` to see why it skipped or stayed silent.
- The hook runs under whatever `python3` and `bash` the user has (macOS: bash 3.2), so
  keep the shell portable and the scorer stdlib-only.
- A new pattern needs its id in `PATTERNS` (regex) or `HEURISTICS` (function), and one
  phrase must not appear in two patterns' regexes.
