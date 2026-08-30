#!/usr/bin/env bash
# Run both console scripts from an installed venv and require a clean exit.
#
# --threshold 100 is what makes this a gate: without it the scorers exit 1 on
# slop by design, so any real failure would be indistinguishable from a hit.
set -euo pipefail

VENV="$1"
SAMPLE="$(mktemp -t humanize-smoke.XXXXXX.md)"
trap 'rm -f "$SAMPLE"' EXIT
printf 'Studies show that this delves into the intricate landscape.\n' > "$SAMPLE"

"$VENV/bin/humanize-score" --help > /dev/null
"$VENV/bin/burstiness-check" --help > /dev/null
"$VENV/bin/humanize-score" --threshold 100 "$SAMPLE"
"$VENV/bin/burstiness-check" --threshold 100 "$SAMPLE"
"$VENV/bin/humanize-score" --json --threshold 100 "$SAMPLE" | "$VENV/bin/python" -c 'import json,sys; assert json.load(sys.stdin)["score"] > 0'
