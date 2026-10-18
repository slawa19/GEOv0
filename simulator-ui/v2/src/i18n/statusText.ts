import { en } from './en'
import { t, type MessageKey } from './t'

/**
 * 046 wave 2 (decision 3): the raw status tokens the action windows show, told in the language of the interface by
 * closed sets of the dictionary. A token outside its set is shown as it came (it is the server's word and stays
 * correlatable), never guessed. Trust line statuses are not shown by these windows (the close action reads
 * `status === 'closed'` only), so they have no set here.
 */
function byClosedSet(area: 'participantStatus' | 'paymentStatus', token: unknown, matchCase: boolean): string {
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
