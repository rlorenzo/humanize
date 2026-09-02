#!/usr/bin/env python3
"""burstiness_check — report statistical signatures of a text file.

Measures burstiness CV, paragraph-length variation, lexical diversity (MATTR-50),
function-word ratio, and a subordinate-clause-depth proxy. **Diagnostics, not a
verdict.** Only `sentence_cv` carries a threshold; the rest are reported as
numbers with nothing asserted about them.

Pure Python, zero dependencies. Compatible with Python 3.9+.

Target (the only one):
   sentence_cv          >= 0.55 (>= 0.50 for ESL profile, an untested allowance
                        rather than a second calibrated bar -- see resolve_targets)

WHY THERE IS ONLY ONE. The composite `signature_score` was measured against two
labelled corpora and retired, because four of its five inputs did not separate
AI text from human text. Separation AUC, folded to [0.5, 1] so 0.5 is chance:

   metric                HC3     RAID    RAID chat-only
   sentence_cv           0.764   0.663   0.720            kept
   lexical_diversity     0.672   0.575   0.582            retired
   function_word_ratio   0.600   0.553   0.576            retired
   subordinate_density   0.518   0.514   0.590            retired
   paragraph_cv          untestable on either corpus      retired

The bar -- 0.65 -- was written down before any result was looked at. Three
findings do not survive summarising:

  * lexical_diversity does not merely fall below the bar, it changes sign: human
    text scores higher on HC3 and lower on RAID. A metric whose direction depends
    on the corpus has no band worth fitting.
  * paragraph_cv could not be tested by either corpus. 85% of HC3 answers are one
    paragraph, and only 9.8% of RAID's human documents run to more than one
    against 61.5% of the AI ones -- so the metric is measurable in 11% of that
    corpus against a 25% floor. The imbalance alone hands it a pooled AUC of
    0.758, which is a fact about how the two classes were extracted rather than
    about how either writes.
  * sentence_cv separates best against instruction-tuned generators, but the
    split is untidy: the top four are cohere-chat 0.809, gpt4 0.789, mistral-chat
    0.785 and chatgpt 0.777, while the weakest are base models at 0.550-0.617 --
    and cohere, a base model at 0.713, beats llama-chat and mpt-chat. Chat tuning
    moves the metric; it does not sort the generators cleanly.

Keeping the retired bands would have kept the original bug: lexical_diversity's
[0.40, 0.65] was inherited from whole-document TTR, while the metric is MATTR-50,
which runs 0.78-0.85 on ordinary prose and so flagged every document ever shown.

Corpora, method and the full per-generator table: CHANGELOG 2.0.0 and
`scripts/calibration/results/raid_phase1.json`.

Exit codes: 0 clean, 1 flagged (see --fail-on), 2 bad path.

Usage:
   python burstiness_check.py FILE.md
   python burstiness_check.py --profile=esl FILE.md
   python burstiness_check.py --fail-on=cv FILE.md
   python burstiness_check.py --json FILE.md
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

# Function words: the closed-class inventory (determiners, pronouns, prepositions,
# conjunctions, auxiliaries, negators, degree adverbs and pro-forms). Grouped by
# class so the list stays auditable; frozenset(...) dedups the overlaps, which are
# real -- "that" is a determiner, a relative pronoun and a subordinator.
#
# Built as a closed-class inventory rather than borrowed from a stopword list:
# stopword lists drop function words that carry no retrieval value ("not", "no")
# and add content words that happen to be frequent. The distinction matters here
# because the ratio is meant to measure grammatical scaffolding, not topicality.
#
# Contractions are listed explicitly. The tokenizer is [A-Za-z']+, so "don't" and
# "it's" arrive as single tokens; without these entries the most frequent function
# words in natural prose count against the ratio instead of toward it.
# fmt: off
_DETERMINERS = [
    "a", "an", "the", "this", "that", "these", "those", "each", "every",
    "either", "neither", "another", "other", "others", "such", "some", "any",
    "no", "all", "both", "half", "several", "enough", "much", "many", "more",
    "most", "less", "least", "few", "fewer", "little", "own", "same", "one",
    "ones", "what", "whatever", "which", "whichever", "whose",
]

_PRONOUNS = [
    "i", "me", "my", "mine", "myself",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "it", "its", "itself",
    "we", "us", "our", "ours", "ourselves",
    "they", "them", "their", "theirs", "themselves", "themself",
    "who", "whom", "whoever", "whomever", "oneself",
    "someone", "somebody", "something", "somewhere",
    "anyone", "anybody", "anything", "anywhere",
    "everyone", "everybody", "everything", "everywhere",
    "nobody", "nothing", "nowhere", "none",
]

_PREPOSITIONS = [
    "about", "above", "across", "after", "against", "along", "alongside",
    "amid", "amidst", "among", "amongst", "around", "as", "at", "before",
    "behind", "below", "beneath", "beside", "besides", "between", "beyond",
    "but", "by", "concerning", "despite", "down", "during", "except", "for",
    "from", "in", "inside", "into", "like", "near", "of", "off", "on", "onto",
    "opposite", "out", "outside", "over", "past", "per", "regarding", "round",
    "since", "than", "through", "throughout", "till", "to", "toward",
    "towards", "under", "underneath", "unlike", "until", "unto", "up", "upon",
    "versus", "via", "with", "within", "without",
]

_CONJUNCTIONS = [
    "and", "or", "nor", "yet", "so",
    "although", "though", "because", "if", "once", "unless", "whenever",
    "where", "whereas", "wherever", "whether", "while", "whilst", "lest",
    "provided", "when", "why", "how",
]

_AUXILIARIES = [
    "be", "am", "is", "are", "was", "were", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did", "doing", "done",
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "ought", "need", "dare", "used",
]

_NEGATORS_AND_DEGREE = [
    "not", "never", "very", "too", "quite", "rather", "just", "only", "even",
    "also", "still", "already", "again", "ever", "almost", "nearly", "hardly",
    "scarcely", "barely", "else", "otherwise", "perhaps", "maybe", "there",
    "here", "then", "now", "thus", "however", "therefore", "instead", "indeed",
]

_CONTRACTIONS = [
    "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't",
    "hasn't", "haven't", "hadn't", "won't", "wouldn't", "shan't", "shouldn't",
    "can't", "cannot", "couldn't", "mustn't", "mightn't", "needn't", "ain't",
    "i'm", "i've", "i'll", "i'd",
    "you're", "you've", "you'll", "you'd",
    "he's", "he'll", "he'd",
    "she's", "she'll", "she'd",
    "it's", "it'll", "it'd",
    "we're", "we've", "we'll", "we'd",
    "they're", "they've", "they'll", "they'd",
    "that's", "that'll", "that'd", "there's", "there'll", "there'd",
    "here's", "who's", "what's", "where's", "when's", "how's", "let's",
]

# fmt: on

FUNCTION_WORDS = frozenset(
    _DETERMINERS
    + _PRONOUNS
    + _PREPOSITIONS
    + _CONJUNCTIONS
    + _AUXILIARIES
    + _NEGATORS_AND_DEGREE
    + _CONTRACTIONS
)


# HTML tags are stripped by name rather than by shape. The shape rule that came
# first, </?[A-Za-z][^<>]*>, also ate "<YOUR_API_KEY>" and the "<Integer>" out of
# "List<Integer>" -- placeholders and generics are ordinary content in the kind of
# technical prose this tool is pointed at, and deleting them silently biases both
# vocabulary metrics. An unknown angle-bracket construct is now left alone: text
# that survives is recoverable, text that is deleted is not.
_HTML_TAGS = (  # noqa: SIM905 -- a word list reads as columns, not 100 one-item lines
    "a abbr address article aside b blockquote body br button caption cite code col "
    "colgroup dd details div dl dt em fieldset figcaption figure footer form h1 h2 h3 "
    "h4 h5 h6 head header hr html i iframe img input ins kbd label legend li link main "
    "mark meta nav ol optgroup option p param picture pre q s samp script section "
    "select small source span strong style sub summary sup table tbody td textarea "
    "tfoot th thead time title tr u ul var video wbr"
).split()
# Block-level tags among them, and the two that stand for a line break. A block
# tag is a boundary; deleting it outright is what an empty replacement did, and
# "<p>One.</p><p>Two.</p>" with no whitespace between the tags collapsed to
# "One.Two." -- one sentence where the same prose gives two. That is the same
# silent corruption of sentence_cv as a URL swallowing the full stop that ends
# its sentence, on the one metric that survived both corpora. Block tags now
# become a blank line, <br> and <hr> a newline, and only inline tags vanish.
_BLOCK_TAGS = (  # noqa: SIM905 -- a word list reads as columns, not one item per line
    "address article aside blockquote body caption col colgroup dd details div dl dt "
    "fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 head header html iframe "
    "legend li main nav ol optgroup option p param picture pre script section select "
    "source style summary table tbody td textarea tfoot th thead title tr ul video"
).split()
_LINE_TAGS = ("br", "hr")


def _tag_pattern(names: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    """Matcher for a set of tag names.

    The attribute part skips over quoted values, so a literal ">" inside one does
    not end the match early and strand the rest of the attribute as visible text.
    """
    return re.compile(
        r"</?(?:" + "|".join(names) + r")(?:\s(?:\"[^\"]*\"|'[^']*'|[^<>\"'])*)?/?>",
        re.IGNORECASE,
    )


_BLOCK_TAG_RE = _tag_pattern(_BLOCK_TAGS)
_LINE_TAG_RE = _tag_pattern(_LINE_TAGS)
_TAG_RE = _tag_pattern(_HTML_TAGS)

# script and style hold raw text, not prose, and the tag passes above only remove
# the tags -- the body survived as words. "var x=1;function f(){return 42;}" was
# reaching the tokenizer as var/x/function/f/return: a run of unique non-words,
# which is the same thing code fences are stripped for. It inflates
# lexical_diversity and depresses function_word_ratio, on documents whose prose
# never contained any of it.
#
# An unclosed element consumes to end of text, which is what a browser does with
# one: everything after an unopened </script> is script until the parser finds a
# closer. Leaving _BLOCK_TAGS to catch it is not enough -- that pass removes the
# tag and leaves the body, which is the whole defect.
_RAW_TEXT_RE = re.compile(
    r"<(script|style)(?:\s(?:\"[^\"]*\"|'[^']*'|[^<>\"'])*)?>(?:[\s\S]*?</\1\s*>|[\s\S]*\Z)",
    re.IGNORECASE,
)

# Inline links and images, tolerating one level of nesting in the anchor text --
# "[this [nested] thing](url)" used to match nothing at all, leaking the brackets
# and the whole URL into the word stream.
_LINK_RE = re.compile(
    r"!?\[([^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*)\]"
    # The target also tolerates one level of nesting: a Wikipedia
    # "..._(disambiguation)" URL ends at the first ")" otherwise, leaving a stray
    # bracket behind in the prose.
    r"\((?:[^()]|\([^()]*\))*\)"
)

# Bare and autolinked URLs, and the targets of reference-style definitions, none
# of which _LINK_RE can see.
_URL_RE = re.compile(r"<?\bhttps?://[^\s<>\]]+>?")

# Punctuation that ends a sentence rather than belonging to the URL before it.
# Without this, "see https://example.com/page. It has more." lost the full stop
# with the URL and became one sentence instead of two -- a silent, systematic
# corruption of sentence_cv, which is the strongest metric measured so far.
_URL_TAIL = ".,;:!?'\")]>"

# The tokenizer every word-based metric reads. Module-level so the calibration
# spike imports it instead of re-declaring the pattern: a change here cannot
# then silently invalidate a committed AUC.
WORD_RE = re.compile(r"[A-Za-z']+")


def _drop_url(match: re.Match[str]) -> str:
    """Remove the URL, hand back the punctuation that was only adjacent to it."""
    url = match.group(0)
    tail = ""
    while url and url[-1] in _URL_TAIL:
        tail = url[-1] + tail
        url = url[:-1]
    # An autolink wrapper is part of the URL, not the sentence.
    return tail[1:] if url.startswith("<") and tail.startswith(">") else tail


def normalise(text: str) -> str:
    """Reduce a document to the prose the metrics are meant to measure.

    Every metric reads the output of this function, so all five measure the same
    string. They did not always: split_sentences stripped code fences, headings
    and table rules while the word tokenizer in analyse saw the raw document, so
    lexical_diversity and function_word_ratio were computed over markup the CV
    metrics had already discarded. Calibrating bands on one string and applying
    them to another is how a corpus-fitted threshold drifts on real files.

    What goes, and why the order is load-bearing: code and URLs are runs of unique
    non-words that inflate lexical diversity and depress the function-word ratio;
    headings and table rules are labels, not sentences. Unescaping runs before the
    comment and tag passes so markup written escaped -- HC3 carries a lot of it --
    arrives as markup, and so "don&#39;t" becomes the one contraction token
    FUNCTION_WORDS knows rather than two non-words. Comments in particular have to
    come after it: stripping them first left "&lt;!-- note --&gt;" untouched, and
    the unescape then turned it into a live comment nothing removed, putting its
    words into the token stream.

    Not idempotent, and it cannot be: "&amp;lt;" unescapes to "&lt;" on one pass
    and to "<" on the next, so a second application always has more to do. That
    is why analyse normalises exactly once and hands the result to the private
    splitters, and why the public split_sentences and split_paragraphs -- which
    do normalise, for callers holding a raw document -- are not on that path.
    """
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    text = html.unescape(text)
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = _RAW_TEXT_RE.sub("", text)
    text = _BLOCK_TAG_RE.sub("\n\n", text)
    text = _LINE_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(_drop_url, text)
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|?[-: |]+\|?\s*$", "", text, flags=re.MULTILINE)
    return text


def _sentences_from(text: str) -> list[str]:
    """Split text that has already been through normalise()."""
    # Sentence boundary: . ! ? followed by space + uppercase, or end of paragraph
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n\s*\n", text)
    return [s for s in (p.strip() for p in parts) if len(s.split()) >= 2]


def _paragraphs_from(text: str) -> list[str]:
    """Split text that has already been through normalise()."""
    parts = re.split(r"\n\s*\n", text)
    return [s for s in (p.strip() for p in parts) if len(s.split()) >= 5]


def split_sentences(text: str) -> list[str]:
    """Split a raw document into sentences, discarding markup first."""
    return _sentences_from(normalise(text))


def split_paragraphs(text: str) -> list[str]:
    """Split a raw document into paragraphs, discarding markup first."""
    return _paragraphs_from(normalise(text))


def coefficient_of_variation(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    m = statistics.mean(values)
    if m == 0:
        return 0.0
    sd = statistics.pstdev(values)
    return sd / m


MATTR_WINDOW = 50


def lexical_diversity(words: list[str]) -> float | None:
    """Moving-average type-token ratio over a 50-word window (MATTR-50).

    Returns None for text shorter than the window, rather than a number that is
    not comparable to one. The previous implementation fell back to whole-document
    TTR below 50 words: TTR falls as a document grows and MATTR does not, so the
    two branches returned different quantities under one name, checked against one
    band. apply_checks skips a metric that is None.
    """
    if len(words) < MATTR_WINDOW:
        return None
    # Cap the work at ~100 windows on long documents; short ones step by 1.
    stride = max(1, (len(words) - MATTR_WINDOW) // 100)
    ttrs = [
        len(set(words[i : i + MATTR_WINDOW])) / MATTR_WINDOW
        for i in range(0, len(words) - MATTR_WINDOW + 1, stride)
    ]
    return statistics.mean(ttrs)


def function_word_ratio(words: list[str]) -> float:
    if not words:
        return 0.0
    fw = sum(1 for w in words if w.lower() in FUNCTION_WORDS)
    return fw / len(words)


def subordinate_density(text: str, word_count: int) -> float:
    if word_count == 0:
        return 0.0
    commas = text.count(",")
    semicolons = text.count(";")
    return (commas + semicolons) / word_count


def shannon_word_entropy(words: list[str]) -> float:
    """Shannon entropy of the word distribution. Higher = more vocabulary."""
    if not words:
        return 0.0
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    total = len(words)
    return -sum((c / total) * math.log2(c / total) for c in freq.values())


# ---- Threshold checks ---------------------------------------------------------

# Only a metric that survived corpus validation gets a threshold. The other four
# are measured and reported, but nothing is asserted about them -- see the module
# docstring for the AUC numbers that retired them. Keeping their old bands would
# have kept the original bug: lexical_diversity's [0.40, 0.65] fires on every
# document of ordinary English prose, which runs 0.78-0.85.


@dataclass(frozen=True)
class Check:
    """A lower bound on one metric.

    Lower bounds only: the upper-bound half went with the metrics that needed it,
    and a branch no CHECKS entry can reach is a branch nobody tests. Reintroduce
    it with the check that needs it.
    """

    metric: str  # key into the measured-values dict
    target: str  # key into the profile-resolved targets dict
    hint: str  # remedy shown to the writer


CHECKS: tuple[Check, ...] = (
    Check("sentence_cv", "sentence_cv", "AI-uniform pacing — vary sentence length"),
)

# Every metric measured, in report order. The calibration spike imports this
# rather than re-listing it, so a metric renamed here cannot leave the corpus
# that validated it measuring something else.
METRICS: tuple[str, ...] = (
    "sentence_cv",
    "paragraph_cv",
    "lexical_diversity",
    "function_word_ratio",
    "subordinate_density",
)

# Reported in `metrics`, never flagged. Named in the JSON as `unbanded` so a
# caller can tell "we measured this and say nothing about it" apart from "this
# passed". Derived, so adding a Check cannot leave a metric in both lists.
UNBANDED_METRICS: tuple[str, ...] = tuple(
    m for m in METRICS if m not in {check.metric for check in CHECKS}
)

# The internal metric names are what CHECKS, METRICS and the calibration spike
# use; the JSON reports one of them under a different key, because the MATTR
# window size is part of what was measured. `unbanded` exists to be indexed into
# `metrics`, so it has to carry the reported key rather than the internal name.
REPORT_KEYS: dict[str, str] = {"lexical_diversity": "lexical_diversity_mattr50"}

# Metrics belonging to the burstiness family, for --fail-on=cv. paragraph_cv is
# listed because it is a CV metric, not because it is trusted; it carries no
# threshold, so today this set behaves identically to --fail-on=any. It is kept
# separate so that gating on the CV family stays meaningful if paragraph_cv is
# ever validated against a corpus that preserves paragraph breaks.
CV_METRICS = frozenset({"sentence_cv", "paragraph_cv"})


# Every profile offered has to change something. `academic`, `blog` and `docs`
# were offered for three releases and all resolved identically to `default` --
# three CLI names that did nothing but suggest a tuning that was never there.
# humanize_score has its own profiles of the same names, and those do carry real
# per-pattern carve-outs; these were never the same thing.
PROFILES: tuple[str, ...] = ("default", "esl")


def resolve_targets(profile: str) -> dict[str, float]:
    """Threshold values for a profile. ESL writers get a looser sentence-CV bar.

    THE ESL OFFSET IS UNVALIDATED. 0.55 was fitted to HC3 and confirmed on RAID;
    0.50 was not, and is the one number in this module that never faced the
    pre-registered bar the composite was retired for failing. What supports it is
    an inference, not a measurement: ESL false-positive rates run 3-6x native
    (DETECTION_ROBUSTNESS.md), but that figure describes AI *detectors*, not
    sentence_cv on ESL prose, and the step from one to the other -- and to 0.05
    specifically -- is an assumption nobody has tested.

    Neither corpus here could test it. HC3 and RAID are both overwhelmingly
    native English, so fitting to them would not have re-derived the allowance,
    it would have deleted it silently and left ESL writers held to a bar set on
    prose unlike theirs. It is kept on that asymmetry: the cost of a slightly
    loose bar for one group is smaller than the cost of a bar that over-flags
    the group already most over-flagged. Treat it as a fairness allowance with a
    known direction and an unknown magnitude, not as a calibrated threshold, and
    re-derive it against an ESL-annotated corpus if one becomes available.

    Unknown names raise rather than falling through to the default. argparse
    already constrains the CLI to PROFILES, but `analyse` is importable, and a
    retired name like "academic" would otherwise apply default targets while the
    result reported the profile as honoured.
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {', '.join(PROFILES)}")
    return {"sentence_cv": 0.50 if profile == "esl" else 0.55}


