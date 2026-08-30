#!/usr/bin/env python3
"""burstiness_check — measure statistical signatures of a text file.

Returns burstiness CV, lexical diversity (MATTR-50), function-word ratio,
paragraph-length variation, and a subordinate-clause-depth proxy. Flags when
signatures fall in the "AI-uniform" band rather than the "human-bursty" band.

Pure Python, zero dependencies. Compatible with Python 3.9+.

Targets (default):
   sentence_cv          >= 0.55 (>= 0.50 for ESL profile)
   paragraph_cv         >= 0.40
   lexical_diversity    in [0.40, 0.65]    -- UNCALIBRATED, see below
   subordinate_density  >= 0.10 (commas+semicolons per word)
   function_word_ratio  in [0.40, 0.55]    -- UNCALIBRATED, see below

KNOWN ISSUE: trust sentence_cv and paragraph_cv; do not trust signature_score.
The two heaviest-weighted checks (x200 and x300) still flag on every document,
so the composite continues to report "heavy-AI-signature" for human prose. What
remains is a threshold problem, not a measurement problem:

  * lexical_diversity: the [0.40, 0.65] band was set for whole-document
    type-token ratio, which falls as a document grows. The metric is MATTR-50,
    which is length-stable by design and runs 0.77-0.90 on ordinary English
    prose. MATTR is the better metric, so the band should move to match it --
    but by how much is a corpus question.
  * function_word_ratio: the 0.40-0.55 band assumes a full function-word
    inventory. FUNCTION_WORDS now holds one (~300 closed-class entries including
    contractions, up from 56), which moved measured prose from 0.15-0.21 to
    0.24-0.31 -- real, and still well short of the band. The short list was one
    cause; the band itself is the other, and it also needs calibrating.

Setting either band from a handful of files would be the same pseudo-precision
this project flags as pattern #43. Doing it properly needs two labelled corpora
-- human and AI-generated -- since tuning against human text alone only proves
the tool stopped firing, not that it still separates. Until that exists,
signature_score is diagnostic output, not a verdict.

Usage:
   python burstiness_check.py FILE.md
   python burstiness_check.py --profile=esl FILE.md
   python burstiness_check.py --json FILE.md
"""

from __future__ import annotations

