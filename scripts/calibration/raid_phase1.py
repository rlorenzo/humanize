#!/usr/bin/env python3
"""Phase 1 -- RAID validation of the metrics that survived phase 0.

Phase 0 (HC3) sent three questions here: does sentence_cv hold up outside Q&A
text, does lexical_diversity hold at 0.672 or drift, and what is paragraph_cv
actually worth -- HC3 could not test it, because 85% of its answers are a single
paragraph.

Same bar, same buckets, same length-matching and cell-validity rules as phase 0,
imported from corpus.py rather than re-derived, so the two AUCs are comparable.

Run raid_build_cache.py first. Usage:
    uv run scripts/calibration/raid_phase1.py [--plot]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import raid_fetch
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
    holdout_split,
    length_match,
    macro_auc,
    measurable,
    overlap_rows,
    separation,
    spearman,
    split_paragraphs,
    to_doc,
)
from hc3_spike import MEASURABLE_FLOOR, decide

CACHE = Path(__file__).parent / "data" / "raid_cache.jsonl"

# Instruction-tuned generators. Not a post-hoc subgroup: this tool exists to
# check prose written through an assistant, so these are its actual target and
# the base models are the off-target half of RAID. Reported separately because
# the two halves disagree sharply -- sentence_cv separates chat output far better
# than base-model output, which is the opposite of the "deviation shrinks as
# models improve" expectation the plan carried in.
CHAT_MODELS = frozenset(
    {"chatgpt", "gpt4", "llama-chat", "mpt-chat", "mistral-chat", "cohere-chat"}
)
OUT = Path(__file__).parent / "results"
PAIRS_PER_DOMAIN = 600


def by_source(rows: list[dict]) -> dict[str, list[dict]]:
    """Rows grouped by the source text they were written from.

    One AI generation per source, so a single source text cannot contribute three
    AI documents against one human document, and each domain's AUC reflects style
    rather than which topics were sampled for which class. Both callers pair
    within these groups.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["source_id"]].append(row)
    return groups


def load(chat_only: bool = False) -> tuple[list[Doc], list[dict], dict]:
    if not CACHE.exists():
        raise SystemExit(f"missing {CACHE}; run raid_build_cache.py first")

    rows = [json.loads(line) for line in CACHE.open(encoding="utf-8")]
    # Restrict the corpus once, here, rather than at the pair-selection step. When
    # only that step honoured chat_only, everything computed from `rows` -- the
    # paragraph audit, the per-generator table, the generator census -- was still
    # the full corpus, so raid_phase1_chat.json carried chat-only metrics beside
    # full-corpus tables under a single "chat_only": true, and the two evidence
    # files' audits were byte-identical. One filter makes the whole file describe
    # one corpus.
    if chat_only:
        rows = [r for r in rows if r["model"] == "human" or r["model"] in CHAT_MODELS]
    generators = Counter(r["model"] for r in rows if r["model"] != "human")

    groups = by_source(rows)
    docs: list[Doc] = []
    kept_pairs = 0
    for source_id in sorted(groups):
        group = groups[source_id]
        humans = [r for r in group if r["model"] == "human"]
        machines = sorted(
            (r for r in group if r["model"] != "human"),
            key=lambda r: r["id"],
        )
        if not humans or not machines:
            continue
        # Both halves or neither. Admitting the scorable half of a pair whose
        # other half was too short to measure leaves an unpaired document that
        # length_match then pairs with some other source's text, which is the
        # shared-source control this loop exists to provide, quietly dropped.
        # kept_pairs is counted here, after both succeeded, so the reported
        # source_groups_with_both_classes matches what entered the analysis.
        pair = [
            to_doc(row["generation"], row["domain"], label)
            for row, label in ((humans[0], 0), (machines[0], 1))
        ]
        if any(doc is None for doc in pair):
            continue
        kept_pairs += 1
        docs.extend(pair)

    meta = {
        "revision": raid_fetch.REVISION,
        "cached_rows": len(rows),
        "source_groups_with_both_classes": kept_pairs,
        "generators": dict(generators.most_common()),
    }
    return docs, rows, meta


