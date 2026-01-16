export function normalizeAdminStatusToUi(status: string | null | undefined): string {
  const v = String(status || '').trim().toLowerCase()
  // UI now uses DB vocabulary (active/suspended/left/deleted).
  // Keep legacy mappings for backward compatibility with older saved URLs/fixtures.
  if (v === 'frozen') return 'suspended'
  if (v === 'banned') return 'deleted'
  return v || ''
}

export function mapUiStatusToAdmin(status: string | null | undefined): string | null {
  const v = String(status || '').trim().toLowerCase()
  if (!v) return null
  // Accept legacy UI values.
  if (v === 'frozen') return 'suspended'
  if (v === 'banned') return 'deleted'
  return v
}
