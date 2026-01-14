export function normalizeAdminStatusToUi(status: string | null | undefined): string {
  const v = String(status || '').trim().toLowerCase()
  if (v === 'suspended') return 'frozen'
  if (v === 'deleted') return 'banned'
  if (v === 'left') return 'banned'
  return v || ''
}

export function mapUiStatusToAdmin(status: string | null | undefined): string | null {
  const v = String(status || '').trim().toLowerCase()
  if (!v) return null
  if (v === 'frozen') return 'suspended'
  if (v === 'banned') return 'deleted'
  return v
}
