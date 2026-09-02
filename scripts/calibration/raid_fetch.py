#!/usr/bin/env python3
"""Fetch a stratified RAID sample over HTTP Range, without the 11.8 GB download.

RAID-train is one CSV of 11,779,491,051 bytes on the HuggingFace hub. Three
things about it drive everything here, all established by probing the live file:

  * It is sorted **domain major, attack minor, model minor still**. So a
    contiguous slice is not a sample of anything -- one 2.5 MB read at offset 0
    returns 1,331 rows that are all domain=abstracts, all attack=none, and all
    llama-chat. Chunks must be spread across a domain's clean region or the
    result measures one generator and reports it as "AI text".
  * `attack` is NOT monotone inside a domain. The sort is domain, then model,
    then attack, so each model block opens with its unattacked rows and then runs
    through the adversarial variants -- probing news across what looked like one
    clean region returned none, whitespace, synonym, perplexity_misspelling,
    number, homoglyph, article_deletion, then none again. So unattacked rows must
    be found by scanning, never by bisection: bisecting for "where attack=none
    ends" assumes monotonicity, and on news it returns a 586 MB "clean" region
    that is mostly adversarial -- 528 rows, none of them human.
  * Every source_id group carries its human row alongside the generations made
    from it, so human and AI text can be paired on a shared source.

The convenient access paths do not work. `datasets-server` and a plain
`load_dataset(..., streaming=True)` both read HuggingFace's auto-converted
parquet branch, whose directory is literally named `partial-train`: it self
-reports "partial": true, holds 2.27M of the rows, and covers only abstracts,
books, news and poetry. Probed at eleven offsets, it never once returned reddit,
reviews, recipes or wiki -- which are exactly the long-form multi-paragraph
domains phase 1 needs, since HC3 could not test paragraph_cv at all. Anyone
taking the documented route gets a domain-biased sample and no warning.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

# Pinned to a commit, not to `main`. Every offset below is a byte offset into one
# exact revision of train.csv, and the recorded AUCs are the AUCs of the rows at
# those offsets. A push to the dataset would silently resample the corpus and
# leave the committed evidence describing documents that were never measured.
REVISION = "865cac74188466cb0c3b7574a10204007b57a459"
URL = f"https://huggingface.co/datasets/liamdugan/raid/resolve/{REVISION}/train.csv"
SIZE = 11_779_491_051

COLUMNS = (  # noqa: SIM905 -- kept as the header line it mirrors
    "id adv_source_id source_id model decoding repetition_penalty "
    "attack domain title prompt generation"
).split()

# Domain order in the file, confirmed by probing. Needed for the bisection: the
# search compares domains by position, so it must match the file exactly.
DOMAIN_ORDER = ("abstracts", "books", "news", "poetry", "recipes", "reddit", "reviews", "wiki")

# A row starts with a UUID at the start of a line. Needed because `generation`
# contains newlines, so a mid-file chunk cannot be split on "\n" -- this anchor
# is what makes an arbitrary byte offset parseable at all.
ROW_START = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12},", re.MULTILINE
)

PROBE_BYTES = 60_000

# domain_bounds resolves to 4 MB, and each domain opens with its unattacked human
# block, so the computed start can land just past it. Reads for the human class
# begin this far back and filter by domain -- without the margin, news and poetry
# returned zero human rows while their 494 and 489 unattacked human documents sat
# a few MB above the boundary.
START_MARGIN = 12_000_000


def resolve() -> str:
    """The hub returns a signed CDN redirect; Range requests go to that target."""
    out = subprocess.run(
        ["curl", "-sSI", "--fail", "-m", "60", URL], capture_output=True, text=True, check=True
    ).stdout
    for line in out.splitlines():
        if line.lower().startswith("location:"):
            return line.split(":", 1)[1].strip()
    return URL


def fetch(loc: str, start: int, length: int) -> str:
    """One Range read, or an exception. Never a short or substituted body.

    `--fail` and the length check are both load-bearing. Without `--fail`, curl
    exits 0 on an HTTP error and hands back the error page as the body; `parse`
    finds no row anchor in it, returns no rows, and `sample_domain` treats the
    failed read as a domain that simply had nothing in it. The cache is then
    written short and the AUCs computed from it look fine. A server that ignores
    the Range header and replies 200 with the whole 11.8 GB file fails the same
    way from the other direction, which is what the length check catches.

    `length` is the span, not the byte count: an HTTP Range is inclusive of both
    endpoints, so a read of `length` returns `length + 1` bytes. Left as it is
    deliberately. Every offset in this module, the committed AUCs, and the
    revision pin above all describe the rows these exact ranges returned, so
    trimming a byte off each read would mean re-fetching the whole cache and
    regenerating the evidence to buy nothing measurable -- the extra byte is one
    part in 60,000 of a probe, and `parse` discards the truncated row at each cut
    edge regardless. The `want` calculation below is the honest count.
    """
    end = min(SIZE - 1, start + length)
    out = subprocess.run(
        ["curl", "-sS", "--fail", "-m", "180", "-H", f"Range: bytes={start}-{end}", loc],
        capture_output=True,
        check=True,
    ).stdout
    want = end - start + 1
    if len(out) != want:
        raise OSError(f"range {start}-{end} returned {len(out)} bytes, expected {want}")
    return out.decode("utf-8", errors="replace")


def parse(raw: str) -> list[dict[str, str]]:
    """Rows fully contained in a chunk.

    Drops everything before the first row anchor and the final row, both of which
    a byte-range cut leaves truncated. Rows whose field count is not 11 are
    dropped too: an embedded-newline row split across the chunk boundary can
    still parse into the wrong shape.
    """
    match = ROW_START.search(raw)
    if not match:
        return []
    rows = [r for r in csv.reader(io.StringIO(raw[match.start() :])) if len(r) == len(COLUMNS)]
    return [dict(zip(COLUMNS, r)) for r in rows[:-1]]


def _probe(loc: str, offset: int, *columns: str) -> tuple[str, ...] | None:
    """The most common value of each column among the rows at a byte offset.

    Counter rather than max over a set: the set spelling rescanned the rows once
    per candidate value, and broke ties by set iteration order, which is
    hash-seeded and so need not repeat between runs of the same fetch.
    """
    rows = parse(fetch(loc, offset, PROBE_BYTES))
    if not rows:
        return None
    return tuple(Counter(r[c] for r in rows).most_common(1)[0][0] for c in columns)


def _bisect(loc: str, lo: int, hi: int, is_before: callable) -> int:
    """Smallest offset in [lo, hi] where is_before(probe) stops holding.

    Only valid for a monotone property. Domain is monotone -- the file is sorted
    by it -- so this is used for domain boundaries and nothing else.
    """
    while hi - lo > 4_000_000:
        mid = (lo + hi) // 2
        got = None
        for nudge in range(4):  # a window can land mid-row and not parse
            got = _probe(loc, min(hi, mid + nudge * PROBE_BYTES), "domain", "attack")
            if got is not None:
                break
        if got is None:
            # Unresolvable window: narrow from the top rather than advancing lo,
            # so an unparseable stretch can never push the boundary past the
            # answer and inflate the region.
            hi = mid
            continue
        if is_before(got):
            lo = mid
        else:
            hi = mid
    return hi


def domain_bounds(loc: str, domain: str) -> tuple[int, int]:
    """Byte range of one domain: [its start, the next domain's start).

    The end is exclusive in every case, the last domain included -- it returns
    SIZE rather than SIZE - 1 so a caller never has to know which domain it is
    holding to know what the bound means.
    """
    index = DOMAIN_ORDER.index(domain)
    earlier = set(DOMAIN_ORDER[:index])
    upto = set(DOMAIN_ORDER[: index + 1])
    start = 0 if index == 0 else _bisect(loc, 0, SIZE - 1, lambda got: got[0] in earlier)
    if index == len(DOMAIN_ORDER) - 1:
        return start, SIZE
    end = _bisect(loc, start, SIZE - 1, lambda got: got[0] in upto)
    return start, end


def _probe_at(args: tuple[str, int]) -> tuple[int, str, str] | None:
    loc, offset = args
    got = _probe(loc, offset, "model", "attack")
    return None if got is None else (offset, *got)


def scan(
    loc: str, start: int, end: int, probes: int = 40, workers: int = 12
) -> list[tuple[int, str, str]]:
    """(offset, dominant model, dominant attack) evenly across a domain.

    The map that replaces the bisection for attack. Unattacked rows recur once
    per model block rather than sitting in one range, and RAID attacks human text
    too, so an unattacked human row is roughly one part in 144 of a domain -- the
    only reliable way to find one is to look in many places.

    Probes run concurrently. Each is a separate Range request against a CDN and
    is almost entirely latency, so serially a 180-probe scan costs minutes per
    domain and the whole build runs longer than the analysis it feeds.
    """
    # i/probes, not i/(probes-1): `end` is the next domain's start, and _bisect
    # only resolves it to 4 MB, so it is an offset that already probed as the
    # next domain. Spreading to i/(probes-1) put the last probe exactly on it,
    # which read the wrong domain's rows -- a wasted probe, and worse, a model
    # census entry that could win an AI chunk slot whose 1.2 MB read was then
    # discarded by the domain filter. A half-open spread stops one step short.
    offsets = [start + (end - start) * i // probes for i in range(probes)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(_probe_at, [(loc, o) for o in offsets])
    return [r for r in results if r is not None]


def sample_domain(
    loc: str,
    domain: str,
    human_bytes: int = 8_000_000,
    ai_chunks: int = 10,
    ai_bytes: int = 1_200_000,
    probes: int = 40,
    dense_probes: int = 180,
) -> list[dict[str, str]]:
    """Unattacked rows for one domain: human blocks, plus one block per generator.

    Read deliberately rather than uniformly, for two reasons.

    Human rows are the scarce class. RAID attacks human text too -- the human
    offsets found in news carried upper_lower and article_deletion -- so an
    unattacked human row is roughly one part in 144 of a domain, and a 40-probe
    scan can miss it entirely. When the first scan finds none, a denser one runs
    before giving up, and the caller sees the shortfall in the reported counts.

    AI rows are abundant but blocked by model, so evenly spaced offsets collect
    whichever generators the spacing happens to hit -- in practice the smaller
    open ones, dropping chatgpt, gpt4, gpt3 and cohere entirely. A sample skewed
    to weaker generators flatters these metrics, the optimistic bias the plan
    warns about for RAID as a whole. Offsets are therefore chosen round-robin
    across the distinct models the scan saw.
    """
    start, end = domain_bounds(loc, domain)
    found = scan(loc, start, end, probes)
    clean = [(o, m) for o, m, a in found if a == "none"]
    human_offsets = [o for o, m in clean if m == "human"]

    if not human_offsets:
        found = scan(loc, start, end, dense_probes)
        clean = [(o, m) for o, m, a in found if a == "none"]
        human_offsets = [o for o, m in clean if m == "human"]

    by_model: dict[str, list[int]] = defaultdict(list)
    for offset, model in clean:
        if model != "human":
            by_model[model].append(offset)

    # Round-robin over generators, so every model the scan saw contributes before
    # any model contributes twice.
    ai_offsets: list[int] = []
    rank = 0
    while len(ai_offsets) < ai_chunks and any(len(v) > rank for v in by_model.values()):
        for model in sorted(by_model):
            if len(by_model[model]) > rank and len(ai_offsets) < ai_chunks:
                ai_offsets.append(by_model[model][rank])
        rank += 1

    rows: list[dict[str, str]] = []
    # The domain's opening block first: this is where the unattacked human rows
    # live, and a scan cannot be relied on to find them (700 probes across news
    # hit 19 human offsets, every one of them adversarial).
    rows.extend(parse(fetch(loc, max(0, start - START_MARGIN), START_MARGIN + human_bytes)))
    for offset in human_offsets[:2]:
        rows.extend(parse(fetch(loc, max(0, offset - PROBE_BYTES), human_bytes)))
    for offset in ai_offsets:
        rows.extend(parse(fetch(loc, max(start, offset - PROBE_BYTES), ai_bytes)))

    keep = [r for r in rows if r["attack"] == "none" and r["domain"] == domain]
    unique: dict[str, dict[str, str]] = {}
    for row in keep:  # overlapping reads can return the same row twice
        unique[row["id"]] = row
    return list(unique.values())
