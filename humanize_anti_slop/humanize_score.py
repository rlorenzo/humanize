#!/usr/bin/env python3
"""humanize_score — quantify AI-writing patterns in a text file.

Returns JSON: {"score": 0-100, "breakdown": {pattern_id: count}, "top_offenders": [...], "profile": "..."}.
Lower score = more human. Threshold convention:
   0-20   clean / human
   20-40  some AI residue, acceptable
   40-60  obvious patterns, needs editing
   60-100 heavy slop, rewrite

WHAT THE SCORE DOES NOT MEAN: it counts the 44 patterns in this catalogue. That is
a claim about writing quality, not a prediction about any AI detector. Pangram, which
trains on Claude's actual phrase distributions, detects at roughly 18% where the
perplexity-and-burstiness detectors sit near 0.24%, and clearing this catalogue does
not move that number. A score of 0 is not a guarantee of anything except that these
44 patterns are absent. See DETECTION_ROBUSTNESS.md.

Pure Python, zero dependencies. Compatible with Python 3.9+.

Usage:
   python humanize_score.py FILE.md
   python humanize_score.py --profile=academic FILE.md
   python humanize_score.py --json FILE.md > result.json
   echo '{"tool_input":{"file_path":"FILE.md"}}' | python humanize_score.py --hook

Profile detection (auto unless --profile= is given):
   MANUSCRIPT*.md, *thesis*.md, *.tex     -> academic
   README.md, docs/*, STAGE3/*.md         -> docs
   .git/COMMIT_EDITMSG, *.commit          -> commit
   else                                   -> blog
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---- Pattern definitions (44 patterns) ----------------------------------------

# Each pattern: (id, name, regex, weight, profile_carveouts).
# profile_carveouts maps profile -> multiplier (1.0 default; 0.0 disables; 0.5 reduces).


@dataclass
class Pattern:
    pid: int
    name: str
    regex: re.Pattern[str]
    weight: float = 1.0
    profile_carveouts: dict[str, float] = field(default_factory=dict)

    def adjusted_weight(self, profile: str) -> float:
        return self.weight * self.profile_carveouts.get(profile, 1.0)


def _re(p: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(p, flags)


PATTERNS: list[Pattern] = [
    # 1 Significance inflation
    Pattern(
        1,
        "significance_inflation",
        _re(
            r"\b(stands? as|serves? as|is a testament|marking a pivotal moment|"
            r"underscoring (its )?(importance|significance)|reflects? (a )?broader|"
            r"setting the stage for|indelible mark|deeply rooted|evolving landscape|"
            r"focal point|key turning point|represents? a shift)\b"
        ),
        weight=1.5,
    ),
    # 2 Notability name-dropping
    Pattern(
        2,
        "notability_name_dropping",
        _re(
            r"\b(cited in|featured in|covered by|written by a leading expert|active social media presence)\b"
        ),
        weight=1.0,
    ),
    # 3 Superficial -ing analyses
    Pattern(
        3,
        "superficial_ing",
        _re(
            r"\b(highlighting|underscoring|emphasizing|symbolizing|reflecting|"
            r"contributing to|cultivating|fostering|encompassing|showcasing) \b"
        ),
        weight=1.2,
    ),
    # 4 Promotional language
    Pattern(
        4,
        "promotional",
        _re(
            r"\b(nestled|breathtaking|stunning|vibrant|boasts?|in the heart of|"
            r"renowned for|must[- ]visit|profound|groundbreaking|exemplifies|"
            r"a commitment to)\b"
        ),
        weight=1.5,
    ),
    # 5 Vague attribution
    Pattern(
        5,
        "vague_attribution",
        _re(
            r"\b(experts? (argue|believe|say|note)|industry (reports|observers)|"
            r"observers (have )?(noted|cited)|some (critics|sources|publications))\b"
        ),
        weight=1.5,
        profile_carveouts={"commit": 0.0},
    ),
    # 6 Formulaic challenges section
    Pattern(
        6,
        "formulaic_challenges",
        _re(
            r"\b(despite (its|these) (challenges|drawbacks|limitations)|"
            r"continues to thrive|future (outlook|prospects)|challenges? and legacy)\b"
        ),
        weight=1.5,
    ),
    # 7 AI vocabulary
    # Figurative "gate/gated" is omitted: indistinguishable from technical usage
    # (feature gates, gated APIs) in regex.
    Pattern(
        7,
        "ai_vocabulary",
        _re(
            r"\b(delve|delves|delving|tapestry|landscape|testament|underscore[sd]?|"
            r"intricate|intricacies|interplay|garner[sed]*|pivotal|aligns? with|"
            r"foster(s|ed|ing)?|enduring|enhanc(e|ed|ing|es|ement)|valuable insights?|"
            r"crucial|quietly)\b"
        ),
        weight=1.0,
    ),
    # 8 Copula avoidance
    Pattern(
        8,
        "copula_avoidance",
        _re(r"\b(serves? as|stands? as|functions? as|represents? a|marks? a)\s+\w+"),
        weight=0.8,
    ),
    # 9 Negative parallelisms
    Pattern(
        9,
        "negative_parallelism",
        _re(
            r"\b(it[''']s not (just|only|merely) about|not (just|only|merely) X[, ]+but|, no \w+\.)"
        ),
        weight=1.2,
    ),
    # 10 Rule of three (any "A, B, and C" — coarse; we count occurrences per paragraph in scoring)
    Pattern(10, "rule_of_three", _re(r"\b\w+,\s*\w+,?\s*and\s+\w+\b"), weight=0.5),
    # 11 Synonym cycling — heuristic, not regex; flagged if same noun has 3+ synonym variants in one paragraph
    Pattern(11, "synonym_cycling", _re(r"$^"), weight=0.0),  # placeholder; computed separately
    # 12 False ranges
    Pattern(
        12,
        "false_ranges",
        _re(
            r"\bfrom\s+(?:the\s+)?\w+\s+to\s+(?:the\s+)?\w+(?:\s*,\s*from\s+(?:the\s+)?\w+\s+to\s+(?:the\s+)?\w+)+"
        ),
        weight=1.5,
    ),
    # 13 Passive voice / subjectless fragments — heuristic
    Pattern(
        13,
        "passive_voice",
        _re(r"\b(is|are|was|were|been|being)\s+\w+ed\b"),
        weight=0.3,
        profile_carveouts={"academic": 0.4},
    ),
    # 14 Em-dash overuse
    Pattern(14, "em_dash_overuse", _re(r"—"), weight=0.4, profile_carveouts={"academic": 0.2}),
    # 15 Boldface overuse — count **bold** phrases per paragraph
    Pattern(15, "boldface_overuse", _re(r"\*\*[^*]{1,40}\*\*"), weight=0.3),
    # 16 Inline-header lists
    Pattern(
        16,
        "inline_header_lists",
        _re(r"^\s*[-*]\s*\*\*[A-Z][^*]+\*\*[: ]", re.MULTILINE),
        weight=1.0,
    ),
    # 17 Title Case Headings (heuristic: heading line where >50% of words start uppercase)
    Pattern(17, "title_case_headings", _re(r"$^"), weight=0.0),  # placeholder
    # 18 Emojis as bullets / decorations
    Pattern(18, "emojis", _re("[\U0001f300-\U0001faff☀-➿]"), weight=1.5),
    # 19 Curly quotes
    Pattern(19, "curly_quotes", _re(r"[‘’“”]"), weight=0.5),
    # 20 Chatbot artifacts
    Pattern(
        20,
        "chatbot_artifacts",
        _re(
            r"\b(I hope this helps|let me know if|here is (a|an|the)|of course!|"
            r"certainly!|you[''']re absolutely right|would you like (me to)?|happy to help)\b"
        ),
        weight=2.0,
    ),
    # 21 Knowledge-cutoff disclaimers
    Pattern(
        21,
        "cutoff_disclaimer",
        _re(
            r"\b(as of my (last )?(training|knowledge)|while specific details (are|appear) (limited|scarce)|"
            r"based on (the )?(available|publicly available) information|"
            r"up to my (last )?training update)\b"
        ),
        weight=2.0,
    ),
    # 22 Sycophantic / servile tone
    Pattern(
        22,
        "sycophantic",
        _re(
            r"\b(great question!|excellent point|that[''']s a (great|fantastic|wonderful)|brilliant observation)\b"
        ),
        weight=2.0,
    ),
    # 23 Filler phrases
    Pattern(
        23,
        "filler_phrases",
        _re(
            r"\b(in order to|due to the fact that|at this point in time|"
            r"in the event that|has the ability to|it is important to note that|"
            r"with regards to|in light of the fact that)\b"
        ),
        weight=1.0,
    ),
    # 24 Excessive hedging
    Pattern(
        24,
        "excessive_hedging",
        _re(
            r"\b(could potentially possibly|might (potentially )?have some|"
            r"may possibly|it could be argued that|one might suggest that)\b"
        ),
        weight=1.5,
    ),
    # 25 Generic positive conclusions
    Pattern(
        25,
        "generic_conclusion",
        _re(
            r"\b(the future looks bright|exciting times (lie ahead|await)|"
            r"a step in the right direction|continues to evolve)\b"
        ),
        weight=1.5,
    ),
    # 26 Hyphenated word-pair overuse
    Pattern(
        26,
        "hyphenated_pairs",
        _re(
            r"\b(cross-functional|data-driven|client-facing|decision-making|"
            r"end-to-end|real-time|long-term|high-quality|well-known)\b"
        ),
        weight=0.4,
        profile_carveouts={"academic": 0.6},
    ),
    # 27 Persuasive authority tropes
    Pattern(
        27,
        "persuasive_authority",
        _re(
            r"\b(at its core|in reality|what really matters|fundamentally|"
            r"the deeper issue|the heart of the matter|the real question)\b"
        ),
        weight=1.2,
    ),
    # 28 Signposting announcements
    Pattern(
        28,
        "signposting",
        _re(
            r"\b(let[''']s (dive in|explore|break this down|walk through|take a look)|"
            r"here[''']s what you need to know|now let[''']s look at|"
            r"without further ado|heads up|quick note|before I forget|"
            r"one thing that bit me)\b"
        ),
        weight=1.5,
    ),
    # 29 Fragmented headers — heuristic, computed separately
    Pattern(29, "fragmented_headers", _re(r"$^"), weight=0.0),  # placeholder
    # ---- Patterns 30-35 (upstream) ----
    # 30 Previous-version writing (docs describing the old implementation, not current behavior)
    Pattern(
        30,
        "previous_version_writing",
        _re(
            r"\b(replac(es?|ed|ing) the (previous|old|earlier)|"
            r"the (previous|earlier) (approach|version|implementation|method)|"
            r"was (added|introduced|created) to replace)\b"
        ),
        weight=1.0,
        profile_carveouts={"commit": 0.0},
    ),
    # 31 Forced punchlines (rows of dramatic short fragments)
    Pattern(
        31,
        "forced_punchlines",
        _re(r"[.!?]\s+(No|Not|Just|Gone)\b[^.!?\n]{0,28}[.!?]\s+(No|Not|Just|Gone)\b"),
        weight=1.0,
    ),
    # 32 Formulaic sayings (pseudo-profound aphorisms)
    Pattern(
        32,
        "formulaic_sayings",
        _re(
            r"\b(becomes? a trap|(is|are|was|were|becomes?) the (language|currency|architecture) of|"
            r"not a tool but a mirror)\b"
        ),
        weight=1.0,
    ),
    # 33 Fake candid openers (staged candor at sentence start)
    Pattern(
        33,
        "fake_candid_openers",
        _re(
            r"(?:^|[.!?]\s+|\n\s*)(Honestly\?|Look,|Here[''']s the thing|"
            r"The thing is,|Let[''']s be honest|Real talk)"
        ),
        weight=1.2,
    ),
    # 34 Shadowboxing (answering objections no one raised)
    Pattern(
        34,
        "shadowboxing",
        _re(
            r"\b(this isn[''']t (mainly|really) about|this is not (about|to say)|"
            r"I[''']m not (saying|arguing)|don[''']t get me wrong|"
            r"some might say[^.!?]{0,60}but)\b"
        ),
        weight=1.0,
    ),
    # 35 Fake alternatives (rejecting options no reader would consider)
    Pattern(
        35,
        "fake_alternatives",
        _re(
            r"\b(a tempting (approach|option) would be|one might be tempted to|"
            r"an obvious approach would be|it would be easy to just|"
            r"you might think[^.!?]{0,60}but)\b"
        ),
        weight=1.0,
    ),
    # ---- Patterns 36-44 (this fork's extensions) ----
    # 36 Citation laundering
    Pattern(
        36,
        "citation_laundering",
        _re(
            r"\b(studies (have )?(show|shows|shown|suggest|reported|indicate)|"
            r"research (suggests|indicates|has shown)|the literature (reports|suggests))\b(?![^.]*\d{4})"
        ),
        weight=2.0,
        profile_carveouts={"academic": 2.5, "commit": 0.0},
    ),
    # 37 Manuscript boilerplate
    Pattern(
        37,
        "manuscript_boilerplate",
        _re(
            r"\b(to the best of our knowledge|fills a critical gap|"
            r"represents a significant advance|of paramount importance|"
            r"constitutes the first comprehensive|lays the foundation for)\b"
        ),
        weight=2.5,
        profile_carveouts={"academic": 3.0, "blog": 1.5, "docs": 1.0, "commit": 0.0},
    ),
    # 38 Tutorial-script scaffolding
    Pattern(
        38,
        "tutorial_scaffolding",
        _re(
            r"\b(let[''']s walk through|let[''']s start with|here[''']s the high-level|"
            r"after which we[''']ll|in this section[, ]+we will)\b"
        ),
        weight=1.2,
    ),
    # 39 Stat parade without effect size
    Pattern(
        39,
        "stat_parade",
        _re(r"\bp\s*[<>=]\s*0?\.\d+(?![^.]{0,80}(95\s*%|CI|Cohen|effect size|d\s*=))"),
        weight=1.5,
        profile_carveouts={"academic": 2.0, "blog": 0.5},
    ),
    # 40 Temporal hedge ladders
    Pattern(
        40,
        "temporal_hedges",
        _re(r"\b(currently|at present|at the time of writing|as of (now|today))\b"),
        weight=0.6,
    ),
    # 41 Polysyndetic tripleting — count "X, Y, and Z" patterns per paragraph
    Pattern(41, "polysyndetic_tripleting", _re(r"$^"), weight=0.0),  # computed separately
    # 42 AI-flavoured commit verbs
    Pattern(
        42,
        "ai_commit_verbs",
        _re(
            r"^(feat|fix|chore|refactor|perf|docs|style|test)(\([^)]+\))?:\s+"
            r"(improves?|enhances?|refines?|leverages?|streamlines?|optimises?)\b",
            re.MULTILINE | re.IGNORECASE,
        ),
        weight=2.0,
        profile_carveouts={"commit": 3.0, "academic": 0.0, "docs": 0.0, "blog": 0.0},
    ),
    # 43 Methodology pseudo-precision
    Pattern(
        43,
        "methodology_pseudo",
        _re(
            r"\b(careful evaluation|rigorous analysis|comprehensive (study|review|analysis)|"
            r"thorough examination|exhaustive review|systematic investigation|"
            r"meticulous (review|analysis))\b"
        ),
        weight=2.0,
        profile_carveouts={"academic": 2.5, "commit": 0.0},
    ),
    # 44 Dissertation-grade hedging
    Pattern(
        44,
        "dissertation_hedging",
        _re(
            r"\b(it can be argued that|one might (consider|suggest|argue)|"
            r"some (would|might) (suggest|argue)|it could be (said|argued))\b"
        ),
        weight=1.8,
        profile_carveouts={"academic": 2.5, "commit": 0.0},
    ),
]


# ---- Heuristic computations for placeholder patterns --------------------------

# Synonym groups for synonym-cycling detection.
SYNONYM_GROUPS: list[set[str]] = [
    {"hero", "protagonist", "main character", "central figure", "individual"},
    {"company", "organization", "organisation", "firm", "enterprise", "business"},
    {"author", "writer", "scribe", "novelist"},
    {"event", "occurrence", "incident", "happening"},
    {"problem", "issue", "challenge", "difficulty", "obstacle"},
]


def count_synonym_cycling(text: str) -> int:
    """#11. Heuristic — for each paragraph, count repeated noun-meaning across 3+ variants.

    Coarse: detects when 3+ candidate synonyms (e.g. hero / protagonist / main character)
    co-occur in the same paragraph. Reports paragraphs that match.
    """
    count = 0
    for para in re.split(r"\n\s*\n", text):
        for group in SYNONYM_GROUPS:
            hits = sum(1 for term in group if re.search(rf"\b{re.escape(term)}\b", para, re.I))
            if hits >= 3:
                count += 1
    return count


def count_title_case_headings(text: str) -> int:
    """#17. Lines starting with #/##/###/etc where >50% of content words are capitalised."""
    count = 0
    for line in text.splitlines():
        m = re.match(r"^\s*#{1,6}\s+(.+)$", line)
        if not m:
            continue
        content = m.group(1).strip()
        words = [w for w in re.findall(r"[A-Za-z]+", content) if len(w) > 2]
        if len(words) < 2:
            continue
        capitalised = sum(1 for w in words if w[0].isupper())
        if capitalised / len(words) > 0.5:
            count += 1
    return count


