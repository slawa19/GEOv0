import { describe, expect, it } from 'vitest'

import { displayParticipantId } from './participantDisplayId'

describe('displayParticipantId', () => {
  const scenario = 'greenfield-village-100-realistic-v2'

  it('removes the prefix of the scene scenario', () => {
    expect(displayParticipantId(`${scenario}:PID_U0046_6df7ddce`, scenario)).toBe('PID_U0046_6df7ddce')
  })

  it("leaves another scenario's prefix alone", () => {
    const other = 'riverside-town-50-realistic-v2:PID_U0029_EC90D'
    expect(displayParticipantId(other, scenario)).toBe(other)
  })

  it('leaves an id without a prefix alone', () => {
    expect(displayParticipantId('PID_U0046_6df7ddce', scenario)).toBe('PID_U0046_6df7ddce')
    expect(displayParticipantId('alice', scenario)).toBe('alice')
  })

  it('leaves the id alone when the scenario is unknown', () => {
    const id = `${scenario}:PID_U0046_6df7ddce`
    expect(displayParticipantId(id, '')).toBe(id)
    expect(displayParticipantId(id, '   ')).toBe(id)
    expect(displayParticipantId(id, undefined)).toBe(id)
    expect(displayParticipantId(id, null)).toBe(id)
  })

  it('leaves an id that is only the prefix alone (nothing would remain to show)', () => {
    expect(displayParticipantId(`${scenario}:`, scenario)).toBe(`${scenario}:`)
  })

  it('matches the scenario id exactly, not as a leading part of a longer one', () => {
    const id = `${scenario}-copy:PID_U0001`
    expect(displayParticipantId(id, scenario)).toBe(id)
  })

  it('removes only one prefix, not a colon found later in the id', () => {
    expect(displayParticipantId(`${scenario}:a:b`, scenario)).toBe('a:b')
    expect(displayParticipantId('x:PID_U0001', scenario)).toBe('x:PID_U0001')
  })

  it('tolerates a missing id', () => {
    expect(displayParticipantId(undefined, scenario)).toBe('')
    expect(displayParticipantId(null, scenario)).toBe('')
  })
})
