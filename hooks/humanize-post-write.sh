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

# Baked in by install.sh (Python literal replacement) for file-based installs. Left as a literal
# placeholder in the repo/plugin copy, where the sibling lookup below already
# finds the scorer. Never read from the environment: a cloned repo's
# .claude/settings.json can set arbitrary env vars, and this used to be
# HUMANIZE_SCORER / CLAUDE_HOME read at hook runtime, which let a repo point
# the hook at its own Python and get it executed with no prompt.
# shellcheck disable=SC2034  # install.sh replaces the token with a shell-quoted path
HUMANIZE_INSTALLED_SCORER=@@HUMANIZE_INSTALLED_SCORER@@

# Scorer lookup order: sibling of this script (repo/plugin layout, already
# trusted there), then the path install.sh baked in above.
if [[ -f "$SCRIPT_DIR/../humanize_anti_slop/humanize_score.py" ]]; then
    SCORER="$SCRIPT_DIR/../humanize_anti_slop/humanize_score.py"
else
    SCORER="$HUMANIZE_INSTALLED_SCORER"
fi

# Defense in depth: only ever execute a path that is absolute and resolves
# under the plugin root or the real Claude home, regardless of which branch
# above chose it. Blocks a tampered or unsubstituted placeholder outright.
case "$SCORER" in
    /*) ;;
    *)
        [[ -n "${HUMANIZE_DEBUG:-}" ]] && echo "[humanize:debug] scorer path not absolute: $SCORER" >&2
        exit 0
        ;;
esac

if [[ ! -f "$SCORER" ]]; then
    [[ -n "${HUMANIZE_DEBUG:-}" ]] && echo "[humanize:debug] no scorer found at $SCORER" >&2
    exit 0
fi

SCORER_DIR="$(cd "$(dirname "$SCORER")" && pwd -P)"
# shellcheck disable=SC2015  # intentional: || true just means "empty on failure", handled by the -z check below
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." 2>/dev/null && pwd -P || true)"
if [[ -z "$PLUGIN_ROOT" ]]; then
    # Same trap as CLAUDE_HOME_REAL below: an empty root would trust "/*".
    [[ -n "${HUMANIZE_DEBUG:-}" ]] && echo "[humanize:debug] cannot resolve plugin root" >&2
    exit 0
fi
# shellcheck disable=SC2015  # intentional: || true just means "empty on failure", handled by the -n check below
CLAUDE_HOME_REAL="$(cd "${HOME:-}/.claude" 2>/dev/null && pwd -P || true)"
TRUSTED=0
case "$SCORER_DIR" in
    "$PLUGIN_ROOT" | "$PLUGIN_ROOT"/*) TRUSTED=1 ;;
esac
# Checked separately: an empty CLAUDE_HOME_REAL (no ~/.claude yet) would
# otherwise turn the pattern into "/*" and trust every absolute path.
if [[ -n "$CLAUDE_HOME_REAL" ]]; then
    case "$SCORER_DIR" in
        "$CLAUDE_HOME_REAL" | "$CLAUDE_HOME_REAL"/*) TRUSTED=1 ;;
    esac
fi
if [[ "$TRUSTED" -ne 1 ]]; then
    [[ -n "${HUMANIZE_DEBUG:-}" ]] && echo "[humanize:debug] scorer outside trusted roots: $SCORER" >&2
    exit 0
fi

# Cheap pre-filter: skip the python launch unless the payload plausibly names a
# prose file. Case-insensitive to match run_hook()'s suffix handling; tr keeps
# it portable to bash 3.2 (macOS), which lacks ${var,,}. False positives fall
# through; the scorer bails on them correctly.
INPUT=$(cat)
case "$(printf '%s' "$INPUT" | tr '[:upper:]' '[:lower:]')" in
    *.md\"*|*.tex\"*|*.rst\"*|*.txt\"*) : ;;
    *)
        [[ -n "${HUMANIZE_DEBUG:-}" ]] && echo "[humanize:debug] payload names no prose file" >&2
        exit 0
        ;;
esac

# stderr is normally discarded so a scoring problem can never reach the
# transcript. Under HUMANIZE_DEBUG it is let through instead, which is the only
# way to tell a working hook from a silently broken one.
if [[ -n "${HUMANIZE_DEBUG:-}" ]]; then
    printf '%s' "$INPUT" | python3 "$SCORER" --hook
else
    printf '%s' "$INPUT" | python3 "$SCORER" --hook 2>/dev/null
fi
exit 0
