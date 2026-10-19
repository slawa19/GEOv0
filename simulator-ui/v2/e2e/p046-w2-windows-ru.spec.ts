/**
 * Programme 046, wave 2 (T4603) - the windows of the actions in Russian, measured (spec 046 decision 6, Verification
 * plan 1 (б) and (д)).
 *
 * At 1366x768, in both product themes (`saas`, `hud`), in both languages (`?lang=en`, `?lang=ru`), on the mocked backend
 * of the 037 specs (`helpers/p037Mock.ts`), the payment window (choice and confirm step), the trust line window (create
 * and edit step) and the clearing window are opened and measured in the DOM:
 *
 *   - the 72 px label rail (045): every label fits its rail - not wider than 72 px, nothing cut (`scrollWidth`);
 *   - the row of buttons: one row, Cancel first, every button inside the panel, no button text cut;
 *   - the information line of the payment confirm step and the figures line of the trust line: inside the panel;
 *   - the window does not grow past the 045 target (the choice step at most 200 px tall), and in Russian it is neither
 *     wider nor taller than in English;
 *   - (б) in Russian the window shows no Latin word but data (names of the mock: Alice, Bob, Carol; the code UAH; the key
 *     name Esc).
 * Every test logs what it measured (`P046W2 ...`), the before/after record of the 046 Changelog.
 *
 * `P046_FRAMES=1` also writes the owner's frames (`.local-run/owner-review/2026-10-18-046-w2/`); the default run writes none.
 *
 * What this does NOT see: phone viewports (037 B1 holds those), the frozen themes, the result steps, whether a
 * translation reads well (the owner's frames decide that).
 */
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

import { t } from '../src/i18n/t'
import { mockApp, ready, RUN_ID, settleShell } from './helpers/p037Mock.js'

const THEMES = ['saas', 'hud'] as const
const LANGS = ['en', 'ru'] as const
type Theme = (typeof THEMES)[number]
type Lang = (typeof LANGS)[number]

// Compared without case: the hud theme sets labels and names in capitals (`innerText` reads them as shown).
const DATA_WORDS = new Set(['alice', 'bob', 'carol', 'uah', 'esc'])
/**
 * Labels allowed to take two lines of the 72 px rail, each by a decision: «Кто доверяет» - the advisor's binding wording
 * of 2026-10-18 (round 1) for the trust line's "From"; on one line it needs about 85 px, the rail is 72 (045 token). Its
 * row is as tall as the control beside it, so the window does not grow (measured below: Russian not taller than English).
 */
const TWO_LINE_LABELS: ReadonlySet<string> = new Set(['Кто доверяет'])
const FRAMES = process.env.P046_FRAMES === '1'
const FRAMES_DIR = fileURLToPath(new URL('../../../.local-run/owner-review/2026-10-18-046-w2/', import.meta.url))