def apply_checks(values: dict[str, float | None], targets: dict[str, float]) -> list[str]:
    """Run every check, returning the flags raised.

    A metric measuring None is undefined for this text (too short to measure) and
    is skipped: it contributes no flag.
    """
    flags: list[str] = []
    for check in CHECKS:
        value = values[check.metric]
        if value is None:
            continue
        target = targets[check.target]
        if value >= target:
            continue
        flags.append(f"{check.metric} too low ({value:.3f} < {target}); {check.hint}")
    return flags


def flagged_metrics(flags: list[str]) -> set[str]:
    """The metric each flag belongs to. Flags lead with the metric name."""
    return {flag.split(" ", 1)[0] for flag in flags}


def should_fail(flags: list[str], fail_on: str) -> bool:
    if fail_on == "never":
        return False
    if fail_on == "cv":
        return bool(flagged_metrics(flags) & CV_METRICS)
    return bool(flags)


# ---- Analysis -----------------------------------------------------------------


def _cv_or_none(lengths: list[int]) -> float | None:
    """CV over fewer than two units is undefined, not zero.

    coefficient_of_variation returns 0.0 there, which reads as perfect uniformity
    and flagged every one-sentence file as AI-uniform pacing. apply_checks skips a
    metric measuring None.
    """
    return coefficient_of_variation(lengths) if len(lengths) >= 2 else None


