import { describe, expect, test } from 'claude-code/testing'

const SLOP = {
  score: 91.7,
  verdict: 'heavy_slop',
  threshold: 60,
  top_offenders: [
    { pattern: 'not_x_but_y', weighted: 1.2 },
    { pattern: 'ai_vocabulary', weighted: 1 },
  ],
}

/**
 * Seats the engine beneath the mod: the write succeeds, the hook script prints
 * `stdout`, and every status line and toast is recorded.
 */
function world(on: any, stdout: string) {
  const seen = { runs: [] as string[][], status: [] as (string | undefined)[], toasts: [] as string[] }
  on('tool.call', () => ({ result: {} }))
  on('process.run', ($: unknown, e: { argv: string[] }) => {
    seen.runs.push(e.argv)
    return { value: { exitCode: 0, stdout, stderr: '' } }
  })
  on('ui.status', ($: unknown, e: { text: string | undefined }) => void seen.status.push(e.text))
  on('ui.toast', ($: unknown, e: { text: string }) => void seen.toasts.push(e.text))
  return seen
}

/** The mod scores after the tool call returns (so the write never waits); let it land. */
async function settle() {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

describe('register', () => {
  test('a sloppy write shows the score and patterns, and toasts', async ($, on) => {
    const seen = world(on, JSON.stringify(SLOP))
    await $.tool.call({ tool: 'Write', file_path: '/repo/docs/draft.md', content: 'x' })
    await settle()
    const line = 'humanize draft.md: 91.7/100 heavy_slop · not_x_but_y, ai_vocabulary'
    expect(seen.runs[0].slice(-1)).toEqual(['--json'])
    expect(seen.status).toEqual([line])
    expect(seen.toasts).toEqual([line])
  })

  test('a clean write updates the status line without a toast', async ($, on) => {
    const seen = world(on, JSON.stringify({ ...SLOP, score: 0, verdict: 'clean', top_offenders: [] }))
    await $.tool.call({ tool: 'Edit', file_path: '/repo/README.md', old_string: 'a', new_string: 'b' })
    await settle()
    expect(seen.status).toEqual(['humanize README.md: 0/100 clean'])
    expect(seen.toasts).toEqual([])
  })

  test('code files are never scored', async ($, on) => {
    const seen = world(on, JSON.stringify(SLOP))
    await $.tool.call({ tool: 'Write', file_path: '/repo/app.ts', content: 'x' })
    await settle()
    expect(seen.runs).toEqual([])
  })

  test('output that is not JSON stays silent and the write still succeeds', async ($, on) => {
    const seen = world(on, 'Traceback (most recent call last):')
    const ran = await $.tool.call({ tool: 'Write', file_path: '/repo/draft.md', content: 'x' })
    await settle()
    expect(ran.deny).toBeUndefined()
    expect(seen.status).toEqual([])
  })

  test('a skipped file (no output) leaves the status alone', async ($, on) => {
    const seen = world(on, '')
    await $.tool.call({ tool: 'Write', file_path: '/repo/.claude/notes.md', content: 'x' })
    await settle()
    expect(seen.runs.length).toBe(1)
    expect(seen.status).toEqual([])
  })
})