def _content_words(text: str) -> set[str]:
    """Lowercased words of 4+ characters, singularised crudely, for overlap tests.

    removesuffix, not rstrip: rstrip("s") strips every trailing s, turning "boss"
    into "bo" and "class" into "cla", which is over-stripping rather than
    singularising and gives short tokens more chances to collide.
    """
    return {w.removesuffix("s") for w in re.findall(r"[A-Za-z]+", text.lower()) if len(w) > 3}


def count_fragmented_headers(text: str) -> int:
    """#29. Heading followed by a short standalone line that restates the heading.

    Three conditions, all required: the line after the heading is short (<= 8
    words), it stands alone as its own paragraph, and it restates the heading
    rather than saying something new.

    The restatement test is what makes this pattern #29 rather than "heading
    followed by a short line": at least half the heading's content words have to
    reappear. A heading followed by a genuinely short sentence that introduces
    new material is normal prose and must not fire.
    """
    count = 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        heading = re.match(r"^\s*#{1,6}\s+(\w.*)$", line)
        if not heading:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        next_line = lines[j].strip()
        if next_line.startswith("#") or len(next_line.split()) > 8:
            continue
        # The line must be its own paragraph: blank line or end of file after it.
        if j + 1 < len(lines) and lines[j + 1].strip():
            continue
        heading_words = _content_words(heading.group(1))
        if not heading_words:
            continue
        echoed = heading_words & _content_words(next_line)
        if len(echoed) / len(heading_words) >= 0.5:
            count += 1
    return count


