import { chromium } from '@playwright/test'

// Quick live-ish check for Single Tx in the running dev server.
// We override navigator.webdriver to avoid the app treating this as test automation.

const baseURL = process.env.GEO_SIM_URL ?? 'http://127.0.0.1:5176/'

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext()

await context.addInitScript(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', {
      get: () => undefined,
      configurable: true,
    })
  } catch {
    // ignore
  }
})

const page = await context.newPage()

page.on('pageerror', (err) => {
  // eslint-disable-next-line no-console
  console.log('[pageerror]', err?.stack || String(err))
})

page.on('console', (msg) => {
  // Useful when debugging locally.
  // eslint-disable-next-line no-console
  console.log('[browser]', msg.type(), msg.text())
})

await page.goto(baseURL, { waitUntil: 'domcontentloaded' })

// Wait until our dev hook is present.
await page.waitForFunction(() => typeof (window).__geoSim === 'object', null, { timeout: 10_000 })

// Wait for fixtures to load.
await page.waitForFunction(() => {
  const g = (window).__geoSim
  return g && g.loading === false && g.hasSnapshot === true && String(g.error ?? '') === ''
}, null, { timeout: 20_000 })

const before = await page.evaluate(() => {
  const g = (window).__geoSim
  return {
    isTestMode: !!g.isTestMode,
    isWebDriver: !!g.isWebDriver,
    loading: !!g.loading,
    hasSnapshot: !!g.hasSnapshot,
    error: String(g.error ?? ''),
    sparks: g.fxState?.sparks?.length ?? -1,
    nodeBursts: g.fxState?.nodeBursts?.length ?? -1,
    edgePulses: g.fxState?.edgePulses?.length ?? -1,
  }
})

await page.getByRole('button', { name: 'Single Tx' }).click()

// Give the app a couple frames to spawn FX.
await page.waitForTimeout(150)

const after = await page.evaluate(() => {
  const g = (window).__geoSim
  return {
    loading: !!g.loading,
    hasSnapshot: !!g.hasSnapshot,
    error: String(g.error ?? ''),
    sparks: g.fxState?.sparks?.length ?? -1,
    nodeBursts: g.fxState?.nodeBursts?.length ?? -1,
    edgePulses: g.fxState?.edgePulses?.length ?? -1,
  }
})

await browser.close()

if (before.isWebDriver) {
  throw new Error(`Expected webdriver override to work, but isWebDriver=true. before=${JSON.stringify(before)}`)
}

if (after.sparks <= before.sparks) {
  throw new Error(`Single Tx did not spawn sparks. before=${JSON.stringify(before)} after=${JSON.stringify(after)}`)
}

// eslint-disable-next-line no-console
console.log('OK: Single Tx spawns FX', { before, after, baseURL })