async function open(page: Page, theme: Theme, lang: Lang, opts: { running?: boolean; debug?: boolean } = {}): Promise<void> {
  // Two more lines alice -> x01 (no debt), alice -> x02 (debt 1.00): the edit row of a line WITH debt is measured too.
  await mockApp(page, { paymentRealBodies: [], extraTargets: 2, aliceLines: 2 })
  if (opts.running) await mockAnalytics(page)
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}&lang=${lang}${opts.debug ? '&debug=1' : ''}`)
  await ready(page, true)
  await expect(page.locator('html')).toHaveAttribute('lang', lang)
}

/** A running run with measurements and bottlenecks of every reason (frames only; registered after `mockApp`). */
async function mockAnalytics(page: Page): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route(`**/simulator/runs/${RUN_ID}`, (r) => r.fulfill(json({ api_version: 'simulator-api/1', run_id: RUN_ID,
    scenario_id: 'greenfield-village-100-realistic-v2', mode: 'real', state: 'running', sim_time_ms: 0, intensity_percent: 30, ops_sec: 1, queue_depth: 0 })))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/metrics`, 'i'), (r) => r.fulfill(json({ api_version: 'simulator-api/1', run_id: RUN_ID,
    equivalent: 'UAH', from_ms: 0, to_ms: 2000, step_ms: 1000, series: [
      { key: 'success_rate', unit: '%', points: [{ t_ms: 0, v: '90' }, { t_ms: 1000, v: null }, { t_ms: 2000, v: '95' }] },
      { key: 'clearing_volume', unit: 'amount', points: [{ t_ms: 0, v: '1.00' }, { t_ms: 2000, v: '7.00' }] },
      { key: 'avg_route_length', unit: 'count', points: [{ t_ms: 0, v: '2' }, { t_ms: 2000, v: '3' }] },
      { key: 'total_debt', unit: 'amount', points: [{ t_ms: 0, v: '100.00' }, { t_ms: 2000, v: '120.00' }] },
    ] })))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/bottlenecks`, 'i'), (r) => r.fulfill(json({ api_version: 'simulator-api/1', run_id: RUN_ID,
    equivalent: 'UAH', items: ['FREQUENT_ABORTS', 'HIGH_USED', 'TOO_MANY_TIMEOUTS'].map((reason_code, i) => ({
      target: { kind: 'edge', from: 'bob', to: 'alice' }, score: 0.8 - 0.25 * i, reason_code, label: 'Server label', suggested_action: 'Server advice' })) })))
}

type Box = { w: number; h: number }

/** Labels, buttons and lines of one window, as they are on screen. */
async function measure(page: Page, panelTestId: string, lines: string[] = []) {
  return await page.locator(`[data-testid="${panelTestId}"]`).evaluate((panel, lineIds) => {
    const r = (n: number) => Math.round(n * 10) / 10
    const pr = panel.getBoundingClientRect()
    const shell = (panel.closest('.ws-shell') ?? panel).getBoundingClientRect()
    const labels = Array.from(panel.querySelectorAll('.ds-controls__row > .ds-label')).map((el) => {
      const b = el.getBoundingClientRect()
      const lh = Number.parseFloat(getComputedStyle(el).lineHeight) || 1
      return { text: (el.textContent ?? '').replace(/\s+/g, ' ').trim(), w: r(b.width), lines: Math.round(b.height / lh), cut: el.scrollWidth > el.clientWidth + 0.5 }
    })
    const row = panel.querySelector('.ds-panel__body > .ds-row--actions') ?? panel.querySelector('.ds-row--actions')
    const buttons = Array.from(row?.querySelectorAll('button') ?? []).filter((b) => b.getBoundingClientRect().width > 0).map((b) => {
      const x = b.getBoundingClientRect()
      return { text: (b.textContent ?? '').trim(), left: r(x.left), right: r(x.right), top: r(x.top), w: r(x.width), cut: b.scrollWidth > b.clientWidth + 0.5 }
    })
    const lineFigures = (lineIds as string[]).map((id) => {
      const el = panel.querySelector(`[data-testid="${id}"]`)
      if (!el) return { id, missing: true }
      const b = el.getBoundingClientRect()
      const lh = Number.parseFloat(getComputedStyle(el).lineHeight) || 1
      return { id, w: r(b.width), h: r(b.height), lines: Math.round(b.height / lh), right: r(b.right), over: el.scrollWidth > el.clientWidth + 0.5 }
    })
    const body = panel.querySelector('.ds-panel__body') as HTMLElement | null
    return {
      shell: { w: r(shell.width), h: r(shell.height) } as { w: number; h: number },
      panelRight: r(pr.right),
      bodyOverflowX: body ? body.scrollWidth > body.clientWidth + 0.5 : false,
      labels, buttons, lines: lineFigures,
    }
  }, lines)
}

type Figures = Awaited<ReturnType<typeof measure>>

/** The text a keyboard or screen-reader user meets in the window: visible text, accessible names, titles, placeholders. */
async function latinWords(page: Page, panelTestId: string): Promise<string[]> {
  const text = await page.locator(`[data-testid="${panelTestId}"]`).evaluate((panel) => {
    const shell = panel.closest('.ws-shell') ?? panel
    const parts = [(shell as HTMLElement).innerText ?? '']
    for (const el of Array.from(shell.querySelectorAll('*'))) {
      for (const a of ['aria-label', 'title', 'placeholder']) {
        const v = el.getAttribute(a)
        if (v) parts.push(v)
      }
    }
    return parts.join(' \n ')
  })
  return (text.match(/[A-Za-z_]+/g) ?? []).filter((w) => !DATA_WORDS.has(w.toLowerCase()))
}

function expectFits(f: Figures, where: string): void {
  for (const l of f.labels) {
    expect(l.w, `${where}: label "${l.text}" fits the 72 px rail`).toBeLessThanOrEqual(72.5)
    expect(l.cut, `${where}: label "${l.text}" is not cut`).toBe(false)
    expect(l.lines, `${where}: label "${l.text}" is one line`).toBe(TWO_LINE_LABELS.has(l.text) ? l.lines <= 2 ? l.lines : 1 : 1)
  }
  expect(f.buttons.length, `${where}: a row of buttons`).toBeGreaterThan(0)
  const top = f.buttons[0]!.top
  for (const b of f.buttons) {
    expect(Math.abs(b.top - top), `${where}: "${b.text}" is on the one row of buttons ${JSON.stringify(f.buttons)}`).toBeLessThan(1)
    expect(b.right, `${where}: "${b.text}" is inside the panel`).toBeLessThanOrEqual(f.panelRight + 0.5)
    expect(b.cut, `${where}: the text of "${b.text}" is not cut`).toBe(false)
  }
  for (const l of f.lines) {
    if ('missing' in l) throw new Error(`${where}: ${l.id} is not on screen`)
    expect(l.over, `${where}: ${l.id} does not overflow`).toBe(false)
    expect(l.right, `${where}: ${l.id} is inside the panel`).toBeLessThanOrEqual(f.panelRight + 0.5)
  }
  expect(f.bodyOverflowX, `${where}: no horizontal overflow of the body`).toBe(false)
}

async function frame(page: Page, name: string): Promise<void> {
  if (!FRAMES) return
  mkdirSync(FRAMES_DIR, { recursive: true })
  await page.screenshot({ path: `${FRAMES_DIR}${name}.png` })
}

async function paymentWindow(page: Page, theme: Theme, lang: Lang) {
  await page.locator('[data-testid="actionbar-payment"]').click()
  await expect(page.locator('#mp-from__trigger')).toBeVisible()
  await settleShell(page)
  const choice = await measure(page, 'manual-payment-panel')
  const choiceLatin = lang === 'ru' ? await latinWords(page, 'manual-payment-panel') : []
  await frame(page, `${theme}-${lang}-1-payment-choice`)
  await page.locator('#mp-from__trigger').click()
  await page.locator('#mp-from__surface [role="option"][data-option-value="alice"]').click()
  await page.locator('#mp-to__surface [role="option"][data-option-value="bob"]').click()
  // 046 round 1 (advisor A): a comma is refused - the reason line says how to type it, Confirm stays disabled.
  await page.locator('#mp-amount').fill('1,5')
  await expect(page.locator('[data-testid="manual-payment-confirm"]')).toBeDisabled()
  await expect(page.locator('[data-testid="mp-confirm-reason"]')).toHaveText(t('common.commaRefused', { example: '1.5' }, lang))
  await settleShell(page)
  const confirm = await measure(page, 'manual-payment-panel', ['mp-info', 'mp-confirm-reason'])
  const confirmLatin = lang === 'ru' ? await latinWords(page, 'manual-payment-panel') : []
  await frame(page, `${theme}-${lang}-2-payment-comma-refused`)
  await page.locator('#mp-amount').fill('1.5')
  await expect(page.locator('[data-testid="manual-payment-confirm"]')).toBeEnabled()
  await settleShell(page)
  await frame(page, `${theme}-${lang}-2b-payment-confirm`)
  await page.locator('[data-testid="manual-payment-cancel"]').click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeHidden()
  return { choice, confirm, latin: [...choiceLatin, ...confirmLatin] }
}

async function trustlineWindows(page: Page, theme: Theme, lang: Lang) {
  const pick = async (from: string, to: string) => {
    await page.locator('[data-testid="actionbar-trustline"]').click()
    await page.locator('#tl-from__trigger').click()
    await page.locator(`#tl-from__surface [role="option"][data-option-value="${from}"]`).click()
    await page.locator('#tl-to__trigger').click()
    await page.locator(`#tl-to__surface [role="option"][data-option-value="${to}"]`).click()
    await settleShell(page, 'trustline-panel')
  }
  await pick('alice', 'carol')
  await expect(page.locator('#tl-limit')).toBeVisible()
  await page.locator('#tl-limit').fill('20')
  await settleShell(page, 'trustline-panel')
  const create = await measure(page, 'trustline-panel')
  const createLatin = lang === 'ru' ? await latinWords(page, 'trustline-panel') : []
  await frame(page, `${theme}-${lang}-3-trustline-create`)
  await page.locator('[data-testid="trustline-cancel"]').click()
  await expect(page.locator('[data-testid="trustline-panel"]')).toBeHidden()

  await pick('bob', 'alice')
  await expect(page.locator('[data-testid="tl-stats"]')).toBeVisible()
  await settleShell(page, 'trustline-panel')
  const edit = await measure(page, 'trustline-panel', ['tl-stats'])
  const editLatin = lang === 'ru' ? await latinWords(page, 'trustline-panel') : []
  await frame(page, `${theme}-${lang}-4-trustline-edit`)
  await page.locator('[data-testid="trustline-cancel"]').click()
  await expect(page.locator('[data-testid="trustline-panel"]')).toBeHidden()

  // A line with debt: the close button asks for a close ("Request close" / «Запросить закрытие»).
  await pick('alice', 'x02')
  await expect(page.locator('[data-testid="tl-close-request-note"]')).toBeVisible()
  await settleShell(page, 'trustline-panel')
  const editDebt = await measure(page, 'trustline-panel', ['tl-stats'])
  const editDebtLatin = lang === 'ru' ? (await latinWords(page, 'trustline-panel')).filter((w) => !/^(x0\d|Extra)$/i.test(w)) : []
  await frame(page, `${theme}-${lang}-4b-trustline-edit-debt`)
  await page.locator('[data-testid="trustline-cancel"]').click()
  await expect(page.locator('[data-testid="trustline-panel"]')).toBeHidden()
  return { create, edit, editDebt, latin: [...createLatin, ...editLatin, ...editDebtLatin] }
}