# Parenthetical and conjunctive adverbs. As the middle element of "X, Y, and Z"
# these signal an aside ("it was done, however, and then we moved on"), not a
# triplet, and the surface punctuation is identical either way.
_PARENTHETICAL_ADVERBS = frozenset(
    [
        "however",
        "though",
        "therefore",
        "thus",
        "moreover",
        "indeed",
        "again",
        "instead",
        "meanwhile",
        "nevertheless",
        "nonetheless",
        "furthermore",
        "also",
        "perhaps",
        "finally",
        "yes",
        "no",
        "then",
        "besides",
        "otherwise",
    ]
)

# An item in a triplet: a bare noun, optionally preceded by a determiner or
# possessive. "the tests" and "their docs" are items; arbitrary clauses are not,
# which is what keeps "the code, then we shipped and moved on" from matching.
_TRIPLET_ITEM = r"(?:(?:the|a|an|its|his|her|their|our|your|my|this|that|these|those)\s+)?[\w']+"

_TRIPLET_RE = re.compile(
    rf"\b({_TRIPLET_ITEM}),\s*({_TRIPLET_ITEM}),?\s+and\s+({_TRIPLET_ITEM})\b",
    re.I,
)


def count_polysyndetic_tripleting(text: str) -> int:
    """#41. Count paragraphs with 3+ 'X, Y, and Z' patterns.

    Items may carry a determiner, so "the code, the tests, and the docs" counts.
    An earlier version matched only bare single words, which missed most real
    triplets in ordinary prose -- the failure mode of a detector nobody had run
    against text with a known answer.
    """
    count = 0
    for para in re.split(r"\n\s*\n", text):
        triplets = [
            m
            for m in _TRIPLET_RE.findall(para)
            if m[1].split()[-1].lower() not in _PARENTHETICAL_ADVERBS
        ]
        if len(triplets) >= 3:
            count += 1
    return count


