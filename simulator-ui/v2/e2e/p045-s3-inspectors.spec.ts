/**
 * Programme 045, slice S3 - the two inspectors: the participant card and the line window (spec 045, decision 3), plus
 * BACKLOG 043-4 (card buttons while another action is open) and 045-2 (the canvas seen through a window).
 *
 * Measured in the DOM at 1366x768, in both product themes (`saas`, `hud`), on the mocked backend of the 037 specs
 * (`helpers/p037Mock.ts`; Alice holds the line Bob -> Alice, and with `aliceLines` eight more lines Alice -> x01..x08):
 *
 *   (е1) card: the head is one block - name and role on a line, balance and close beside it (the meta line under the name);
 *   (е2) card: the statistics are one line (Out, In - Degree was dropped in S4: the group counters carry the number of lines);
 *   (е3) card: a row of a line has no third column - what is available is in the row's `title`;
 *   (е4) card: with nine lines the list shows at most five rows and scrolls;
 *   (е5) card: while another action is open, Pay and New line are `disabled` and say why on a visible line (043-4);
 *   (ж1) line window: as wide as its shell's preferred width (until S3: 486, the content's maximum);
 *   (ж2) line window: Used / Limit · avail is one line (the status ends the bar's line); every button is on one row;
 *   (з)  a window hides what the canvas draws under it (045-2): the pixels inside the window are the same with the canvas
 *        hidden and shown.
 *
 * A "visual row" is a group of on-screen leaf elements (an element with its own text, a button, a progress bar) whose
 * vertical centres are within 4 px. Sizes are read from `getBoundingClientRect`. What this does NOT see: the frozen
 * themes, phone viewports, the wording of any line (the mount tests hold those by `data-testid`), a window over a canvas
 * that draws nothing under it (then (з) holds vacuously - its counter-check asserts the canvas does draw there).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'

import { mockApp, ready, settleShell } from './helpers/p037Mock.js'

const THEMES = ['saas', 'hud'] as const
type Theme = (typeof THEMES)[number]

const CARD = '.ws-shell[data-win-type="node-card"]'
const EDGE = '.ws-shell[data-win-type="edge-detail"]'

async function open(page: Page, theme: Theme, o: { aliceLines?: number } = {}): Promise<void> {
  await mockApp(page, { paymentRealBodies: [], extraTargets: o.aliceLines ?? 0, aliceLines: o.aliceLines })
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}`)
  await ready(page, true)
  await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
  await page.waitForFunction(() => Boolean((window as unknown as { __geoSim?: unknown }).__geoSim))
}

async function openCard(page: Page, nodeId = 'alice'): Promise<Locator> {
  await page.evaluate((nodeId) => (window as unknown as { __geoSim: { openNodeCard: (o: unknown) => void } }).__geoSim
    .openNodeCard({ nodeId, anchor: { x: 500, y: 300 } }), nodeId)
  const card = page.locator(CARD)
  await expect(card.locator('.ds-ov-node-card')).toBeVisible()
  // The lines arrive from the mocked backend after the card opens.
  await expect(card.locator('.nco-trustline-row').first()).toBeVisible()
  // The window manager re-clamps a shell that grew a few frames later: measure the card where it stays, not in transit.
  await settleShell(page, 'node-card-stats')
  return card
}

/** The line Bob -> Alice, through the DOM graph navigator (as `p037-c-edge-entry`): the window then holds the line's figures. */
async function openLine(page: Page): Promise<Locator> {
  await page.locator('[data-testid="details-toggle"]').click()
  // 046 wave 1, full (2026-10-19): first-screen controls by test id - their names are the dictionary's (language of the page).
  const nav = page.getByTestId('graph-navigator')
  await nav.getByTestId('graph-navigator-summary').click()
  await nav.getByTestId('graph-navigator-open-edge').click()
  const edge = page.locator(EDGE)
  await expect(edge.locator('[data-testid="edge-detail-popup"]')).toBeVisible()
  await page.locator('[data-testid="details-toggle"]').click()
  await page.evaluate(() => { (document.querySelector('.root') as HTMLElement).scrollTop = 0 })
  await settleShell(page, 'edge-detail-popup')
  return edge
}

