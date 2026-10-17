/**
 * Programme 045, slice S1 - one size scale for the pop-up windows (spec 045, decision 1; Verification plan 1 (а), (б), (в)).
 *
 * Measured in the DOM at 1366x768, in both themes of the product (`saas`, `hud`), on the mocked backend of the 037 specs
 * (`helpers/p037Mock.ts`): what the window manager gives a window (the `.ws-shell`) and what the panel inside it takes.
 *
 *   (а) the payment window on the participant-choice step: the panel takes the whole width of its shell (until S1:
 *       416 of 440 - a 24 px dead strip, F-045-1) and the shell is not taller than 200 px;
 *   (б) the hint `.ds-help` is set at the size of the label `.ds-label` (until S1: the 15 px of the body, F-045-5);
 *   (в) the participant card takes the whole width of its shell (until S1: 320 in 360, F-045-9).
 *
 * Every test logs what it measured (`P045 ...`), so the figures survive in the run output when an assertion fails.
 * The last test only records numbers (no assertion on them): the confirm step, the trust line and clearing panels,
 * the line window and the share of the canvas the payment window covers - the before/after record of the 045 Changelog.
 *
 * What this does NOT see: the frozen themes (`shadcn`, `library`), phone viewports (037 B1 holds those), the result step.
 * A size is read from `getBoundingClientRect`, i.e. what is on screen, not from CSS.
 */
import { expect, test, type Page } from '@playwright/test'

import { mockApp, ready, settleShell } from './helpers/p037Mock.js'

const THEMES = ['saas', 'hud'] as const
type Theme = (typeof THEMES)[number]

type Box = { w: number; h: number }
type WindowFigures = { shell: Box; panel: Box; deadStrip: number }

