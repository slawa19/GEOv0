import { defineConfig, devices } from '@playwright/test'

const e2ePort = Number.parseInt(process.env.PW_E2E_PORT ?? '', 10) || 5173
const e2eBaseUrl = `http://127.0.0.1:${e2ePort}`

// Playwright VS Code extension can run `playwright test-server` in the background
// for test discovery. That process is long-lived; if we start `webServer` there,
// it will keep `npm run dev` (Vite watchers) running and can churn HDD.
// Only auto-start the dev server for normal `playwright test` runs.
const isTestServer = process.argv.some((a) => a === 'test-server' || a.endsWith('test-server'))
const shouldStartWebServer = !isTestServer

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: e2eBaseUrl,
    trace: 'on-first-retry',
  },
  webServer: shouldStartWebServer
    ? {
        command: `npm run dev -- --host 127.0.0.1 --port ${e2ePort}`,
        url: e2eBaseUrl,
        reuseExistingServer: process.env.PW_REUSE_SERVER === '1',
        env: {
          ...process.env,
          VITE_API_MODE: 'mock',
        },
        timeout: 120_000,
      }
    : undefined,
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
