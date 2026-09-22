#!/usr/bin/env bash
# install.sh — install humanize globally into Claude Code config.
# Idempotent. Re-run to update.
#
# Prefer the plugin install when you can (see README § Install) — it registers
# the hook automatically. This script remains for file-based installs and for
# the always-on rule, which the plugin system does not manage.

set -e

CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[install] CLAUDE_HOME = $CLAUDE_HOME"
echo "[install] REPO_DIR    = $REPO_DIR"

# The README's file-based install clones the repo *to* $CLAUDE_HOME/skills/humanize,
# so several of the copies below have the same file as source and destination. Plain
# cp treats that as an error, which under `set -e` aborted the whole install on the
# very first file and left nothing installed. Skip those; copy everything else.
install_into() {
    local src=$1 dir=$2
    local dst
    dst="$dir/$(basename "$src")"
    if [[ -e "$dst" ]] && [[ "$src" -ef "$dst" ]]; then
        return 0
    fi
    cp "$src" "$dst"
}

# 1. Skill (SKILL.md references patterns/core.md, so ship both)
mkdir -p "$CLAUDE_HOME/skills/humanize/patterns"
install_into "$REPO_DIR/SKILL.md" "$CLAUDE_HOME/skills/humanize"
install_into "$REPO_DIR/patterns/core.md" "$CLAUDE_HOME/skills/humanize/patterns"
mkdir -p "$CLAUDE_HOME/skills/humanize/scripts"
install_into "$REPO_DIR/humanize_anti_slop/humanize_score.py" "$CLAUDE_HOME/skills/humanize/scripts"
install_into "$REPO_DIR/humanize_anti_slop/burstiness_check.py" "$CLAUDE_HOME/skills/humanize/scripts"
chmod +x "$CLAUDE_HOME/skills/humanize/scripts/humanize_score.py"
chmod +x "$CLAUDE_HOME/skills/humanize/scripts/burstiness_check.py"
echo "[install] skill installed at $CLAUDE_HOME/skills/humanize/"

# 2. Subagent
mkdir -p "$CLAUDE_HOME/agents"
install_into "$REPO_DIR/agents/humanizer-reviewer.md" "$CLAUDE_HOME/agents"
echo "[install] agent installed at $CLAUDE_HOME/agents/humanizer-reviewer.md"

# 3. Always-on rule
mkdir -p "$CLAUDE_HOME/rules"
install_into "$REPO_DIR/rules/10-anti-slop.md" "$CLAUDE_HOME/rules"
echo "[install] rule installed at $CLAUDE_HOME/rules/10-anti-slop.md"

# 4. Hook (the user must register this in settings.json manually; we just place the script)
mkdir -p "$CLAUDE_HOME/hooks"
install_into "$REPO_DIR/hooks/humanize-post-write.sh" "$CLAUDE_HOME/hooks"
chmod +x "$CLAUDE_HOME/hooks/humanize-post-write.sh"
echo "[install] hook script at $CLAUDE_HOME/hooks/humanize-post-write.sh"

# 5. Smoke-test the scorer
echo ""
echo "[install] smoke-test the scorer..."
SMOKE="$(mktemp -t humanize_smoke.XXXXXX)"
trap 'rm -f "$SMOKE"' EXIT
echo "Studies show that this delves into the intricate landscape." > "$SMOKE"
python3 "$CLAUDE_HOME/skills/humanize/scripts/humanize_score.py" "$SMOKE" || true
python3 "$CLAUDE_HOME/skills/humanize/scripts/burstiness_check.py" "$SMOKE" || true

echo ""
echo "[install] done. Next steps:"
echo "  1. Wire the hook into ~/.claude/settings.json: copy the PostToolUse entry"
echo "     from hooks/hooks.json, replacing \${CLAUDE_PLUGIN_ROOT} with ~/.claude."
echo "     Skip this step if you installed via the plugin."
echo "  2. Try: /humanize [paste some AI text]"