# ---- Profile detection --------------------------------------------------------


def detect_profile(path: Path) -> str:
    name = path.name.lower()
    full = str(path).lower().replace("\\", "/")
    if name == "commit_editmsg" or name.endswith(".commit"):
        return "commit"
    if any(x in name for x in ("manuscript", "thesis", "paper")) or name.endswith(".tex"):
        return "academic"
    if name == "readme.md" or "/docs/" in full or "/stage3/" in full:
        return "docs"
    return "blog"


# ---- Scoring ------------------------------------------------------------------


def score_text(text: str, profile: str = "blog") -> dict:
    """Compute the AI-slop score and breakdown."""
    breakdown: dict[str, int] = {}
    weighted: dict[str, float] = {}
    total_words = max(len(text.split()), 1)

    for p in PATTERNS:
        if p.weight == 0:
            continue
        hits = len(p.regex.findall(text))
        if hits:
            breakdown[p.name] = hits
            weighted[p.name] = hits * p.adjusted_weight(profile)

    # Heuristic computations
    sc = count_synonym_cycling(text)
    if sc:
        breakdown["synonym_cycling"] = sc
        weighted["synonym_cycling"] = sc * 1.0

    th = count_title_case_headings(text)
    if th:
        breakdown["title_case_headings"] = th
        weighted["title_case_headings"] = th * 0.7

    fh = count_fragmented_headers(text)
    if fh:
        breakdown["fragmented_headers"] = fh
        weighted["fragmented_headers"] = fh * 1.0

    pt = count_polysyndetic_tripleting(text)
    if pt:
        breakdown["polysyndetic_tripleting"] = pt
        weighted["polysyndetic_tripleting"] = pt * 1.5

    # Normalise: weighted score per 100 words, capped at 100
    raw = sum(weighted.values()) / total_words * 100
    score = min(100.0, raw * 5.0)  # 5× scaling so 20 weighted hits / 100 words = 100

    top_offenders = sorted(weighted.items(), key=lambda kv: -kv[1])[:5]

    return {
        "score": round(score, 1),
        "profile": profile,
        "word_count": total_words,
        "breakdown": breakdown,
        "weighted": {k: round(v, 2) for k, v in weighted.items()},
        "top_offenders": [{"pattern": k, "weighted": round(v, 2)} for k, v in top_offenders],
        "verdict": (
            "clean"
            if score < 20
            else "minor_residue"
            if score < 40
            else "needs_editing"
            if score < 60
            else "heavy_slop"
        ),
    }


