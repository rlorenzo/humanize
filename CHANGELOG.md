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

## [2.0.1] — 2026-09-02

### Fixed

- **The plugin would not install.** `plugin.json` declared
  `"hooks": "./hooks/hooks.json"`, which is the path Claude Code already loads on
  its own. The manifest's `hooks` key is for *additional* hook files, so naming the
  standard one loaded it twice and the install failed with `Duplicate hooks file
  detected`. Removed the key; the hook is picked up automatically from
  `hooks/hooks.json` exactly as before. Nothing about the hook's behaviour changes.

  This affected every version that shipped the key, but v2.0.0 was the first
  release anyone could install from this fork's own marketplace, so it is where it
  surfaced. A test now asserts the manifest never names the auto-loaded path again.

## [2.0.0] — 2026-09-02

> **This release cannot be installed as a Claude Code plugin; use 2.0.1.** The
> manifest declared the hooks file the host already loads on its own. Nothing else
> differs between the two, so everything below applies to 2.0.1.

### Removed — BREAKING

- **`signature_score` and `verdict` are gone from `burstiness_check`, from both the
  JSON and the human-readable output.** The composite was tested against two
  labelled corpora and only one of its five inputs cleared the bar on both. Three
  failed it; the fourth, `paragraph_cv`, neither passed nor failed — neither
  corpus could measure it, which is a different result and is marked separately
  below. Separation AUC, folded so 0.5 is chance, against a 0.65 bar written down
  before any result was seen:

  | metric | HC3 | RAID | RAID chat-only | |
  |---|---|---|---|---|
  | `sentence_cv` | 0.764 | 0.663 | 0.720 | kept |
  | `lexical_diversity` | 0.672 | 0.575 | 0.582 | retired — reverses sign |
  | `function_word_ratio` | 0.600 | 0.553 | 0.576 | retired |
  | `subordinate_density` | 0.518 | 0.514 | 0.590 | retired |
  | `paragraph_cv` | — | — | — | retired — untestable, not failed |

  HC3: 4,330 length-matched documents over five domains. RAID-train: 4,867
  human/AI pairs sharing a source text, over eight domains and eleven generators,
  unattacked rows only. `lexical_diversity` does not merely fall below the bar — it
  changes sign, scoring higher for human text on HC3 and lower on RAID.
  `paragraph_cv` was untestable on both: 85% of HC3 answers are a single
  paragraph, and RAID's extraction leaves only 9.8% of its human documents
  multi-paragraph against 61.5% of the AI ones, so the metric is measurable in 11%
  of the corpus against a floor of 25%.

- **The bands for the four retired metrics are removed, not merely unscored.**
  Keeping them would have kept the original bug: the `lexical_diversity` band of
  [0.40, 0.65] was inherited from whole-document TTR while the metric is MATTR-50,
  which runs 0.78-0.85 on ordinary English prose, so it flagged every document it
  was shown. This repository's own `README.md`, `DETECTION_ROBUSTNESS.md` and
  `CHANGELOG.md` now report no flags; all three previously reported
  `AI-uniform` or `heavy-AI-signature`.

- **`--profile` no longer accepts `academic`, `blog` or `docs` on
  `burstiness-check`.** All three resolved identically to `default`: three CLI
  names promising a tuning that was never implemented. `default` and `esl` remain,
  and `esl` is the only one that changes anything (`sentence_cv` 0.50 against
  0.55). Note that `humanize-score` has profiles of the same names and those are
  unaffected — they carry real per-pattern carve-outs and were never the same
  feature.

### Changed — BREAKING

- **Exit code semantics.** Was `1 if signature_score > --threshold else 0`; now
  `1` if the flags raised are ones the selected `--fail-on` gates on, else `0` —
  so `any` (the default) exits `1` on any flag, `cv` only on a burstiness-CV
  flag, and `never` always exits `0`. Non-zero still means
  "something to look at", so a caller checking only for non-zero keeps working, but
  a pipeline that reasoned about the score's magnitude will not. `2` still means a
  bad path.
- **`--json` shape.** `signature_score` and `verdict` are gone. `profile`,
  `metrics`, `targets` and `flags` remain, `flags` is now the headline output, and
  a new `unbanded` array names the four metrics that are reported without any
  threshold asserted. `targets` now contains only `sentence_cv`.
