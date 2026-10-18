/**
 * 046: the English dictionary of the interface - the reference one: its keys are the keys of every other language
 * (`ru.ts` is typed against it). Areas of the screen nest; a leaf is a whole sentence with `{name}` placeholders (never
 * a sentence glued from fragments), or a `plural(...)` message for a count. Names of participants and scenarios, codes
 * of equivalents and amounts are data and never live here.
 *
 * Wave 0 (T4601) holds only what the language switcher needs; the strings of the screen move in by waves 1-3
 * (`interactStrings.ts` and the refusal maps of `utils/paymentRefusalText.ts` included). See spec 046.
 */
export const en = {
  locale: {
    switcherLabel: 'Language',
    switcherAria: 'Interface language',
  },
} as const
