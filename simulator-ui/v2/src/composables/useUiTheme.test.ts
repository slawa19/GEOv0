/**
 * 039 slice B, PR 3 - which theme the app shows, and what it writes back (spec 039, "Решения по проекту среза B",
 * point 6; owner's decision R1). Two themes remain, `saas` (the default) and `hud`. Priority: an explicit URL
 * parameter, then the stored choice, then `saas`. A removed (`shadcn`, `library`) or unknown value becomes `saas`:
 * in storage rewritten ONCE through the storage composable, in the URL through `history.replaceState` (no reload).
 * The absence of the parameter never overwrites the stored choice; `popstate` follows the same rules.
 *
 * Real `useSimulatorStorage` over a counting in-memory storage; the URL is happy-dom's `window.location`.
 *
 * LIMITS: no mounted application - that the root passes `uiTheme` to `data-theme`, listens to `popstate` and re-resolves
 * the data palette after a normalised switch is the stand's (`e2e/qa/themes.qa.ts`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSimulatorStorage } from './usePersistedSimulatorPrefs'
import { useUiTheme } from './useUiTheme'

const KEY = 'geo.uiTheme'

function memoryStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial))
  const writes: Array<[string, string]> = []
  return {
    writes,
    map,
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => { writes.push([k, v]); map.set(k, v) },
    removeItem: (k: string) => { map.delete(k) },
  }
}

function at(search: string) {
  window.history.replaceState(null, '', `/${search}`)
}

const themeParam = () => new URLSearchParams(window.location.search).get('theme')

let replaceSpy: ReturnType<typeof vi.spyOn>
let pushSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  at('?mode=real')
  replaceSpy = vi.spyOn(window.history, 'replaceState')
  pushSpy = vi.spyOn(window.history, 'pushState')
})

afterEach(() => {
  replaceSpy.mockRestore()
  pushSpy.mockRestore()
})

function start(stored: Record<string, string> = {}) {
  // The test's own `at()` moves the URL with replaceState too: count only what the app does from here.
  replaceSpy.mockClear()
  pushSpy.mockClear()
  const mem = memoryStorage(stored)
  const t = useUiTheme(useSimulatorStorage(mem))
  return { mem, ...t }
}

describe('039 B3 - the theme on the first load', () => {
  it('no parameter and nothing stored: saas, and nothing is written anywhere', () => {
    const { uiTheme, mem } = start()
    expect(uiTheme.value).toBe('saas')
    expect(mem.writes, 'the default is not written into storage').toEqual([])
    expect(replaceSpy, 'the URL is left alone').not.toHaveBeenCalled()
  })

  it('stored hud, no parameter: hud - the absence of the parameter does not overwrite the stored choice', () => {
    const { uiTheme, mem } = start({ [KEY]: 'hud' })
    expect(uiTheme.value).toBe('hud')
    expect(mem.writes).toEqual([])
  })

  it('?theme=hud wins over a stored saas, and the stored choice stays saas', () => {
    at('?mode=real&theme=hud')
    const { uiTheme, mem } = start({ [KEY]: 'saas' })
    expect(uiTheme.value).toBe('hud')
    expect(mem.map.get(KEY)).toBe('saas')
    expect(mem.writes).toEqual([])
    expect(replaceSpy, 'a valid parameter is not rewritten').not.toHaveBeenCalled()
  })

  it('?theme=saas wins over a stored hud', () => {
    at('?theme=saas')
    expect(start({ [KEY]: 'hud' }).uiTheme.value).toBe('saas')
  })

  for (const removed of ['shadcn', 'library', 'nonsense', 'SHADCN']) {
    it(`stored "${removed}": saas, the stored value is rewritten ONCE, and not again on the next load`, () => {
      const first = start({ [KEY]: removed })
      expect(first.uiTheme.value).toBe('saas')
      expect(first.mem.writes).toEqual([[KEY, 'saas']])
      // The next load over the same storage: nothing to rewrite.
      const again = useUiTheme(useSimulatorStorage(first.mem))
      expect(again.uiTheme.value).toBe('saas')
      expect(first.mem.writes, 'rewritten once, not on every load').toEqual([[KEY, 'saas']])
    })

    it(`?theme=${removed}: saas, the URL is normalised by replaceState (no push, no reload), other parameters kept`, () => {
      at(`?mode=real&theme=${removed}&ui=interact#x`)
      const { uiTheme, mem } = start({ [KEY]: 'hud' })
      expect(uiTheme.value, 'an explicit parameter, even a removed one, wins over the stored choice').toBe('saas')
      expect(replaceSpy).toHaveBeenCalledTimes(1)
      expect(pushSpy).not.toHaveBeenCalled()
      expect(themeParam()).toBe('saas')
      const q = new URLSearchParams(window.location.search)
      expect([q.get('mode'), q.get('ui'), window.location.hash]).toEqual(['real', 'interact', '#x'])
      expect(mem.map.get(KEY), 'a URL does not write the stored choice').toBe('hud')
    })
  }

  it('stored shadcn and ?theme=hud: hud on screen, and the stored shadcn is still rewritten to saas (once)', () => {
    at('?theme=hud')
    const { uiTheme, mem } = start({ [KEY]: 'shadcn' })
    expect(uiTheme.value).toBe('hud')
    expect(mem.writes).toEqual([[KEY, 'saas']])
  })

  it('an empty ?theme= is the absence of the parameter: the stored choice holds', () => {
    at('?theme=')
    expect(start({ [KEY]: 'hud' }).uiTheme.value).toBe('hud')
  })
})

describe('039 B3 - a live switch and back/forward', () => {
  it('setUiTheme(hud) stores hud and puts ?theme=hud; setUiTheme(saas) stores saas and drops the parameter', () => {
    const { uiTheme, setUiTheme, mem } = start()
    setUiTheme('hud')
    expect([uiTheme.value, mem.map.get(KEY), themeParam()]).toEqual(['hud', 'hud', 'hud'])
    setUiTheme('saas')
    expect([uiTheme.value, mem.map.get(KEY), themeParam()]).toEqual(['saas', 'saas', null])
    expect(pushSpy, 'a switch does not add history entries').not.toHaveBeenCalled()
  })

  it('setUiTheme of a removed id (a stale caller) shows and stores saas, never the removed id', () => {
    const { uiTheme, setUiTheme, mem } = start({ [KEY]: 'hud' })
    setUiTheme('shadcn' as never)
    expect([uiTheme.value, mem.map.get(KEY)]).toEqual(['saas', 'saas'])
  })

  it('popstate: the URL of the entry wins; an entry without the parameter falls back to the stored choice, not to a stale one', () => {
    const { uiTheme, onPopState, setUiTheme } = start()
    setUiTheme('hud') // stored hud
    at('?theme=saas')
    onPopState()
    expect(uiTheme.value).toBe('saas')
    at('?mode=real')
    onPopState()
    expect(uiTheme.value, 'no parameter: the stored choice (hud), not the default and not the last URL').toBe('hud')
    at('?theme=library')
    onPopState()
    expect([uiTheme.value, themeParam()]).toEqual(['saas', 'saas'])
  })

  it('popstate does not rewrite a valid stored choice (a newer choice is never overwritten)', () => {
    const { onPopState, setUiTheme, mem } = start({ [KEY]: 'shadcn' })
    expect(mem.writes).toEqual([[KEY, 'saas']])
    setUiTheme('hud')
    at('?mode=real')
    onPopState()
    expect(mem.map.get(KEY)).toBe('hud')
    expect(mem.writes).toEqual([[KEY, 'saas'], [KEY, 'hud']])
  })
})
