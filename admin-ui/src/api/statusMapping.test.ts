import { describe, expect, it } from 'vitest'

import { mapUiStatusToAdmin, normalizeAdminStatusToUi } from './statusMapping'

describe('status mapping', () => {
  it('normalizes admin statuses to UI lexicon', () => {
    expect(normalizeAdminStatusToUi('SUSPENDED')).toBe('frozen')
    expect(normalizeAdminStatusToUi('deleted')).toBe('banned')
    expect(normalizeAdminStatusToUi(' left ')).toBe('banned')
    expect(normalizeAdminStatusToUi('active')).toBe('active')
    expect(normalizeAdminStatusToUi(null)).toBe('')
  })

  it('maps UI statuses to admin values for filters', () => {
    expect(mapUiStatusToAdmin('frozen')).toBe('suspended')
    expect(mapUiStatusToAdmin('banned')).toBe('deleted')
    expect(mapUiStatusToAdmin(' active ')).toBe('active')
    expect(mapUiStatusToAdmin('')).toBeNull()
    expect(mapUiStatusToAdmin(undefined)).toBeNull()
  })
})
