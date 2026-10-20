/**
 * 049 S1 (T4910), decision 3 and 9 - reproducer (в) of the Verification plan: the head of the participant card in two
 * rows, at its 320 px, in both product themes and both languages at 1366x768, on the mocked backend of the 037 specs
 * (`helpers/p037Mock.ts`) whose preview is replaced here by one with long names and an absent balance:
 *
 *   row 1 - the name (cut by an ellipsis, the whole name its `title`) and the two buttons (pin, close);
 *   row 2 - «человек · активен», the role (or the balance state), the balance with its unit - nothing of it cut;
 *   a participant whose balance did not arrive says ONE thing, «Баланс не получен» / «Balance unavailable», in place of
 *   «неизвестно» and «— UAH» (owner frame 18, 2026-10-20: «Andrii Boiko (Fishi…» «(?) неи…» «—» «UAH»); never a zero.
 *
 * Geometry is the browser's (boxes of the settled window shell), not a mount: happy-dom lays nothing out.
 * NOT seen here: phone widths (spec 049 Non-goals); the words themselves (the parity guard of 046, a human).
 */
import { expect, test, type Locator, type Page } from '@playwright/test'

import { RUN_ID, mockApp, ready, settleShell, snapshot } from './helpers/p037Mock.js'
import { t } from '../src/i18n/t'

const THEMES = ['saas', 'hud'] as const
const LANGS = ['en', 'ru'] as const
type Lang = (typeof LANGS)[number]

const CARD = '.ws-shell[data-win-type="node-card"]'
/** The spec's name (reproducer (в)), and one no 320 px card can hold. */
const ANDRII = 'Andrii Boiko (Fishing cooperative)'
const LONG = 'Carol Montgomery-Vasylenko (Lower Dnipro Fishing and Bakery Cooperative)'

/** The 037 scene (preview and run snapshot) with Alice renamed and WITHOUT a balance (no `net_*`), and Carol renamed with a seven-digit balance. */
function preview() {
  const s = snapshot()
  s.nodes = s.nodes.map((n) => {
    if (n.id === 'alice') {
      const { net_balance_atoms: _a, net_sign: _s, net_balance: _b, ...rest } = n
      return { ...rest, name: ANDRII } as typeof n
    }
    if (n.id === 'carol') return { ...n, name: LONG, net_sign: 1, net_balance_atoms: '104974590', net_balance: '1049745.90' }
    return n
  })
  return s
}

async function open(page: Page, theme: string, lang: Lang): Promise<void> {
  await mockApp(page, { paymentRealBodies: [] })
  const body = { status: 200, contentType: 'application/json', body: JSON.stringify(preview()) }
  await page.route(/\/simulator\/scenarios\/[^/]+\/graph\/preview/i, (r) => r.fulfill(body))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/graph/snapshot`, 'i'), (r) => r.fulfill(body))
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}&lang=${lang}`)
  await ready(page, true)
  await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
  await page.waitForFunction(() => Boolean((window as unknown as { __geoSim?: unknown }).__geoSim))
}

async function openCard(page: Page, nodeId: string): Promise<Locator> {
  await page.evaluate((id) => (window as unknown as { __geoSim: { openNodeCard: (o: unknown) => void } }).__geoSim
    .openNodeCard({ nodeId: id, anchor: { x: 500, y: 300 } }), nodeId)
  const card = page.locator(CARD)
  await expect(card.locator('.ds-ov-node-card')).toBeVisible()
  await settleShell(page, 'node-card-stats')
  return card
}

/** The head as measured: the boxes of its parts, and for each part of row 2 whether its text is cut (scrollWidth > clientWidth). */
const head = (card: Locator) => card.evaluate((root) => {
  const box = (el: Element | null) => {
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, cy: r.top + r.height / 2, h: r.height }
  }
  const name = root.querySelector<HTMLElement>('[data-testid="node-card-name"]')!
  const close = root.querySelector('.ds-ov-node-card__service button:last-child')
  const facts = [...root.querySelectorAll<HTMLElement>('[data-testid="node-card-meta"], [data-testid="node-card-role"], .ds-ov-node-card__money')]
  // Every text-bearing element of row 2, its own text cut or not (an inner span cut by its parent counts as cut).
  const cut = facts.flatMap((f) => [f, ...f.querySelectorAll<HTMLElement>('*')])
    .filter((el) => el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0)
    .map((el) => `${el.className}: ${(el.textContent ?? '').trim()}`)
  const cardBox = root.querySelector('.ds-ov-node-card')!.getBoundingClientRect()
  const outside = facts.filter((f) => { const r = f.getBoundingClientRect(); return r.left < cardBox.left - 0.5 || r.right > cardBox.right + 0.5 }).map((f) => f.className)
  const identity = root.querySelector('.ds-ov-node-card__identity')!
  return {
    name: box(name), nameTitle: name.getAttribute('title'), nameCut: name.scrollWidth > name.clientWidth + 1, close: box(close),
    facts: facts.map((f) => ({ cls: f.className.split(' ')[0], ...box(f)! })), cut, outside,
    headText: (identity.textContent ?? '').replace(/\s+/g, ' ').trim(),
    balance: root.querySelectorAll('.ds-ov-node-card__identity .ds-ov-node-card__balance').length,
    money: (root.querySelector('.ds-ov-node-card__money')?.textContent ?? '').replace(/\s+/g, ' ').trim(),
    amount: box(root.querySelector('.ds-ov-node-card__money .ds-ov-node-card__balance')),
    unit: box(root.querySelector('.ds-ov-node-card__money .ds-ov-node-card__unit')),
  }
})

