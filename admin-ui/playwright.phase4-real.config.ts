import { defineConfig, devices } from '@playwright/test'

const uiPort = Number.parseInt(process.env.PHASE4_UI_PORT ?? '', 10) || 41741
const baseURL = `http://127.0.0.1:${uiPort}`

export default defineConfig({
  testDir: './e2e-real',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: process.env.PHASE4_PLAYWRIGHT_OUTPUT,
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
