#!/usr/bin/env bash
# humanize-post-write.sh
# PostToolUse hook for Claude Code. Fires after Write/Edit and pipes the tool
# JSON from stdin to the scorer's --hook mode, which scores prose files
# (.md, .tex, .rst, .txt) outside .claude/ and emits
# hookSpecificOutput.additionalContext JSON when the score exceeds the
# threshold (HUMANIZE_THRESHOLD, default 60) so Claude sees the warning.
#
# Installed as a plugin, hooks/hooks.json registers this automatically. For a
# manual install, copy the PostToolUse entry from hooks/hooks.json into
# ~/.claude/settings.json, replacing ${CLAUDE_PLUGIN_ROOT} with the install dir.
#
# Manual test:
#   echo '{"tool_input":{"file_path":"draft.md"}}' | ./humanize-post-write.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Scorer lookup order: explicit override, sibling of this script (repo/plugin
# layout), then the copy install.sh places under ~/.claude/skills.
if [[ -n "${HUMANIZE_SCORER:-}" ]]; then
    SCORER="$HUMANIZE_SCORER"
elif [[ -f "$SCRIPT_DIR/../humanize_anti_slop/humanize_score.py" ]]; then
    SCORER="$SCRIPT_DIR/../humanize_anti_slop/humanize_score.py"
elif [[ -f "$SCRIPT_DIR/../scripts/humanize_score.py" ]]; then
    # Pre-1.1.1 repo/plugin layout.
    SCORER="$SCRIPT_DIR/../scripts/humanize_score.py"
else
    SCORER="$HOME/.claude/skills/humanize/scripts/humanize_score.py"
fi

[[ -f "$SCORER" ]] || exit 0

# Cheap pre-filter: skip the python launch unless the payload plausibly names a
# prose file. Case-insensitive to match run_hook()'s suffix handling; tr keeps
# it portable to bash 3.2 (macOS), which lacks ${var,,}. False positives fall
# through; the scorer bails on them correctly.
INPUT=$(cat)
case "$(printf '%s' "$INPUT" | tr '[:upper:]' '[:lower:]')" in
    *.md\"*|*.tex\"*|*.rst\"*|*.txt\"*) : ;;
    *) exit 0 ;;
esac

printf '%s' "$INPUT" | python3 "$SCORER" --hook 2>/dev/null
exit 0