async function clearingWindow(page: Page, theme: Theme, lang: Lang) {
  await page.locator('[data-testid="actionbar-clearing"]').click()
  await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
  await settleShell(page, 'clearing-panel')
  const confirm = await measure(page, 'clearing-panel')
  const latin = lang === 'ru' ? await latinWords(page, 'clearing-panel') : []
  await frame(page, `${theme}-${lang}-5-clearing`)
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="clearing-panel"]')).toBeHidden()
  return { confirm, latin }
}

for (const theme of THEMES) {
  test(`046 W2 [${theme}] the action windows fit in both languages; in Russian they show no English word`, async ({ page }, info) => {
    test.setTimeout(90_000)
    const rec: Record<string, unknown> = {}
    const shells: Record<Lang, Record<string, Box>> = { en: {}, ru: {} }
    for (const lang of LANGS) {
      await open(page, theme, lang)
      const pay = await paymentWindow(page, theme, lang)
      const tl = await trustlineWindows(page, theme, lang)
      const cl = await clearingWindow(page, theme, lang)
      rec[lang] = { paymentChoice: pay.choice, paymentConfirm: pay.confirm, trustlineCreate: tl.create, trustlineEdit: tl.edit,
        trustlineEditDebt: tl.editDebt, clearing: cl.confirm }
      console.log(`P046W2 ${theme} ${lang} ${JSON.stringify(rec[lang])}`)
      info.annotations.push({ type: `P046W2-${lang}`, description: JSON.stringify(rec[lang]) })

      for (const [where, f] of Object.entries(rec[lang] as Record<string, Figures>)) {
        expectFits(f, `${theme} ${lang} ${where}`)
        shells[lang][where] = f.shell
      }
      expect(pay.choice.shell.h, `${theme} ${lang}: the choice step is at most 200 px tall (045 S1)`).toBeLessThanOrEqual(200)
      if (lang === 'ru') expect([...pay.latin, ...tl.latin, ...cl.latin], `${theme} ru: no English word in the windows`).toEqual([])
    }
    for (const where of Object.keys(shells.en)) {
      expect(shells.ru[where]!.w, `${theme} ${where}: the Russian window is not wider than the English one`).toBeLessThanOrEqual(shells.en[where]!.w + 0.5)
      // 1 px: the two-line label «Кто доверяет» (TWO_LINE_LABELS) adds 0.7 px to the create step at 1366x768 (2026-10-18).
      expect(shells.ru[where]!.h, `${theme} ${where}: the Russian window is not taller than the English one`).toBeLessThanOrEqual(shells.en[where]!.h + 1)
    }
  })
}

