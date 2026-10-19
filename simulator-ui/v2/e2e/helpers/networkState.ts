/**
 * 046 wave 1, §15 Codex round (2026-10-19, P3 "VERDICT-STAND-RECIPES: WEAK"): the state of the network on the first row,
 * judged by BOTH halves together - the machine attribute `data-network-state` (which words are shown, language-free)
 * and the word the person actually reads, from the dictionary of the page's own language (`<html lang>`). An attribute
 * alone let a mismatch through: `paused` on the element while the RUNNING word is drawn passed every check.
 *
 * Both halves are read in ONE snapshot of the page (one `evaluate`), so a state that changes between two reads cannot
 * pair the attribute of one state with the word of another; the snapshot is polled until it shows one of `states`.
 * What this does NOT see: the colour of the dot (the tone) and whether the word is visible on screen (its box).
 */
import { expect, type Page } from '@playwright/test'

import { t, type MessageKey } from '../../src/i18n/t'

export type NetworkStateKey = 'offline' | 'noConnection' | 'running' | 'paused' | 'created' | 'stopping' | 'stopped' | 'error' | 'pending' | 'noRun'

const wordKey = (key: string): MessageKey => (key === 'noConnection' ? 'topBar.noConnection' : (`topBar.state.${key}` as MessageKey))

type Snapshot = { key: string | null; word: string; lang: string }

function judge(s: Snapshot): string {
  if (!s.key) return 'no network state on the page'
  const lang = s.lang.toLowerCase().startsWith('ru') ? 'ru' : 'en'
  const expected = t(wordKey(s.key), undefined, lang)
  return s.word === expected ? `ok:${s.key}` : `mismatch:${s.key}: the element says "${s.word}", the ${lang} word of ${s.key} is "${expected}"`
}

/** The network state is one of `states`, and the word drawn is that state's word in the page's language. */
export async function expectNetworkState(
  page: Page,
  states: NetworkStateKey | readonly NetworkStateKey[],
  opts: { timeout?: number; message?: string } = {},
): Promise<void> {
  const list = (Array.isArray(states) ? states : [states]) as readonly NetworkStateKey[]
  await expect
    .poll(async () => judge(await page.evaluate(() => {
      const el = document.querySelector('[data-testid="network-state"]')
      return {
        key: el?.getAttribute('data-network-state') ?? null,
        word: (el?.querySelector('.ds-value')?.textContent ?? '').trim(),
        lang: document.documentElement.lang || '',
      }
    })), { timeout: opts.timeout, message: opts.message ?? `the network state is ${list.join(' or ')}, with its word` })
    .toMatch(new RegExp(`^ok:(${list.join('|')})$`))
}