type Rows = { count: number; rows: string[][] }

/** Visual rows of `root` (see the header). */
async function visualRows(root: Locator): Promise<Rows> {
  return await root.evaluate((el) => {
    const items: Array<{ c: number; t: string }> = []
    const scrollers = Array.from(el.querySelectorAll<HTMLElement>('*')).filter((n) => n.scrollHeight > n.clientHeight + 1 && getComputedStyle(n).overflowY !== 'visible')
    const clipped = (n: Element, r: DOMRect) => scrollers.some((s) => s.contains(n) && s !== n && (() => { const b = s.getBoundingClientRect(); return r.bottom <= b.top + 1 || r.top >= b.bottom - 1 })())
    for (const n of Array.from(el.querySelectorAll<HTMLElement>('*'))) {
      const own = Array.from(n.childNodes).some((c) => c.nodeType === 3 && (c.textContent ?? '').trim() !== '')
      if (!own && !n.matches('button, [role="progressbar"]')) continue
      if (n.closest('button') && n.tagName !== 'BUTTON') continue
      if (n.closest('details:not([open])') && !n.closest('summary')) continue
      const r = n.getBoundingClientRect()
      const s = getComputedStyle(n)
      if (r.width <= 1 || r.height <= 1 || s.visibility === 'hidden' || s.display === 'none' || clipped(n, r)) continue
      items.push({ c: r.top + r.height / 2, t: n.matches('[role="progressbar"]') ? '[bar]' : (n.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 24) })
    }
    items.sort((a, b) => a.c - b.c)
    const rows: Array<{ c: number; t: string[] }> = []
    for (const it of items) {
      const last = rows[rows.length - 1]
      if (last && Math.abs(it.c - last.c) <= 4) last.t.push(it.t)
      else rows.push({ c: it.c, t: [it.t] })
    }
    return { count: rows.length, rows: rows.map((r) => r.t) }
  })
}

const centreY = async (l: Locator) => { const b = (await l.boundingBox())!; return b.y + b.height / 2 }
const size = async (l: Locator) => { const b = (await l.boundingBox())!; return { w: Math.round(b.width * 10) / 10, h: Math.round(b.height * 10) / 10 } }

