/**
 * Format participant label: "Name (pid)" or just "pid" if name is empty.
 */
export function participantLabel(p: { name?: string | null; pid?: string | null }): string {
  const name = String(p?.name ?? '').trim()
  const pid = String(p?.pid ?? '').trim()
  if (!pid) return name || '—'
  if (!name || name === pid) return pid
  return `${name} (${pid})`
}
