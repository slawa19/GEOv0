/**
 * 046 wave 0 (T4601): the shape of the interface dictionary and the two pure operations on its entries - filling the
 * placeholders of a template and choosing the plural form of a count. No dictionary, no locale state here (`en.ts`,
 * `ru.ts`, `locale.ts`, `t.ts`).
 *
 * A dictionary is a tree of areas of the screen whose leaves are either a template string or a plural message
 * (`plural({ one, few, many, other })`). `ru` is typed as `Messages<typeof en>`: a key missing in `ru`, an extra key, or a
 * leaf of the other kind fails the build. Which plural categories a language needs is not a type - it is the parity
 * guard's (`dictionaryParity.policy.test.ts`), from `Intl.PluralRules` of that language.
 *
 * Money is NOT formatted here: a value is printed with `String(value)`. An amount arrives as a decimal string and is
 * printed as it came (a dot in both languages, spec 046 decision 4); `Intl`/`toLocale*` would group digits and turn the
 * dot into a comma in Russian. `Intl.PluralRules` is used only to pick the form of a count.
 */

/** The brand of a plural leaf. A symbol, so no area key of a dictionary (always a string) can collide with it. */
export const PLURAL: unique symbol = Symbol('geo.i18n.plural')

/** The forms of a plural message by CLDR category; `other` is always required. */
export type PluralForms = { readonly other: string } & { readonly [C in Exclude<Intl.LDMLPluralRule, 'other'>]?: string }

export type PluralMessage = { readonly [PLURAL]: PluralForms }

/** A plural leaf of a dictionary. The template of each form takes `{count}` like any other placeholder. */
export function plural(forms: PluralForms): PluralMessage {
  return Object.freeze({ [PLURAL]: Object.freeze({ ...forms }) })
}

export function isPluralMessage(v: unknown): v is PluralMessage {
  return typeof v === 'object' && v !== null && PLURAL in v
}

/** The dictionary of another language must have exactly these keys, with leaves of the same kind. */
export type Messages<T> = {
  readonly [K in keyof T]: T[K] extends string ? string : T[K] extends PluralMessage ? PluralMessage : Messages<T[K]>
}

type Join<P extends string, K extends string> = P extends '' ? K : `${P}.${K}`

/** The dotted paths of the leaves of kind `Leaf` (a template string or a plural message). */
export type KeysOfKind<T, Leaf, P extends string = ''> = {
  [K in keyof T & string]: T[K] extends Leaf
    ? Join<P, K>
    : T[K] extends string | PluralMessage
      ? never
      : KeysOfKind<T[K], Leaf, Join<P, K>>
}[keyof T & string]

/** A value of a placeholder. A number is printed with `String()` - never through `Intl` (see the header). */
export type MessageVars = Readonly<Record<string, string | number>>

/** Fill `{name}` placeholders; an unknown one is left visible rather than guessed. */
export function fillTemplate(template: string, vars: MessageVars = {}): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (Object.hasOwn(vars, name) ? String(vars[name]) : whole))
}

/** The placeholder names of a template, for the parity guard. */
export function placeholdersOf(template: string): string[] {
  return [...template.matchAll(/\{(\w+)\}/g)].map((m) => m[1])
}

const pluralRules = new Map<string, Intl.PluralRules>()

function rulesOf(locale: string): Intl.PluralRules {
  let rules = pluralRules.get(locale)
  if (!rules) {
    rules = new Intl.PluralRules(locale)
    pluralRules.set(locale, rules)
  }
  return rules
}

/** The CLDR plural categories a language distinguishes (en: one, other; ru: one, few, many, other). */
export function pluralCategoriesOf(locale: string): readonly Intl.LDMLPluralRule[] {
  return rulesOf(locale).resolvedOptions().pluralCategories
}

/**
 * The form of `message` for `count` in `locale`, with `{count}` and `vars` filled. A form the dictionary lacks falls
 * back to `other` - the parity guard keeps that from happening in the shipped dictionaries.
 */
export function formatPlural(message: PluralMessage, locale: string, count: number, vars: MessageVars = {}): string {
  const forms = message[PLURAL]
  const category = rulesOf(locale).select(count)
  return fillTemplate(forms[category] ?? forms.other, { ...vars, count })
}
