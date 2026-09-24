#!/usr/bin/env python3
"""humanize_score — quantify AI-writing patterns in a text file.

Returns JSON: {"score": 0-100, "breakdown": {pattern_name: count}, "top_offenders": [...], "profile": "..."}.
Lower score = more human. Threshold convention:
   0-20   clean / human
   20-40  some AI residue, acceptable
   40-60  obvious patterns, needs editing
   60-100 heavy slop, rewrite

WHAT THE SCORE DOES NOT MEAN: it is a weighted rate of the 34 patterns in this
catalogue, per 100 words, not a raw count of hits. That is a claim about writing
quality, not a prediction about any AI detector. Pangram, which trains on Claude's
actual phrase distributions, detects at roughly 18% where detectors built on
perplexity and burstiness sit near 0.24%. Clearing this catalogue does not move that
number, and a score of 0 guarantees nothing except that these 34 patterns are
absent. See DETECTION_ROBUSTNESS.md.

Pure Python, zero dependencies. Requires Python 3.14+.

Usage:
   python humanize_score.py FILE.md
   python humanize_score.py --profile=academic FILE.md
   python humanize_score.py --json FILE.md > result.json
   echo '{"tool_input":{"file_path":"FILE.md"}}' | python humanize_score.py --hook

Profile detection (auto unless --profile= is given):
   manuscript/thesis/paper in name, *.tex -> academic
   README.md, docs/*, STAGE3/*            -> docs
   .git/COMMIT_EDITMSG, *.commit          -> commit
   else                                   -> blog
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---- Pattern definitions (34 patterns) ----------------------------------------

# Numbered as in blader/humanizer v3.0.0 (1-25, strongest first) plus this fork's
# extensions (26-34). Most are regexes, listed here; #7, #24, #31 and the title-case
# half of #20 are counted by functions, listed in HEURISTICS. #19 and #20 each have
# two parts with their own names and weights.
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
    # ---- A. Staging instead of stating (1-5) ----
    # 1 Not X but Y: the paired form, "it's not X, it's Y", the split-sentence form
    # ("This does not mean X. It means Y.") and the clipped tail (", no guessing.")
    Pattern(
        1,
        "not_x_but_y",
        _re(
            r"\b(it['‘’]s not (just|only|merely) about|not (just|only|merely) [^.,;!?\n]{1,40},?\s+but\b|"
            r"it['‘’]s not [^.,;!?\n]{1,40}[,;]\s*it['‘’]s\b|"
            # Split form: a negated sentence answered by one that opens with a
            # pronoun ("isn't a plan. It's noise." / "They're not X. They're Y.").
            r"(?:(?:is|are|was|were|does|do|did)(?:n['‘’]t| not)|"
            r"(?:it|they|that|this|we|you)['‘’](?:s|re) not)\b[^.!?\n]{1,80}[.!?]\s+"
            r"(?:It|They|That|This|He|She|We|You)(?:['‘’](?:s|re)\b|\s+(?:is|are|was|were|means|looks)\b)|"
            r", no \w+\.)"
        ),
        weight=1.2,
    ),
    # 2 One-line closers and dramatic fragments
    Pattern(
        2,
        "one_line_closers",
        _re(
            r"[.!?]\s+(No|Not|Just|Gone)\b[^.!?\n]{0,28}[.!?]\s+(No|Not|Just|Gone)\b|"
            r"\b(let that sink in|read that again|that(?: is|['‘’]s) the real win)\b"
        ),
        weight=1.0,
    ),
    # 3 Sayings that sound deep
    Pattern(
        3,
        "deep_sayings",
        _re(
            r"\b(at its core|in reality|what really matters|fundamentally|"
            r"the deeper issue|the heart of the matter|the real question|becomes? a trap|"
            r"(is|are|was|were|becomes?) the (language|currency|architecture) of|"
            r"not a tool but a mirror)\b"
        ),
        weight=1.2,
    ),
    # 4 Staged run-up before the point: announcements and staged candor
    Pattern(
        4,
        "staged_runup",
        _re(
            r"\b(let['‘’]s (dive in|explore|break this down|take a look)|"
            r"here['‘’]s what you need to know|now let['‘’]s look at|"
            r"without further ado|heads up|quick note|before I forget|"
            r"one thing that bit me)\b|"
            r"(?:^|[.!?]\s+|\n\s*)(Honestly\?|Look,|Here['‘’]s the thing|"
            r"The thing is,|Let['‘’]s be honest|Real talk)"
        ),
        weight=1.5,
    ),
    # 5 Arguing with no one: unraised objections and options no reader would weigh
    Pattern(
        5,
        "arguing_with_no_one",
        _re(
            r"\b(this isn['‘’]t (mainly|really) about|this is not (about|to say)|"
            r"I['‘’]m not (saying|arguing)|don['‘’]t get me wrong|"
            r"some might say[^.!?]{0,60}but|"
            r"a tempting (approach|option) would be|one might be tempted to|"
            r"an obvious approach would be|it would be easy to just|"
            r"you might think[^.!?]{0,60}but)\b"
        ),
        weight=1.0,
    ),
    # ---- B. Rhythm by rule (6-11) ----
    # 6 Forced triads (any "A, B, and C" — coarse; #31 catches paragraphs of them)
    Pattern(6, "forced_triads", _re(r"\b\w+,\s*\w+,?\s*and\s+\w+\b"), weight=0.5),
    # 7 Repeated sentence openings: counted by a function, see HEURISTICS
    # 8 Dashes: em dashes, and en dashes or double hyphens used as dashes. Unspaced
    # en dashes are left alone because they are number ranges (1990–2000).
    Pattern(8, "dashes", _re(r"—|\s–\s|\s--\s"), weight=0.4, profile_carveouts={"academic": 0.2}),
    # 9 Stacked qualifiers
    Pattern(
        9,
        "stacked_qualifiers",
        _re(
            r"\b(could potentially|might arguably|may possibly|might (potentially )?have some|"
            r"in some cases it may|it['‘’]s also possible)\b"
        ),
        weight=1.5,
    ),
    # 10 Hyphenated pairs. The hyphen is correct before a noun ("a high-quality
    # report"), so only the predicate position counts: the pair followed by
    # punctuation or the end of a line ("the report is high-quality."). A comma
    # before another hyphenated pair is a stacked modifier ("a high-quality,
    # data-driven report"), so it does not count.
    Pattern(
        10,
        "hyphenated_pairs",
        _re(
            r"\b(cross-functional|data-driven|client-facing|decision-making|"
            r"end-to-end|real-time|long-term|high-quality|well-known)\b"
            r"(?=[.;:!?)]|,(?!\s*\w+-\w)|[ \t]*$)",
            re.IGNORECASE | re.MULTILINE,
        ),
        weight=0.4,
        profile_carveouts={"academic": 0.6},
    ),
    # 11 Passive voice / subjectless fragments (a rough regex: a form of "be" + an -ed word)
    Pattern(
        11,
        "passive_voice",
        _re(r"\b(is|are|was|were|been|being)\s+\w+ed\b"),
        weight=0.3,
        profile_carveouts={"academic": 0.4},
    ),
    # ---- C. Inflation and borrowed authority (12-18) ----
    # 12 AI vocabulary. Figurative "gate", "robust" and "key" are omitted: a regex
    # cannot tell them from technical usage (feature gates, robust estimators).
    # "vibrant" belongs to #16, "emphasizing" and "showcasing" to #15, and
    # "meticulous review" to #33, so each is counted once.
    Pattern(
        12,
        "ai_vocabulary",
        _re(
            r"\b(delve|delves|delving|tapestry|landscape|testament|underscore[sd]?|"
            r"intricate|intricacies|interplay|garner[sed]*|pivotal|aligns? with|"
            r"foster(s|ed|ing)?|enduring|enhanc(e|ed|ing|es|ement)|valuable|"
            r"crucial|quietly|additionally|bolstered|deep dive|showcases?|showcased|"
            r"meticulously)\b"
        ),
        weight=1.0,
    ),
    # 13 Inflated significance, at all three scales: the phrase, the stock
    # "challenges and outlook" section, and the upbeat send-off.
    # Each phrase is counted by one pattern only. "serves as", "stands as" and
    # "represents a" belong to #18; "testament", "pivotal" and "landscape" to #12;
    # "underscoring" to #15.
    Pattern(
        13,
        "inflated_significance",
        _re(
            r"\b(reflects? (a )?broader|setting the stage for|indelible mark|"
            r"deeply rooted|focal point|key turning point|plays? a (key|vital|significant) role|"
            r"lasting legacy|"
            r"despite (its|these) (challenges|drawbacks|limitations)|"
            r"continues to thrive|future (outlook|prospects)|challenges? and legacy|"
            r"the future looks bright|exciting times (lie ahead|await)|"
            r"a step in the right direction|continues to evolve)\b"
        ),
        weight=1.5,
    ),
    # 14 Vague connection or association. Common in technical and academic prose
    # ("the risk associated with"), so it weighs little on its own.
    Pattern(
        14,
        "vague_connection",
        _re(
            r"\b(in association with|associated with|in connection with|"
            r"connected to|tied to|"
            # A literal hyperlink ("headline linked to your page") names the relationship.
            r"linked to(?!\s+(?:\w+\s+){0,2}(?:page|article|site|url|post|story|document)s?\b))\b"
        ),
        weight=0.5,
        profile_carveouts={"academic": 0.6, "commit": 0.0},
    ),
    # 15 Shallow -ing riders
    Pattern(
        15,
        "shallow_ing",
        _re(
            r"\b(highlighting|underscoring|emphasizing|ensuring|symbolizing|reflecting|"
            r"contributing to|cultivating|encompassing|showcasing)\s+\b"
        ),
        weight=1.2,
    ),
    # 16 Sales language
    Pattern(
        16,
        "sales_language",
        _re(
            r"\b(nestled|breathtaking|stunning|vibrant|boasts?|in the heart of|"
            r"renowned for|must[- ]visit|profound|groundbreaking|exemplifies|"
            r"a commitment to|diverse array|natural beauty|rich cultural)\b"
        ),
        weight=1.5,
    ),
    # 17 Borrowed authority: unnamed experts, and prestige lists standing in for
    # what was said
    Pattern(
        17,
        "borrowed_authority",
        _re(
            r"\b(experts? (argue|believe|say|note)|industry (reports|observers)|"
            r"observers (have )?(noted|cited)|some (critics|sources|publications)|"
            r"cited in|featured in|covered by|written by a leading expert|"
            r"active social media presence)\b"
        ),
        weight=1.5,
        profile_carveouts={"commit": 0.0},
    ),
    # 18 Avoiding is, are, and has
    Pattern(
        18,
        "copula_avoidance",
        _re(r"\b(serves? as|stands? as|functions? as|operates? as|represents? a|marks? a)\s+\w+"),
        weight=0.8,
    ),
    # ---- D. Formatting by rule (19-21) ----
    # 19 Bold as decoration, in two parts with different carve-outs.
    # Bold density: a legitimate emphasis mark, so the weight is low everywhere and
    # off in docs, where bolded terms and labels are house style.
    Pattern(
        19,
        "bold_decoration",
        _re(r"\*\*[^*]{1,40}\*\*"),
        weight=0.3,
        profile_carveouts={"docs": 0.0, "blog": 0.5},
    ),
    # Bold labels. The label is not the tell and is never flagged: a bulleted label
    # is a normal way to write a reference list. What is flagged is the label
    # restating itself ("**Performance:** Performance has improved"), which makes
    # the list look like structure while carrying one fact per bullet. The
    # backreference is what does the work; the (?i:) group lets "**User
    # Experience:** The user experience..." match while keeping the label itself
    # capitalised.
    Pattern(
        19,
        "restated_bold_labels",
        _re(
            r"^[ \t]*[-*][ \t]+\*\*(?P<label>[A-Z][^*\n]{1,40}?):?\*\*:?[ \t]+"
            r"(?i:(?:the |a |an )?(?P=label))\b",
            re.MULTILINE,
        ),
        weight=1.0,
    ),
    # 20 Decorative headings: title case is counted by a function (HEURISTICS),
    # emojis and arrows here
    Pattern(20, "decorative_emojis", _re("[\U0001f300-\U0001faff☀-➿]"), weight=1.5),
    # 21 Curly quotes
    Pattern(21, "curly_quotes", _re(r"[‘’“”]"), weight=0.5),
    # ---- E. Leftovers from the chat and the draft (22-25) ----
    # 22 Chatbot residue: greetings, praise, offers, and sign-offs
    Pattern(
        22,
        "chatbot_residue",
        _re(
            r"\b(I hope this helps|let me know if|here is (a|an|the)|of course!|"
            r"certainly!|you['‘’]re absolutely right|would you like (me to)?|happy to help|"
            r"want me to|should I continue|"
            r"great question!|excellent point|that['‘’]s a (great|fantastic|wonderful)|"
            r"brilliant observation)(?!\w)"
            # (?!\w), not \b: after "question!" a closing \b never matches.
        ),
        weight=2.0,
    ),
    # 23 Knowledge-limit disclaimers and guesses
    Pattern(
        23,
        "cutoff_disclaimer",
        _re(
            r"\b(as of my (last )?(training|knowledge)|while specific details (are|appear) (limited|scarce)|"
            r"based on (the )?(available|publicly available) information|"
            r"up to my (last )?training update|not publicly available|"
            r"not widely (documented|disclosed)|maintains a low profile|"
            r"keeps personal details private|in the (provided|available) sources)\b"
        ),
        weight=2.0,
    ),
    # 24 A heading repeated in the first sentence: counted by a function, see HEURISTICS
    # 25 Writing about the previous version (docs describing the old implementation)
    Pattern(
        25,
        "previous_version_writing",
        _re(
            r"\b(replac(es?|ed|ing) the (previous|old|earlier)|"
            r"the (previous|earlier) (approach|version|implementation|method)|"
            r"was (added|introduced|created) to replace)\b"
        ),
        weight=1.0,
        profile_carveouts={"commit": 0.0},
    ),
    # ---- Patterns 26-34 (this fork's extensions) ----
    # 26 Citation laundering
    Pattern(
        26,
        "citation_laundering",
        _re(
            r"\b(studies (have )?(show|shows|shown|suggest|reported|indicate)|"
            r"research (suggests|indicates|has shown)|the literature (reports|suggests))\b"
            # Cited if a year or a numeric reference follows in the same sentence.
            # "et al." is skipped over so its period does not end the sentence.
            r"(?!(?:[^.!?]|\bet al\.)*(?:\d{4}|\[\d+))"
        ),
        weight=2.0,
        profile_carveouts={"academic": 2.5, "commit": 0.0},
    ),
    # 27 Manuscript boilerplate
    Pattern(
        27,
        "manuscript_boilerplate",
        _re(
            r"\b(to the best of our knowledge|fills a critical gap|of paramount importance|"
            r"constitutes the first comprehensive|lays the foundation for)\b"
        ),
        weight=2.5,
        profile_carveouts={"academic": 3.0, "blog": 1.5, "docs": 1.0, "commit": 0.0},
    ),
    # 28 Tutorial-script scaffolding
    Pattern(
        28,
        "tutorial_scaffolding",
        _re(
            r"\b(let['‘’]s walk through|let['‘’]s start with|here['‘’]s the high-level|"
            r"after which we['‘’]ll|in this section[, ]+we will)\b"
        ),
        weight=1.2,
    ),
    # 29 Stat parade without effect size
    Pattern(
        29,
        "stat_parade",
        _re(r"\bp\s*[<>=]\s*0?\.\d+(?![^.]{0,80}(95\s*%|CI|Cohen|effect size|d\s*=))"),
        weight=1.5,
        profile_carveouts={"academic": 2.0, "blog": 0.5},
    ),
    # 30 Temporal hedge ladders
    Pattern(
        30,
        "temporal_hedges",
        _re(r"\b(currently|at present|at the time of writing|as of (now|today))\b"),
        weight=0.6,
    ),
    # 31 Polysyndetic tripleting: counted by a function, see HEURISTICS
    # 32 AI-flavoured commit verbs
    Pattern(
        32,
        "ai_commit_verbs",
        _re(
            r"^(feat|fix|chore|refactor|perf|docs|style|test)(\([^)]+\))?:\s+"
            r"(improves?|enhances?|refines?|leverages?|streamlines?|optimises?)\b",
            re.MULTILINE | re.IGNORECASE,
        ),
        weight=2.0,
        profile_carveouts={"commit": 3.0, "academic": 0.0, "docs": 0.0, "blog": 0.0},
    ),
    # 33 Methodology pseudo-precision
    Pattern(
        33,
        "methodology_pseudo",
        _re(
            r"\b(careful evaluation|rigorous analysis|comprehensive (study|review|analysis)|"
            r"thorough examination|exhaustive review|systematic investigation|"
            r"meticulous (review|analysis))\b"
        ),
        weight=2.0,
        profile_carveouts={"academic": 2.5, "commit": 0.0},
    ),
    # 34 Dissertation-grade hedging
    Pattern(
        34,
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

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+)$")
_WORD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(\w.*)$")
_WORD_RE = re.compile(r"[A-Za-z]+")


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Articles open sentences by grammar, not habit; three "The ..." in a row is normal.
_OPENING_EXEMPT = frozenset(["the", "a", "an"])
# Bullets, numbered items, headings, tables and quotes: formats, not prose.
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*+#|>]|\d+[.)])")


def count_repeated_openings(text: str) -> int:
    """#7. Runs of 3+ consecutive sentences in a paragraph that open with the same word.

    "She noted the door. She noted the lock. She filed both away." counts once.
    List items and headings are dropped before splitting, since a list of
    "Add ..." items, bulleted or numbered, is a format, not a habit.
    """
    count = 0
    for para in _PARAGRAPH_SPLIT_RE.split(text):
        prose = " ".join(line for line in para.splitlines() if not _LIST_LINE_RE.match(line))
        run, prev = 0, None
        for sentence in _SENTENCE_SPLIT_RE.split(prose.strip()):
            words = _WORD_RE.findall(sentence)
            first = words[0].lower() if words else None
            if first in _OPENING_EXEMPT:
                first = None
            run = run + 1 if first is not None and first == prev else 1
            prev = first
            if run == 3:
                count += 1
    return count


def count_title_case_headings(text: str) -> int:
    """#20. Lines starting with #/##/###/etc where >50% of content words are capitalised."""
    count = 0
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if not m:
            continue
        words = [w for w in _WORD_RE.findall(m.group(1)) if len(w) > 2]
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
    return {w.removesuffix("s") for w in _WORD_RE.findall(text.lower()) if len(w) > 3}


