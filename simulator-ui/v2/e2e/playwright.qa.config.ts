/**
 * 038 QA stand: the product runs as it does for a user. No test mode here: FX, physics, dragging and render-loop sleep
 * stay on. The only differences from a user's browser are the Vite dev server (it installs `window.__geoSim`), a backend
 * mocked on `page.route`, and Chromium started without a GPU.
 *
 * Two projects, never mixed: `qa-stand` is deterministic (`page.clock`), `qa-perf` measures wall clock.
 * Files are `*.qa.ts` / `*.qaperf.ts` on purpose: the default config (`testDir: ./e2e`) collects `*.spec.ts` only, so the
 * snapshot stand does not pick these up.
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
      testMatch: '**/*.qaperf.ts',
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
    },
  },
})