# ---- PostToolUse hook mode ----------------------------------------------------

PROSE_SUFFIXES = {".md", ".tex", ".rst", ".txt"}
SKIP_PATH_PARTS = (".claude/", "/node_modules/", "/.git/")

# The hook runs on every Write/Edit, so scoring cost is paid interactively.
# Measured: ~180 ms of interpreter startup regardless of size, 760 ms at 1 MB,
# 6.7 s at 9 MB. A prose draft is not 2 MB; something that big is generated or
# vendored, and making every write wait on it is worse than not scoring it.
MAX_HOOK_BYTES = 2_000_000


def debug(message: str) -> None:
    """Diagnostics for hook mode, off unless HUMANIZE_DEBUG is set.

    Goes to stderr so it can never corrupt the JSON contract on stdout.
    """
    if os.environ.get("HUMANIZE_DEBUG"):
        print(f"[humanize:debug] {message}", file=sys.stderr)


def hook_skip_reason(path: Path, file_path: str) -> str | None:
    """Why this payload should not be scored, or None to go ahead."""
    if not file_path:
        return "payload has no tool_input.file_path"
    if path.suffix.lower() not in PROSE_SUFFIXES:
        return f"suffix {path.suffix!r} is not prose {sorted(PROSE_SUFFIXES)}"
    if any(part in str(path).replace("\\", "/") for part in SKIP_PATH_PARTS):
        return f"path matches an excluded part {SKIP_PATH_PARTS}"
    if not path.is_file():
        return "path is not an existing file"
    size = path.stat().st_size
    if size > MAX_HOOK_BYTES:
        return f"{size} bytes exceeds MAX_HOOK_BYTES ({MAX_HOOK_BYTES})"
    return None