// Codex §15 round 1 (P3): the Russian refusal of a payment was asserted in a browser only by the phase 5 matrix, which
// runs in English since wave 2. Here: ?lang=ru, the server refuses with its English message, the window says it in Russian.
test('046 W2 [saas] a payment refused by the server reads in Russian under ?lang=ru, with the figure of the answer', async ({ page }) => {
  await mockApp(page, { paymentRealBodies: [] })
  // Registered after `mockApp`: Playwright asks the newest route first.
  await page.route(`**/simulator/runs/${RUN_ID}/actions/payment-real`, (r) => r.fulfill({
    status: 409, contentType: 'application/json', headers: { 'X-Request-ID': 'req-ru-1' },
    body: JSON.stringify({ code: 'NO_ROUTE', message: 'No route found between the participants',
      details: { reason: 'no_route', max_available: '0', equivalent: 'UAH', idempotency_key_spent: true } }),
  }))
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto('/?mode=real&ui=interact&e2eReal=1&theme=saas&lang=ru')
  await ready(page, true)
  await page.locator('[data-testid="actionbar-payment"]').click()
  await page.locator('#mp-from__trigger').click()
  await page.locator('#mp-from__surface [role="option"][data-option-value="alice"]').click()
  await page.locator('#mp-to__surface [role="option"][data-option-value="bob"]').click()
  await page.locator('#mp-amount').fill('1.00')
  await page.locator('[data-testid="manual-payment-confirm"]').click()
  const expected = t('refusal.noRouteMax', { max: '0.00', eq: 'UAH' }, 'ru')
  expect(expected, 'the dictionary text is Russian').toMatch(/[Ѐ-ӿ]/)
  await expect(page.locator('[data-testid="manual-payment-error"]')).toContainText(expected)
  // 048 S3 (decision 3): the id is a line of its own and `data-request-id` - rewritten, not removed (was: glued into the sentence).
  await expect(page.locator('[data-testid="manual-payment-error"]')).toHaveAttribute('data-request-id', 'req-ru-1')
  await expect(page.locator('[data-testid="manual-payment-error"] [data-testid="error-request-id"]')).toHaveText(t('common.requestId', { id: 'req-ru-1' }, 'ru'))
  await expect(page.locator('[data-testid="manual-payment-error"] [data-testid="error-text"]')).not.toContainText('req-ru-1')
  await expect(page.locator('[data-testid="manual-payment-error"]')).not.toContainText('No route found')
  await frame(page, 'saas-ru-8-payment-refused')
})

