"""Shared corpus and statistics helpers for the burstiness calibration spikes.

Phase 0 (HC3) and phase 1 (RAID) both import this so their AUCs are comparable:
same length buckets, same length-matching rule, same AUC and correlation
estimators. Anything that would change a number for both phases belongs here,
not in a phase driver.

Spike-only code. Not packaged -- [tool.hatch.build.targets.wheel] sets
packages = ["humanize_anti_slop"], so the wheel is an allowlist and this tree is
already outside it. Pure stdlib for the same reason the shipped package is:
matplotlib is imported lazily and only to draw the overlap plot.
"""

from __future__ import annotations

import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# METRICS is re-exported: the phase drivers take it from here, so the metric set
# they iterate is the one the tool ships rather than a second list to keep in sync.
from humanize_anti_slop.burstiness_check import (  # noqa: E402,F401
    METRICS,
    measure_text,
    split_paragraphs,
)

# Pre-registered decision bar. Written down before any result was looked at; see
# PLAN.md phase 0 step 5. A metric separates only if its AUC is at least this far
# from chance in either direction.
AUC_BAR = 0.65

# Two metrics correlating at or above this are one signal, and the phase 0 exit
# count treats them as one.
COLLINEAR_RHO = 0.8

# Word-count buckets, left-closed. Length-matching happens inside a bucket, so
# these are the resolution at which the two classes are held comparable. MATTR-50
# is undefined below 50 words, which sets the floor.
BUCKETS = (
    (50, 75),
    (75, 100),
    (100, 150),
    (150, 200),
    (200, 300),
    (300, 500),
    (500, 1000),
    (1000, 10**9),
)


def bucket_of(word_count: int) -> tuple[int, int] | None:
    for lo, hi in BUCKETS:
        if lo <= word_count < hi:
            return (lo, hi)
    return None


@dataclass(frozen=True)
class Doc:
    domain: str
    label: int  # 1 = AI-generated, 0 = human
    bucket: tuple[int, int]
    words: int
    metrics: dict[str, float]
    # Metrics that were defined for this document. An undefined CV is stored as
    # 0.0 in `metrics` to keep that dict plain floats, so this is the only way to
    # tell it apart from a real 0.0. See measure().
    defined: frozenset[str]


def measure(text: str) -> tuple[int, dict[str, float], frozenset[str]] | None:
    """Metric vector for one document, or None if it is too short to measure.

    Calls the shipped measurement entry point, so the spike measures
    burstiness_check.py rather than a reimplementation of it -- one normalisation,
    one tokenizer, one metric set, shared with the runtime by import.

    Returns which metrics were actually defined alongside the values. An
    undefined CV still becomes 0.0 so the numeric path stays plain floats, but
    "undefined" is now carried separately rather than inferred from the zero.
    Inferring it conflated two different documents: one sentence, where the CV
    does not exist, and several sentences of identical length, where it exists
    and is exactly 0.0. The second is not a measurement failure -- it is perfect
    uniformity, the thing sentence_cv exists to detect -- so counting it as
    unmeasurable dropped the most AI-looking documents out of the denominator.
    """
    m = measure_text(text)
    if len(m.words) < 50 or m.values["lexical_diversity"] is None:
        return None
    defined = frozenset(k for k, v in m.values.items() if v is not None)
    values = {k: (0.0 if v is None else v) for k, v in m.values.items()}
    return len(m.words), values, defined


def to_doc(text: str, domain: str, label: int) -> Doc | None:
    """Measure one document into a Doc, or None if it cannot be scored.

    Two ways to be unscoreable -- too short to measure, or too long for the last
    bucket -- and every caller has to handle both the same way, so the guards live
    here rather than being re-inlined at each site.
    """
    measured = measure(text)
    if measured is None:
        return None
    words, metrics, defined = measured
    bucket = bucket_of(words)
    if bucket is None:
        return None
    return Doc(domain, label, bucket, words, metrics, defined)


def length_match(docs: list[Doc], seed: int = 20260901) -> list[Doc]:
    """Equalise the two classes within every (domain, bucket) cell.

    HC3's ChatGPT answers run longer than its human ones, and MATTR-50 and
    subordinate_density are length-sensitive; an unmatched sample measures length
    and reports it as style. Sampling down to min(n_human, n_ai) per cell makes
    the length distributions identical by construction rather than by hope.
    """
    rng = random.Random(seed)
    cells: dict[tuple[str, tuple[int, int], int], list[Doc]] = defaultdict(list)
    for d in docs:
        cells[(d.domain, d.bucket, d.label)].append(d)

    kept: list[Doc] = []
    keys = {(domain, bucket) for domain, bucket, _ in cells}
    for domain, bucket in sorted(keys):
        human = cells.get((domain, bucket, 0), [])
        ai = cells.get((domain, bucket, 1), [])
        n = min(len(human), len(ai))
        if n == 0:
            continue
        kept.extend(rng.sample(human, n))
        kept.extend(rng.sample(ai, n))
    return kept


