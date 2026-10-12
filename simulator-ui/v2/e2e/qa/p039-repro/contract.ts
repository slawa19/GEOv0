/**
 * 039 T3901 - the NAMES the browser reproducers look for, in one place. Not a test file.
 *
 * None of these exists in the product yet: they are what spec 039 names (the legend, the data colour tokens) plus the
 * minimum a test needs to tell one legend entry from another without reading a language. The slice that builds the
 * thing may rename any of them - HERE, once - and the reproducers follow.
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

/** Roles whose legend entry carries a COLOUR swatch (a hub is told by shape). */
export const COLOURED_ROLES: readonly LegendRole[] = ['debtor', 'creditor', 'frozen']

/** Every theme the design system has tokens for; `shadcn` is frozen (owner's decision R1): out of the switcher, tokens kept. */
export const ALL_THEME_IDS = ['hud', 'saas', 'library', 'shadcn'] as const

/** The theme switcher of the running app and where the theme is applied. */
export const THEME = { switcher: { role: 'combobox' as const, name: 'UI theme' }, root: '.root', attribute: 'data-theme' } as const

/** The total debt "was -> now" after a clearing (spec 039, P-5): the bar it is shown in, and the two values, in this order. */
export const DEBT = {
  bar: '[aria-label="System balance"]',
  before: '[data-testid="total-debt-before"]',
  after: '[data-testid="total-debt-after"]',
} as const
