import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:5177',
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    screenshot: 'only-on-failure',
    video: 'off',
    trace: 'off',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5177',
    url: 'http://127.0.0.1:5177/',
    reuseExistingServer: process.env.CI ? false : true,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      VITE_DEMO_FIXTURES: '1',
      VITE_TEST_MODE: '1',
    },
  },
})
