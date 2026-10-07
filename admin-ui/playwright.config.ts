import { fileURLToPath } from 'node:url'

import { defineConfig, devices } from '@playwright/test'

import { e2eBackend } from './e2e/backend'

const e2ePort = Number.parseInt(process.env.PW_E2E_PORT ?? '', 10) || 5173
const e2eBaseUrl = `http://127.0.0.1:${e2ePort}`

// Playwright VS Code extension can run `playwright test-server` in the background
// for test discovery. That process is long-lived; if we start `webServer` there,
// it will keep `npm run dev` (Vite watchers) running and can churn HDD.
// Only auto-start the dev server for normal `playwright test` runs.
const isTestServer = process.argv.some((a) => a === 'test-server' || a.endsWith('test-server'))
const shouldStartWebServer = !isTestServer
const outputDir = process.env.GEO_ADMIN_PLAYWRIGHT_OUTPUT_DIR
  ?? fileURLToPath(new URL('../.local-run/playwright/admin/results/', import.meta.url))
const reportDir = process.env.GEO_ADMIN_PLAYWRIGHT_REPORT_DIR
  ?? fileURLToPath(new URL('../.local-run/playwright/admin/report/', import.meta.url))

// THE ADMIN E2E RUNS AGAINST A REAL BACKEND (032 S4, 2026-10-07). There is no mock to fall back on:
// the backend origin and the admin token are required, and a run without them stops here instead of
// rendering empty screens. The backend is seeded with `scripts/seed_db.py --source recipe --community
// riverside-town-50`; locally `scripts/verify_admin_e2e.ps1` brings it up on a disposable database,
// in CI the `ui-smoke` and `admin-e2e` jobs do (`.github/workflows/quality.yml`).
const backend = isTestServer ? null : e2eBackend()

// Tests that change server state (config, participant status) run in their own project, one at a
// time and only after the read-only project has finished: the main project is fully parallel, and a
// frozen participant or a changed config key must never be observed by a neighbouring test.
const MUTATIONS = /mutations\.spec\.ts$/

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never', outputFolder: reportDir }]]
    : [['list'], ['html', { open: 'never', outputFolder: reportDir }]],
  outputDir,
  use: {
    baseURL: e2eBaseUrl,
    trace: 'on-first-retry',
  },
  webServer: shouldStartWebServer && backend
    ? {
        command: `npm run dev -- --host 127.0.0.1 --port ${e2ePort} --strictPort`,
        url: e2eBaseUrl,
        reuseExistingServer: process.env.PW_REUSE_SERVER === '1',
        env: {
          ...process.env,
          VITE_API_BASE_URL: backend.origin,
          VITE_ADMIN_TOKEN: backend.token,
        },
        timeout: 120_000,
      }
    : undefined,
  projects: [
    {
      name: 'chromium',
      testIgnore: MUTATIONS,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chromium-mutations',
      testMatch: MUTATIONS,
      fullyParallel: false,
      dependencies: ['chromium'],
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
