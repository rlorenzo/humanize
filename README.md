# humanize

> Anti-slop framework for Claude Code. Skill + scorer + agent + hook + always-on rule. Strips 44 AI-writing patterns. Domain-aware (academic / docs / blog / commit). Extends [blader/humanizer](https://github.com/blader/humanizer) (synced at v2.11.2).

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Compatible: Claude Code](https://img.shields.io/badge/Compatible-Claude%20Code-blue)](https://claude.ai/claude-code)
[![Compatible: OpenCode](https://img.shields.io/badge/Compatible-OpenCode-green)](https://opencode.ai)

## What this is

`humanize` is six tools that work together so AI slop has no clear path into your prose:

| Layer | Component | Triggered by |
|---|---|---|
| 1 | Always-on rule file at `~/.claude/rules/10-anti-slop.md` | Loaded into every session |
| 2 | `/humanize` slash command | Manual invocation |
| 3 | PostToolUse hook on Write/Edit | Fires automatically when Claude writes prose |
| 4 | `humanizer-reviewer` subagent | Called by `/ship`, `/review-paper`, on demand |
| 5 | `humanize_score.py` CLI | Pattern scoring; exits non-zero above threshold |
| 6 | `burstiness_check.py` CLI | Statistical signatures the pattern list cannot see |

Plus four **domain profiles** (academic, docs, blog, commit) with their own carve-outs. Em-dashes are fine in scientific prose. Passive voice is correct in IMRaD methods. Vague attributions are flagged hard in academic but allowed (with citation) in docs. The framework adapts.

## What it detects (44 patterns)

Inherits 35 from [blader/humanizer](https://github.com/blader/humanizer) v2.11.2 (significance inflation, em-dash overuse, rule of three, sycophancy, shadowboxing, fake alternatives, …) — summaries in [SKILL.md](SKILL.md), full text with before/after in [patterns/core.md](patterns/core.md). Upstream's no-fabrication rule and false-positive guardrails are adopted too.

New extensions (#36-44):

| # | Pattern | Why it matters |
|---|---|---|
| 36 | Citation laundering ("studies show" with no citation) | Academic-killer |
| 37 | Manuscript boilerplate ("To the best of our knowledge…") | Generic paper opener |
| 38 | Tutorial-script scaffolding ("Let's walk through…") | Doc tutorial-script feel |
| 39 | Stat parade without effect size | Frequentist hedging |
| 40 | Temporal hedge ladders | Stacked time-disclaimers |
| 41 | Polysyndetic tripleting | Stronger rule-of-three |
| 42 | AI-flavoured commit verbs ("improves", "enhances") | Commit-specific |
| 43 | Methodology pseudo-precision ("careful", "rigorous", "comprehensive") | Self-praise without specifics |
| 44 | Dissertation-grade hedging where stance is required | Academic-only |

## Install (Claude Code plugin — recommended)

```
/plugin marketplace add kimhons/humanize
/plugin install humanize@humanize
```

The plugin registers the skill, the `humanizer-reviewer` subagent, and the PostToolUse
hook automatically. To add the optional always-on rule, run `install.sh` too.

## Install (Claude Code, file-based)

```bash
# Clone
git clone https://github.com/kimhons/humanize.git ~/.claude/skills/humanize
# Activate the agent
cp ~/.claude/skills/humanize/agents/humanizer-reviewer.md ~/.claude/agents/
# Install skill + rule, then wire the hook into settings.json (copy the
# PostToolUse entry from hooks/hooks.json, adjusting the script path)
bash ~/.claude/skills/humanize/install.sh
```

For OpenCode users:

```bash
git clone https://github.com/kimhons/humanize.git ~/.config/opencode/skills/humanize
```

## Install (skill only, no git history)

```bash
git clone --depth 1 https://github.com/kimhons/humanize.git /tmp/humanize &&
  mkdir -p ~/.claude/skills/humanize &&
  cp -r /tmp/humanize/SKILL.md /tmp/humanize/patterns ~/.claude/skills/humanize/ &&
  rm -rf /tmp/humanize
```

## Usage

### Manual invocation

```
/humanize [paste your text]
```

```
/humanize --profile=academic [paste]
```

```
/humanize --voice=path/to/sample-of-my-writing.md [paste]
```

### CLI scoring

```bash
$ python ~/.claude/skills/humanize/scripts/humanize_score.py STAGE3/MANUSCRIPT.md
humanize_score: 38.4/100  (minor_residue)
profile:        academic  (3,420 words)
scope:          44 known patterns, not detector evasion — see DETECTION_ROBUSTNESS.md
top offenders:
  - methodology_pseudo                weighted=12.50
  - significance_inflation            weighted=7.50
  - citation_laundering               weighted=5.00
  - hyphenated_pairs                  weighted=2.40
  - excessive_hedging                 weighted=1.50
```

```bash
# JSON output for CI / pipelines
python humanize_score.py --json --profile=docs README.md
```

### What the score does not mean

The number counts the 44 patterns in this catalogue. That is a claim about writing
quality, and nothing more.

It is **not** a prediction about any AI detector. [Pangram](https://www.pangram.com/),
which trains on the phrase distributions of specific models rather than on
perplexity, detects at roughly 18% where perplexity-and-burstiness detectors sit
near 0.24% — and clearing this catalogue does not move that number, because the two
are measuring different things. A score of 0 means these 44 patterns are absent. It
does not mean text will pass a classifier, and nothing here should be relied on as
though it did.

[DETECTION_ROBUSTNESS.md](DETECTION_ROBUSTNESS.md) has the detector landscape and the
argument in full. It is worth reading before trusting any score, including this one.

The defensible reason to use this tool is the one the always-on rule argues for:
future sessions read past output and infer "this is how we write here", so an
untreated pattern becomes house style. Catching it at write-time is cheaper than
catching it at review.

### Statistical check

`burstiness_check.py` measures what the pattern list structurally cannot: a draft
can score clean on all 44 patterns and still read as machine-written because every
sentence is the same length.

```bash
$ python ~/.claude/skills/humanize/scripts/burstiness_check.py STAGE3/MANUSCRIPT.md
sentence_cv:     0.61   (want >= 0.55, or >= 0.50 with --profile=esl)
paragraph_cv:    0.44   (want >= 0.40)
```

Read those two metrics. **Ignore its `signature_score` and `verdict`** — two of the
five underlying checks are calibrated against different quantities than the ones
computed, so every document over 50 words currently reports `heavy-AI-signature`
regardless of quality. The module docstring has the measurements and the fix.

### Installing the scorers as commands

Optional, if you want them on your `PATH` outside Claude Code:

```bash
pip install .          # or: uv tool install .
humanize-score --profile=academic MANUSCRIPT.md
burstiness-check MANUSCRIPT.md
```

Both scorers are pure Python with **zero dependencies** and run on 3.9+.

### Hooked into commit time

The hook auto-fires after any `.md` / `.tex` Edit/Write and warns when the score exceeds 60. Tune with `HUMANIZE_THRESHOLD=70` in your shell.

It stays silent otherwise, including when it fails — a scoring bug must never block a write. That silence hid a real bug once, so set `HUMANIZE_DEBUG=1` to see on stderr what it decided and why:

```bash
$ HUMANIZE_DEBUG=1 echo '{"tool_input":{"file_path":"draft.md"}}' | ~/.claude/hooks/humanize-post-write.sh
[humanize:debug] draft.md scored 12.0 at or under threshold 60
```

Files over 2 MB are skipped. The hook runs on every write, and a 9 MB file took 6.7 s to score; a prose draft is not 2 MB.

### Subagent for deep review

```
> Have the humanizer-reviewer audit this manuscript
```

The agent loads the file, runs the scorer, identifies top-5 offenders by line, rewrites them, self-audits, and reports back.

## Voice calibration

Drop a sample of your own writing and the skill will match your rhythm, vocabulary, and quirks rather than producing a generic "clean" rewrite:

```
/humanize --voice=blog-archive/2024-09-thoughts.md
[paste AI-generated draft]
```

## Domain profiles — at a glance

| Pattern | academic | docs | blog | commit |
|---|---|---|---|---|
| Em-dash overuse | OK in moderation | flag | flag | flag |
| Passive voice | OK in IMRaD methods | flag | flag in active voice | flag |
| Hedging | report-grade OK | flag | flag hard | flag hard |
| Stat parade | flag hard | flag | n/a | n/a |
| Citation laundering | flag hard | flag | flag | n/a |
| Manuscript boilerplate | flag hard | n/a | n/a | n/a |
| AI commit verbs | n/a | n/a | n/a | flag hard |

## Why six layers?

The blader/humanizer skill is excellent but **passive only** — Claude only humanises when you type `/humanizer`. Every other write goes through unfiltered. AI slop compounds across sessions because future sessions index "this is how we write here."

`humanize` closes the loop:

- The **rule** in `~/.claude/CLAUDE.md` puts the directive into every session prompt.
- The **hook** catches AI patterns at write-time, before they're committed.
- The **scorer** gives a number you can grep in CI and pre-commit.
- The **subagent** does the heavy review on demand.
- The **skill** does the manual cleanup with voice calibration.
- The **profiles** stop us from "fixing" passive voice in a methods section or em-dashes in an academic paper.

## Acknowledgments

This work would not exist without:

- [blader/humanizer](https://github.com/blader/humanizer) — MIT, the foundation
- [Wikipedia: Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) — pattern catalog
- [WikiProject AI Cleanup](https://en.wikipedia.org/wiki/Wikipedia:WikiProject_AI_Cleanup) — maintains the source

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Releases are tagged `vMAJOR.MINOR.PATCH`; a
marketplace or clone install can pin to a tag.

## License

MIT — see [LICENSE](LICENSE).

## Author

Kimal Honour Djam ([@kimhons](https://github.com/kimhons))
