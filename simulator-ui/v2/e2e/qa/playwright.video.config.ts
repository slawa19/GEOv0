/**
 * 038 S1 - videos and frames for a human, in real time. NOT a check: nothing here decides pass or fail of the product;
 * the deterministic stand (`../playwright.qa.config.ts`, `page.clock`) does. This project runs on the wall clock and
 * never installs `page.clock`, so the two are not mixed in one test. Standard Playwright only: `video: 'on'` and the
 * HTML reporter; there is no reporter of our own.
 *
 * Files are `*.qavideo.ts`: neither the snapshot stand (`*.spec.ts`) nor the QA stand (`*.qa.ts`, `*.qaperf.ts`)
 * collects them. Run: `npm run test:e2e:qa:video`. Videos land in the Playwright output directory, one folder a test;
 * the HTML report next to it embeds them.
 */
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from '@playwright/test'

const port = Number.parseInt(process.env.PW_E2E_PORT ?? '', 10) || 5197
const baseURL = `http://127.0.0.1:${port}`
// `resolve` drops a trailing separator of either kind, so that the report folder is a sibling of the output folder
// and never inside it (Playwright refuses a report folder that clashes with the output folder).
const outputDir = resolve(process.env.GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR
  ?? fileURLToPath(new URL('../../../../.local-run/playwright/simulator/qa-video/', import.meta.url)))

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.qavideo.ts',
  timeout: 120_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [['list'], ['html', { outputFolder: `${outputDir}-report`, open: 'never' }]],
  outputDir,
  use: {
    baseURL,
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    video: { mode: 'on', size: { width: 1280, height: 720 } },
    screenshot: 'off',
    trace: 'off',
    browserName: 'chromium',
    // The same browser as the QA stand: no GPU, and not announced as WebDriver (the product hides FX under WebDriver).
    launchOptions: { args: ['--disable-gpu', '--disable-accelerated-2d-canvas', '--disable-gpu-compositing', '--disable-blink-features=AutomationControlled'] },
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: `${baseURL}/`,
    timeout: 240_000,
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'pipe',
    env: { VITE_DEMO_FIXTURES: '1', VITE_TEST_MODE: '0' },
  },
})
