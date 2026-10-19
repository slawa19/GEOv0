import { en } from './en'
import { uiLocaleRef, type UiLocale } from './locale'
import {
  fillTemplate,
  formatPlural,
  isPluralMessage,
  type KeysOfKind,
  type MessageVars,
  type Messages,
  type PluralMessage,
} from './messages'
import { ru } from './ru'

/**
 * 046 wave 0 (T4601): the text of a key in the language of the interface. `t` for a template, `tn` for a plural
 * message and its count. Both read `uiLocaleRef`, so a template that calls them renders again on a switch.
 */

export type MessageKey = KeysOfKind<typeof en, string>
export type PluralKey = KeysOfKind<typeof en, PluralMessage>

export const DICTIONARIES: Readonly<Record<UiLocale, Messages<typeof en>>> = Object.freeze({ en, ru })

function lookup(locale: UiLocale, key: string): unknown {
  let node: unknown = DICTIONARIES[locale]
  for (const part of key.split('.')) {
    if (typeof node !== 'object' || node === null || isPluralMessage(node)) return undefined
    node = (node as Record<string, unknown>)[part]
  }
  return node
}

/**
 * The template of `key` with `vars` filled. A key the dictionary lacks cannot be named (the types); should one reach
 * here anyway, the key itself is shown - a visible defect, not a text from another language.
 */
export function t(key: MessageKey, vars?: MessageVars, locale: UiLocale = uiLocaleRef.value): string {
  const m = lookup(locale, key)
  return typeof m === 'string' ? fillTemplate(m, vars) : key
}

/**
 * The template of `key` as its parts, in order: literal text and placeholder names - for a sentence whose figures each
 * keep an element of their own (a test id, a monospace face) while the sentence stays ONE dictionary entry.
 */
export function templateParts(key: MessageKey, locale: UiLocale = uiLocaleRef.value): Array<{ text: string } | { name: string }> {
  const m = lookup(locale, key)
  const template = typeof m === 'string' ? m : key
  const out: Array<{ text: string } | { name: string }> = []
  let at = 0
  for (const match of template.matchAll(/\{(\w+)\}/g)) {
    if (match.index > at) out.push({ text: template.slice(at, match.index) })
    out.push({ name: match[1] })
    at = match.index + match[0].length
  }
  if (at < template.length) out.push({ text: template.slice(at) })
  return out
}

/** The form of the plural message `key` for `count`, `{count}` and `vars` filled. */
export function tn(key: PluralKey, count: number, vars?: MessageVars, locale: UiLocale = uiLocaleRef.value): string {
  const m = lookup(locale, key)
  return isPluralMessage(m) ? formatPlural(m, locale, count, vars) : key
}

/**
 * 046 wave 3 (BACKLOG 046-3): a text KEPT as what made it - the key and its data - and drawn in the language of NOW, so a
 * text that outlives the moment it was made (a history line, a success notification) follows a switch. The same pattern
 * as the navigator's announcement (wave 1) and the run's error line (`RunErrorLine`): the value carries its cause; the
 * text is never parsed back into one. `vars` are data (names, amounts, counts) - the same in every language.
 */
export type KeptText =
  | { readonly key: MessageKey; readonly vars?: MessageVars }
  | { readonly plural: PluralKey; readonly count: number; readonly vars?: MessageVars }

/** The text of a kept text in `locale` (the language of the interface by default: a template calling this follows it). */
export function keptText(v: KeptText, locale: UiLocale = uiLocaleRef.value): string {
  return 'plural' in v ? tn(v.plural, v.count, v.vars, locale) : t(v.key, v.vars, locale)
}
