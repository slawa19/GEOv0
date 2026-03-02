import { createApp, h } from 'vue'
import { describe, expect, it } from 'vitest'

import {
  readWindowManagerEnabledFromUrl,
  provideWindowManagerEnabled,
  useWindowManagerEnabled,
} from './featureFlag'

function setUrl(search: string) {
  window.history.replaceState({}, '', search)
}

describe('featureFlag', () => {
  describe('readWindowManagerEnabledFromUrl', () => {
    it('returns true by default (no wm param)', () => {
      setUrl('/?mode=real&ui=interact')
      expect(readWindowManagerEnabledFromUrl()).toBe(true)
    })

    it('returns true for wm=1', () => {
      setUrl('/?wm=1')
      expect(readWindowManagerEnabledFromUrl()).toBe(true)
    })

    it('returns false for wm=0 (explicit opt-out)', () => {
      setUrl('/?wm=0')
      expect(readWindowManagerEnabledFromUrl()).toBe(false)
    })

    it('returns true for unknown wm values (e.g. wm=2)', () => {
      setUrl('/?wm=2')
      expect(readWindowManagerEnabledFromUrl()).toBe(true)
    })
  })

  describe('useWindowManagerEnabled (without injection context)', () => {
    it('defaults to true when called outside Vue setup', () => {
      // Outside of a Vue component setup(), getCurrentInstance() === null
      // → should return the default (true, i.e. WM enabled).
      expect(useWindowManagerEnabled()).toBe(true)
    })
  })

  describe('provide/inject round-trip', () => {
    it('injects provided value (true)', () => {
      let result: boolean | undefined
      const host = document.createElement('div')
      document.body.appendChild(host)

      const app = createApp({
        setup() {
          provideWindowManagerEnabled(true)
          return () =>
            h({
              setup() {
                result = useWindowManagerEnabled()
                return () => null
              },
            })
        },
      })
      app.mount(host)
      app.unmount()
      host.remove()

      expect(result).toBe(true)
    })

    it('injects provided value (false)', () => {
      let result: boolean | undefined
      const host = document.createElement('div')
      document.body.appendChild(host)

      const app = createApp({
        setup() {
          provideWindowManagerEnabled(false)
          return () =>
            h({
              setup() {
                result = useWindowManagerEnabled()
                return () => null
              },
            })
        },
      })
      app.mount(host)
      app.unmount()
      host.remove()

      expect(result).toBe(false)
    })
  })
})
