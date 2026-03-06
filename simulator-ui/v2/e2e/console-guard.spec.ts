import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'

function attachErrorGuards(page: Page) {
  const errors: string[] = []

  page.on('pageerror', (err: Error) => {
    const msg = String(err?.message ?? err)
    errors.push(`pageerror: ${msg}`)
  })

  page.on('console', (msg: ConsoleMessage) => {
    try {
      if (msg.type && msg.type() === 'error') {
        errors.push(`console.error: ${msg.text?.() ?? String(msg)}`)
      }
    } catch {
      // ignore
    }
  })

  return {
    getErrors: () => errors.slice(),
  }
}

/** Wait until the app signals data-ready (same convention as screenshot tests). */
async function waitReady(page: Page) {
  await page.waitForSelector('[data-ready="1"]', { timeout: 20_000 })
  await page.waitForTimeout(200)
}

test('first load has no pageerror / console.error', async ({ page }) => {
  const guard = attachErrorGuards(page)

  await page.goto('/')
  await waitReady(page)

  const errs = guard.getErrors()
  expect(errs, errs.join('\n')).toEqual([])
})
