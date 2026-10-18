import { en } from './en'
import { t, type MessageKey } from './t'

/**
 * 046 wave 2 (decision 3): the raw status tokens the action windows show, told in the language of the interface by
 * closed sets of the dictionary. A token outside its set is shown as it came (it is the server's word and stays
 * correlatable), never guessed. Wave 1 (T4602, narrowed) adds the stop reason of a run.
 */
type ClosedSetArea = 'participantStatus' | 'paymentStatus'

function byClosedSet(area: ClosedSetArea, token: unknown, matchCase: boolean): string {
  const raw = String(token ?? '').trim()
  const key = matchCase ? raw : raw.toLowerCase()
  return Object.hasOwn(en[area], key) ? t(`${area}.${key}` as MessageKey) : raw
}

/** A participant status (`active`, `frozen`, `suspended`, `left`, `deleted`); case-insensitive, as `isActiveStatus`. */
export function participantStatusText(token: unknown): string {
  return byClosedSet('participantStatus', token, false)
}

/** A payment status (`COMMITTED`, `ABORTED`); as the server spells it. */
export function paymentStatusText(token: unknown): string {
  return byClosedSet('paymentStatus', token, true)
}

/**
 * 046 wave 1: why a run stopped (`RunStatus.stop_reason`) - the closed set of `runStop` (the reasons the clients of this
 * repository send, and the server's idle stop of 044 stage 2) in the language of the interface. Any other non-empty
 * value is the general text WITH the value (`runStop.unknown`): it is a code to correlate, never dropped and never
 * guessed into a known reason. Empty for no reason.
 */
export function runStopText(reason: unknown): string {
  const raw = String(reason ?? '').trim()
  if (!raw) return ''
  if (raw !== 'unknown' && Object.hasOwn(en.runStop, raw)) return t(`runStop.${raw}` as MessageKey)
  return t('runStop.unknown', { code: raw })
}