def run_hook() -> int:
    """Read a Claude Code PostToolUse JSON payload from stdin, score the written
    file if it is prose, and emit hookSpecificOutput.additionalContext JSON when
    the score exceeds HUMANIZE_THRESHOLD (default 60).

    Always exits 0: a scoring problem must never block a write. That silence is
    deliberate but it hid a real bug once -- the hook emitted a format Claude
    never read and went unnoticed for months -- so set HUMANIZE_DEBUG=1 to see
    on stderr what it decided and why.
    """
    try:
        file_path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
        path = Path(file_path)
        skip = hook_skip_reason(path, file_path)
        if skip:
            debug(f"skipped {file_path!r}: {skip}")
            return 0
        try:
            threshold = float(os.environ.get("HUMANIZE_THRESHOLD", "60"))
        except ValueError:
            threshold = 60.0
        text = path.read_text(encoding="utf-8", errors="replace")
        result = score_text(text, profile=detect_profile(path))
        if result["score"] <= threshold:
            debug(f"{file_path} scored {result['score']} at or under threshold {threshold:g}")
            return 0
        offenders = ", ".join(
            f"{o['pattern']} (weighted={o['weighted']})" for o in result["top_offenders"][:3]
        )
        context = (
            f"[humanize] {file_path} scored {result['score']}/100 ({result['verdict']}), "
            f"above threshold {threshold:g}. Top offenders: {offenders}. "
            f"Consider rewriting with the humanize skill (/humanize {file_path})."
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": context,
                    }
                }
            )
        )
    except Exception as exc:  # noqa: BLE001 - a scoring bug must never block a write
        debug(f"{type(exc).__name__}: {exc}")
    return 0


