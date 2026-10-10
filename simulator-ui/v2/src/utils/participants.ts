/**
 * Format participant label: "Name (pid)" or just "pid" if name is empty.
 *
 * `showPid` turns the id into the text a person reads (`displayParticipantId` for the scene on screen); the comparison
 * "name equals id" is made on the full id first. Without it the id is printed as it is.
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
  return `${name} (${shown})`
}
