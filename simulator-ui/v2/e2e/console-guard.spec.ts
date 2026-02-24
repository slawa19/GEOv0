import { expect, test } from '@playwright/test'

function attachErrorGuards(page: any) {
  const errors: string[] = []

  page.on('pageerror', (err: any) => {
    const msg = String(err?.message ?? err)
    errors.push(`pageerror: ${msg}`)
  })

  page.on('console', (msg: any) => {
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
async function waitReady(page: any) {
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
