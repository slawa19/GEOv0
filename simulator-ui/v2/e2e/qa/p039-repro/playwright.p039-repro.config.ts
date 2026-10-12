/**
 * 039 T3901 - reproducers and measurements of programme 039 that need a browser. NOT part of the QA stand's gate:
 * files are `*.p039-repro.ts`, which neither `qa-stand` (`*.qa.ts`) nor `qa-perf` (`*.qaperf.ts`) collects, so the
 * recorded counts of `run-stand.mjs` and the budgets of `budgets.ts` do not move. A run of this config is DEBUG-ONLY
 * evidence (AGENTS.md section 5): it is not a canonical gate and is not counted.
 *
 * The browser and the server are the stand's own (`../../playwright.qa.config.ts`): Chromium without a GPU and not
 * announced as WebDriver, Vite dev server out of test mode; `boot()` of the stand checks both in the page.
 *
 *   PW_E2E_PORT=<free port> npx playwright test --config=e2e/qa/p039-repro/playwright.p039-repro.config.ts
 *
 * Reproducers that are RED on the base are plain failing tests here (no `test.fail`): a run of this config on the base
 * exits 1, and that is the recorded evidence. Measurements carry no threshold and pass.
 */
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from '@playwright/test'

const port = Number.parseInt(process.env.PW_E2E_PORT ?? '', 10) || 5197
const baseURL = `http://127.0.0.1:${port}`
const root = fileURLToPath(new URL('../../../', import.meta.url))
const outputDir = resolve(process.env.GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR
  ?? fileURLToPath(new URL('../../../../../.local-run/playwright/simulator/p039-repro/', import.meta.url)))

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.p039-repro.ts',
  timeout: 180_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: `${outputDir}-result.json` }]],
  outputDir,
  use: {
    baseURL,
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    screenshot: 'off',
    video: 'off',
    trace: 'off',
    browserName: 'chromium',
    launchOptions: { args: ['--disable-gpu', '--disable-accelerated-2d-canvas', '--disable-gpu-compositing', '--disable-blink-features=AutomationControlled'] },
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    cwd: root,
    url: `${baseURL}/`,
    timeout: 240_000,
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'pipe',
    env: { VITE_DEMO_FIXTURES: '1', VITE_TEST_MODE: '0' },
  },
})
