import { t } from '../i18n'

export type ParticipantStatusTagType = 'success' | 'warning' | 'info' | 'danger'

export function normalizeParticipantStatusKey(v: unknown): string {
  return String(v ?? '').trim().toLowerCase()
}

export function labelParticipantStatus(v: unknown): string {
  const s = normalizeParticipantStatusKey(v)
  if (!s) return ''
  if (s === 'active') return t('participant.status.active')
  if (s === 'suspended') return t('participant.status.suspended')
  if (s === 'left') return t('participant.status.left')
  if (s === 'deleted') return t('participant.status.deleted')
  if (s === 'unknown') return t('common.unknown')
  return s
}

export function participantStatusTagType(v: unknown): ParticipantStatusTagType {
  const s = normalizeParticipantStatusKey(v)
  if (s === 'active') return 'success'
  if (s === 'suspended') return 'warning'
  if (s === 'deleted') return 'danger'
  return 'info'
}

export function isLockedParticipantStatus(v: unknown): boolean {
  const s = normalizeParticipantStatusKey(v)
  return s === 'deleted' || s === 'left'
}

export function participantStatusOptions(): Array<{ label: string; value: string }> {
  return [
    { label: t('participant.status.any'), value: '' },
    { label: t('participant.status.active'), value: 'active' },
    { label: t('participant.status.suspended'), value: 'suspended' },
    { label: t('participant.status.left'), value: 'left' },
    { label: t('participant.status.deleted'), value: 'deleted' },
  ]
}
