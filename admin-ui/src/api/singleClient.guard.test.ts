import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

import { describe, expect, it } from 'vitest'

import { api } from './index'
import { realApi } from './realApi'

// POLICY GUARD (032 S4, 2026-10-07): the Admin UI has ONE API client, the real one. The mock client
// (`mockApi.ts`), its mode switch (`apiMode.ts`, `VITE_API_MODE`, the `admin-ui.apiModeOverride`
// localStorage key) and its fixtures were deleted; this guard fails if any of them, or another way
// to choose a client, comes back. It checks FORM - file names and source text under `src/` - not
// behaviour: whether the UI works against the backend is the e2e suite's job
// (`npm --prefix admin-ui run e2e` against `scripts/verify_admin_e2e.ps1`).

// Vitest runs from `admin-ui/` (vitest.config.ts); jsdom gives `import.meta.url` no file scheme.
const SRC = resolve(process.cwd(), 'src')

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(path)
    return /\.(ts|vue)$/.test(entry.name) ? [path] : []
  })
}

const SELF = join(SRC, 'api', 'singleClient.guard.test.ts')

describe('the Admin UI has a single, real API client', () => {
  it('exports the real client as `api`', () => {
    expect(api).toBe(realApi)
  })

  it('has no mock client, mode switch or fixture loader in src/', () => {
    const files = sourceFiles(SRC)
    expect(files.length, 'the scan found no source files: it would prove nothing').toBeGreaterThan(50)

    const forbiddenNames = files
      .map((path) => relative(SRC, path).split(sep).join('/'))
      .filter((path) => /(^|\/)(mockApi|apiMode|fixtures)\.ts$/.test(path))
    expect(forbiddenNames).toEqual([])

    const forbiddenText = /mockApi|apiMode|VITE_API_MODE|apiModeOverride|admin-fixtures/
    const offenders = files
      .filter((path) => path !== SELF)
      .filter((path) => forbiddenText.test(readFileSync(path, 'utf-8')))
      .map((path) => relative(SRC, path).split(sep).join('/'))
    expect(offenders).toEqual([])
  })

  it('selects no client at runtime: api/index.ts only re-exports the real one', () => {
    const index = readFileSync(join(SRC, 'api', 'index.ts'), 'utf-8')
    expect(index).toMatch(/\brealApi\b/)
    expect(index).not.toMatch(/\?|\bif\b|import\.meta\.env|localStorage/)
  })

  it('the guard sees the shapes it exists to refuse', () => {
    expect(/mockApi|apiMode|VITE_API_MODE|apiModeOverride|admin-fixtures/.test("import { mockApi } from './mockApi'")).toBe(true)
    expect(/\?|\bif\b|import\.meta\.env|localStorage/.test("export const api = mode === 'real' ? realApi : other")).toBe(true)
  })
})