def cap_domains(docs: list[Doc], cap: int, seed: int = 20260901) -> list[Doc]:
    """Cap how many matched pairs any one domain contributes.

    Not equalisation. HC3's open_qa holds only 116 human answers of 50+ words, so
    cutting every domain to the smallest would discard ~90% of the corpus to buy
    exact domain balance. Capping instead keeps the power, and domain balance is
    recovered in the statistic: the report prints a per-domain AUC and a
    macro-average that weights every domain equally regardless of its size.

    Samples whole (human, AI) pairs so length_match's per-cell balance survives.
    """
    rng = random.Random(seed)
    by_domain: dict[str, list[Doc]] = defaultdict(list)
    for d in docs:
        by_domain[d.domain].append(d)

    kept: list[Doc] = []
    for domain in sorted(by_domain):
        by_cell: dict[tuple[int, int], tuple[list[Doc], list[Doc]]] = {}
        for d in by_domain[domain]:
            h, a = by_cell.setdefault(d.bucket, ([], []))
            (a if d.label else h).append(d)
        pairs = [(h[i], a[i]) for h, a in by_cell.values() for i in range(min(len(h), len(a)))]
        for h, a in rng.sample(pairs, min(len(pairs), cap)):
            kept.extend((h, a))
    return kept


def measurable(docs: list[Doc], metric: str) -> float:
    """Fraction of documents where the metric was defined, in the *worse* class.

    A coefficient of variation is undefined below two values, so a corpus of
    one-sentence or one-paragraph documents cannot test a CV metric at all.
    Taking the minimum across classes rather than the pooled rate is what makes
    this catch the dangerous case: HC3's open_qa keeps 9.8% of human answers
    against 91% of AI ones, and 96% of the human answers that survive are a
    single sentence. Pooled, that cell looks half-measurable; per class, the
    human side is 4% and the AUC is reporting the length filter, not the writing.

    Keyed on `defined`, not on a value of 0.0. Two different documents produce
    that zero -- one sentence, where the CV does not exist, and several sentences
    of identical length, where it exists and is 0.0 -- and only the first is a
    failure to measure. The second is perfect uniformity, which is the signature
    sentence_cv is for, so counting it as unmeasurable quietly dropped the most
    AI-looking documents out of the denominator. Keying on `defined` also gives
    the non-CV columns 1.0 without needing a special case: they are never
    undefined, and a subordinate_density of 0.0 is a document with none of the
    thing, which is a measurement.
    """
    rates = []
    for label in (0, 1):
        side = [d for d in docs if d.label == label]
        if side:
            usable = sum(1 for d in side if metric in d.defined)
            rates.append(usable / len(side))
    return min(rates) if rates else 0.0


def macro_auc(
    docs: list[Doc], metric: str, floor: float = 0.0
) -> tuple[float, dict[str, float], list[str], dict[str, float]]:
    """Per-domain AUC and their unweighted mean, skipping degenerate cells.

    The macro average is the domain-balanced number: a domain contributing ten
    times the documents still contributes one AUC to it, so a single large domain
    cannot carry the verdict. A domain whose measurable() falls below `floor` for
    this metric contributes nothing and is returned in the dropped list -- it is
    not evidence either way, and averaging it in would let a filter artifact set
    the gate.

    Returns the filtered mean, the surviving cells, the dropped domains, and every
    cell including the dropped ones -- the report prints both readings, and one
    pass produces them, so no caller has to compute the AUCs a second time.
    """
    all_cells: dict[str, float] = {}
    per_domain: dict[str, float] = {}
    dropped: list[str] = []
    for domain in sorted({d.domain for d in docs}):
        cell = [d for d in docs if d.domain == domain]
        h = [d.metrics[metric] for d in cell if d.label == 0]
        a = [d.metrics[metric] for d in cell if d.label == 1]
        if not h or not a:
            continue
        all_cells[domain] = auc(a, h)
        if measurable(cell, metric) < floor:
            dropped.append(domain)
            continue
        per_domain[domain] = all_cells[domain]
    mean = sum(per_domain.values()) / len(per_domain) if per_domain else 0.5
    return mean, per_domain, dropped, all_cells