def _standalone_stub(lines: list[str], j: int) -> str | None:
    """The line at ``j`` if it is a short non-heading line in a paragraph of its own.

    "Its own paragraph" means a blank line or end of file follows it: a short
    first line of a wrapped paragraph is ordinary prose, not a stub.
    """
    line = lines[j].strip()
    if line.startswith("#") or len(line.split()) > 8:
        return None
    if j + 1 < len(lines) and lines[j + 1].strip():
        return None
    return line


def _restates_heading(heading: str, line: str) -> bool:
    """Whether at least half the heading's content words reappear in ``line``.

    A heading with no content words at all restates nothing, and returning False
    there keeps the caller from dividing by zero.
    """
    heading_words = _content_words(heading)
    if not heading_words:
        return False
    return len(heading_words & _content_words(line)) / len(heading_words) >= 0.5


def count_fragmented_headers(text: str) -> int:
    """#24. Heading followed by a short standalone line that restates the heading.

    Three conditions, all required: the line after the heading is short (<= 8
    words), it stands alone as its own paragraph, and it restates the heading
    rather than saying something new. The first two are checked by
    ``_standalone_stub``, the third by ``_restates_heading``.

    The restatement test is what makes this pattern #24 rather than "heading
    followed by a short line": at least half the heading's content words have to
    reappear. A heading followed by a genuinely short sentence that introduces
    new material is normal prose and must not fire.
    """
    count = 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        heading = _WORD_HEADING_RE.match(line)
        if not heading:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        stub = _standalone_stub(lines, j)
        if stub is not None and _restates_heading(heading.group(1), stub):
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
    """#31. Count paragraphs with 3+ 'X, Y, and Z' patterns.

    Items may carry a determiner, so "the code, the tests, and the docs" counts.
    An earlier version matched only bare single words, which missed most real
    triplets in ordinary prose -- the failure mode of a detector nobody had run
    against text with a known answer.
    """
    count = 0
    for para in _PARAGRAPH_SPLIT_RE.split(text):
        triplets = sum(
            1
            for m in _TRIPLET_RE.findall(para)
            if m[1].split()[-1].lower() not in _PARENTHETICAL_ADVERBS
        )
        if triplets >= 3:
            count += 1
    return count


