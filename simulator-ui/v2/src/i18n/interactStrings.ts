import { t, type MessageKey } from './t'

/**
 * 046 wave 2 (T4603): the strings of the action windows moved into the one dictionary (`en.ts` / `ru.ts`, areas
 * `payment`, `clearing`, `card`). This module is what is LEFT of the 037 A2 list: a re-export of the dictionary keys
 * still read under their old names by the files of wave 3 - the participant card (`NodeCardOverlay.vue`) and the line
 * window (`EdgeDetailPopup.vue`), which this wave does not edit. They get the ENGLISH text: those two windows are
 * English as a whole until wave 3 moves them, and one Russian sentence inside an English card would be the mixed string
 * spec 046 forbids. Wave 3 calls `t()` directly (the language of the interface) and removes this file; no new key is
 * added here.
 */
const KEYS = {
  cardFlowOpenReason: 'card.flowOpenReason',
  cardBusyReason: 'card.busyReason',
  cardLineAvailable: 'card.lineAvailable',
  lineDirectionTitle: 'payment.lineDirectionTitle',
  lineDirectionNote: 'payment.lineDirectionNote',
} as const satisfies Record<string, MessageKey>

export type InteractStringKey = keyof typeof KEYS

/** The English text of an old key, as before wave 2; placeholders not given stay visible (as before). */
export function interactText(key: InteractStringKey, vars: Record<string, string | number> = {}): string {
  return t(KEYS[key], vars, 'en')
}