def holdout_split(
    docs: list[Doc], fraction: float = 0.25, seed: int = 20260902
) -> tuple[list[Doc], list[Doc]]:
    """Carve a held-out slice before any band is fitted, and do not look at it.

    PLAN.md phase 1 step 4. RAID-test is unlabelled and scored through a public
    leaderboard, so it cannot serve as a local held-out set; this is the local
    substitute, and it exists so the phase 2 acceptance check (composite AUC
    >= 0.75, false-positive rate <= 10% at --threshold 35) is a test rather than
    a restatement of the fit.

    Splits whole (human, AI) pairs, stratified by (domain, bucket). Splitting
    documents instead would put a length-matched pair's two halves on opposite
    sides of the boundary, leaking the match across it.

    Known limitation, and it has to be closed before this set is scored. The
    pairs here are the ones length_match built, which are (domain, bucket) pairs,
    not source pairs -- Doc does not carry source_id. So RAID rows generated from
    one source text can land on both sides of the split, putting the topic of a
    held-out document in the training half. That does not affect the phase 1
    result, which is computed on `train` alone and never touches `held_out`, and
    the gate returned phase X, so the set was never scored. Phase 2 must make the
    split source-disjoint first: carry source_id through Doc and assign whole
    source groups before length matching and domain capping.
    """
    rng = random.Random(seed)
    cells: dict[tuple[str, tuple[int, int]], tuple[list[Doc], list[Doc]]] = {}
    for d in docs:
        human, ai = cells.setdefault((d.domain, d.bucket), ([], []))
        (ai if d.label else human).append(d)

    train: list[Doc] = []
    test: list[Doc] = []
    leftover = 0
    for human, ai in cells.values():
        n = min(len(human), len(ai))
        leftover += len(human) - n + len(ai) - n
        pairs = list(zip(human[:n], ai[:n]))
        rng.shuffle(pairs)
        cut = round(len(pairs) * fraction)
        for pair in pairs[:cut]:
            test.extend(pair)
        for pair in pairs[cut:]:
            train.extend(pair)
    # length_match should leave nothing unpaired. A nonempty remainder means the
    # matching upstream is broken, and silently dropping it would hide that.
    assert leftover == 0, f"{leftover} unpaired documents reached holdout_split"
    return train, test


# ---- Statistics ---------------------------------------------------------------


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, 1-based, ties shared."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def auc(positive: list[float], negative: list[float]) -> float:
    """P(positive > negative) + 0.5 P(tie), exactly, via the rank-sum identity.

    Oriented so 'positive' is the AI class: > 0.5 means the metric runs higher on
    AI text, < 0.5 means higher on human text. Both are signal; the bar is applied
    to the distance from 0.5, and the direction is reported alongside.
    """
    if not positive or not negative:
        return 0.5
    ranks = _ranks(positive + negative)
    rank_sum = sum(ranks[: len(positive)])
    return (rank_sum - len(positive) * (len(positive) + 1) / 2) / (len(positive) * len(negative))


def separation(value: float) -> float:
    """AUC folded to [0.5, 1]: how well the metric separates, ignoring direction."""
    return max(value, 1 - value)


def direction(macro: float) -> str:
    """Which class runs higher. Exactly 0.5 is chance, so it gets no direction.

    Both phase drivers write this into their evidence JSON, and a metric the
    corpus cannot measure lands on exactly 0.5 -- paragraph_cv did on HC3, at a
    measurable fraction of 0.008 -- so a two-way split labels a coin flip as a
    finding.
    """
    if macro == 0.5:
        return "no direction"
    return "higher in AI" if macro > 0.5 else "higher in human"


def collinear_pairs(rho: dict[str, dict[str, float]]) -> list[tuple[str, str]]:
    """Metric pairs correlating at or above COLLINEAR_RHO, which counts as one signal."""
    return [
        (a, b)
        for i, a in enumerate(METRICS)
        for b in METRICS[i + 1 :]
        if abs(rho[a][b]) >= COLLINEAR_RHO
    ]


def auc_ci(
    positive: list[float], negative: list[float], reps: int = 1000, seed: int = 20260901
) -> tuple[float, float]:
    """Percentile bootstrap CI, resampling each class at its own size."""
    rng = random.Random(seed)
    draws = []
    for _ in range(reps):
        p = [positive[rng.randrange(len(positive))] for _ in range(len(positive))]
        n = [negative[rng.randrange(len(negative))] for _ in range(len(negative))]
        draws.append(auc(p, n))
    draws.sort()
    return draws[int(0.025 * reps)], draws[min(reps - 1, int(0.975 * reps))]


def spearman(a: list[float], b: list[float]) -> float:
    """Pearson correlation of the average ranks."""
    ra, rb = _ranks(a), _ranks(b)
    sa, sb = statistics.pstdev(ra), statistics.pstdev(rb)
    if sa == 0 or sb == 0:
        return 0.0
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / len(ra)
    return cov / (sa * sb)


def histogram(
    values: list[float], bins: int = 20, lo: float | None = None, hi: float | None = None
) -> tuple[list[int], float, float]:
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    if hi <= lo:
        return [len(values)] + [0] * (bins - 1), lo, hi
    counts = [0] * bins
    for v in values:
        idx = min(bins - 1, int((v - lo) / (hi - lo) * bins))
        counts[max(0, idx)] += 1
    return counts, lo, hi


def overlap_rows(human: list[float], ai: list[float], bins: int = 20, width: int = 34) -> list[str]:
    """Text overlap plot: one row per bin, human left-shaded, AI right-shaded."""
    lo = min(min(human), min(ai))
    hi = max(max(human), max(ai))
    h, _, _ = histogram(human, bins, lo, hi)
    a, _, _ = histogram(ai, bins, lo, hi)
    peak = max(max(h), max(a)) or 1
    rows = []
    for i in range(bins):
        edge = lo + (hi - lo) * i / bins
        hb = "#" * round(width * h[i] / peak)
        ab = "#" * round(width * a[i] / peak)
        rows.append(f"{edge:8.3f} |{hb:>{width}}|{ab:<{width}}|")
    return rows