test('046 W2 counter-check: the scanner and the rail check see an English word and a cut label', async ({ page }) => {
  await open(page, 'saas', 'en')
  await page.locator('[data-testid="actionbar-payment"]').click()
  await expect(page.locator('#mp-from__trigger')).toBeVisible()
  expect(await latinWords(page, 'manual-payment-panel'), 'English is seen (this is the en window)').toContain('Cancel')
  await page.locator('#mp-from-label').evaluate((el) => { el.textContent = 'Отправитель платежа' })
  const f = await measure(page, 'manual-payment-panel')
  expect(f.labels.some((l) => l.cut || l.w > 72.5 || l.lines > 1), `a long label is caught ${JSON.stringify(f.labels)}`).toBe(true)
})

test('046 W2 frames: the analytics panel and its bottlenecks, both themes and languages (P046_FRAMES=1 only)', async ({ page }) => {
  test.skip(!FRAMES, 'frames are written on request only (P046_FRAMES=1)')
  for (const theme of THEMES) {
    for (const lang of LANGS) {
      await page.unrouteAll({ behavior: 'ignoreErrors' })
      // 048 S2 (amendment 10): the analytics toggle is in the service row with `?debug=1` ("Admin ▾" is gone).
      await open(page, theme, lang, { running: true, debug: true })
      await page.locator('[data-testid="details-toggle"]').click()
      await page.locator('[data-testid="analytics-toggle"]').click()
      await expect(page.locator('.rmp')).toBeVisible()
      // Wait for the measurements and the bottlenecks to arrive (a frame of the loading state is still a frame).
      await expect(page.locator('.rmp .bnl__item').first()).toBeVisible({ timeout: 10_000 }).catch(() => undefined)
      await frame(page, `${theme}-${lang}-6-analytics`)
      await page.locator('.rmp__body').evaluate((el) => { el.scrollTop = el.scrollHeight })
      await frame(page, `${theme}-${lang}-7-bottlenecks`)
    }
  }
})