# Patterns whose counts come from a function rather than a regex: (id, name, count, weight).
HEURISTICS = (
    (7, "repeated_openings", count_repeated_openings, 0.5),
    (20, "title_case_headings", count_title_case_headings, 0.7),
    (24, "repeated_heading", count_fragmented_headers, 1.0),
    (31, "polysyndetic_tripleting", count_polysyndetic_tripleting, 1.5),
)

# Upper bound (exclusive) of each score band, lowest first.
VERDICT_BANDS = ((20, "clean"), (40, "minor_residue"), (60, "needs_editing"))


# ---- Profile detection --------------------------------------------------------


# A whole word in the filename, so "my_thesis.md" and "paper-draft.md" match but
# "hypothesis.md", "wallpaper.md" and "paperwork.md" do not.
_ACADEMIC_NAME_RE = re.compile(r"(?:^|[^a-z])(?:manuscript|thesis|paper)s?(?![a-z])")


def detect_profile(path: Path) -> str:
    name = path.name.lower()
    dirs = {part.lower() for part in path.parts[:-1]}
    if name == "commit_editmsg" or name.endswith(".commit"):
        return "commit"
    if _ACADEMIC_NAME_RE.search(name) or name.endswith(".tex"):
        return "academic"
    if name == "readme.md" or dirs & {"docs", "stage3"}:
        return "docs"
    return "blog"


