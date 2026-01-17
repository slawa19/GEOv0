import { t } from './index'

function translateOrFallback(key: string, fallback: string): string {
  const v = t(key)
  return v === key ? fallback : v
}

export function labelParticipantType(type: string): string {
  const raw = String(type || '').trim()
  if (!raw) return ''
  return translateOrFallback(`participant.type.${raw}`, raw)
}

export function labelTrustlineStatus(status: string): string {
  const raw = String(status || '').trim()
  if (!raw) return ''
  return translateOrFallback(`trustlines.status.${raw}`, raw)
}