import argparse
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
# Grouped by grammatical class and kept dense on purpose: a word list reads better
# as columns than as 300 one-item lines, and the grouping is what makes it reviewable.
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


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences. Strips fenced code blocks first."""
    # Strip fenced code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Strip inline code
    text = re.sub(r"`[^`]*`", "", text)
    # Strip Markdown headings (the heading text itself isn't prose)
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
    # Strip table separator lines
    text = re.sub(r"^\s*\|?[-: |]+\|?\s*$", "", text, flags=re.MULTILINE)
    # Sentence boundary: . ! ? followed by space + uppercase, or end of paragraph
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n\s*\n", text)
    return [p.strip() for p in parts if p.strip() and len(p.split()) >= 2]


def split_paragraphs(text: str) -> list[str]:
    text = re.sub(r"```[\s\S]*?```", "", text)
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and len(p.split()) >= 5]


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

# Each metric is checked against one bound. Flag text and score penalty come from
# the same table, so a target can never drift between "what we warn about" and
# "what we score".


@dataclass(frozen=True)
class Check:
    metric: str  # key into the measured-values dict
    bound: str  # "min" (flag when below) or "max" (flag when above)
    target: str  # key into the profile-resolved targets dict
    penalty: float  # deviation multiplier contributed to signature_score
    hint: str  # remedy shown to the writer


CHECKS: tuple[Check, ...] = (
    Check("sentence_cv", "min", "sentence_cv", 100, "AI-uniform pacing — vary sentence length"),
    Check(
        "paragraph_cv",
        "min",
        "paragraph_cv",
        100,
        "AI-uniform paragraphs — vary paragraph length",
    ),
    Check(
        "lexical_diversity",
        "min",
        "lexical_diversity_min",
        200,
        "vocabulary too repetitive",
    ),
    Check(
        "lexical_diversity",
        "max",
        "lexical_diversity_max",
        200,
        "synonym cycling or thesaurus attack",
    ),
    Check(
        "subordinate_density",
        "min",
        "subordinate_density_min",
        200,
        "uniform syntax — add subordinate clauses, parenthetical asides",
    ),
    Check(
        "function_word_ratio",
        "min",
        "function_word_ratio_min",
        300,
        "noun/verb-heavy prose typical of AI",
    ),
    Check(
        "function_word_ratio",
        "max",
        "function_word_ratio_max",
        300,
        "filler-heavy prose",
    ),
)


def resolve_targets(profile: str) -> dict[str, float]:
    """Threshold values for a profile. ESL writers get a looser sentence-CV bar."""
    return {
        "sentence_cv": 0.50 if profile == "esl" else 0.55,
        "paragraph_cv": 0.40,
        "lexical_diversity_min": 0.40,
        "lexical_diversity_max": 0.65,
        "subordinate_density_min": 0.10,
        "function_word_ratio_min": 0.40,
        "function_word_ratio_max": 0.55,
    }


def apply_checks(
    values: dict[str, float | None], targets: dict[str, float]
) -> tuple[list[str], float]:
    """Run every check, returning (flags, total weighted deviation).

    A metric measuring None is undefined for this text (too short to measure) and
    is skipped: it contributes no flag and no penalty.
    """
    flags: list[str] = []
    deviations = 0.0
    for check in CHECKS:
        value = values[check.metric]
        if value is None:
            continue
        target = targets[check.target]
        if check.bound == "min":
            shortfall = target - value
            comparison = f"{value:.3f} < {target}"
            direction = "too low"
        else:
            shortfall = value - target
            comparison = f"{value:.3f} > {target}"
            direction = "too high"
        if shortfall <= 0:
            continue
        flags.append(f"{check.metric} {direction} ({comparison}); {check.hint}")
        deviations += shortfall * check.penalty
    return flags, deviations


def verdict_for(signature_score: float) -> str:
    if signature_score < 15:
        return "human-like"
    if signature_score < 35:
        return "borderline"
    if signature_score < 60:
        return "AI-uniform"
    return "heavy-AI-signature"


# ---- Analysis -----------------------------------------------------------------


def analyse(text: str, profile: str = "default") -> dict:
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    words = re.findall(r"[A-Za-z']+", text.lower())

    sentence_lengths = [len(s.split()) for s in sentences]
    paragraph_lengths = [len(p.split()) for p in paragraphs]

    values: dict[str, float | None] = {
        "sentence_cv": coefficient_of_variation(sentence_lengths),
        "paragraph_cv": coefficient_of_variation(paragraph_lengths),
        "lexical_diversity": lexical_diversity(words),
        "function_word_ratio": function_word_ratio(words),
        "subordinate_density": subordinate_density(text, len(words)),
    }

    ld = values["lexical_diversity"]

    targets = resolve_targets(profile)
    flags, deviations = apply_checks(values, targets)
    signature_score = min(100.0, deviations)

    return {
        "profile": profile,
        "signature_score": round(signature_score, 1),
        "verdict": verdict_for(signature_score),
        "metrics": {
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
            "word_count": len(words),
            "sentence_length_mean": round(statistics.mean(sentence_lengths), 1)
            if sentence_lengths
            else 0,
            "sentence_length_stdev": round(statistics.pstdev(sentence_lengths), 1)
            if len(sentence_lengths) > 1
            else 0,
            "sentence_cv": round(values["sentence_cv"], 3),
            "paragraph_length_mean": round(statistics.mean(paragraph_lengths), 1)
            if paragraph_lengths
            else 0,
            "paragraph_cv": round(values["paragraph_cv"], 3),
            "lexical_diversity_mattr50": round(ld, 3) if ld is not None else None,
            "function_word_ratio": round(values["function_word_ratio"], 3),
            "subordinate_density": round(values["subordinate_density"], 3),
            "shannon_entropy_bits": round(shannon_word_entropy(words), 2),
        },
        "targets": targets,
        "flags": flags,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure statistical signatures of a text file. Lower signature_score = more human-like."
    )
    parser.add_argument("path", help="Path to text file (.md, .tex, .txt, ...)")
    parser.add_argument(
        "--profile",
        choices=["default", "esl", "academic", "blog", "docs"],
        default="default",
        help="Profile (esl loosens sentence_cv target to 0.50)",
    )
    parser.add_argument("--json", action="store_true", help="Emit raw JSON.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=35.0,
        help="Exit non-zero if signature_score exceeds threshold (default 35).",
    )
    args = parser.parse_args(argv)

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
        print(f"signature_score: {result['signature_score']}/100  ({result['verdict']})")
        print(f"profile:         {args.profile}")
        print(
            f"sentences:       {m['sentence_count']}  (mean {m['sentence_length_mean']} ± {m['sentence_length_stdev']} words, CV {m['sentence_cv']})"
        )
        print(f"paragraphs:      {m['paragraph_count']}  (CV {m['paragraph_cv']})")
        mattr = m["lexical_diversity_mattr50"]
        print(f"lexical MATTR50: {mattr if mattr is not None else 'n/a (under 50 words)'}")
        print(f"func-word ratio: {m['function_word_ratio']}")
        print(f"subord density:  {m['subordinate_density']}")
        if result["flags"]:
            print("flags:")
            for f in result["flags"]:
                print(f"  - {f}")

    return 1 if result["signature_score"] > args.threshold else 0


if __name__ == "__main__":
    sys.exit(main())