def generator_breakdown(rows: list[dict], min_pairs: int = 60) -> dict[str, dict]:
    """Separation per generator, each one length-matched against human text.

    The pooled figure mixes eleven generators, and RAID is dominated by the
    smaller base models, so it answers "can these metrics tell human text from
    2023-era open-model output" rather than the question the tool is pointed at.
    Splitting by generator is what shows that instruction tuning, not capability,
    drives sentence_cv separation -- the opposite of the assumption this work
    started from, and quotable enough that it has to be reproducible rather than
    computed once in a shell.

    Each generator is length-matched against the human side independently, using
    the same buckets as everything else, so a generator that happens to write
    longer cannot show up as a style difference.
    """
    per_generator: dict[str, list[Doc]] = defaultdict(list)
    for _, group in sorted(by_source(rows).items()):
        humans = [r for r in group if r["model"] == "human"]
        if not humans:
            continue
        # Measured once and reused across the group's generators; a source group
        # is fetched per domain, so replace() carries the pair's domain across
        # without re-measuring the human text.
        human_doc = to_doc(humans[0]["generation"], humans[0]["domain"], 0)
        if human_doc is None:
            continue
        for row in group:
            if row["model"] == "human":
                continue
            ai_doc = to_doc(row["generation"], row["domain"], 1)
            if ai_doc is not None:
                per_generator[row["model"]].extend(
                    (replace(human_doc, domain=row["domain"]), ai_doc)
                )

    out: dict[str, dict] = {}
    for generator, docs in per_generator.items():
        matched = length_match(docs)
        human = [d for d in matched if d.label == 0]
        ai = [d for d in matched if d.label == 1]
        if len(human) < min_pairs:
            continue
        out[generator] = {
            "pairs": len(human),
            "separation": {
                metric: round(
                    separation(
                        auc([d.metrics[metric] for d in ai], [d.metrics[metric] for d in human])
                    ),
                    4,
                )
                for metric in METRICS
            },
        }
    return dict(sorted(out.items(), key=lambda kv: kv[1]["separation"]["sentence_cv"]))


def paragraph_audit(docs_rows: list[dict]) -> dict:
    """How much multi-paragraph text each class actually has.

    paragraph_cv is undefined on single-paragraph text -- coefficient_of_variation
    returns 0.0 for one element -- so this decides whether RAID can answer the
    question HC3 could not, before any AUC is computed for it.

    Counts paragraphs the way the metric does, through split_paragraphs. A raw
    "\\n\\n" scan is a second, quieter definition of "paragraph": it misses CRLF
    and whitespace-only separators, and it reads the text before normalise() has
    touched it. The two disagreed on the committed evidence -- the audit reported
    0.0% multi-paragraph human documents while human paragraph_cv came out
    nonzero, which cannot both be true of the same corpus.
    """
    out = {}
    for label, name in ((0, "human"), (1, "ai")):
        side = [r for r in docs_rows if (r["model"] != "human") == label]
        if not side:
            continue
        multi = sum(1 for r in side if len(split_paragraphs(r["generation"])) > 1)
        out[name] = {
            "documents": len(side),
            "multi_paragraph_fraction": round(multi / len(side), 4),
        }
    return out