- **`sentence_cv` and `paragraph_cv` report `null` below two sentences or two
  paragraphs**, where they previously reported `0.0`. A coefficient of variation
  over fewer than two values is undefined, not zero, and 0.0 read as perfect
  uniformity — which flagged every one-sentence file as AI-uniform pacing. This is
  the same degeneracy that made `paragraph_cv` untestable on both corpora.

### Changed — install paths

- **Every install path now points at this fork.** The marketplace-add line, both
  `git clone` lines and the `--depth 1` clone named the upstream repository, so
  anyone following this README installed upstream's code rather than this one.
  Both plugin manifests' owner blocks and the three `pyproject.toml` URLs move
  with them.
- **The marketplace is renamed `humanize-rlorenzo`.** The plugin itself is still
  `humanize`, so `/humanize` and the skill paths are unchanged, but the install
  command embeds the marketplace id and becomes
  `/plugin install humanize@humanize-rlorenzo`. Anyone who added the marketplace
  under its old id needs to re-add it; `/plugin marketplace update` cannot follow
  a rename.
- **`pyproject.toml` gains `maintainers`.** `authors` still names Kimal Honour
  Djam, which is what PEP 621 means by the field, but it also carried the contact
  address, so publishing this fork would have routed its bug mail to someone who
  did not ship it. README's `## Author` was doing the same double duty and is now
  `## Credits`, naming the original author, blader/humanizer and the fork
  maintainer separately. The `LICENSE` copyright line and the `SKILL.md` credit
  for patterns 36-44 are untouched: forking transfers maintenance, not authorship.

### Changed — stated purpose

- **`DETECTION_ROBUSTNESS.md` no longer states its purpose as passing detection
  checks at submission.** The line contradicted the one directly beneath it, the
  ethical frame in §5 and the honest limits in §8, all of which say the opposite,
  and it contradicted `README.md`. It now says what the rest of the document
  already said: the added layers measure statistical signatures the pattern list
  cannot see, so that genuinely human writing is not misread as machine-written.
- **The document no longer justifies itself on one person's dissertation.** Six
  passages named the upstream author, including a §5 paragraph resting the case on
  his data and patent number and an L8 example manifest that was his CV. These
  now describe the case the framework is built for, and say where it stops
  applying.

### Deprecated

- **`--threshold` on `burstiness-check`** is accepted, ignored, and warns on stderr
  for one minor version. It is kept rather than removed because an unrecognised
  argument exits 2, which would break any caller still passing it. It has no
  meaning now that there is no score to compare against.

### Added

- **A per-generator separation table** in `scripts/calibration/results/raid_phase1.json`,
  each generator length-matched against human text independently. `sentence_cv` runs
  0.550-0.617 against the weakest base models and 0.777-0.809 against chatgpt,
  mistral-chat, gpt4 and cohere-chat — the regime this tool is actually pointed at,
  and the reason the pooled RAID figure understates it. Not a clean split, though:
  cohere is a base model and reaches 0.713.
- **`--fail-on=any|cv|never`** replaces `--threshold` as the gate. `any` (default)
  reproduces today's "non-zero means look at this"; `cv` gates only on the
  burstiness-CV family; `never` gives pure reporting.
- **`normalise()`**, one input-cleaning step feeding all five metrics. The sentence
  and paragraph splitters previously stripped code fences, headings and table rules
  while the word tokenizer saw the raw document, so the two halves of the module
  measured different strings. It also handles HTML tags, comments and entity
  escapes, matches tags by name so `<YOUR_API_KEY>` and `List<Integer>` survive,
  and hands back punctuation adjacent to a stripped URL so a sentence ending in a
  link keeps the full stop that ends it.
- Calibration spikes and their committed evidence under `scripts/calibration/`.
- `tests/test_heuristic_counters.py` — the first tests for the four heuristic
  counters (#11, #17, #29, #41), the patterns a regex cannot express. Coverage of
  `humanize_score.py` went 69% → 80%, repository total 83% → 88%, and the CI floor
  rose from 80% to 85%.
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

[Unreleased]: https://github.com/rlorenzo/humanize/compare/v2.0.1...HEAD
[2.0.1]: https://github.com/rlorenzo/humanize/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/rlorenzo/humanize/compare/v1.2.0...v2.0.0
[1.2.0]: https://github.com/rlorenzo/humanize/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/rlorenzo/humanize/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/rlorenzo/humanize/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/rlorenzo/humanize/releases/tag/v1.0.0
