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
  // 054-B-7b (the owner's decision of 2026-10-30): every real failure is shown - the sanitised CLEARING_ERROR too (it was
  // the anti-vacuum «still not shown» until 054); a refusal of a payment stays out (the anti-vacuum now).
  it('treats CLEARING_REFUSED (the step refusal of the tick) and CLEARING_ERROR as real failures', () => {
    expect(isUserFacingRunErrorCode('CLEARING_REFUSED')).toBe(true)
    expect(isUserFacingRunErrorCode('clearing_refused')).toBe(true)
    expect(isUserFacingRunErrorCode('CLEARING_ERROR')).toBe(true)
    expect(isUserFacingRunErrorCode('ROUTING_NO_CAPACITY')).toBe(false)
  })

  // 048 S3: "other codes keep CODE: message" -> other codes are a sentence with the code (the server's prose is the title).
  // 054-B-7b: the code, the message and the time are in the text, the step refusal's too (its own human text is gone).
  it('runErrorText: the code, the server message and the time are in the text, for the step refusal too', () => {
    const refused = { code: 'CLEARING_REFUSED', message: 'occurrence amount is not a multiple of the step', at: 't' }
    expect(runErrorText(refused, 'en')).toBe(
      'Something failed during the run: CLEARING_REFUSED — occurrence amount is not a multiple of the step (t); the run goes on.',
    )
    expect(runErrorText(refused, 'ru')).toBe(
      'Во время прогона произошёл сбой: CLEARING_REFUSED — occurrence amount is not a multiple of the step (t); прогон продолжается.',
    )
    expect(runErrorText({ code: 'INTERNAL_ERROR', message: 'boom', at: 't' }, 'en')).toBe('Something failed during the run: INTERNAL_ERROR — boom (t); the run goes on.')
  })
})