for (const theme of THEMES) {
  test.describe(`045 S3 [${theme}] inspectors at 1366x768`, () => {
    test('record: card and line window sizes and visual rows (numbers only)', async ({ page }, info) => {
      await open(page, theme)
      const card = await openCard(page)
      const rec: Record<string, unknown> = { card: await size(card), cardInner: await size(card.locator('.ds-ov-node-card')), cardRows: await visualRows(card) }
      await page.keyboard.press('Escape')
      await expect(card).toBeHidden()
      const edge = await openLine(page)
      rec.edge = await size(edge)
      rec.edgeInner = await size(edge.locator('[data-testid="edge-detail-popup"]'))
      rec.edgeRows = await visualRows(edge)
      console.log(`P045S3 record ${theme} ${JSON.stringify(rec)}`)
      info.annotations.push({ type: 'P045S3-record', description: JSON.stringify(rec) })
    })

    test('record: card with nine lines (numbers only)', async ({ page }, info) => {
      await open(page, theme, { aliceLines: 8 })
      const card = await openCard(page)
      await expect(card.locator('.nco-trustline-row')).toHaveCount(9)
      const rec = { card: await size(card), rows: await visualRows(card) }
      console.log(`P045S3 record9 ${theme} ${JSON.stringify(rec)}`)
      info.annotations.push({ type: 'P045S3-record9', description: JSON.stringify(rec) })
    })

    test('(и) 045 S4: neither inspector positions itself - the card and the line window are static blocks inside their shell', async ({ page }) => {
      await open(page, theme)
      const card = await openCard(page)
      const position = (l: Locator) => l.evaluate((el) => getComputedStyle(el).position)
      expect(await position(card.locator('.ds-ov-node-card')), 'the card is a static block').toBe('static')
      expect(await position(card), 'counter-check: the shell is what positions (it is not static)').not.toBe('static')
      await page.keyboard.press('Escape')
      await expect(card).toBeHidden()
      const edge = await openLine(page)
      expect(await position(edge.locator('[data-testid="edge-detail-popup"]')), 'the line window is a static block').toBe('static')
    })

    test('(е1)(е2) card: the head is one line, the statistics are one line', async ({ page }, info) => {
      await open(page, theme)
      const card = await openCard(page)
      const head = {
        name: await centreY(card.getByText('Alice', { exact: true }).first()),
        role: await centreY(card.locator('[data-testid="node-card-role"]')),
        balance: await centreY(card.locator('.ds-ov-node-card__balance')),
        close: await centreY(card.getByRole('button', { name: 'Close' })),
      }
      const stats = await Promise.all(['Out', 'In'].map(async (t) => centreY(card.locator('.ds-label', { hasText: new RegExp(`^${t}$`) }))))
      const values = await card.locator('.ds-ov-node-card__stats .ds-value').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return r.top + r.height / 2 }))
      info.annotations.push({ type: 'P045S3-e1e2', description: JSON.stringify({ head, stats, values }) })
      const spread = (xs: number[]) => Math.max(...xs) - Math.min(...xs)
      // 045 S3 round (Codex P2, 2026-10-18): the head is one block - name and role on a line, the meta line (type · status)
      // under the name; balance and close share a line, beside the name (within the block, not a row of their own).
      expect(spread([head.name, head.role]), `name and role are one line: ${JSON.stringify(head)}`).toBeLessThan(4)
      expect(spread([head.balance, head.close]), `balance and close are one line: ${JSON.stringify(head)}`).toBeLessThan(4)
      expect(Math.abs(head.balance - head.name), `balance is beside the name: ${JSON.stringify(head)}`).toBeLessThan(12)
      expect(spread([...stats, ...values]), `the statistics are one line: ${JSON.stringify({ stats, values })}`).toBeLessThan(4)
    })

    test('(е3) card: a row of a line has no third column; what is available is in its title', async ({ page }) => {
      await open(page, theme)
      const card = await openCard(page)
      const row = card.locator('.nco-trustline-row').first()
      await expect(row).toHaveAttribute('title', /avail: 100\.00/)
      const texts = await row.evaluate((el) => Array.from(el.children).filter((c) => !c.matches('button') && c.getBoundingClientRect().width > 1).map((c) => (c.textContent ?? '').replace(/\s+/g, ' ').trim()))
      expect(texts, 'peer and used / limit only').toEqual(['Bob', '0.00 / 100.00'])
      expect((await row.boundingBox())!.height, 'a row is 24 px (the smallest pointer target)').toBeLessThanOrEqual(24.5)
    })

    test('(е4) card: with nine lines at most five rows are on screen and the list scrolls', async ({ page }, info) => {
      await open(page, theme, { aliceLines: 8 })
      const card = await openCard(page)
      await expect(card.locator('.nco-trustline-row')).toHaveCount(9)
      const m = await card.locator('.nco-trustline-row').first().evaluate((first) => {
        const list = first.parentElement!
        const b = list.getBoundingClientRect()
        const rows = Array.from(list.querySelectorAll('.nco-trustline-row')).map((r) => r.getBoundingClientRect())
        return { scrolls: list.scrollHeight > list.clientHeight + 1, visible: rows.filter((r) => r.top >= b.top - 0.5 && r.bottom <= b.bottom + 0.5).length, listH: Math.round(b.height) }
      })
      info.annotations.push({ type: 'P045S3-e4', description: JSON.stringify(m) })
      expect(m.scrolls, 'the list scrolls').toBe(true)
      expect(m.visible, 'at most five whole rows on screen').toBeLessThanOrEqual(5)
      expect(m.visible, 'counter-check: rows are on screen at all').toBeGreaterThanOrEqual(4)
    })

    test('(к) 045 S4: with nine lines the totals (snapshot) and the rows (list) describe the same lines; the stats line is Out and In', async ({ page }) => {
      await open(page, theme, { aliceLines: 8 })
      const card = await openCard(page)
      await expect(card.locator('.nco-trustline-row')).toHaveCount(9)
      const stats = card.locator('[data-testid="node-card-stats"]')
      await expect(stats.locator('.ds-label'), 'the statistics say Out and In, and nothing else').toHaveText(['Out', 'In'])
      // Alice is the creditor of eight lines of 50.00 (Out) and the debtor of one line of 100.00 (In).
      await expect(stats.locator('.ds-value')).toHaveText(['400.00', '100.00'])
      await expect(card.getByText('OUT → 8', { exact: true })).toBeVisible()
      await expect(card.getByText('IN ← 1', { exact: true })).toBeVisible()
    })

    test('(е5) 043-4: while the payment is open, Pay and New line are disabled and the card says why', async ({ page }) => {
      await open(page, theme)
      const card = await openCard(page)
      const pay = card.locator('[data-testid="node-card-send-payment"]')
      const line = card.locator('[data-testid="node-card-new-trustline"]')
      await expect(pay, 'counter-check: with nothing open Pay is enabled').toBeEnabled()
      await expect(card.locator('[data-testid="node-card-actions-reason"]')).toHaveCount(0)
      await page.locator('[data-testid="actionbar-payment"]').click()
      await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
      await expect(pay).toBeDisabled()
      await expect(line).toBeDisabled()
      const reason = card.locator('[data-testid="node-card-actions-reason"]')
      await expect(reason, 'the reason is on a visible line').toBeVisible()
      await expect(pay).toHaveAttribute('title', (await reason.textContent())!.trim())
      // Back to idle: the buttons come back.
      await page.locator('[data-testid="manual-payment-cancel"]').click()
      await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeHidden()
      await expect(pay).toBeEnabled()
      await expect(reason).toHaveCount(0)
    })

    test('(е7) Codex P2: type and status are visible; focus on a line row shows what is available, with the unit', async ({ page }, info) => {
      await open(page, theme)
      const card = await openCard(page)
      await expect(card.locator('[data-testid="node-card-meta"]'), 'type · status is visible text').toBeVisible()
      await expect(card.locator('[data-testid="node-card-meta"]')).toHaveText('person · active')
      const avail = card.locator('[data-testid="node-card-line-available"]').first()
      await card.locator('.nco-trustline-row__edit').first().focus()
      await expect(avail, 'keyboard focus on the row shows the availability').toBeVisible()
      await expect(avail).toHaveText('available 100.00 UAH')
      const box = (await avail.boundingBox())!
      expect(box.width, 'shown, not a 1 px clip').toBeGreaterThan(20)
      info.annotations.push({ type: 'P045S3-e7', description: JSON.stringify({ card: (await card.boundingBox())!.height }) })
    })

    test('(е6) card: buttons carry no emoji; Escape from a focused button closes the card', async ({ page }) => {
      await open(page, theme)
      const card = await openCard(page)
      const labels = await card.locator('button').evaluateAll((bs) => bs.map((b) => (b.textContent ?? '').trim()))
      expect(labels.filter((t) => /\p{Extended_Pictographic}/u.test(t)), `emoji in ${JSON.stringify(labels)}`).toEqual([])
      await card.locator('[data-testid="node-card-send-payment"]').focus()
      await page.keyboard.press('Escape')
      await expect(card).toBeHidden()
    })

    test('(ж1)(ж2) line window: 320 wide, the figures one line, every button on one row; Escape closes it', async ({ page }, info) => {
      await open(page, theme)
      const edge = await openLine(page)
      const w = (await edge.boundingBox())!.width
      const buttons = await edge.locator('button').evaluateAll((bs) => bs.filter((b) => b.getBoundingClientRect().width > 0 && !b.closest('.popup__head')).map((b) => { const r = b.getBoundingClientRect(); return { t: (b.textContent ?? '').trim(), c: r.top + r.height / 2 } }))
      // Every piece of text of the figures line - labels, values, the status - not only the labels.
      const figures = await edge.locator('[data-testid="edge-stats"]').evaluate((el) => {
        const tops: number[] = []
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          if (!(n.textContent ?? '').trim()) continue
          const range = document.createRange()
          range.selectNodeContents(n)
          for (const r of Array.from(range.getClientRects())) tops.push(r.top + r.height / 2)
        }
        return tops
      })
      info.annotations.push({ type: 'P045S3-zh', description: JSON.stringify({ w, buttons, figures }) })
      expect(w, 'the window is the preferred width of its shell').toBeLessThanOrEqual(320.5)
      expect(figures.length, 'counter-check: the figures are found').toBeGreaterThanOrEqual(6)
      expect(Math.max(...figures) - Math.min(...figures), 'the figures are one line').toBeLessThan(4)
      const status = await centreY(edge.locator('[data-testid="edge-status"]'))
      expect(Math.abs(status - await centreY(edge.locator('[data-testid="edge-utilization-pct"]'))), 'the status ends the bar line').toBeLessThan(4)
      const cs = buttons.map((b) => b.c)
      expect(Math.max(...cs) - Math.min(...cs), `every button on one row: ${JSON.stringify(buttons)}`).toBeLessThan(4)
      expect(buttons.filter((b) => /\p{Extended_Pictographic}/u.test(b.t)), 'no emoji in the buttons').toEqual([])
      await edge.locator('[data-testid="edge-send-payment"]').focus()
      await page.keyboard.press('Escape')
      await expect(edge).toBeHidden()
    })

    test('(з) 045-2: a window hides what the canvas draws under it', async ({ page }, info) => {
      await open(page, theme)
      await page.locator('[data-testid="actionbar-payment"]').click()
      await page.locator('#mp-from__trigger').click()
      await page.locator('#mp-from__surface [role="option"][data-option-value="alice"]').click()
      await page.locator('#mp-to__surface [role="option"][data-option-value="bob"]').click()
      await page.locator('#mp-amount').fill('1.00')
      await settleShell(page)
      await page.locator('#mp-amount').blur()
      const r = await page.locator('[data-testid="manual-payment-panel"]').evaluate((el) => {
        const b = el.closest('.ws-shell')!.getBoundingClientRect()
        // Inset by the corner radius and the border: the window's own rounded corners are not what is judged.
        return { x: Math.ceil(b.left + 14), y: Math.ceil(b.top + 14), width: Math.floor(b.width - 28), height: Math.floor(b.height - 28) }
      })
      const canvas = page.locator('canvas').first()
      const shown = await page.screenshot({ clip: r, animations: 'disabled' })
      await canvas.evaluate((c) => { (c as HTMLElement).style.visibility = 'hidden' })
      const hidden = await page.screenshot({ clip: r, animations: 'disabled' })
      const bare = await page.locator('.ws-shell').first().evaluate((s) => { (s as HTMLElement).style.visibility = 'hidden'; return true })
      // Counter-check: the canvas draws something in that rectangle at all (with the window out of the way).
      await canvas.evaluate((c) => { (c as HTMLElement).style.visibility = 'visible' })
      const canvasOn = await page.screenshot({ clip: r, animations: 'disabled' })
      await canvas.evaluate((c) => { (c as HTMLElement).style.visibility = 'hidden' })
      const canvasOff = await page.screenshot({ clip: r, animations: 'disabled' })
      info.annotations.push({ type: 'P045S3-z', description: JSON.stringify({ clip: r, bare }) })
      expect(canvasOn.equals(canvasOff), 'counter-check: the canvas draws under the window').toBe(false)
      expect(shown.equals(hidden), 'the window looks the same with the canvas shown and hidden').toBe(true)
    })
  })
}
