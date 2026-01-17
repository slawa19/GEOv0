import { describe, expect, it } from 'vitest'

import fs from 'node:fs'
import path from 'node:path'

type Issue = {
  file: string
  line: number
  rule: string
  excerpt: string
}

function walkFiles(dir: string, out: string[]) {
  if (!fs.existsSync(dir)) return
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === 'node_modules' || ent.name === 'dist') continue
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) {
      walkFiles(p, out)
      continue
    }

    if (!/\.(vue|ts)$/.test(ent.name)) continue
    if (/\.(test|spec)\.ts$/.test(ent.name)) continue
    if (/\.d\.ts$/.test(ent.name)) continue

    out.push(p)
  }
}

function lineNumberAt(text: string, idx: number): number {
  // 1-based
  return text.slice(0, Math.max(0, idx)).split(/\r?\n/).length
}

function lineExcerpt(text: string, line: number): string {
  const lines = text.split(/\r?\n/)
  return lines[Math.max(0, line - 1)]?.trim() ?? ''
}

describe('i18n guardrails', () => {
  it('UI code does not use hardcoded user-facing strings in toast/prompt APIs', () => {
    const srcRoot = path.resolve(__dirname, '..')
    const targets = ['pages', 'components', 'ui', 'layout'].map((d) => path.join(srcRoot, d))

    const files: string[] = []
    for (const d of targets) walkFiles(d, files)

    const issues: Issue[] = []

    const forbidden = [
      {
        rule: 'ElMessage/ElMessageBox/ElNotification with literal first arg',
        re: /(ElMessageBox|ElMessage|ElNotification)\.[A-Za-z_][A-Za-z0-9_]*\(\s*(['"`])/g,
      },
      {
        rule: 'ElementPlus dialog/toast option uses literal string',
        re: /\b(confirmButtonText|cancelButtonText|inputPlaceholder|inputErrorMessage|title|message)\s*:\s*(['"`])/g,
      },
    ]

    for (const abs of files) {
      const rel = path.relative(srcRoot, abs).split(path.sep).join('/')
      const text = fs.readFileSync(abs, 'utf8')

      // Escape hatch for rare cases.
      if (text.includes('i18n-ignore-hardcoded')) continue

      for (const f of forbidden) {
        f.re.lastIndex = 0
        let m: RegExpExecArray | null
        while ((m = f.re.exec(text))) {
          const line = lineNumberAt(text, m.index)
          issues.push({
            file: rel,
            line,
            rule: f.rule,
            excerpt: lineExcerpt(text, line),
          })
        }
      }
    }

    const printable = issues
      .slice(0, 50)
      .map((i) => `${i.file}:${i.line} [${i.rule}] ${i.excerpt}`)
      .join('\n')

    expect(issues, printable).toEqual([])
  })
})
