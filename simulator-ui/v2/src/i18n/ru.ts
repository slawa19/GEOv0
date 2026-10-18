import type { en } from './en'
import type { Messages } from './messages'

/**
 * 046: the Russian dictionary. `satisfies Messages<typeof en>`: a key missing here, an extra key, or a leaf of the
 * other kind (a template where English has a plural, or back) fails the build. The plural categories Russian needs
 * (one, few, many, other) and the rule "no Latin-only text" are held by the parity guard
 * (`dictionaryParity.policy.test.ts`), not by the type.
 */
export const ru = {
  locale: {
    switcherLabel: 'Язык',
    switcherAria: 'Язык интерфейса',
  },
} as const satisfies Messages<typeof en>
