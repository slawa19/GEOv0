// 029 S4, review T2990: the rules of `scripts/validate-fixtures.mjs` are checked by running the script itself on a copy of
// the canonical pack with ONE thing broken. A rule that only ever sees valid files can be blind (AGENTS.md section 9), so
// every case below breaks exactly what its rule is for and the unbroken copy is the control.
//
// What this does not see: it runs the script as a child process on `admin-fixtures/v1` as committed, so it says nothing
// about a pack generated elsewhere, and it does not judge whether the rules are the right ones - only that each fires.
import { spawnSync } from 'node:child_process'
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

const adminUiDir = process.cwd()
const repoRoot = path.resolve(adminUiDir, '..')
const canonicalV1 = path.join(repoRoot, 'admin-fixtures', 'v1')
const script = path.join(adminUiDir, 'scripts', 'validate-fixtures.mjs')
// Inside the project, under the ignored `.local-run/` (AGENTS.md section 7): no directories outside the repository.
const sandboxRoot = path.join(repoRoot, '.local-run', 'validate-fixtures-rules')

type Debt = { equivalent: string; debtor: string; creditor: string; amount: string }

let sandbox = ''
let counter = 0

beforeAll(() => {
  mkdirSync(sandboxRoot, { recursive: true })
  sandbox = mkdtempSync(path.join(sandboxRoot, 'run-'))
})

afterAll(() => {
  rmSync(sandbox, { recursive: true, force: true })
})

function copyPack(): string {
  counter += 1
  const dir = path.join(sandbox, `pack-${counter}`)
  cpSync(canonicalV1, dir, { recursive: true })
  return dir
}

function readDebts(dir: string): Debt[] {
  return JSON.parse(readFileSync(path.join(dir, 'datasets', 'debts.json'), 'utf8')) as Debt[]
}

function writeDebts(dir: string, debts: Debt[]): void {
  writeFileSync(path.join(dir, 'datasets', 'debts.json'), JSON.stringify(debts, null, 2) + '\n')
}

function run(args: string[]): { status: number | null; out: string } {
  const r = spawnSync(process.execPath, [script, ...args], { cwd: adminUiDir, encoding: 'utf8', timeout: 60_000 })
  return { status: r.status, out: `${r.stdout}${r.stderr}` }
}

function validatePack(dir: string) {
  return run(['--only-pack', '--v1-dir', dir])
}

describe('validate-fixtures: every rule fires on the one thing it exists for', () => {
  it('control: the unbroken copy of the canonical pack passes', () => {
    const r = validatePack(copyPack())
    expect(r.out).toContain('Fixtures OK (pack)')
    expect(r.status).toBe(0)
  })

  it('a debt that is not a multiple of the equivalent step is refused, naming the file and the row (27.501 at step 0.01)', () => {
    const dir = copyPack()
    const debts = readDebts(dir)
    const row = debts.findIndex((d) => d.equivalent === 'UAH')
    debts[row].amount = `${debts[row].amount}1`
    writeDebts(dir, debts)

    const r = validatePack(dir)
    expect(r.status).toBe(1)
    expect(r.out).toContain('debts.json')
    expect(r.out).toContain(`#${row}`)
    expect(r.out).toContain('multiple of the 0.01 step of UAH')
  })

  it('a debt to an unknown participant is refused, not dropped from the net', () => {
    const dir = copyPack()
    const debts = readDebts(dir)
    debts.push({ equivalent: 'UAH', debtor: debts[0].debtor, creditor: 'PID_NOBODY', amount: '1.00' })
    writeDebts(dir, debts)

    const r = validatePack(dir)
    expect(r.status).toBe(1)
    expect(r.out).toContain('debts.json')
    expect(r.out).toContain(`#${debts.length - 1}`)
    expect(r.out).toContain('PID_NOBODY')
  })

  it('a debt in an unknown equivalent is refused', () => {
    const dir = copyPack()
    const debts = readDebts(dir)
    debts[3].equivalent = 'XYZ'
    writeDebts(dir, debts)

    const r = validatePack(dir)
    expect(r.status).toBe(1)
    expect(r.out).toContain('#3')
    expect(r.out).toContain('XYZ')
  })

  it.each(['0.00', '-5.00'])('a debt of %s is refused, not skipped', (amount) => {
    const dir = copyPack()
    const debts = readDebts(dir)
    debts[5].amount = amount
    writeDebts(dir, debts)

    const r = validatePack(dir)
    expect(r.status).toBe(1)
    expect(r.out).toContain('#5')
    expect(r.out).toContain('positive')
  })

  it('a participants.viz net that disagrees with debts.json is refused (the rule of F-029-18 still fires)', () => {
    const dir = copyPack()
    const file = path.join(dir, 'datasets', 'participants.viz-UAH.json')
    const viz = JSON.parse(readFileSync(file, 'utf8')) as Array<{ net_balance_atoms: string }>
    viz[0].net_balance_atoms = String(BigInt(viz[0].net_balance_atoms) + 1n)
    writeFileSync(file, JSON.stringify(viz, null, 2) + '\n')

    const r = validatePack(dir)
    expect(r.status).toBe(1)
    expect(r.out).toContain('UAH 1')
  })
})

describe('validate-fixtures: the public copy must be the canonical pack, every file of it', () => {
  function validateCopy(canonical: string, publicDir: string) {
    return run(['--v1-dir', canonical, '--public-v1-dir', publicDir])
  }

  it('control: an identical public copy passes', () => {
    const r = validateCopy(copyPack(), copyPack())
    expect(r.out).toContain('Fixtures OK')
    expect(r.status).toBe(0)
  })

  it.each([
    ['datasets/health.json', 'a dataset the mock reads that the old list did not name'],
    ['datasets/migrations.json', 'another one'],
    ['scenarios/happy.json', 'a scenario'],
  ])('a changed %s in the public copy is refused (%s)', (relative) => {
    const canonical = copyPack()
    const publicDir = copyPack()
    const file = path.join(publicDir, relative)
    writeFileSync(file, readFileSync(file, 'utf8').replace(/\n$/, '') + ' \n')
    writeFileSync(file, JSON.stringify({ tampered: true }) + '\n')

    const r = validateCopy(canonical, publicDir)
    expect(r.status).toBe(1)
    expect(r.out).toContain(relative)
  })

  it('a file only the public copy has (a deleted file that sync does not prune) is refused', () => {
    const canonical = copyPack()
    const publicDir = copyPack()
    mkdirSync(path.join(publicDir, 'api-snapshots'), { recursive: true })
    writeFileSync(path.join(publicDir, 'api-snapshots', 'health.get.json'), '{}\n')

    const r = validateCopy(canonical, publicDir)
    expect(r.status).toBe(1)
    expect(r.out).toContain('api-snapshots/health.get.json')
  })

  it('a file only the canonical pack has is refused', () => {
    const canonical = copyPack()
    const publicDir = copyPack()
    rmSync(path.join(publicDir, 'datasets', 'health-db.json'))

    const r = validateCopy(canonical, publicDir)
    expect(r.status).toBe(1)
    expect(r.out).toContain('datasets/health-db.json')
  })
})