const spread = (xs: number[]) => Math.max(...xs) - Math.min(...xs)

for (const theme of THEMES) {
  for (const lang of LANGS) {
    test.describe(`049 S1 (в) [${theme}] [${lang}] the participant card head at 1366x768`, () => {
      test('a balance that did not arrive: two rows, nothing cut in row 2, «Balance unavailable» once, no «—» and no unit', async ({ page }, info) => {
        await open(page, theme, lang)
        const card = await openCard(page, 'alice')
        const h = await head(card)
        info.annotations.push({ type: 'P049S1-card-unknown', description: JSON.stringify(h) })
        expect(h.nameTitle, 'the whole name is the title').toBe(ANDRII)
        // Row 1: the name and the close button share a line.
        expect(Math.abs(h.name!.cy - h.close!.cy), `name and close are one row: ${JSON.stringify(h)}`).toBeLessThan(4)
        // Row 2: below the name, on one line, nothing cut, inside the card.
        expect(h.facts.length, 'counter-check: the meta and the balance state are measured').toBeGreaterThanOrEqual(2)
        for (const f of h.facts) expect(f.top, `${f.cls} is under the name: ${JSON.stringify(h)}`).toBeGreaterThanOrEqual(h.name!.bottom - 0.5)
        expect(spread(h.facts.map((f) => f.cy)), `row 2 is one line: ${JSON.stringify(h.facts)}`).toBeLessThan(4)
        expect(h.cut, 'nothing in row 2 is cut').toEqual([])
        expect(h.outside, 'nothing in row 2 sticks out of the card').toEqual([])
        // One message for the absent balance.
        const unavailable = t('card.balanceUnavailable', {}, lang)
        await expect(card.locator('.ds-ov-node-card__identity').getByText(unavailable, { exact: true }), 'the absent balance says so once').toHaveCount(1)
        expect(h.balance, 'no balance figure in the head').toBe(0)
        expect(h.headText, 'no dash for the balance').not.toContain('—')
        expect(h.headText, 'no unit without an amount').not.toContain('UAH')
        expect(h.headText, 'not the legend\'s «unknown» beside it').not.toContain(t('legend.entry.unknown', {}, lang))
        expect(h.headText, 'never a zero for it').not.toMatch(/\b0\.00\b/)
      })

      test('a long name and a seven-digit balance: the name cut with its title, the balance and its unit one unbroken group in row 2', async ({ page }, info) => {
        await open(page, theme, lang)
        const card = await openCard(page, 'carol')
        const h = await head(card)
        info.annotations.push({ type: 'P049S1-card-long', description: JSON.stringify(h) })
        expect(h.nameCut, `counter-check: this name does not fit the card - the ellipsis is exercised: ${JSON.stringify(h.name)}`).toBe(true)
        expect(h.nameTitle, 'the whole name is the title').toBe(LONG)
        expect(Math.abs(h.name!.cy - h.close!.cy), `name and close are one row: ${JSON.stringify(h)}`).toBeLessThan(4)
        for (const f of h.facts) expect(f.top, `${f.cls} is under the name: ${JSON.stringify(h)}`).toBeGreaterThanOrEqual(h.name!.bottom - 0.5)
        expect(h.cut, 'nothing in row 2 is cut').toEqual([])
        expect(h.outside, 'nothing in row 2 sticks out of the card').toEqual([])
        expect(h.money, 'the amount with its unit').toBe('1049745.90 UAH')
        // One line: the unit follows the amount on the amount's line (its box within the amount's), not under it.
        expect(h.unit!.left, `the unit follows the amount: ${JSON.stringify([h.amount, h.unit])}`).toBeGreaterThanOrEqual(h.amount!.right - 0.5)
        expect(h.unit!.top, `the unit is on the amount's line: ${JSON.stringify([h.amount, h.unit])}`).toBeGreaterThanOrEqual(h.amount!.top - 0.5)
        expect(h.unit!.bottom, `the unit is on the amount's line: ${JSON.stringify([h.amount, h.unit])}`).toBeLessThanOrEqual(h.amount!.bottom + 0.5)
      })
    })
  }
}
