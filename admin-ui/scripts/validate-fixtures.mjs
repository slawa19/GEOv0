import fs from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

const repoRoot = path.resolve(process.cwd(), '..')
const defaultCanonicalV1Dir = path.join(repoRoot, 'admin-fixtures', 'v1')
const defaultCanonicalDir = path.join(repoRoot, 'admin-fixtures', 'v1', 'datasets')
const defaultPublicV1Dir = path.join(process.cwd(), 'public', 'admin-fixtures', 'v1')
const defaultPublicDir = path.join(process.cwd(), 'public', 'admin-fixtures', 'v1', 'datasets')

function parseArgs(argv) {
  const out = {
    v1Dir: null,
    onlyPack: false,
  }

  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i]
    if (a === '--only-pack') {
      out.onlyPack = true
    } else if (a === '--v1-dir') {
      const v = argv[i + 1]
      if (!v) throw new Error('Missing value for --v1-dir')
      out.v1Dir = path.resolve(process.cwd(), v)
      i += 1
    } else if (typeof a === 'string' && a.length > 0 && !a.startsWith('-')) {
      // Convenience / robustness: allow passing a v1 dir as positional arg.
      // Treat it as pack validation (no canonical/public comparison).
      if (out.v1Dir) throw new Error('Multiple positional v1-dir args are not supported')
      out.v1Dir = path.resolve(process.cwd(), a)
      out.onlyPack = true
    } else {
      throw new Error(`Unknown arg: ${a}`)
    }
  }

  return out
}

