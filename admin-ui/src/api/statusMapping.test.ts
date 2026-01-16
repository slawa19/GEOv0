import { describe, expect, it } from 'vitest'

import { mapUiStatusToAdmin, normalizeAdminStatusToUi } from './statusMapping'

describe('status mapping', () => {
  it('normalizes admin statuses to UI lexicon', () => {
    // UI now uses DB vocabulary (active/suspended/left/deleted).
    expect(normalizeAdminStatusToUi('SUSPENDED')).toBe('suspended')
    expect(normalizeAdminStatusToUi('deleted')).toBe('deleted')
    expect(normalizeAdminStatusToUi(' left ')).toBe('left')
    expect(normalizeAdminStatusToUi('active')).toBe('active')
    expect(normalizeAdminStatusToUi(null)).toBe('')

    // Legacy compatibility.
    expect(normalizeAdminStatusToUi('frozen')).toBe('suspended')
    expect(normalizeAdminStatusToUi('banned')).toBe('deleted')
  })

  it('maps UI statuses to admin values for filters', () => {
    expect(mapUiStatusToAdmin('frozen')).toBe('suspended')
    expect(mapUiStatusToAdmin('banned')).toBe('deleted')
    expect(mapUiStatusToAdmin('suspended')).toBe('suspended')
    expect(mapUiStatusToAdmin('left')).toBe('left')
    expect(mapUiStatusToAdmin('deleted')).toBe('deleted')
    expect(mapUiStatusToAdmin(' active ')).toBe('active')
    expect(mapUiStatusToAdmin('')).toBeNull()
    expect(mapUiStatusToAdmin(undefined)).toBeNull()
  })
})
