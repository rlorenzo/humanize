# Calibration spikes

Throwaway evaluation code for the `signature_score` recalibrate-or-retire work
(`PLAN.md`, and the board note it points at). Outside the packaged tree:
`[tool.hatch.build.targets.wheel]` sets `packages = ["humanize_anti_slop"]`, so
the wheel is an allowlist and nothing here can reach it. **Delete this directory
with `PLAN.md` when the work lands.**

| file | what it is |
|---|---|
| `corpus.py` | Shared buckets, length-matching, AUC, bootstrap CI, Spearman. Both phases import it so their numbers are comparable. |
| `hc3_spike.py` | Phase 0 — the HC3 go/no-go. |
| `raid_fetch.py` | RAID access by HTTP Range. Read its docstring before touching it: the documented access paths return a domain-biased partial slice. |
| `raid_build_cache.py` | Fetches the RAID sample once into `data/raid_cache.jsonl`. |
| `raid_phase1.py` | Phase 1 — RAID validation, `--chat-only` for instruction-tuned generators. |
| `results/` | Committed evidence: `hc3_phase0.json` and the overlap plot. |
| `data/` | Downloaded corpora. Gitignored; regenerate with the command below. |

## Reproducing phase 0

```sh
mkdir -p scripts/calibration/data && cd scripts/calibration/data
for f in finance medicine open_qa reddit_eli5 wiki_csai; do
  curl -sSL -o "$f.jsonl" \
    "https://huggingface.co/datasets/Hello-SimpleAI/HC3/resolve/main/$f.jsonl"
done
cd .. && uv run --with matplotlib python hc3_spike.py --plot
```

Deterministic: every sample is seeded at 20260901, so a re-run reproduces the
committed JSON exactly. Drop `--with matplotlib --plot` for the text-only report;
the core analysis is pure stdlib.

## Corpora and licences

- **HC3** — [Hello-SimpleAI/HC3](https://huggingface.co/datasets/Hello-SimpleAI/HC3),
  CC-BY-SA-4.0. Human-expert vs ChatGPT answers across five domains.
- **RAID** — [liamdugan/raid](https://huggingface.co/datasets/liamdugan/raid), MIT.
  RAID-train only; RAID-test is unlabelled and reserved for its public leaderboard.

## Reproducing phase 1

```sh
cd scripts/calibration
uv run python raid_build_cache.py     # tens of MB of Range reads, ~10 min
uv run python raid_phase1.py
uv run python raid_phase1.py --chat-only
```

`train.csv` is **11.8 GB**, not the 802 MB the dataset card quotes for the
non-adversarial subset — the hub file includes every adversarial variant. Do not
reach for `datasets-server` or `load_dataset(..., streaming=True)`: both read
HuggingFace's auto-converted parquet branch, whose directory is named
`partial-train`, and which covers only abstracts, books, news and poetry. Probed
at eleven offsets it never returned reddit, recipes, reviews or wiki — the
long-form domains phase 1 most needed.

Neither is checked in. The only corpus text that enters the repo is the phase 3
fixture set, which carries its own attribution and licence note.
