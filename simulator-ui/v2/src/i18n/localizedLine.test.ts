/**
 * 046 wave 1 (§15 Codex P2): a kept line is remade in another language from how it was made; a text no render made
 * is not touched; the memory is bounded.
 */
import { describe, expect, it } from 'vitest'

import { localizedLine, relocalizeLine } from './localizedLine'
import { t, tn } from './t'

describe('046 localizedLine', () => {
  it('a made line is remade in the other language and back, with the data of its cause', () => {
    const ru = localizedLine((l) => tn('runStart.limitAll', 7, undefined, l), 'ru')
    expect(ru).toBe('Сейчас заняты все 7 мест: демо смотрят 7 человек. Попробуйте через минуту.')
    const en = relocalizeLine(ru, 'en')
    expect(en).toBe('All 7 places of the demo are taken right now: 7 people are watching it. Try again in a minute.')
    expect(relocalizeLine(en, 'ru')).toBe(ru)
  })

  it('counter-check: a text no render made, an empty text - null (kept as it is)', () => {
    expect(relocalizeLine('HTTP 503 for /simulator/runs', 'ru')).toBeNull()
    expect(relocalizeLine('', 'ru')).toBeNull()
    expect(relocalizeLine(null, 'ru')).toBeNull()
  })

  it('bounded: the oldest of more than 64 made lines is forgotten', () => {
    const first = localizedLine((l) => `${t('runStart.noScenario', undefined, l)} #first`, 'en')
    for (let i = 0; i < 64; i += 1) localizedLine((l) => `${l} line ${i}`, 'en')
    expect(relocalizeLine(first, 'ru')).toBeNull()
    expect(relocalizeLine('en line 63', 'ru')).toBe('ru line 63')
  })
})
