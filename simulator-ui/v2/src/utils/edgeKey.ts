export function keyEdge(a: string, b: string) {
  return `${a}→${b}`
}

export function parseEdgeKey(edgeKey: string): { from: string; to: string } | null {
  const s = String(edgeKey ?? '')
  const i = s.indexOf('→')
  if (i <= 0) return null
  const from = s.slice(0, i)
  const to = s.slice(i + 1)
  if (!from || !to) return null
  return { from, to }
}