def print_preamble(
    para: dict, generators: dict, human: list[Doc], ai: list[Doc], train: list[Doc]
) -> None:
    """The three descriptive tables that precede the per-metric separation."""
    print("paragraph audit -- can RAID test paragraph_cv at all?")
    for name, v in para.items():
        print(
            f"  {name:<6} n={v['documents']:<6} multi-paragraph {v['multi_paragraph_fraction']:.1%}"
        )
    print()

    print("separation per generator, each length-matched against human text")
    print(f"  {'generator':<16}{'pairs':>7}{'sentence_cv':>13}{'lex_div':>10}")
    for generator, stats in generators.items():
        print(
            f"  {generator:<16}{stats['pairs']:>7}"
            f"{stats['separation']['sentence_cv']:>13.3f}"
            f"{stats['separation']['lexical_diversity']:>10.3f}"
        )
    print()

    print(f"  {'domain':<12}{'human':>7}{'AI':>7}{'mean w H':>11}{'mean w AI':>11}")
    for domain in sorted({d.domain for d in train}):
        h = [d for d in human if d.domain == domain]
        a = [d for d in ai if d.domain == domain]
        print(
            f"  {domain:<12}{len(h):>7}{len(a):>7}"
            f"{sum(d.words for d in h) / len(h):>11.1f}{sum(d.words for d in a) / len(a):>11.1f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plot", action="store_true")
    parser.add_argument(
        "--chat-only",
        action="store_true",
        help="pair humans only against instruction-tuned generators (this tool's target)",
    )
    args = parser.parse_args()

    docs, rows, meta = load(chat_only=args.chat_only)
    meta["chat_only"] = args.chat_only
    para = paragraph_audit(rows)
    generators = generator_breakdown(rows)

    docs = cap_domains(length_match(docs), PAIRS_PER_DOMAIN)
    train, held_out = holdout_split(docs)

    human = [d for d in train if d.label == 0]
    ai = [d for d in train if d.label == 1]
    print(
        f"cached rows: {meta['cached_rows']}, source groups with both classes: "
        f"{meta['source_groups_with_both_classes']}"
    )
    print(f"generators: {', '.join(meta['generators'])}")
    print(
        f"train {len(train)} docs ({len(human)}/{len(ai)}), held out {len(held_out)} "
        f"-- held out is NOT scored here; it is for the phase 2 acceptance check\n"
    )

    print_preamble(para, generators, human, ai, train)
    results: dict[str, dict] = {}
    print(f"\nper-metric separation (bar {AUC_BAR}, same as phase 0)")
    print(
        f"  {'metric':<21}{'pooled':>8}{'95% CI':>17}{'macro':>8}{'sep':>7}{'meas':>7}"
        f"{'human':>9}{'AI':>8}  verdict"
    )
    for metric in METRICS:
        hv = [d.metrics[metric] for d in human]
        av = [d.metrics[metric] for d in ai]
        pooled = auc(av, hv)
        lo, hi = auc_ci(av, hv)
        macro, per_domain, dropped, _ = macro_auc(train, metric, MEASURABLE_FLOOR)
        sep = separation(macro)
        meas = measurable(train, metric)
        passed = sep >= AUC_BAR and meas >= MEASURABLE_FLOOR
        results[metric] = {
            "auc_pooled": round(pooled, 4),
            "ci95_pooled": [round(lo, 4), round(hi, 4)],
            "auc_macro": round(macro, 4),
            "auc_by_domain": {k: round(v, 4) for k, v in per_domain.items()},
            "domains_dropped_as_degenerate": dropped,
            "separation": round(sep, 4),
            "measurable_fraction": round(meas, 4),
            "untested_by_corpus": meas < MEASURABLE_FLOOR,
            "passes_bar": passed,
            "direction": direction(macro),
            "human_mean": round(sum(hv) / len(hv), 4),
            "ai_mean": round(sum(av) / len(av), 4),
        }
        print(
            f"  {metric:<21}{pooled:>8.3f}  [{lo:.3f}, {hi:.3f}]{macro:>8.3f}{sep:>7.3f}"
            f"{meas:>7.2f}{sum(hv) / len(hv):>9.3f}{sum(av) / len(av):>8.3f}  "
            f"{'PASS' if passed else 'UNTESTED' if meas < MEASURABLE_FLOOR else 'fail'}"
        )

    domains = sorted({d.domain for d in train})
    print("\n  AUC by domain; '--' = cell dropped, one class degenerate")
    print(f"  {'metric':<21}" + "".join(f"{d[:10]:>12}" for d in domains))
    for metric in METRICS:
        cells = results[metric]["auc_by_domain"]
        print(
            f"  {metric:<21}"
            + "".join(f"{cells[d]:>12.3f}" if d in cells else f"{'--':>12}" for d in domains)
        )

    print("\nSpearman correlation across metrics")
    rho: dict[str, dict[str, float]] = {}
    print(f"  {'':<21}" + "".join(f"{m[:10]:>12}" for m in METRICS))
    for a_name in METRICS:
        row = []
        for b_name in METRICS:
            r = spearman([d.metrics[a_name] for d in train], [d.metrics[b_name] for d in train])
            rho.setdefault(a_name, {})[b_name] = round(r, 3)
            row.append(f"{r:>12.3f}")
        print(f"  {a_name:<21}{''.join(row)}")

    collinear = collinear_pairs(rho)
    print(f"\ncollinear pairs (|rho| >= {COLLINEAR_RHO}): {collinear or 'none'}")

    print("\ndistribution overlap (left = human, right = AI)")
    for metric in METRICS:
        print(f"\n  {metric}")
        for line in overlap_rows(
            [d.metrics[metric] for d in human], [d.metrics[metric] for d in ai]
        ):
            print(f"  {line}")

    survivors = [m for m in METRICS if results[m]["passes_bar"]]
    deduped = list(survivors)
    for a, b in collinear:
        if a in deduped and b in deduped:
            deduped.remove(a if results[a]["separation"] < results[b]["separation"] else b)
    gate = decide(deduped, {"sentence_cv", "paragraph_cv"})
    print(f"\nsurvivors: {deduped or 'none'}\n-> {gate}")

    summary = {
        "corpus": {
            "source": "liamdugan/raid RAID-train (MIT), attack=none, one AI generation per source",
            **meta,
            "train_documents": len(train),
            "held_out_documents": len(held_out),
            "domains": domains,
        },
        "paragraph_audit": para,
        "separation_by_generator": generators,
        "bar": {
            "auc": AUC_BAR,
            "collinear_rho": COLLINEAR_RHO,
            "measurable_floor": MEASURABLE_FLOOR,
        },
        "metrics": results,
        "spearman": rho,
        "collinear_pairs": collinear,
        "survivors": deduped,
        "gate": gate,
    }
    OUT.mkdir(exist_ok=True)
    name = "raid_phase1_chat.json" if args.chat_only else "raid_phase1.json"
    (OUT / name).write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nwrote {OUT / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