@dataclass(frozen=True)
class Measurement:
    """Every metric, measured from one normalisation of the text."""

    words: list[str]
    sentence_lengths: list[int]
    paragraph_lengths: list[int]
    values: dict[str, float | None]


def measure_text(text: str) -> Measurement:
    """Normalise once, then measure every metric on that one string.

    The single measurement entry point: analyse() only formats what this returns,
    and the calibration spike calls it too, so the corpus that validates a band
    measures exactly what the tool ships. Normalising here is also why the private
    splitters are the ones called -- see normalise() for why a second pass is not
    safe.
    """
    text = normalise(text)
    words = WORD_RE.findall(text.lower())
    sentence_lengths = [len(s.split()) for s in _sentences_from(text)]
    paragraph_lengths = [len(p.split()) for p in _paragraphs_from(text)]
    return Measurement(
        words=words,
        sentence_lengths=sentence_lengths,
        paragraph_lengths=paragraph_lengths,
        values={
            "sentence_cv": _cv_or_none(sentence_lengths),
            "paragraph_cv": _cv_or_none(paragraph_lengths),
            "lexical_diversity": lexical_diversity(words),
            "function_word_ratio": function_word_ratio(words),
            "subordinate_density": subordinate_density(text, len(words)),
        },
    )


def analyse(text: str, profile: str = "default") -> dict:
    m = measure_text(text)
    values = m.values
    targets = resolve_targets(profile)
    flags = apply_checks(values, targets)

    def r3(value: float | None) -> float | None:
        return round(value, 3) if value is not None else None

    return {
        "profile": profile,
        "metrics": {
            "sentence_count": len(m.sentence_lengths),
            "paragraph_count": len(m.paragraph_lengths),
            "word_count": len(m.words),
            "sentence_length_mean": round(statistics.mean(m.sentence_lengths), 1)
            if m.sentence_lengths
            else 0,
            "sentence_length_stdev": round(statistics.pstdev(m.sentence_lengths), 1)
            if len(m.sentence_lengths) > 1
            else 0,
            "sentence_cv": r3(values["sentence_cv"]),
            "paragraph_length_mean": round(statistics.mean(m.paragraph_lengths), 1)
            if m.paragraph_lengths
            else 0,
            "paragraph_cv": r3(values["paragraph_cv"]),
            "lexical_diversity_mattr50": r3(values["lexical_diversity"]),
            "function_word_ratio": r3(values["function_word_ratio"]),
            "subordinate_density": r3(values["subordinate_density"]),
            "shannon_entropy_bits": round(shannon_word_entropy(m.words), 2),
        },
        "targets": targets,
        "unbanded": [REPORT_KEYS.get(m, m) for m in UNBANDED_METRICS],
        "flags": flags,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report statistical signatures of a text file. Diagnostics, not a verdict: "
            "sentence_cv is the only metric with validated separating power."
        )
    )
    parser.add_argument("path", help="Path to text file (.md, .tex, .txt, ...)")
    parser.add_argument(
        "--profile",
        choices=list(PROFILES),
        default="default",
        help=(
            "Profile (esl loosens the sentence_cv target to 0.50; that offset is "
            "an untested allowance, not a calibrated threshold)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit raw JSON.")
    parser.add_argument(
        "--fail-on",
        choices=["any", "cv", "never"],
        default="any",
        help=(
            "Exit non-zero when: any flag is raised (default), only a burstiness-CV "
            "flag is raised (cv), or never."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=argparse.SUPPRESS,  # deprecated; accepted for one minor version
    )
    args = parser.parse_args(argv)

    if args.threshold is not None:
        # Kept accepted rather than removed so existing callers do not die on an
        # unrecognised argument -- .github/smoke-console-scripts.sh passes
        # --threshold 100 as its CI gate. Remove after one minor version.
        print(
            "warning: --threshold is deprecated and ignored; signature_score was "
            "retired. Use --fail-on=any|cv|never.",
            file=sys.stderr,
        )

    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2

    text = path.read_text(encoding="utf-8", errors="replace")
    result = analyse(text, profile=args.profile)
    result["path"] = str(path)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        m = result["metrics"]

        def shown(value: float | None, unit: str) -> str:
            # :.3f, not str(): the value is already rounded to 3dp, and bare
            # formatting drops the trailing zeros, so a CV of 0.610 printed as
            # "0.61" and the README's own example output was unreproducible.
            # The flag strings have always used :.3f; this matches them.
            return f"{value:.3f}" if value is not None else f"n/a (needs 2+ {unit})"

        print(f"profile:         {args.profile}")
        print(
            f"sentences:       {m['sentence_count']}  (mean {m['sentence_length_mean']} "
            f"± {m['sentence_length_stdev']} words, CV {shown(m['sentence_cv'], 'sentences')})"
        )
        print(
            f"paragraphs:      {m['paragraph_count']}  "
            f"(CV {shown(m['paragraph_cv'], 'paragraphs')})"
        )
        mattr = m["lexical_diversity_mattr50"]
        print(f"lexical MATTR50: {mattr if mattr is not None else 'n/a (under 50 words)'}")
        print(f"func-word ratio: {m['function_word_ratio']}")
        print(f"subord density:  {m['subordinate_density']}")
        print(
            "                 (paragraph_cv, MATTR50, func-word ratio and subord "
            "density are reported only; no threshold is asserted)"
        )
        if result["flags"]:
            print("flags:")
            for f in result["flags"]:
                print(f"  - {f}")
        else:
            print("flags:           none")

    return 1 if should_fail(result["flags"], args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