# ---- Scoring ------------------------------------------------------------------


def score_text(text: str, profile: str = "blog") -> dict:
    """Compute the AI-slop score and breakdown."""
    breakdown: dict[str, int] = {}
    weighted: dict[str, float] = {}
    total_words = max(len(text.split()), 1)

    for p in PATTERNS:
        hits = len(p.regex.findall(text))
        if hits:
            breakdown[p.name] = hits
            weighted[p.name] = hits * p.adjusted_weight(profile)

    for _pid, name, count, weight in HEURISTICS:
        hits = count(text)
        if hits:
            breakdown[name] = hits
            weighted[name] = hits * weight

    # Normalise: weighted score per 100 words, capped at 100
    raw = sum(weighted.values()) / total_words * 100
    score = min(100.0, raw * 5.0)  # 5× scaling so 20 weighted hits / 100 words = 100

    top_offenders = sorted(weighted.items(), key=lambda kv: kv[1], reverse=True)[:5]
    verdict = next((name for bound, name in VERDICT_BANDS if score < bound), "heavy_slop")

    return {
        "score": round(score, 1),
        "profile": profile,
        "word_count": total_words,
        "breakdown": breakdown,
        "weighted": {k: round(v, 2) for k, v in weighted.items()},
        "top_offenders": [{"pattern": k, "weighted": round(v, 2)} for k, v in top_offenders],
        "verdict": verdict,
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
# statement about 34 known patterns and nothing else -- see DETECTION_ROBUSTNESS.md
# for why a classifier trained on Claude's phrase distributions is a different
# question entirely. Deliberately absent from --json: machines do not misread a
# verdict, people do.
SCORE_SCOPE = "34 known patterns, not detector evasion — see DETECTION_ROBUSTNESS.md"


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
