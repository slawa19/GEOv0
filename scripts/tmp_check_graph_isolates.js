const fs = require('fs')

const participants = JSON.parse(fs.readFileSync('admin-ui/public/admin-fixtures/v1/datasets/participants.json', 'utf8'))
const trustlines = JSON.parse(fs.readFileSync('admin-ui/public/admin-fixtures/v1/datasets/trustlines.json', 'utf8'))

const normEq = (v) => String(v || '').trim().toUpperCase()

function compute({ eq = 'ALL', status = ['active', 'frozen', 'closed'], types = ['person', 'business'], hideIsolates = false, minDeg = 0 }) {
  const allowedStatus = new Set(status.map((s) => String(s).toLowerCase()))
  const allowedTypes = new Set(types.map((t) => String(t).toLowerCase()))

  const pidSet = new Set()
  for (const t of trustlines) {
    if (eq !== 'ALL' && normEq(t.equivalent) !== eq) continue
    if (allowedStatus.size && !allowedStatus.has(String(t.status || '').toLowerCase())) continue
    pidSet.add(t.from)
    pidSet.add(t.to)
  }

  if (!hideIsolates) {
    for (const p of participants) {
      if (p?.pid) pidSet.add(p.pid)
    }
  }

  const pIndex = new Map(participants.filter((p) => p?.pid).map((p) => [p.pid, p]))

  const prelim = new Set()
  for (const pid of pidSet) {
    const p = pIndex.get(pid)
    const t = String(p?.type || '').toLowerCase()
    if (allowedTypes.size && !allowedTypes.has(t)) continue
    prelim.add(pid)
  }

  const edges = []
  for (const t of trustlines) {
    if (eq !== 'ALL' && normEq(t.equivalent) !== eq) continue
    if (allowedStatus.size && !allowedStatus.has(String(t.status || '').toLowerCase())) continue
    if (prelim.has(t.from) && prelim.has(t.to)) edges.push(t)
  }

  const degreeByPid = new Map()
  for (const e of edges) {
    degreeByPid.set(e.from, (degreeByPid.get(e.from) || 0) + 1)
    degreeByPid.set(e.to, (degreeByPid.get(e.to) || 0) + 1)
  }

  const finalPids = [...prelim].filter((pid) => {
    const d = degreeByPid.get(pid) || 0
    if (minDeg > 0 && d < minDeg) return false
    return true
  })

  let isoPerson = 0,
    isoBiz = 0,
    isoOther = 0

  for (const pid of finalPids) {
    const d = degreeByPid.get(pid) || 0
    if (d !== 0) continue
    const p = pIndex.get(pid)
    const t = String(p?.type || '').toLowerCase()
    if (t === 'person') isoPerson += 1
    else if (t === 'business') isoBiz += 1
    else isoOther += 1
  }

  return { prelim: prelim.size, final: finalPids.length, edges: edges.length, isoPerson, isoBiz, isoOther }
}

function computeSameTypeEdgesOnly({ eq = 'ALL', status = ['active', 'frozen', 'closed'], types = ['person', 'business'], hideIsolates = false, minDeg = 0 }) {
  const allowedStatus = new Set(status.map((s) => String(s).toLowerCase()))
  const allowedTypes = new Set(types.map((t) => String(t).toLowerCase()))

  const pIndex = new Map(participants.filter((p) => p?.pid).map((p) => [p.pid, p]))
  const typeOf = (pid) => String(pIndex.get(pid)?.type || '').toLowerCase()

  const pidSet = new Set()
  for (const t of trustlines) {
    if (eq !== 'ALL' && normEq(t.equivalent) !== eq) continue
    if (allowedStatus.size && !allowedStatus.has(String(t.status || '').toLowerCase())) continue
    pidSet.add(t.from)
    pidSet.add(t.to)
  }

  if (!hideIsolates) {
    for (const p of participants) {
      if (p?.pid) pidSet.add(p.pid)
    }
  }

  const prelim = new Set()
  for (const pid of pidSet) {
    const t = typeOf(pid)
    if (allowedTypes.size && !allowedTypes.has(t)) continue
    prelim.add(pid)
  }

  const edges = []
  for (const t of trustlines) {
    if (eq !== 'ALL' && normEq(t.equivalent) !== eq) continue
    if (allowedStatus.size && !allowedStatus.has(String(t.status || '').toLowerCase())) continue
    if (!prelim.has(t.from) || !prelim.has(t.to)) continue
    if (typeOf(t.from) !== typeOf(t.to)) continue
    edges.push(t)
  }

  const degreeByPid = new Map()
  for (const e of edges) {
    degreeByPid.set(e.from, (degreeByPid.get(e.from) || 0) + 1)
    degreeByPid.set(e.to, (degreeByPid.get(e.to) || 0) + 1)
  }

  const finalPids = [...prelim].filter((pid) => {
    const d = degreeByPid.get(pid) || 0
    if (minDeg > 0 && d < minDeg) return false
    return true
  })

  let isoPerson = 0,
    isoBiz = 0,
    isoOther = 0

  for (const pid of finalPids) {
    const d = degreeByPid.get(pid) || 0
    if (d !== 0) continue
    const t = typeOf(pid)
    if (t === 'person') isoPerson += 1
    else if (t === 'business') isoBiz += 1
    else isoOther += 1
  }

  return { prelim: prelim.size, final: finalPids.length, edges: edges.length, isoPerson, isoBiz, isoOther }
}

console.log('current logic (both):', compute({ hideIsolates: false, types: ['person', 'business'] }))
console.log('current logic (only person):', compute({ hideIsolates: false, types: ['person'] }))
console.log('current logic (only business):', compute({ hideIsolates: false, types: ['business'] }))

console.log('same-type edges only (both):', computeSameTypeEdgesOnly({ hideIsolates: false, types: ['person', 'business'] }))