test('046 W2 frames: a failed analytics request and a clearing refused on a stopped equivalent (P046_FRAMES=1 only)', async ({ page }) => {
  test.skip(!FRAMES, 'frames are written on request only (P046_FRAMES=1)')
  for (const lang of LANGS) {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await open(page, 'saas', lang, { running: true, debug: true }) // 048 S2: the analytics toggle is `?debug=1`'s
    await page.route(new RegExp(`/simulator/runs/${RUN_ID}/(metrics|bottlenecks)`, 'i'), (r) => r.fulfill({
      status: 500, contentType: 'text/plain', headers: { 'X-Request-ID': 'req-500-a' }, body: 'Internal Server Error' }))
    await page.route(`**/simulator/runs/${RUN_ID}/actions/clearing-real`, (r) => r.fulfill({
      status: 409, contentType: 'application/json', headers: { 'X-Request-ID': 'req-409-c' },
      body: JSON.stringify({ code: 'CONFLICT', message: 'Equivalent UAH is not active', details: { reason: 'equivalent_inactive' } }) }))
    await page.locator('[data-testid="details-toggle"]').click()
    await page.locator('[data-testid="analytics-toggle"]').click()
    await expect(page.locator('.rmp [data-state="error"]').first()).toBeVisible({ timeout: 10_000 })
    await frame(page, `saas-${lang}-9-analytics-error`)
    await page.locator('[data-testid="analytics-toggle"]').click()
    await page.locator('[data-testid="actionbar-clearing"]').click()
    await page.locator('[data-testid="clearing-confirm"]').click()
    await expect(page.locator('[data-testid="clearing-error"]')).toContainText(t('clearing.equivalentInactive', {}, lang))
    await frame(page, `saas-${lang}-10-clearing-stopped-equivalent`)
  }
})

test('046 W2 frames: the result of a clearing, both themes and languages (P046_FRAMES=1 only)', async ({ page }) => {
  test.skip(!FRAMES, 'frames are written on request only (P046_FRAMES=1)')
  for (const theme of THEMES) {
    for (const lang of LANGS) {
      await page.unrouteAll({ behavior: 'ignoreErrors' })
      await open(page, theme, lang)
      await page.locator('[data-testid="actionbar-clearing"]').click()
      await page.locator('[data-testid="clearing-confirm"]').click()
      await expect(page.locator('[data-testid="clearing-edge-line"]').first()).toBeVisible()
      await settleShell(page, 'clearing-panel')
      await frame(page, `${theme}-${lang}-11-clearing-result`)
    }
  }
})

// 046 (advisor, 2026-10-18): the sum of a clearing result keeps its figure and unit on one line - in hud the label
// «Погашено циклов: 2 · Сумма по циклам: 10.00 UAH» wraps, and before the no-break space the unit went alone to the next line.
for (const theme of THEMES) {
  test(`046 W2 [${theme}] ru: the sum of a clearing result keeps "10.00 UAH" on one line`, async ({ page }, info) => {
    await open(page, theme, 'ru')
    await page.locator('[data-testid="actionbar-clearing"]').click()
    await page.locator('[data-testid="clearing-confirm"]').click()
    const amount = page.locator('[data-testid="clearing-total-amount"]')
    await expect(amount).toBeVisible()
    await settleShell(page, 'clearing-panel')
    const m = await amount.evaluate((el) => {
      const r = el.getBoundingClientRect()
      const label = el.closest('.ds-label')!.getBoundingClientRect()
      const lh = Number.parseFloat(getComputedStyle(el.closest('.ds-label')!).lineHeight) || 1
      return { boxes: el.getClientRects().length, w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
        labelLines: Math.round(label.height / lh), text: (el.textContent ?? '').replace(/\s+/g, ' ') }
    })
    console.log(`P046W2 clearing-sum ${theme} ${JSON.stringify(m)}`)
    info.annotations.push({ type: 'P046W2-clearing-sum', description: JSON.stringify(m) })
    expect(m.text).toBe('10.00 UAH')
    expect(m.boxes, `the figure and its unit are one line box ${JSON.stringify(m)}`).toBe(1)
  })
}
