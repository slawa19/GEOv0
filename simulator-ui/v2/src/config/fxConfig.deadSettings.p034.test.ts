/**
 * POLICY GUARD (034 S8, F-034-D15; reproducer of T3404): `FX_CONFIG` holds no setting that nothing reads.
 *
 * `FX_CONFIG.tx` and `FX_CONFIG.clearing` looked like the source of the FX timings while the FX code used its own
 * literals; a structure test of the dead object kept it looking alive. A setting that is not read is a false
 * statement about what configures the screen.
 *
 * What this guard checks: every top-level group of `FX_CONFIG` is MENTIONED as `FX_CONFIG.<group>` in some
 * non-test source under `src/` outside its own declaration.
 * What it does not check: that a mention is a real use, that each key INSIDE a group is read, or that the FX code
 * has no literals of its own. If it fails: delete the group, or make the code that owns the behaviour read it;
 * `git grep -n "FX_CONFIG" -- simulator-ui/v2/src` shows which.
 */
import { describe, expect, it } from 'vitest'

import { FX_CONFIG } from './fxConfig'

const sources = import.meta.glob('../**/*.{ts,vue}', { query: '?raw', import: 'default', eager: true }) as Record<
  string,
  string
>

const isFxConfigModule = (path: string) => /(^|\/)fxConfig\.ts$/.test(path)

function productSources(): Array<[string, string]> {
  return Object.entries(sources).filter(([path]) => !path.endsWith('.test.ts'))
}

/** `fxConfig.ts` without the `FX_CONFIG = { ... } as const` literal: what is left are its own readers. */
function withoutTheDeclaration(path: string, text: string): string {
  if (!isFxConfigModule(path)) return text
  const start = text.indexOf('export const FX_CONFIG')
  const end = text.indexOf('} as const', start)
  return start >= 0 && end > start ? text.slice(0, start) + text.slice(end) : text
}

function groupsNobodyMentions(): string[] {
  const haystack = productSources().map(([path, text]) => withoutTheDeclaration(path, text))
  return Object.keys(FX_CONFIG).filter((group) => {
    const mention = new RegExp(`\\bFX_CONFIG\\.${group}\\b(?!\\s*[:=])`)
    return !haystack.some((text) => text.split('\n').some((line) => !/^\s*export type\b/.test(line) && mention.test(line)))
  })
}

describe('POLICY GUARD: FX_CONFIG has no unread group', () => {
  it('every group of FX_CONFIG is read by product code', () => {
    expect(groupsNobodyMentions(), 'groups of FX_CONFIG that no product source reads').toEqual([])
  })

  it('anti-vacuum: the guard sees the sources and the reader of `intensity`', () => {
    const paths = productSources().map(([path]) => path)
    expect(paths.length).toBeGreaterThan(100)
    expect(paths.some(isFxConfigModule)).toBe(true)
    expect(Object.keys(FX_CONFIG)).toContain('intensity')
    expect(groupsNobodyMentions()).not.toContain('intensity')
  })
})
