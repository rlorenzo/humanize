import type { EngineInterface, Register } from 'claude-code'

// Shows the humanize score of each prose file Claude writes: a status line
// under the prompt, and a toast when the file is over the threshold. Display
// only: the PostToolUse settings hook in hooks.json is still what tells Claude.
//
// ponytail: scores each write a second time (the settings hook already did),
// about 200 ms of Python startup off the critical path; share one result if a
// mod event ever exposes the settings hook's stdout.

type Score = {
  score: number
  verdict: string
  threshold: number
  top_offenders: { pattern: string; weighted: number }[]
}

const PROSE = /\.(md|tex|rst|txt)$/i

export const register: Register = on => {
  for (const tool of ['Edit', 'Write'] as const) {
    on('tool.call', { tool }, async ($, e, next) => {
      const ran = await next(e)
      if (ran.deny === undefined && !ran.isError && PROSE.test(e.file_path)) {
        // Display only, so a failure (no bash on PATH, odd output) stays silent.
        show($, e.file_path).catch(() => {})
      }
      return ran
    })
  }
}

async function show($: EngineInterface, file: string) {
  const { stdout } = await $.process.run(
    ['bash', `${$.plugin.root}/hooks/humanize-post-write.sh`, '--json'],
    { stdin: JSON.stringify({ tool_input: { file_path: file } }) },
  )
  // Empty when the hook skipped the file or found no Python 3.14+ (the
  // SessionStart message covers that case).
  if (!stdout.trim()) return
  const s: Score = JSON.parse(stdout)
  const name = file.split(/[\\/]/).pop()
  const top = s.top_offenders.slice(0, 3).map(o => o.pattern).join(', ')
  const line = `humanize ${name}: ${s.score}/100 ${s.verdict}${top ? ` · ${top}` : ''}`
  $.ui.status(line)
  if (s.score > s.threshold) $.ui.toast(line, { timeoutMs: 8000 })
}
