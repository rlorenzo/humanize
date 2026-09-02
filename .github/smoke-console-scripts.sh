#!/usr/bin/env bash
# Run both console scripts from an installed venv and require a clean exit.
#
# The scorers exit 1 on slop by design, so a real failure would be
# indistinguishable from a hit unless the gate is disarmed: humanize-score takes
# --threshold 100, burstiness-check takes --fail-on=never (its --threshold was
# retired with signature_score).
set -euo pipefail

VENV="$1"
SAMPLE="$(mktemp -t humanize-smoke.XXXXXX.md)"
trap 'rm -f "$SAMPLE"' EXIT
printf 'Studies show that this delves into the intricate landscape.\n' > "$SAMPLE"

"$VENV/bin/humanize-score" --help > /dev/null
"$VENV/bin/burstiness-check" --help > /dev/null
"$VENV/bin/humanize-score" --threshold 100 "$SAMPLE"
"$VENV/bin/burstiness-check" --fail-on=never "$SAMPLE"
# The retired flag stays accepted for one minor version; this asserts that a
# caller still passing it gets a warning and a working run, not exit 2. Captured
# rather than piped to grep: grep -q exits on the first match, which SIGPIPEs the
# writer, and under `set -o pipefail` that fails the script.
deprecation="$("$VENV/bin/burstiness-check" --threshold 100 "$SAMPLE" 2>&1)"
case "$deprecation" in
  *deprecated*) ;;
  *) echo "expected --threshold to warn that it is deprecated" >&2; exit 1 ;;
esac
"$VENV/bin/humanize-score" --json --threshold 100 "$SAMPLE" | "$VENV/bin/python" -c 'import json,sys; assert json.load(sys.stdin)["score"] > 0'