# ---- CLI ----------------------------------------------------------------------


# Printed under every human-readable score. A number this tool calls "clean" is a
# statement about 44 known patterns and nothing else -- see DETECTION_ROBUSTNESS.md
# for why a classifier trained on Claude's phrase distributions is a different
# question entirely. Deliberately absent from --json: machines do not misread a
# verdict, people do.
SCORE_SCOPE = "44 known patterns, not detector evasion — see DETECTION_ROBUSTNESS.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a text file on AI-writing patterns. Lower score = more human."
    )
    parser.add_argument("path", nargs="?", help="Path to text file (.md, .tex, .txt, ...)")
    parser.add_argument(
        "--profile",
        choices=["academic", "docs", "blog", "commit", "auto"],
        default="auto",
        help="Domain profile (default: auto-detect from filename).",
    )
    parser.add_argument("--json", action="store_true", help="Emit raw JSON.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=60.0,
        help="Exit non-zero if score exceeds threshold (default 60).",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="PostToolUse hook mode: read tool JSON from stdin, warn via hookSpecificOutput.",
    )
    args = parser.parse_args(argv)

    if args.hook:
        return run_hook()
    if not args.path:
        parser.error("path is required unless --hook is given")

    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2

    text = path.read_text(encoding="utf-8", errors="replace")
    profile = args.profile if args.profile != "auto" else detect_profile(path)
    result = score_text(text, profile=profile)
    result["path"] = str(path)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"humanize_score: {result['score']}/100  ({result['verdict']})")
        print(f"profile:        {profile}  ({result['word_count']} words)")
        print(f"scope:          {SCORE_SCOPE}")
        print("top offenders:")
        for off in result["top_offenders"]:
            print(f"  - {off['pattern']:32s} weighted={off['weighted']}")

    return 1 if result["score"] > args.threshold else 0


if __name__ == "__main__":
    sys.exit(main())
