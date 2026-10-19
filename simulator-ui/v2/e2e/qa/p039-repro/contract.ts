/**
 * 039 T3901 - the NAMES the browser reproducers look for, in one place. Not a test file.
 *
 * They are what spec 039 names (the legend, the data colour tokens) plus the minimum a test needs to tell one legend
 * entry from another without reading a language. The slice that builds the thing may rename any of them - HERE,
 * once - and the reproducers follow.
 *
 * 039 slice B, PR 1 (2026-10-12): the legend, its entries and the data tokens EXIST now, under exactly these names
 * (`src/components/Legend.vue`, `src/vizMapping.ts` `DATA_TOKEN`). Nothing was renamed. Added: the entries the design
 * decisions of slice B brought (`LEGEND_ENTRIES_ALL`) and the fifth token, the neutral. `DEBT` is still slice D's.
 */

/** Net roles a legend explains (spec 039, owner's decision R3 and amendment P-1). A hub is a shape or a ring, not a colour. */
export const LEGEND_ROLES = ['debtor', 'creditor', 'frozen', 'hub'] as const
export type LegendRole = (typeof LEGEND_ROLES)[number]

export const LEGEND = {
  /** The legend itself. */
  root: '[data-testid="legend"]',
  /** One entry of the legend, by the role it explains: `[data-legend-role="debtor"]`. */
  entry: (role: LegendRole) => `[data-legend-role="${role}"]`,
  /** The colour sample inside an entry; its computed `background-color` is the colour of the role. */
  swatch: '[data-legend-swatch]',
} as const

/** The data colour of a role: the same value in every theme (spec 039, P-6). */
export const DATA_TOKEN: Record<LegendRole, `--${string}`> = {
  debtor: '--ds-data-debtor',
  creditor: '--ds-data-creditor',
  frozen: '--ds-data-frozen',
  hub: '--ds-data-hub',
}

/**
 * Every entry the legend can show, in its fixed order (slice B, decision 7). An entry is on screen only when the shown
 * snapshot has the feature; `LEGEND_ROLES` above are the four the first form of R2 asked for.
 */
export const LEGEND_ENTRIES_ALL = ['debtor', 'creditor', 'neutral', 'unknown', 'frozen', 'hub', 'person', 'organisation'] as const

/** The fifth data token: the fill of an even and of an unknown net position. */
export const NEUTRAL_TOKEN = '--ds-data-neutral' as const

/** Roles whose legend entry carries a COLOUR swatch (a hub is told by shape). */
export const COLOURED_ROLES: readonly LegendRole[] = ['debtor', 'creditor', 'frozen']

/** Every theme the design system has tokens for; `shadcn` is frozen (owner's decision R1): out of the switcher, tokens kept. */
export const ALL_THEME_IDS = ['hud', 'saas', 'library', 'shadcn'] as const

/** The theme switcher of the running app and where the theme is applied. */
// 046 wave 1, full (2026-10-19): by test id / data attribute - the words are the dictionary's (language of the page).
export const THEME = { switcher: { testId: 'theme-select' }, root: '.root', attribute: 'data-theme' } as const

/** The total debt "was -> now" after a clearing (spec 039, P-5): the bar it is shown in, and the two values, in this order. */
export const DEBT = {
  bar: '[data-testid="system-balance"]',
  before: '[data-testid="total-debt-before"]',
  after: '[data-testid="total-debt-after"]',
} as const
