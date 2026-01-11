export function formatIsoInTimeZone(iso: string, timeZone: string): string {
  const s = String(iso || '').trim()
  if (!s) return ''

  const d = new Date(s)
  if (!Number.isFinite(d.getTime())) return s

  const tz = String(timeZone || '').trim() || 'UTC'

  try {
    const fmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })

    // en-GB yields DD/MM/YYYY, we want YYYY-MM-DD.
    const parts = fmt.formatToParts(d)
    const get = (t: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === t)?.value || ''
    const yyyy = get('year')
    const mm = get('month')
    const dd = get('day')
    const hh = get('hour')
    const mi = get('minute')
    const ss = get('second')
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`
  } catch {
    return s
  }
}
