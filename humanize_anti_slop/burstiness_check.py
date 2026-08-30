#!/usr/bin/env python3
"""burstiness_check — measure statistical signatures of a text file.

Returns burstiness CV, lexical-diversity (TTR), function-word ratio, paragraph-length
variation, and subordinate-clause-depth proxy. Flags when signatures fall in the
"AI-uniform" band rather than the "human-bursty" band.

Pure Python, zero dependencies. Compatible with Python 3.9+.

Targets (default):
   sentence_cv          >= 0.55 (>= 0.50 for ESL profile)
   paragraph_cv         >= 0.40
   lexical_diversity    in [0.40, 0.65]
   subordinate_density  >= 0.10 (commas+semicolons per word)
   function_word_ratio  in [0.40, 0.55]

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

# A short list of high-frequency function words (for function-word ratio).
FUNCTION_WORDS = frozenset(
    [
        "the",
        "and",
        "of",
        "to",
        "in",
        "a",
        "is",
        "for",
        "on",
        "with",
        "as",
        "by",
        "at",
        "this",
        "that",
        "be",
        "it",
        "from",
        "or",
        "are",
        "which",
        "an",
        "but",
        "not",
        "have",
        "has",
        "had",
        "was",
        "were",
        "been",
        "being",
        "do",
        "does",
        "did",
        "so",
        "if",
        "then",
        "than",
        "them",
        "their",
        "these",
        "those",
        "there",
        "here",
        "we",
        "he",
        "she",
        "you",
        "i",
        "me",
        "my",
        "our",
        "your",
        "his",
        "her",
        "its",
        "their",
    ]
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


def lexical_diversity(words: list[str]) -> float:
    """Type-token ratio (TTR). Stable for any length via moving-window TTR (MATTR-50)."""
    if len(words) < 50:
        if not words:
            return 0.0
        return len(set(words)) / len(words)
    # Moving-average TTR with window 50
    window = 50
    ttrs = []
    for i in range(0, len(words) - window + 1, max(1, (len(words) - window) // 100)):
        chunk = words[i : i + window]
        ttrs.append(len(set(chunk)) / window)
    return statistics.mean(ttrs) if ttrs else 0.0


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


def apply_checks(values: dict[str, float], targets: dict[str, float]) -> tuple[list[str], float]:
    """Run every check, returning (flags, total weighted deviation)."""
    flags: list[str] = []
    deviations = 0.0
    for check in CHECKS:
        value = values[check.metric]
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

    values = {
        "sentence_cv": coefficient_of_variation(sentence_lengths),
        "paragraph_cv": coefficient_of_variation(paragraph_lengths),
        "lexical_diversity": lexical_diversity(words),
        "function_word_ratio": function_word_ratio(words),
        "subordinate_density": subordinate_density(text, len(words)),
    }

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
            "lexical_diversity_ttr": round(values["lexical_diversity"], 3),
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
        print(f"lexical TTR:     {m['lexical_diversity_ttr']}")
        print(f"func-word ratio: {m['function_word_ratio']}")
        print(f"subord density:  {m['subordinate_density']}")
        if result["flags"]:
            print("flags:")
            for f in result["flags"]:
                print(f"  - {f}")

    return 1 if result["signature_score"] > args.threshold else 0


if __name__ == "__main__":
    sys.exit(main())