async function open(page: Page, theme: Theme): Promise<void> {
  await mockApp(page, { paymentRealBodies: [] })
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}`)
  await ready(page, true)
  await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
}

/** The shell of the window holding `inner`, and `inner` itself. */
async function figures(page: Page, inner: string): Promise<WindowFigures> {
  return await page.locator(inner).first().evaluate((el) => {
    const shell = (el.closest('.ws-shell') ?? el).getBoundingClientRect()
    const panel = el.getBoundingClientRect()
    const r = (n: number) => Math.round(n * 10) / 10
    return { shell: { w: r(shell.width), h: r(shell.height) }, panel: { w: r(panel.width), h: r(panel.height) }, deadStrip: r(shell.width - panel.width) }
  })
}

const fontPx = (page: Page, css: string) => page.locator(css).first().evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize))

async function openPayment(page: Page): Promise<void> {
  await page.locator('[data-testid="actionbar-payment"]').click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
  await expect(page.locator('#mp-from__trigger')).toBeVisible()
  await settleShell(page)
}

async function openNodeCard(page: Page): Promise<void> {
  await page.waitForFunction(() => Boolean((window as unknown as { __geoSim?: unknown }).__geoSim))
  await page.evaluate(() => (window as unknown as { __geoSim: { openNodeCard: (o: unknown) => void } }).__geoSim
    .openNodeCard({ nodeId: 'alice', anchor: { x: 500, y: 300 } }))
  await expect(page.locator('.ws-shell[data-win-type="node-card"] .ds-ov-node-card')).toBeVisible()
}

for (const theme of THEMES) {
  test.describe(`045 S1 [${theme}] window scale at 1366x768`, () => {
    test('(а) payment window, choice step: the panel takes the whole shell, the shell is at most 200 px tall', async ({ page }, info) => {
      await open(page, theme)
      await openPayment(page)
      const f = await figures(page, '[data-testid="manual-payment-panel"]')
      console.log(`P045 (а) ${theme} ${JSON.stringify(f)}`)
      info.annotations.push({ type: 'P045-a', description: JSON.stringify(f) })
      expect(f.deadStrip, 'no dead strip between the panel and its shell (F-045-1)').toBeLessThanOrEqual(0.5)
      expect(f.shell.h, 'the choice step is at most 200 px tall').toBeLessThanOrEqual(200)
    })

    test('(б) the hint is set at the size of the label', async ({ page }, info) => {
      await open(page, theme)
      await openPayment(page)
      const sizes = { help: await fontPx(page, '[data-testid="manual-payment-panel"] .mp-pick-help'), label: await fontPx(page, '#mp-from-label') }
      console.log(`P045 (б) ${theme} ${JSON.stringify(sizes)}`)
      info.annotations.push({ type: 'P045-b', description: JSON.stringify(sizes) })
      expect(sizes.help, '.ds-help is the label size (F-045-5)').toBe(sizes.label)
    })

    test('(в) participant card: the card takes the whole shell', async ({ page }, info) => {
      await open(page, theme)
      await openNodeCard(page)
      const f = await figures(page, '.ws-shell[data-win-type="node-card"] .ds-ov-node-card')
      console.log(`P045 (в) ${theme} ${JSON.stringify(f)}`)
      info.annotations.push({ type: 'P045-c', description: JSON.stringify(f) })
      expect(f.deadStrip, 'no dead strip between the card and its shell (F-045-9)').toBeLessThanOrEqual(0.5)
    })

    test('record: confirm step, trust line, clearing, line window, canvas share (numbers only)', async ({ page }, info) => {
      await open(page, theme)
      const canvas = await page.locator('canvas').first().evaluate((el) => { const r = el.getBoundingClientRect(); return r.width * r.height })
      const rec: Record<string, unknown> = {}

      await openPayment(page)
      rec.paymentChoice = await figures(page, '[data-testid="manual-payment-panel"]')
      rec.controlH = await page.locator('#mp-from__trigger').evaluate((el) => Math.round(el.getBoundingClientRect().height * 10) / 10)
      rec.labelW = await page.locator('#mp-from-label').evaluate((el) => Math.round(el.getBoundingClientRect().width * 10) / 10)
      await page.locator('#mp-from__trigger').click()
      await page.locator('#mp-from__surface [role="option"][data-option-value="alice"]').click()
      await page.locator('#mp-to__surface [role="option"][data-option-value="bob"]').click()
      await page.locator('#mp-amount').fill('1.00')
      await expect(page.locator('[data-testid="manual-payment-confirm"]')).toBeEnabled()
      await settleShell(page)
      rec.paymentConfirm = await figures(page, '[data-testid="manual-payment-panel"]')
      const shell = (rec.paymentConfirm as WindowFigures).shell
      rec.paymentConfirmCanvasShare = Math.round((shell.w * shell.h / canvas) * 1000) / 10
      await page.locator('[data-testid="manual-payment-cancel"]').click()
      await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeHidden()

      await page.locator('[data-testid="actionbar-trustline"]').click()
      await expect(page.locator('[data-testid="trustline-panel"]')).toBeVisible()
      await settleShell(page, 'trustline-panel')
      rec.trustline = await figures(page, '[data-testid="trustline-panel"]')
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-testid="trustline-panel"]')).toBeHidden()

      await page.locator('[data-testid="actionbar-clearing"]').click()
      await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
      await settleShell(page, 'clearing-panel')
      rec.clearing = await figures(page, '[data-testid="clearing-panel"]')
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-testid="clearing-panel"]')).toBeHidden()

      await openNodeCard(page)
      rec.nodeCard = await figures(page, '.ws-shell[data-win-type="node-card"] .ds-ov-node-card')
      await page.keyboard.press('Escape')

      await page.evaluate(() => (window as unknown as { __geoSim: { openEdgeDetail: (o: unknown) => void } }).__geoSim
        .openEdgeDetail({ fromPid: 'bob', toPid: 'alice', anchor: { x: 500, y: 300 } }))
      const edge = page.locator('.ws-shell[data-win-type="edge-detail"]')
      await expect(edge).toBeVisible()
      rec.edge = await figures(page, '.ws-shell[data-win-type="edge-detail"] > *')

      console.log(`P045 record ${theme} ${JSON.stringify(rec)}`)
      info.annotations.push({ type: 'P045-record', description: JSON.stringify(rec) })
    })
  })
}