function allowedSeedIds() {
  return ['greenfield-village-100', 'riverside-town-50', 'greenfield-village-100-v2', 'riverside-town-50-v2']
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function expectedParticipantsCount() {
  const raw = process.env.EXPECTED_PARTICIPANTS
  if (!raw) return null
  const n = Number.parseInt(raw, 10)
  if (!Number.isFinite(n) || n <= 0) return null
  return n
}

function isAllowedParticipantsCount(n) {
  // Support multiple demo seeds while keeping a guardrail against obviously broken fixture sets.
  return n === 50 || n === 100
}

async function readJson(filePath) {
  let text
  try {
    text = await fs.readFile(filePath, 'utf8')
  } catch (err) {
    throw new Error(`Failed to read ${filePath}: ${err?.message ?? String(err)}`)
  }

  // Strip UTF-8 BOM if it exists (common on Windows).
  if (text.charCodeAt(0) === 0xfeff) {
    text = text.slice(1)
  }

  try {
    return JSON.parse(text)
  } catch (err) {
    throw new Error(`Invalid JSON in ${filePath}: ${err?.message ?? String(err)}`)
  }
}

async function exists(filePath) {
  try {
    await fs.access(filePath)
    return true
  } catch {
    return false
  }
}

function asEquivalentCodes(equivalents) {
  if (!Array.isArray(equivalents)) {
    throw new Error('equivalents must be an array')
  }

  return equivalents.map((e) => {
    if (typeof e === 'string') return e
    if (e && typeof e === 'object' && typeof e.code === 'string') return e.code
    return null
  })
}

function deepEqual(a, b) {
  // Fixtures are deterministic; JSON.stringify is enough for stable arrays/objects here.
  return JSON.stringify(a) === JSON.stringify(b)
}

function validateTrustlines(trustlines, label) {
  assert(Array.isArray(trustlines), `${label} trustlines must be an array`)

  const bad = []
  for (let i = 0; i < trustlines.length; i += 1) {
    const t = trustlines[i]
    if (!t || typeof t !== 'object' || Array.isArray(t)) {
      bad.push({ i, reason: 'not an object' })
      continue
    }

    // Catch "{}" and other malformed entries.
    const keys = Object.keys(t)
    if (keys.length === 0) {
      bad.push({ i, reason: 'empty object' })
      continue
    }

    if (typeof t.from !== 'string' || t.from.length === 0) bad.push({ i, reason: 'missing from' })
    if (typeof t.to !== 'string' || t.to.length === 0) bad.push({ i, reason: 'missing to' })
    if (typeof t.equivalent !== 'string' || t.equivalent.length === 0)
      bad.push({ i, reason: 'missing equivalent' })
  }

  if (bad.length > 0) {
    const sample = bad.slice(0, 8).map((x) => `#${x.i}(${x.reason})`).join(', ')
    throw new Error(`${label} trustlines invalid entries: ${bad.length}. Sample: ${sample}`)
  }
}

function validateDebts(debts, label) {
  if (debts == null) return
  assert(Array.isArray(debts), `${label} debts must be an array`)

  const bad = []
  for (let i = 0; i < debts.length; i += 1) {
    const d = debts[i]
    if (!d || typeof d !== 'object' || Array.isArray(d)) {
      bad.push({ i, reason: 'not an object' })
      continue
    }

    if (typeof d.debtor !== 'string' || d.debtor.length === 0) bad.push({ i, reason: 'missing debtor' })
    if (typeof d.creditor !== 'string' || d.creditor.length === 0) bad.push({ i, reason: 'missing creditor' })
    if (typeof d.equivalent !== 'string' || d.equivalent.length === 0) bad.push({ i, reason: 'missing equivalent' })
    if (typeof d.amount !== 'string' || d.amount.length === 0) bad.push({ i, reason: 'missing amount' })
  }

  if (bad.length > 0) {
    const sample = bad.slice(0, 8).map((x) => `#${x.i}(${x.reason})`).join(', ')
    throw new Error(`${label} debts invalid entries: ${bad.length}. Sample: ${sample}`)
  }
}

function validateClearingCycles(cyclesDoc, label) {
  if (cyclesDoc == null) return
  assert(cyclesDoc && typeof cyclesDoc === 'object' && !Array.isArray(cyclesDoc), `${label} clearing-cycles must be an object`)
  // Expected: { equivalents: { [code]: { cycles: Array<Array<Edge>> } } }
  const equivalents = cyclesDoc.equivalents
  assert(equivalents && typeof equivalents === 'object' && !Array.isArray(equivalents), `${label} clearing-cycles.equivalents must be an object`)

  for (const [code, entry] of Object.entries(equivalents)) {
    assert(typeof code === 'string' && code.length > 0, `${label} clearing-cycles invalid code`) // defensive
    assert(entry && typeof entry === 'object' && !Array.isArray(entry), `${label} clearing-cycles[${code}] must be an object`)
    assert(Array.isArray(entry.cycles), `${label} clearing-cycles[${code}].cycles must be an array`)

    for (const cycle of entry.cycles) {
      assert(Array.isArray(cycle), `${label} clearing-cycles[${code}] cycle must be an array`)
      for (const edge of cycle) {
        assert(edge && typeof edge === 'object' && !Array.isArray(edge), `${label} clearing-cycles[${code}] edge must be an object`)
        assert(typeof edge.debtor === 'string' && edge.debtor.length > 0, `${label} clearing-cycles[${code}] edge missing debtor`)
        assert(typeof edge.creditor === 'string' && edge.creditor.length > 0, `${label} clearing-cycles[${code}] edge missing creditor`)
        if ('equivalent' in edge) {
          assert(typeof edge.equivalent === 'string', `${label} clearing-cycles[${code}] edge.equivalent must be string`)
        }
        assert(typeof edge.amount === 'string' && edge.amount.length > 0, `${label} clearing-cycles[${code}] edge missing amount`)
      }
    }
  }
}

function validateMeta(meta, label) {
  assert(meta && typeof meta === 'object' && !Array.isArray(meta), `${label} _meta.json must be an object`)
  assert(typeof meta.version === 'string' && meta.version.length > 0, `${label} _meta.json.version must be string`)
  assert(typeof meta.generated_at === 'string' && meta.generated_at.length > 0, `${label} _meta.json.generated_at must be string`)
  assert(typeof meta.seed_id === 'string' && meta.seed_id.length > 0, `${label} _meta.json.seed_id must be string`)
  assert(typeof meta.generator === 'string' && meta.generator.length > 0, `${label} _meta.json.generator must be string`)

  const allowed = new Set(allowedSeedIds())
  assert(
    allowed.has(meta.seed_id),
    `${label} seed_id must be one of ${JSON.stringify(Array.from(allowed).sort())}. Got ${JSON.stringify(meta.seed_id)}.`,
  )

  assert(meta.counts && typeof meta.counts === 'object' && !Array.isArray(meta.counts), `${label} _meta.json.counts must be object`)
}

function isIsoDateString(s) {
  if (typeof s !== 'string' || s.length < 10) return false
  const t = Date.parse(s)
  return Number.isFinite(t)
}

function validateTransactions(transactions, label, participants, equivalents) {
  if (transactions == null) return
  assert(Array.isArray(transactions), `${label} transactions must be an array`)

  const pidSet = new Set((participants || []).map((p) => p?.pid).filter((x) => typeof x === 'string'))
  const eqSet = new Set(asEquivalentCodes(equivalents).filter((x) => typeof x === 'string'))

  const allowedTypes = new Set([
    'TRUST_LINE_CREATE',
    'TRUST_LINE_UPDATE',
    'TRUST_LINE_CLOSE',
    'PAYMENT',
    'CLEARING',
    'COMPENSATION',
    'COMMODITY_REDEMPTION',
  ])
  const allowedStates = new Set([
    'NEW',
    'ROUTED',
    'PREPARE_IN_PROGRESS',
    'PREPARED',
    'COMMITTED',
    'ABORTED',
    'PROPOSED',
    'WAITING',
    'REJECTED',
  ])

  const bad = []
  for (let i = 0; i < transactions.length; i += 1) {
    const t = transactions[i]
    if (!t || typeof t !== 'object' || Array.isArray(t)) {
      bad.push({ i, reason: 'not an object' })
      continue
    }

    if (typeof t.tx_id !== 'string' || t.tx_id.length === 0) bad.push({ i, reason: 'missing tx_id' })
    if (typeof t.type !== 'string' || !allowedTypes.has(t.type)) bad.push({ i, reason: 'invalid type' })
    if (typeof t.state !== 'string' || !allowedStates.has(t.state)) bad.push({ i, reason: 'invalid state' })
    if (typeof t.initiator_pid !== 'string' || !pidSet.has(t.initiator_pid)) bad.push({ i, reason: 'invalid initiator_pid' })
    if (!t.payload || typeof t.payload !== 'object' || Array.isArray(t.payload)) bad.push({ i, reason: 'invalid payload' })
    if (!isIsoDateString(t.created_at)) bad.push({ i, reason: 'invalid created_at' })
    if (!isIsoDateString(t.updated_at)) bad.push({ i, reason: 'invalid updated_at' })

    const pEq = t.payload?.equivalent
    if (typeof pEq === 'string' && pEq.length > 0 && !eqSet.has(pEq)) bad.push({ i, reason: 'payload.equivalent not in equivalents' })
  }

  if (bad.length > 0) {
    const sample = bad.slice(0, 8).map((x) => `#${x.i}(${x.reason})`).join(', ')
    throw new Error(`${label} transactions invalid entries: ${bad.length}. Sample: ${sample}`)
  }
}

function getIncidentItems(incidents, label) {
  if (Array.isArray(incidents)) return incidents
  if (incidents && typeof incidents === 'object' && Array.isArray(incidents.items)) return incidents.items
  throw new Error(
    `${label} incidents must be an array or { items: [...] }. Got: ${Object.prototype.toString.call(incidents)}`,
  )
}

async function validateSide(label, dir) {
  const equivalents = await readJson(path.join(dir, 'equivalents.json'))
  const participants = await readJson(path.join(dir, 'participants.json'))
  const trustlines = await readJson(path.join(dir, 'trustlines.json'))
  const incidents = await readJson(path.join(dir, 'incidents.json'))

  const debtsPath = path.join(dir, 'debts.json')
  const cyclesPath = path.join(dir, 'clearing-cycles.json')
  const txPath = path.join(dir, 'transactions.json')
  const debts = (await exists(debtsPath)) ? await readJson(debtsPath) : null
  const clearingCycles = (await exists(cyclesPath)) ? await readJson(cyclesPath) : null
  const transactions = (await exists(txPath)) ? await readJson(txPath) : null

  assert(Array.isArray(participants), `${label} participants must be an array`)

  // Guardrail: keep participant types consistent across the project.
  const allowedTypes = new Set(['person', 'business', 'hub'])
  const badTypes = new Set(
    participants
      .map((p) => String(p?.type || '').trim())
      .filter((t) => t.length > 0 && !allowedTypes.has(t)),
  )
  assert(
    badTypes.size === 0,
    `${label} participants contain unsupported types: ${JSON.stringify(Array.from(badTypes).sort())}. ` +
      `Allowed: ${JSON.stringify(Array.from(allowedTypes))}`,
  )

  const expectedCount = expectedParticipantsCount()
  if (typeof expectedCount === 'number') {
    assert(
      participants.length === expectedCount,
      `${label} participants.length must be ${expectedCount}, got ${participants.length}`,
    )
  } else {
    assert(
      isAllowedParticipantsCount(participants.length),
      `${label} participants.length must be one of [50, 100] (or set EXPECTED_PARTICIPANTS). Got ${participants.length}`,
    )
  }

  const codes = asEquivalentCodes(equivalents)
  assert(!codes.includes(null), `${label} equivalents must be strings or {code: string}`)

  const uniqueCodes = Array.from(new Set(codes)).sort()
  assert(
    deepEqual(uniqueCodes, ['EUR', 'HOUR', 'UAH']),
    `${label} equivalents must be exactly ["UAH","EUR","HOUR"], got ${JSON.stringify(uniqueCodes)}`,
  )

  validateTrustlines(trustlines, label)
  const incidentItems = getIncidentItems(incidents, label)
  assert(Array.isArray(incidentItems), `${label} incidents items must be an array`)

  validateDebts(debts, label)
  validateClearingCycles(clearingCycles, label)
  validateTransactions(transactions, label, participants, equivalents)

  return { equivalents, participants, trustlines, incidents, incidentItems, debts, clearingCycles, transactions }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2))

  if (opts.onlyPack) {
    const v1Dir = opts.v1Dir ?? defaultCanonicalV1Dir
    const datasetsDir = path.join(v1Dir, 'datasets')

    const meta = await readJson(path.join(v1Dir, '_meta.json'))
    validateMeta(meta, 'PACK')
    const pack = await validateSide('PACK', datasetsDir)

    console.log('Fixtures OK (pack)')
    console.log(`- v1Dir: ${v1Dir}`)
    console.log(`- seed_id: ${meta.seed_id}`)
    console.log(`- participants: ${pack.participants.length}`)
    console.log(`- equivalents: ${Array.from(new Set(asEquivalentCodes(pack.equivalents))).sort().join(', ')}`)
    console.log(`- trustlines: ${pack.trustlines.length}`)
    console.log(`- incidents: ${pack.incidentItems.length}`)
    if (Array.isArray(pack.debts)) console.log(`- debts: ${pack.debts.length}`)
    if (pack.clearingCycles) console.log(`- clearing-cycles: yes`)
    if (Array.isArray(pack.transactions)) console.log(`- transactions: ${pack.transactions.length}`)
    return
  }

  const canonicalV1Dir = defaultCanonicalV1Dir
  const canonicalDir = defaultCanonicalDir
  const publicV1Dir = defaultPublicV1Dir
  const publicDir = defaultPublicDir

  const canonicalMeta = await readJson(path.join(canonicalV1Dir, '_meta.json'))
  const publicMeta = await readJson(path.join(publicV1Dir, '_meta.json'))
  validateMeta(canonicalMeta, 'CANONICAL')
  validateMeta(publicMeta, 'PUBLIC')
  assert(
    deepEqual(canonicalMeta, publicMeta),
    'PUBLIC _meta.json differs from CANONICAL. Run `npm run sync:fixtures` in admin-ui.',
  )

  const canonical = await validateSide('CANONICAL', canonicalDir)
  const publicSide = await validateSide('PUBLIC', publicDir)

  assert(
    deepEqual(canonical.equivalents, publicSide.equivalents) &&
      deepEqual(canonical.participants, publicSide.participants) &&
      deepEqual(canonical.trustlines, publicSide.trustlines) &&
      deepEqual(canonical.incidents, publicSide.incidents) &&
      deepEqual(canonical.debts, publicSide.debts) &&
      deepEqual(canonical.clearingCycles, publicSide.clearingCycles) &&
      deepEqual(canonical.transactions, publicSide.transactions),
    'PUBLIC fixtures differ from CANONICAL. Run `npm run sync:fixtures` in admin-ui.',
  )

  console.log('Fixtures OK')
  console.log(`- seed_id: ${publicMeta.seed_id}`)
  console.log(`- participants: ${publicSide.participants.length}`)
  console.log(`- equivalents: ${Array.from(new Set(asEquivalentCodes(publicSide.equivalents))).sort().join(', ')}`)
  console.log(`- trustlines: ${publicSide.trustlines.length}`)
  console.log(`- incidents: ${publicSide.incidentItems.length}`)
  if (Array.isArray(publicSide.debts)) console.log(`- debts: ${publicSide.debts.length}`)
  if (publicSide.clearingCycles) console.log(`- clearing-cycles: yes`)
  if (Array.isArray(publicSide.transactions)) console.log(`- transactions: ${publicSide.transactions.length}`)
}

main().catch((err) => {
  console.error(String(err?.message ?? err))
  process.exit(1)
})
