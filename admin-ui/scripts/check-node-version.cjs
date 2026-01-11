function parse(v) {
  const m = String(v || '').trim().match(/^(\d+)\.(\d+)\.(\d+)/)
  if (!m) return null
  return { major: Number(m[1]), minor: Number(m[2]), patch: Number(m[3]) }
}

function gte(a, b) {
  if (a.major !== b.major) return a.major > b.major
  if (a.minor !== b.minor) return a.minor > b.minor
  return a.patch >= b.patch
}

const currentRaw = process.versions && process.versions.node
const current = parse(currentRaw)

const requiredText = 'Node ^20.19.0 (>=20.19.0 <21) OR >=22.12.0'

if (!current) {
  console.error(`[admin-ui] Unable to detect Node version. Required: ${requiredText}`)
  process.exit(1)
}

const ok20 = current.major === 20 && gte(current, { major: 20, minor: 19, patch: 0 })
const ok22 = current.major === 22 && gte(current, { major: 22, minor: 12, patch: 0 })
const okNewer = current.major > 22

if (!ok20 && !ok22 && !okNewer) {
  console.error(`[admin-ui] Unsupported Node version ${currentRaw}.\n` + `Required: ${requiredText}.\n` + `Fix: install Node 22.12+ (recommended) or Node 20.19+, then re-run npm install.`)
  process.exit(1)
}
