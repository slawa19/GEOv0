/**
 * 038 S2 - the ratchet of the window matrix (AGENTS.md §6). Not a test file.
 *
 * Two constants, both TWO-SIDED: a run that finds more than is written here is red, and a run that finds LESS is red
 * too ("it got better - update the constant"), so the constant cannot drift above what is on screen.
 *
 * Why anything is allowed to stand here at all: the stand does not change the look of the product (owner's decision
 * 2026-10-10), and a finding whose only cure is a size, a spacing or a colour - or which lives outside the owner
 * surface of 038 - is recorded for its recipient instead of being fixed. Each line carries the reason.
 *
 * Measured on Chromium without GPU, dev server, the ring of six, theme and viewport as named. Every movement of
 * either constant is a dated line here:
 *
 * - 2026-10-11 (038 S2): first record.
 */
import type { Theme } from './surfaceMatrix.js'

/**
 * Geometry and target-size findings in the `hud` theme, per entry of the matrix. A finding is a key without pixel
 * amounts (those go to the report): `outside:<edge>@<viewport>`, `small-target:<control>`.
 */
export const KNOWN_FINDINGS: Record<string, Array<{ finding: string; why: string; /** YYYY-MM-DD, the day the line was written. */ since: string }>> = {}

/**
 * axe (WCAG 2.1 A/AA), nodes of impact `serious` or `critical` by rule, for the surface alone (`include` of its
 * root). `page` is the whole page with nothing summoned. A missing entry or theme means zero.
 *
 * No rule is switched off anywhere. `color-contrast` is NOT in these numbers for most nodes: over a canvas axe cannot
 * decide and reports them as `incomplete` - a blind zone of the stand, not a zero (see `qa-stand.md`).
 */
export const AXE_BASELINE: Record<string, Partial<Record<Theme, Record<string, number>>>> = {
  // `.hud-bar__center` and `.wm-layer`: the S0 start value (R8), unchanged.
  page: { hud: { 'aria-prohibited-attr': 2 }, shadcn: { 'aria-prohibited-attr': 2 }, saas: { 'aria-prohibited-attr': 2 }, library: { 'aria-prohibited-attr': 2 } },
  // `.hud-bar__center` again, seen from inside the top bar; in Auto-Run the same node holds controls and axe does not flag it.
  'top-bar-interact': { hud: { 'aria-prohibited-attr': 1 }, shadcn: { 'aria-prohibited-attr': 1 }, saas: { 'aria-prohibited-attr': 1 }, library: { 'aria-prohibited-attr': 1 } },
  // hud: "Send payment", "Change limit", the ghost close button. shadcn: the ghost close button.
  'edge-popup': { hud: { 'color-contrast': 3 }, shadcn: { 'color-contrast': 1 } },
  // The "Copy" button of a dev-only overlay (`?perf=1`).
  'dev-perf-overlay': { hud: { 'color-contrast': 1 } },
  // shadcn: the muted "(ESC to close)" of the header, the pick hint, the ghost Cancel; in clearing also the primary Confirm.
  'payment-panel': { shadcn: { 'color-contrast': 3 } },
  'trustline-panel': { shadcn: { 'color-contrast': 3 } },
  'clearing-panel': { shadcn: { 'color-contrast': 4 } },
  // shadcn: the meta line and the primary button of the card.
  'node-card': { shadcn: { 'color-contrast': 2 } },
}

/** Why each baselined axe rule stands. A rule that appears in `AXE_BASELINE` must be named here. */
export const AXE_WHY: Record<string, string> = {
  'aria-prohibited-attr': 'An `aria-label` on a `div` without a role: `.hud-bar__center` (TopBar.vue) and `.wm-layer` (SimulatorAppRoot.vue). '
    + 'Both files are outside the owner surface of 038, and the cure - a role, or no label - is a decision of the maintainer of the HUD. Recorded 2026-10-11 (S0, R8).',
  'color-contrast': 'The cure is a colour, and the stand does not change colours (owner\'s decision 2026-10-10: he does the visual acceptance himself). '
    + 'The nodes are in the report of the run, for him. Recorded 2026-10-11.',
}
