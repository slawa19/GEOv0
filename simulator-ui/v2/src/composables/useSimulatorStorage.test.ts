/**
 * Unit tests for useSimulatorStorage (DevTools panel prefs, snapshot/restore logic).
 *
 * Tests cover:
 *  a) readDevtoolsOpenReal / writeDevtoolsOpenReal — read/write real pref
 *  b) readDevtoolsOpenDemo / writeDevtoolsOpenDemo — read/write demo pref
 *  c) snapshot/restore pattern:
 *     - entering demo: save snapshot of real state, read demo pref
 *     - exiting demo: restore snapshot into real state, clear snapshot
 *  d) clearDevtoolsOpenRealSnapshot — removes key, returns null on next read
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

describe('useSimulatorStorage — DevTools panel prefs', () => {
  let store: ReturnType<typeof makeStorage>
  let prefs: ReturnType<typeof useSimulatorStorage>

  beforeEach(() => {
    store = makeStorage()
    prefs = useSimulatorStorage(store)
  })

  // -------------------------------------------------------------------------
  // a) Real pref read/write
  // -------------------------------------------------------------------------
  describe('readDevtoolsOpenReal / writeDevtoolsOpenReal', () => {
    it('returns null when key is absent', () => {
      expect(prefs.readDevtoolsOpenReal()).toBeNull()
    })

    it('persists true as "1" and reads back true', () => {
      prefs.writeDevtoolsOpenReal(true)
      expect(store._map.get('geo.sim.v2.devtools.open.real')).toBe('1')
      expect(prefs.readDevtoolsOpenReal()).toBe(true)
    })

    it('persists false as "0" and reads back false', () => {
      prefs.writeDevtoolsOpenReal(false)
      expect(store._map.get('geo.sim.v2.devtools.open.real')).toBe('0')
      expect(prefs.readDevtoolsOpenReal()).toBe(false)
    })

    it('overwrite: write true then false', () => {
      prefs.writeDevtoolsOpenReal(true)
      prefs.writeDevtoolsOpenReal(false)
      expect(prefs.readDevtoolsOpenReal()).toBe(false)
    })
  })

  // -------------------------------------------------------------------------
  // b) Demo pref read/write
  // -------------------------------------------------------------------------
  describe('readDevtoolsOpenDemo / writeDevtoolsOpenDemo', () => {
    it('returns null when key is absent', () => {
      expect(prefs.readDevtoolsOpenDemo()).toBeNull()
    })

    it('persists true as "1" and reads back true', () => {
      prefs.writeDevtoolsOpenDemo(true)
      expect(store._map.get('geo.sim.v2.devtools.open.demo')).toBe('1')
      expect(prefs.readDevtoolsOpenDemo()).toBe(true)
    })

    it('persists false as "0" and reads back false', () => {
      prefs.writeDevtoolsOpenDemo(false)
      expect(prefs.readDevtoolsOpenDemo()).toBe(false)
    })
  })

  // -------------------------------------------------------------------------
  // c) Snapshot read/write/clear
  // -------------------------------------------------------------------------
  describe('readDevtoolsOpenRealSnapshot / writeDevtoolsOpenRealSnapshot / clearDevtoolsOpenRealSnapshot', () => {
    it('returns null when snapshot key is absent', () => {
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBeNull()
    })

    it('writes and reads back snapshot true', () => {
      prefs.writeDevtoolsOpenRealSnapshot(true)
      expect(store._map.get('geo.sim.v2.devtools.open.realSnapshot')).toBe('1')
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBe(true)
    })

    it('writes and reads back snapshot false', () => {
      prefs.writeDevtoolsOpenRealSnapshot(false)
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBe(false)
    })

    it('clearDevtoolsOpenRealSnapshot removes the key → returns null', () => {
      prefs.writeDevtoolsOpenRealSnapshot(true)
      prefs.clearDevtoolsOpenRealSnapshot()
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBeNull()
      // key must be absent from storage (not "null" string)
      expect(store._map.has('geo.sim.v2.devtools.open.realSnapshot')).toBe(false)
    })
  })

  // -------------------------------------------------------------------------
  // d) Enter-demo pattern: snapshot real → switch to demo
  // -------------------------------------------------------------------------
  describe('Enter-demo pattern (snapshot current real state before switching)', () => {
    it('saves snapshot of real open state when entering demo, demo pref is independent', () => {
      // Simulate: real devtools was open
      prefs.writeDevtoolsOpenReal(true)

      // Before entering demo: save snapshot
      const currentReal = prefs.readDevtoolsOpenReal()
      prefs.writeDevtoolsOpenRealSnapshot(currentReal ?? false)

      // Write demo pref (e.g. auto-open on first entry)
      prefs.writeDevtoolsOpenDemo(false)

      // Snapshot must reflect real state
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBe(true)
      // Demo pref is independent
      expect(prefs.readDevtoolsOpenDemo()).toBe(false)
    })

    it('snapshot is null when real pref was never set, still saves false', () => {
      // Real pref absent — treat as false
      const currentReal = prefs.readDevtoolsOpenReal() // null
      prefs.writeDevtoolsOpenRealSnapshot(currentReal ?? false)
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBe(false)
    })
  })

  // -------------------------------------------------------------------------
  // e) Exit-demo pattern: restore snapshot to real, clear snapshot
  // -------------------------------------------------------------------------
  describe('Exit-demo pattern (restore snapshot to real state after exiting demo)', () => {
    it('restores snapshot into real pref and clears snapshot', () => {
      // Setup: had real=true, snapshot saved as true, demo=false
      prefs.writeDevtoolsOpenReal(true)
      prefs.writeDevtoolsOpenRealSnapshot(true)
      prefs.writeDevtoolsOpenDemo(false)

      // Simulate exiting demo: restore snapshot → real, clear snapshot
      const snapshot = prefs.readDevtoolsOpenRealSnapshot()
      if (snapshot !== null) {
        prefs.writeDevtoolsOpenReal(snapshot)
      }
      prefs.clearDevtoolsOpenRealSnapshot()

      expect(prefs.readDevtoolsOpenReal()).toBe(true)
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBeNull()
    })

    it('restore false snapshot overrides real pref', () => {
      prefs.writeDevtoolsOpenReal(true) // was open
      prefs.writeDevtoolsOpenRealSnapshot(false) // snapshot: was closed

      // Exit demo
      const snapshot = prefs.readDevtoolsOpenRealSnapshot()
      if (snapshot !== null) {
        prefs.writeDevtoolsOpenReal(snapshot)
      }
      prefs.clearDevtoolsOpenRealSnapshot()

      expect(prefs.readDevtoolsOpenReal()).toBe(false) // restored to closed
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBeNull()
    })
  })

  // -------------------------------------------------------------------------
  // f) All three keys are independent (no cross-contamination)
  // -------------------------------------------------------------------------
  describe('Keys are independent', () => {
    it('writing real pref does not affect demo pref or snapshot', () => {
      prefs.writeDevtoolsOpenReal(true)
      expect(prefs.readDevtoolsOpenDemo()).toBeNull()
      expect(prefs.readDevtoolsOpenRealSnapshot()).toBeNull()
    })

    it('clearing snapshot does not affect real or demo prefs', () => {
      prefs.writeDevtoolsOpenReal(true)
      prefs.writeDevtoolsOpenDemo(false)
      prefs.writeDevtoolsOpenRealSnapshot(true)

      prefs.clearDevtoolsOpenRealSnapshot()

      expect(prefs.readDevtoolsOpenReal()).toBe(true)
      expect(prefs.readDevtoolsOpenDemo()).toBe(false)
    })
  })
})
