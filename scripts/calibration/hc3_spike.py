#!/usr/bin/env python3
"""Phase 0 -- HC3 go/no-go for the burstiness composite.

Answers one question: do the five metrics in burstiness_check.py separate AI text
from human text at all? The bar was pre-registered in PLAN.md before any result
was seen -- a metric carries detection signal only if its AUC sits at least 0.15
from chance, and a collinear pair counts once.

Usage:
    uv run scripts/calibration/hc3_spike.py            # text + JSON report
    uv run --with matplotlib scripts/calibration/hc3_spike.py --plot

Corpus: HC3 (Hello-SimpleAI/HC3, CC-BY-SA-4.0), five domain files under
scripts/calibration/data/, which is gitignored -- see the fixtures README for what
does get checked in.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from corpus import (
    AUC_BAR,
    COLLINEAR_RHO,
    METRICS,
    Doc,
    auc,
    auc_ci,
    cap_domains,
    collinear_pairs,
    direction,
    length_match,
    macro_auc,
    measurable,
    overlap_rows,
    separation,
    spearman,
    to_doc,
)

DOMAINS = ("finance", "medicine", "open_qa", "reddit_eli5", "wiki_csai")
DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "results"

# Cap per domain before matching. reddit_eli5 ships 17x the rows wiki_csai does;
# reading all of it only to discard it in cap_domains wastes minutes.
ROWS_PER_DOMAIN = 4000

# Cap per domain after matching, in (human, AI) pairs. Keeps reddit_eli5 and
# finance from between them owning three quarters of the pooled AUC.
PAIRS_PER_DOMAIN = 600

# Below this fraction of non-degenerate values, a metric was not tested by the
# corpus and its AUC is a statement about ties, not about style. Added after
# seeing that HC3 answers are overwhelmingly single-paragraph -- it is a validity
# check on the corpus, not an adjustment to the pre-registered 0.65 bar.
MEASURABLE_FLOOR = 0.25


def load(domain: str, limit: int = ROWS_PER_DOMAIN) -> list[Doc]:
    docs: list[Doc] = []
    with (DATA / f"{domain}.jsonl").open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh):
            if lineno >= limit:
                break
            row = json.loads(line)
            for key, label in (("human_answers", 0), ("chatgpt_answers", 1)):
                for text in row.get(key) or []:
                    doc = to_doc(text, domain, label)
                    if doc is not None:
                        docs.append(doc)
    return docs


def build_corpus() -> list[Doc]:
    raw = [doc for domain in DOMAINS for doc in load(domain)]
    return cap_domains(length_match(raw), PAIRS_PER_DOMAIN)


def print_length_match(human: list[Doc], ai: list[Doc]) -> tuple[float, float]:
    """Per-domain counts and mean lengths, the evidence that matching worked.

    Returns the corpus-wide mean word counts, which the JSON report records.
    """
    print("per-domain counts and length match")
    print(f"  {'domain':<14}{'human':>8}{'AI':>8}{'mean words H':>15}{'mean words AI':>15}")
    rows = [
        (d, [x for x in human if x.domain == d], [x for x in ai if x.domain == d]) for d in DOMAINS
    ]
    for domain, h, a in [*rows, ("ALL", human, ai)]:
        if not h:
            continue
        mh = sum(d.words for d in h) / len(h)
        ma = sum(d.words for d in a) / len(a)
        print(f"  {domain:<14}{len(h):>8}{len(a):>8}{mh:>15.1f}{ma:>15.1f}")
    print()
    return sum(d.words for d in human) / len(human), sum(d.words for d in ai) / len(ai)


def spearman_matrix(docs: list[Doc]) -> tuple[dict[str, dict[str, float]], list[tuple[str, str]]]:
    """Print the metric-by-metric rank correlations and name the collinear pairs."""
    print("\nSpearman correlation across metrics (both classes pooled)")
    print(f"  {'':<22}" + "".join(f"{m[:10]:>12}" for m in METRICS))
    columns = {m: [d.metrics[m] for d in docs] for m in METRICS}
    rho: dict[str, dict[str, float]] = {}
    for a_name in METRICS:
        row = []
        for b_name in METRICS:
            r = spearman(columns[a_name], columns[b_name])
            rho.setdefault(a_name, {})[b_name] = round(r, 3)
            row.append(f"{r:>12.3f}")
        print(f"  {a_name:<22}{''.join(row)}")

    collinear = collinear_pairs(rho)
    print(f"\ncollinear pairs (|rho| >= {COLLINEAR_RHO}): {collinear or 'none'}")
    return rho, collinear


def report(docs: list[Doc], plot: bool) -> dict:
    human = [d for d in docs if d.label == 0]
    ai = [d for d in docs if d.label == 1]

    print(f"corpus: {len(docs)} documents ({len(human)} human / {len(ai)} AI)")
    print(
        f"pre-registered bar: separation AUC >= {AUC_BAR}, collinear at |rho| >= {COLLINEAR_RHO}\n"
    )
    mh, ma = print_length_match(human, ai)

    results: dict[str, dict] = {}
    print("per-metric separation. AUC is oriented as P(AI > human); the bar is")
    print("applied to `sep`, the domain-macro-averaged AUC folded to [0.5, 1].")
    print(
        f"  {'metric':<21}{'pooled':>8}{'95% CI':>17}{'macro':>8}{'sep':>7}"
        f"{'meas':>7}{'human':>9}{'AI':>8}  verdict"
    )
    for metric in METRICS:
        hv = [d.metrics[metric] for d in human]
        av = [d.metrics[metric] for d in ai]
        pooled = auc(av, hv)
        lo, hi = auc_ci(av, hv)
        macro, per_domain, dropped, all_cells = macro_auc(docs, metric, MEASURABLE_FLOOR)
        sep = separation(macro)
        meas = measurable(docs, metric)
        passed = sep >= AUC_BAR and meas >= MEASURABLE_FLOOR
        mean_h = sum(hv) / len(hv)
        mean_a = sum(av) / len(av)
        results[metric] = {
            "auc_pooled": round(pooled, 4),
            "ci95_pooled": [round(lo, 4), round(hi, 4)],
            "auc_macro": round(macro, 4),
            "auc_by_domain": {k: round(v, 4) for k, v in per_domain.items()},
            "domains_dropped_as_degenerate": dropped,
            "auc_all_cells": {k: round(v, 4) for k, v in all_cells.items()},
            "separation": round(sep, 4),
            "measurable_fraction": round(meas, 4),
            "untested_by_corpus": meas < MEASURABLE_FLOOR,
            "passes_bar": passed,
            "direction": direction(macro),
            "human_mean": round(mean_h, 4),
            "ai_mean": round(mean_a, 4),
        }
        print(
            f"  {metric:<21}{pooled:>8.3f}  [{lo:.3f}, {hi:.3f}]{macro:>8.3f}{sep:>7.3f}"
            f"{meas:>7.2f}{mean_h:>9.3f}{mean_a:>8.3f}  "
            f"{'PASS' if passed else 'UNTESTED' if meas < MEASURABLE_FLOOR else 'fail'}"
        )

    print("\n  AUC by domain; '--' = cell dropped, one class degenerate below the floor")
    print(f"  {'metric':<21}" + "".join(f"{d[:11]:>13}" for d in DOMAINS))
    for metric in METRICS:
        cells = results[metric]["auc_by_domain"]
        row = "".join(f"{cells[d]:>13.3f}" if d in cells else f"{'--':>13}" for d in DOMAINS)
        print(f"  {metric:<21}{row}")

    rho, collinear = spearman_matrix(docs)

    print("\ndistribution overlap (left = human, right = AI)")
    for metric in METRICS:
        print(f"\n  {metric}")
        for row in overlap_rows(
            [d.metrics[metric] for d in human], [d.metrics[metric] for d in ai]
        ):
            print(f"  {row}")

    # The gate under three readings of "per-metric AUC". They are reported
    # together because they are not interchangeable and, on this corpus, they do
    # not agree: open_qa keeps only 116 of its 3242 rows once human answers are
    # held to 50+ words, so its cell is a heavily selected sample that a
    # macro-average weights as heavily as reddit_eli5's 600.
    variants = {
        "macro (domain-balanced, degenerate cells dropped)": lambda m: separation(
            results[m]["auc_macro"]
        ),
        "pooled (document-weighted)": lambda m: separation(results[m]["auc_pooled"]),
        "macro over all cells, keeping the degenerate ones": lambda m: separation(
            sum(results[m]["auc_all_cells"].values()) / len(results[m]["auc_all_cells"])
        ),
    }
    gates = {}
    print("\ngate sensitivity")
    for name, score_of in variants.items():
        survivors = [
            m for m in METRICS if score_of(m) >= AUC_BAR and not results[m]["untested_by_corpus"]
        ]
        deduped = list(survivors)
        for a, b in collinear:
            if a in deduped and b in deduped:
                deduped.remove(a if score_of(a) < score_of(b) else b)
        gate = decide(deduped, {"sentence_cv", "paragraph_cv"})
        gates[name] = {
            "separation": {m: round(score_of(m), 4) for m in METRICS},
            "survivors": survivors,
            "survivors_deduped": deduped,
            "gate": gate,
        }
        print(f"  {name}")
        print(f"    survivors: {deduped or 'none'}")
        print(f"    -> {gate}")

    summary = {
        "corpus": {
            "documents": len(docs),
            "human": len(human),
            "ai": len(ai),
            "mean_words_human": round(mh, 1),
            "mean_words_ai": round(ma, 1),
            "domains": list(DOMAINS),
            "source": "Hello-SimpleAI/HC3 (CC-BY-SA-4.0)",
        },
        "bar": {"auc": AUC_BAR, "collinear_rho": COLLINEAR_RHO},
        "metrics": results,
        "spearman": rho,
        "collinear_pairs": collinear,
        "measurable_floor": MEASURABLE_FLOOR,
        "gates": gates,
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "hc3_phase0.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nwrote {OUT / 'hc3_phase0.json'}")

    if plot:
        draw(human, ai)
    return summary


def decide(deduped: list[str], cv_family: set[str]) -> str:
    """The phase 0 exit condition, transcribed from PLAN.md."""
    if len(deduped) < 2:
        return "phase X -- fewer than two independent metrics survive; nothing left to composite"
    if set(deduped) <= cv_family:
        return "phase X -- only the CV family survives; the composite would restate the docstring"
    return "phase 1 -- two or more survive spanning both families; rebuild over the survivors"


def draw(human: list[Doc], ai: list[Doc]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(METRICS), figsize=(4 * len(METRICS), 3.2))
    for ax, metric in zip(axes, METRICS):
        hv = [d.metrics[metric] for d in human]
        av = [d.metrics[metric] for d in ai]
        lo, hi = min(min(hv), min(av)), max(max(hv), max(av))
        bins = [lo + (hi - lo) * i / 40 for i in range(41)]
        ax.hist(hv, bins=bins, alpha=0.55, label="human", density=True)
        ax.hist(av, bins=bins, alpha=0.55, label="AI", density=True)
        ax.set_title(metric, fontsize=10)
        ax.legend(fontsize=8)
    fig.tight_layout()
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / "hc3_phase0_overlap.png", dpi=120)
    print(f"wrote {OUT / 'hc3_phase0_overlap.png'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plot", action="store_true", help="also write a PNG overlap plot")
    args = parser.parse_args()
    report(build_corpus(), args.plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
