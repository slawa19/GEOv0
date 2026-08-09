import { fileURLToPath } from 'node:url'

import { defineConfig } from '@playwright/test'

const port = Number.parseInt(process.env.PW_E2E_PORT ?? '', 10) || 5187
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: '.',
  testMatch: 'phase5-functional-smoke.spec.ts',
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  workers: 1,
  reporter: 'list',
  outputDir: fileURLToPath(new URL('../../../.local-run/phase5-e2e/', import.meta.url)),
  use: {
    baseURL,
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    screenshot: 'only-on-failure',
    video: 'off',
    trace: 'off',
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: `${baseURL}/`,
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      VITE_DEMO_FIXTURES: '1',
      VITE_TEST_MODE: '1',
    },
  },
})
