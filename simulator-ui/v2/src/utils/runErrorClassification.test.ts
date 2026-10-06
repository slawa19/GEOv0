import { describe, expect, it } from 'vitest'

import { isUserFacingRunErrorCode, runErrorText } from './runErrorClassification'

describe('utils/runErrorClassification', () => {
  it('treats PAYMENT_TIMEOUT and INTERNAL_ERROR as user-facing', () => {
    expect(isUserFacingRunErrorCode('PAYMENT_TIMEOUT')).toBe(true)
    expect(isUserFacingRunErrorCode('payment_timeout')).toBe(true)
    expect(isUserFacingRunErrorCode('INTERNAL_ERROR')).toBe(true)
  })

  it('treats other codes as non-user-facing', () => {
    expect(isUserFacingRunErrorCode('ROUTING_CAPACITY')).toBe(false)
    expect(isUserFacingRunErrorCode('')).toBe(false)
  })

  // 031 slice C (item 17): the tick's step refusal is named by the server, so it is shown, not dropped.
  it('treats CLEARING_REFUSED (the step refusal of the tick) as user-facing', () => {
    expect(isUserFacingRunErrorCode('CLEARING_REFUSED')).toBe(true)
    expect(isUserFacingRunErrorCode('clearing_refused')).toBe(true)
    // anti-vacuum: the sanitised class of every other clearing failure is still not shown
    expect(isUserFacingRunErrorCode('CLEARING_ERROR')).toBe(false)
  })

  it('runErrorText: CLEARING_REFUSED is the human step text in the interface language; other codes keep CODE: message', () => {
    const refused = { code: 'CLEARING_REFUSED', message: 'occurrence amount is not a multiple of the step', at: 't' }
    expect(runErrorText(refused, 'en')).toBe(
      'Clearing refused: the database holds debts finer than the accounting step of the equivalent. Reseed the database.',
    )
    expect(runErrorText(refused, 'ru')).toBe(
      'Клиринг отклонён: в базе есть долги мельче шага учёта эквивалента. Нужен пересев базы.',
    )
    expect(runErrorText({ code: 'INTERNAL_ERROR', message: 'boom', at: 't' }, 'en')).toBe('INTERNAL_ERROR: boom')
  })
})
