/**
 * 043 S2 - the Simulator UI against a REAL, seeded backend (spec 043, Verification plan, reproducer (b)).
 *
 * The backend is not started here: the caller gives its origin in `GEO_SIM_LIVE_BACKEND_ORIGIN` (the required CI job
 * `ui-smoke` passes the backend it already runs for the Admin smoke: the `postgres:16` service, the seed
 * `riverside-town-50`, uvicorn). Without the variable this config REFUSES to load - a missing backend is a failed
 * step, not a skipped test. Playwright starts the Vite dev server, whose `/api/v1` proxy points at that origin.
 *
 * The product runs as a user's: no test mode, `navigator.webdriver` false (the product reads it as "screenshot
 * stand"), and NO `VITE_API_MODE` - what is proven is the default a person gets when opening the address.
 */
import { fileURLToPath } from 'node:url'

import { defineConfig } from '@playwright/test'

const origin = String(process.env.GEO_SIM_LIVE_BACKEND_ORIGIN ?? '').trim()
if (!/^https?:\/\/[^/]+$/.test(origin)) {
  throw new Error('043 S2 live test: GEO_SIM_LIVE_BACKEND_ORIGIN must be the origin of a running, seeded backend (e.g. http://127.0.0.1:18141); '
    + `got "${origin}". This test does not start a backend and does not skip without one.`)
}
const port = Number.parseInt(process.env.PW_E2E_PORT ?? '', 10) || 5198
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './live',
  testMatch: '**/*.live.ts',
  timeout: 120_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  reporter: 'list',
  outputDir: process.env.GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR
    ?? fileURLToPath(new URL('../../../.local-run/playwright/simulator/live/', import.meta.url)),
  use: {
    baseURL,
    viewport: { width: 1366, height: 768 },
    deviceScaleFactor: 1,
    browserName: 'chromium',
    screenshot: 'only-on-failure',
    video: 'off',
    trace: 'off',
    launchOptions: { args: ['--disable-blink-features=AutomationControlled'] },
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: `${baseURL}/`,
    timeout: 240_000,
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      VITE_GEO_BACKEND_ORIGIN: origin,
      VITE_TEST_MODE: '0',
    },
  },
})
