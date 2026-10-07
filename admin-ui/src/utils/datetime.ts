// The one timestamp format of the Admin UI: `YYYY-MM-DD HH:mm:ss`, in UTC.
//
// UTC because the server stores and sends UTC and the audit log is read against server logs, which are UTC. The
// time zone was once read from a runtime config key `ui.timezone`; the server has no such key (032 S6, E-11), so
// the setting was always its default. A real per-operator zone would be a new setting, not this function.
const FORMAT = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

/** An ISO timestamp as `YYYY-MM-DD HH:mm:ss` (UTC); `''` for an empty value, the input itself when it is not a date. */
export function formatTs(iso: string | null | undefined): string {
  const s = String(iso ?? '').trim()
  if (!s) return ''

  const d = new Date(s)
  if (!Number.isFinite(d.getTime())) return s

  // en-GB yields DD/MM/YYYY, we want YYYY-MM-DD.
  const parts = FORMAT.formatToParts(d)
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value || ''
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`
}
