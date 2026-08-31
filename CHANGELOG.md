# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **On the tags below 1.2.0.** Nothing was ever published to a package index and the
> repository carried no tags until 2026-08-31, so `v1.0.0`, `v1.1.0` and `v1.1.1` are
> retroactive. Each points at the last commit where all three version manifests
> (`pyproject.toml`, `.claude-plugin/plugin.json`, `SKILL.md`) agreed on that number.
> They mark the state of the tree, not a release that was distributed at the time.

## [Unreleased]

### Added

- The scorer's human-readable output carries a `scope:` line naming what the number
  does *not* mean: it is a weighted rate of the 44 patterns in this catalogue,
  which is a claim about writing quality, not a prediction about any AI detector.
  Previously a user reading `humanize_score: 8.7/100 (clean)` had nothing telling
  them that `DETECTION_ROBUSTNESS.md` argues a classifier trained on model phrase
  distributions is a different question entirely. Deliberately absent from `--json`,
  which stays a data contract — machines do not misread a verdict.
- A "What the score does not mean" section in the README, and a staleness marker on
  the detector table in `DETECTION_ROBUSTNESS.md`, whose figures were gathered
  2026-04-27 and have not been re-verified.

### Fixed

- **`count_fragmented_headers` (#29) detected the opposite of what it documented.**
  It required the line after the short one to be non-blank, so a real fragmented
  header — heading, restating stub, blank line — never fired, while every ordinary
  hard-wrapped paragraph did. It also never tested for restatement at all, only for
  length. It now requires the stub to stand alone as its own paragraph *and* to echo
  at least half the heading's content words. On this repository's own documents the
  false-positive count fell from 11 to 1 in `README.md`, 4 to 0 in
  `DETECTION_ROBUSTNESS.md`, and 3 to 0 in `CHANGELOG.md`.
- **`count_polysyndetic_tripleting` (#41) missed most real triplets.** The regex
  matched only bare single words between commas, so `"The code, the tests, and the
  docs"` did not register. Items may now carry a determiner or possessive.
  Separately, comma-delimited parenthetical adverbs (`"it was done, however, and
  then we moved on"`) have identical surface punctuation to a triplet and were
  counting as one; they are now excluded.

### Added

- `tests/test_heuristic_counters.py` — the first tests for the four heuristic
  counters (#11, #17, #29, #41), the patterns a regex cannot express. Coverage of
  `humanize_score.py` went 69% → 80%, repository total 83% → 88%, and the CI floor
  rose from 80% to 85%.

### Known issues

- `count_title_case_headings` (#17) fires on sentence-case headings containing two
  proper nouns (`"## Working with GitHub Actions"`). The `len(w) > 2` filter drops
  the lowercase function words that would otherwise distinguish the two cases.
  Pinned by a test rather than fixed: separating a proper noun from a title-cased
  common noun needs a dictionary.
- `count_fragmented_headers` still fires on a heading followed by a link or a bold
  structured entry that reuses its words (`"## License"` / `"MIT — see [LICENSE](LICENSE)."`).

## [1.2.0] — 2026-08-31

### Changed

- **Breaking (JSON output).** `burstiness_check --json` renamed the metric key
  `lexical_diversity_ttr` to `lexical_diversity_mattr50`, so the key names the
  quantity actually reported. Anything parsing that field needs updating.
- **Breaking (JSON output).** `lexical_diversity_mattr50` is now `null` for text
  shorter than the 50-word MATTR window, where it previously reported a number.
  MATTR-50 is undefined below its window; `apply_checks` skips a metric that is
  `None` rather than scoring a guess. Previously the function returned
  whole-document TTR under 50 words and MATTR-50 at or above it — two quantities
  that move in opposite directions with length, carried under one name and checked
  against a single threshold band.
- `FUNCTION_WORDS` grew from 56 entries to roughly 300, organised by grammatical
  class (determiners, pronouns, prepositions, conjunctions, auxiliaries, negators,
  degree adverbs). Built as a closed-class inventory rather than borrowed from a
  stopword list: stopword lists drop function words carrying no retrieval value
  (`not`, `no`) and add frequent content words.
- Contractions (`don't`, `it's`, `they're`, …) are now counted as function words.
  The tokenizer is `[A-Za-z']+`, so they arrive as single tokens; with none of them
  listed, the most frequent function words in natural prose had been counting
  against the ratio instead of toward it.

### Fixed

- `function_word_ratio` now measures against a real inventory. On this repository's
  own documents the ratio moved from 0.15–0.21 to 0.24–0.31.

### Known issues

- **`signature_score` remains uncalibrated and should be read as diagnostic output,
  not a verdict.** Human prose still reports `heavy-AI-signature`. The 1.2.0 work
  fixed the *measurement* defects; both threshold bands (`lexical_diversity`
  0.40–0.65 and `function_word_ratio` 0.40–0.55) are still set for quantities other
  than the ones computed. Trust `sentence_cv` and `paragraph_cv`, which are sound.
  Calibrating the rest needs two labelled corpora — human and AI-generated — since
  tuning against human text alone proves only that the tool stopped firing, not that
  it still separates.

## [1.1.1] — 2026-08-30

### Added

- `tests/test_burstiness.py`, covering `burstiness_check.py` for the first time
  (0% → 100%). Repository coverage went 37% → 82%.
- `tests/test_metadata.py`, guarding what a linter cannot: version agreement across
  the three manifests, the hook manifest's script path, installer completeness, and
  the executable bit on shebanged files.
- CI gates for lint and static analysis — `ruff check` (including C901 complexity),
  `ruff format --check`, `vulture`, `pylint`'s duplicate-code detector, and
  `shellcheck` — plus an 80% coverage floor and a `package` job that installs the
  built wheel *and* an editable install and runs both console scripts.
- `burstiness_check.py` is now shipped by `install.sh` and invoked from `SKILL.md`
  step 6. It had been present in the tree since 1.0.0 with no caller anywhere.
- `HUMANIZE_DEBUG=1` routes the PostToolUse hook's skip and scoring decisions to
  stderr. Previously a broken scorer and a clean file were indistinguishable: both
  produced no output and exited 0.

### Changed

- The package directory was renamed `scripts/` → `humanize_anti_slop/`, and the
  distribution renamed to `humanize-anti-slop`. `humanize` is a long-established
  unrelated package on PyPI, and a top-level `scripts` package in `site-packages` is
  a name collision waiting to happen. The *installed* layout under
  `~/.claude/skills/humanize/scripts/` is deliberately unchanged.
- `install.sh` moved to the repository root.
- `burstiness_check.analyse()` derives its writer-facing flags and its score from a
  single `CHECKS` table. It previously carried two copies of the same seven
  threshold comparisons, so a target changed in one block and not the other would
  have made the tool warn about something it did not score. Complexity went
  D (24) → B (6); behaviour verified identical across five profiles × seven texts.
- The PostToolUse hook caps the file size it will score at 2 MB. There had been no
  limit; a 9 MB file took 6.7 s, under the 30 s hook timeout, so it never errored —
  it just made Claude feel slow. That case now returns in 0.08 s.

### Fixed

- **The console scripts had never worked in any version.** The wheel shipped a
  top-level `scripts/` package while `[project.scripts]` named
  `humanize_score:main`, a module that existed under no name at all, so
  `humanize-score` and `burstiness-check` both died with `ModuleNotFoundError` on
  install. Nothing caught it because nothing in CI had ever installed the artefact
  it was building.
- `install.sh` survives the clone-into-place layout its own README documents.

## [1.1.0] — 2026-08-27

### Added

- Distribution as a Claude Code plugin: `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`, and `hooks/hooks.json`.
- `patterns/core.md`, holding the upstream pattern text with before/after examples,
  kept separate so `SKILL.md` stays loadable without dragging in every example.
- `tests/test_scoring.py` and GitHub Actions CI — the project's first tests.

### Changed

- Pattern catalogue re-synced with [blader/humanizer](https://github.com/blader/humanizer)
  v2.11.2: 35 base patterns, with the local extensions renumbered to 36–44.
- `agents/humanizer-reviewer.md` uses the documented `tools` field and resolves the
  scorer through a fallback chain instead of a fixed path.

### Fixed

- **The PostToolUse hook had never reached Claude.** It printed to stdout in a format
  Claude does not read, so write-time enforcement — the premise the project is built
  on — had silently done nothing since the hook was written. It now speaks the
  documented PostToolUse JSON contract via the scorer's `--hook` mode.

## [1.0.0] — 2026-04-27

### Added

- Initial framework: `SKILL.md` with the pattern catalogue and rewrite procedure,
  the `humanize_score.py` regex scorer, `burstiness_check.py`, the
  `humanizer-reviewer` subagent, the PostToolUse hook, and `install.sh`.
- `DETECTION_ROBUSTNESS.md`, recording what the score does and does not promise.

[Unreleased]: https://github.com/rlorenzo/humanize/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/rlorenzo/humanize/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/rlorenzo/humanize/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/rlorenzo/humanize/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/rlorenzo/humanize/releases/tag/v1.0.0
