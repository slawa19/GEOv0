import fs from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

const repoRoot = path.resolve(process.cwd(), '..')
const canonicalDir = path.join(repoRoot, 'admin-fixtures', 'v1', 'datasets')
const publicDir = path.join(process.cwd(), 'public', 'admin-fixtures', 'v1', 'datasets')

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

function assert(condition, message) {
  if (!condition) throw new Error(message)
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

  assert(Array.isArray(participants), `${label} participants must be an array`)
  assert(participants.length === 50, `${label} participants.length must be 50, got ${participants.length}`)

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

  return { equivalents, participants, trustlines, incidents, incidentItems }
}

async function main() {
  const canonical = await validateSide('CANONICAL', canonicalDir)
  const publicSide = await validateSide('PUBLIC', publicDir)

  assert(
    deepEqual(canonical.equivalents, publicSide.equivalents) &&
      deepEqual(canonical.participants, publicSide.participants) &&
      deepEqual(canonical.trustlines, publicSide.trustlines) &&
      deepEqual(canonical.incidents, publicSide.incidents),
    'PUBLIC fixtures differ from CANONICAL. Run `npm run sync:fixtures` in admin-ui.',
  )

  console.log('Fixtures OK')
  console.log(`- participants: ${publicSide.participants.length}`)
  console.log(`- equivalents: ${Array.from(new Set(asEquivalentCodes(publicSide.equivalents))).sort().join(', ')}`)
  console.log(`- trustlines: ${publicSide.trustlines.length}`)
  console.log(`- incidents: ${publicSide.incidentItems.length}`)
}

main().catch((err) => {
  console.error(String(err?.message ?? err))
  process.exit(1)
})
