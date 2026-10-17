/**
 * Unit tests for useSimulatorStorage: the 0/1 preference flags of the two bars (analytics panel, "Подробнее").
 * 043 S3: the DevTools prefs (real / demo / snapshot for the reload-based Demo UI enter/exit) are gone with Demo UI.
 */

import { describe, expect, it, beforeEach } from 'vitest'
import { useSimulatorStorage } from './usePersistedSimulatorPrefs'

// ---------------------------------------------------------------------------
// Minimal in-memory storage stub
// ---------------------------------------------------------------------------
function makeStorage() {
  const map = new Map<string, string>()
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => { map.set(k, v) },
    removeItem: (k: string) => { map.delete(k) },
    _map: map,
  }
}

// ---------------------------------------------------------------------------
// Analytics overlay surface visibility (spec 007, T705)
// ---------------------------------------------------------------------------
describe('useSimulatorStorage — analytics panel visibility', () => {
  let store: ReturnType<typeof makeStorage>
  let prefs: ReturnType<typeof useSimulatorStorage>

  beforeEach(() => {
    store = makeStorage()
    prefs = useSimulatorStorage(store)
  })

  it('returns null when the user has never touched the toggle', () => {
    // Null, not false: "never chosen" is what lets the caller apply its own default.
    expect(prefs.readAnalyticsPanelOpen()).toBeNull()
  })

  it('round-trips through the same 0/1 encoding as its bottom-bar neighbours', () => {
    prefs.writeAnalyticsPanelOpen(true)
    expect(store._map.get('geo.sim.v2.analytics.open')).toBe('1')
    expect(prefs.readAnalyticsPanelOpen()).toBe(true)

    prefs.writeAnalyticsPanelOpen(false)
    expect(store._map.get('geo.sim.v2.analytics.open')).toBe('0')
    expect(prefs.readAnalyticsPanelOpen()).toBe(false)
  })

  it('keeps its own key: writing it does not disturb the "Подробнее" flag beside it', () => {
    prefs.writeDetailsOpen(true)
    prefs.writeAnalyticsPanelOpen(false)

    expect(prefs.readDetailsOpen()).toBe(true)
    expect(prefs.readAnalyticsPanelOpen()).toBe(false)
  })
})

describe('useSimulatorStorage — the "Подробнее" toggle (043 S1)', () => {
  it('reads null before the first choice and round-trips its own key in the 0/1 encoding', () => {
    const store = makeStorage()
    const prefs = useSimulatorStorage(store)
    // Null, not false: the caller's default (off) applies to a first visit.
    expect(prefs.readDetailsOpen()).toBeNull()

    prefs.writeDetailsOpen(true)
    expect(store._map.get('geo.sim.v2.details.open')).toBe('1')
    expect(prefs.readDetailsOpen()).toBe(true)

    prefs.writeDetailsOpen(false)
    expect(store._map.get('geo.sim.v2.details.open')).toBe('0')
    expect(prefs.readDetailsOpen()).toBe(false)
    // Its own key: the analytics flag beside it is untouched.
    expect(prefs.readAnalyticsPanelOpen()).toBeNull()
  })
})
