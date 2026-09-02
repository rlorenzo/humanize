#!/usr/bin/env python3
"""Fetch the RAID phase 1 sample once and cache it, so analysis re-runs are free.

Writes scripts/calibration/data/raid_cache.jsonl (gitignored). Tens of MB of
HTTP Range reads against an 11.8 GB file; see raid_fetch for why the documented
access paths cannot be used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import raid_fetch as rf

OUT = Path(__file__).parent / "data" / "raid_cache.jsonl"
KEEP = ("id", "source_id", "model", "decoding", "attack", "domain", "generation")


def main() -> int:
    """Build the cache in a temporary file and move it into place at the end.

    The build is eight domains of Range reads over several minutes, and
    raid_phase1 only checks that the cache exists. Writing OUT directly means a
    Ctrl-C or a dropped connection leaves a truncated cache that the analysis
    will happily read as the whole sample.
    """
    loc = rf.resolve()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".jsonl.partial")
    total = 0
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for domain in rf.DOMAIN_ORDER:
                rows = rf.sample_domain(loc, domain)
                humans = sum(1 for r in rows if r["model"] == "human")
                for row in rows:
                    fh.write(json.dumps({k: row[k] for k in KEEP}) + "\n")
                total += len(rows)
                print(f"{domain:<10} rows={len(rows):<6} humans={humans:<6} cached", flush=True)
        os.replace(tmp, OUT)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    print(f"total {total} rows -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
