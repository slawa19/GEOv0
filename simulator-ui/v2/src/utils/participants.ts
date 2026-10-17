/**
 * Format participant label: the name, or the shown id if the name is empty (044 `T4405`, decision (iv): a run's
 * participant id is an identity of the run, `<run id>:p<n>`, and is not printed next to the name any more).
 *
 * `showPid` turns the id into the text a person reads (`useParticipantDisplayId` for the scene on screen); the
 * comparison "name equals id" is made on the full id first. Without it the id is printed as it is.
 */
export function participantLabel(
  p: { name?: string | null; pid?: string | null },
  showPid?: (pid: string) => string,
): string {
  const name = String(p?.name ?? '').trim()
  const pid = String(p?.pid ?? '').trim()
  const shown = showPid ? showPid(pid) : pid
  if (!pid) return name || '—'
  if (!name || name === pid) return shown
  return name
}

/**
 * The labels of a list of participants, by pid: `participantLabel`, and where two participants of the list would read
 * the same, a ` (1)`, ` (2)`... in list order, so a picker never offers two options a person cannot tell apart
 * (since 044 the id that told them apart is not printed). Ids without a pid are left out.
 */
export function participantLabelsOf(
  list: ReadonlyArray<{ name?: string | null; pid?: string | null }>,
  showPid?: (pid: string) => string,
): Map<string, string> {
  const rows = list
    .map((p) => ({ pid: String(p?.pid ?? '').trim(), label: participantLabel(p, showPid) }))
    .filter((r) => r.pid)
  const total = new Map<string, number>()
  for (const r of rows) total.set(r.label, (total.get(r.label) ?? 0) + 1)
  const seen = new Map<string, number>()
  const out = new Map<string, string>()
  for (const r of rows) {
    if (out.has(r.pid)) continue
    if ((total.get(r.label) ?? 0) < 2) { out.set(r.pid, r.label); continue }
    const k = (seen.get(r.label) ?? 0) + 1
    seen.set(r.label, k)
    out.set(r.pid, `${r.label} (${k})`)
  }
  return out
}
