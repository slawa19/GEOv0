/**
 * 038 QA stand: the product runs as it does for a user. No test mode here: FX, physics, dragging and render-loop sleep
 * stay on. The only differences from a user's browser are the Vite dev server (it installs `window.__geoSim`), a backend
 * mocked on `page.route`, and Chromium started without a GPU.
 *
 * Two projects, never mixed: `qa-stand` is deterministic (`page.clock`), `qa-perf` measures wall clock.
 * Files are `*.qa.ts` / `*.qaperf.ts` on purpose: the default config (`testDir: ./e2e`) collects `*.spec.ts` only, so the
 * snapshot stand does not pick these up.
 *
 * The discrete budgets (`qa/budgets.qa.ts`, `qa/no-gpu-effects.qa.ts`: counts on `page.clock`) are members of `qa-stand`
 * since 2026-10-12 (039 slice A, R14): the counted project, run by the required gate. Until then `qa-perf` collected
 * them, which the gate never runs. On a production build (`QA_STAND_SERVER=preview`) `qa-perf` collects them AS WELL:
 * that is the only run that holds the draw and label counts on a production build, and there three of them skip for
 * want of the dev hook - which the counted gate would refuse, and `qa-perf` (not counted) reports.
 */
import { fileURLToPath } from 'node:url'

import { defineConfig } from '@playwright/test'

const port = Number.parseInt(process.env.PW_E2E_PORT ?? '', 10) || 5197
const baseURL = `http://127.0.0.1:${port}`

/** Software-only Chromium: the target machine of the stand has no GPU. */
const NO_GPU_ARGS = ['--disable-gpu', '--disable-accelerated-2d-canvas', '--disable-gpu-compositing']
/**
 * Playwright sets `navigator.webdriver = true`, and the product reads it as "keep screenshots stable" (label glow off,
 * edge hover off, quality autoguard off, DPR clamp 1). The stand must see what a user sees, so the flag is cleared.
 */
const AS_USER_ARGS = ['--disable-blink-features=AutomationControlled']

/**
 * `QA_STAND_SERVER=preview`: the perf project against a production build (`vite build` + `vite preview`). There is
 * no dev hook there, so only what the page itself can measure is recorded (frame times, heap), not FX state.
 */
const preview = process.env.QA_STAND_SERVER === 'preview'
/** The deterministic budget files: of `qa-stand` always, of `qa-perf` on a production build too (see the header). */
const BUDGET_FILES = ['**/budgets.qa.ts', '**/no-gpu-effects.qa.ts']

const use = {
  baseURL,
  viewport: { width: 1280, height: 720 },
  deviceScaleFactor: 1,
  screenshot: 'only-on-failure' as const,
  video: 'off' as const,
  trace: 'off' as const,
}

export default defineConfig({
  testDir: './qa',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  // No retries (038 S2): a test of the stand that passes on a second try failed on the first; the gate refuses a flaky run.
  retries: 0,
  reporter: 'list',
  outputDir: process.env.GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR
    ?? fileURLToPath(new URL('../../../.local-run/playwright/simulator/qa/', import.meta.url)),
  projects: [
    {
      name: 'qa-stand',
      testMatch: '**/*.qa.ts',
      use: { ...use, browserName: 'chromium', launchOptions: { args: [...NO_GPU_ARGS, ...AS_USER_ARGS] } },
    },
    {
      name: 'qa-perf',
      testMatch: preview ? ['**/*.qaperf.ts', ...BUDGET_FILES] : '**/*.qaperf.ts',
      timeout: 300_000,
      use: {
        ...use,
        browserName: 'chromium',
        launchOptions: {
          args: [...NO_GPU_ARGS, ...AS_USER_ARGS, '--enable-precise-memory-info', '--js-flags=--expose-gc'],
        },
      },
    },
  ],
  webServer: {
    command: preview
      ? `npm run build && npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`
      : `npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: `${baseURL}/`,
    timeout: 240_000,
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      VITE_DEMO_FIXTURES: '1',
      // An existing environment variable wins over `.env.local` in Vite, so a developer's local VITE_TEST_MODE=1
      // cannot put this server (or the production build) in test mode. `boot()` still checks the running page.
      VITE_TEST_MODE: '0',
    },
  },
})
