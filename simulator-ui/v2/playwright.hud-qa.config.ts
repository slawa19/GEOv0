/**
 * Playwright config for HUD QA §4 post-fix verification.
 * Connects to the already-running dev server on port 5176
 * (started via scripts/run_simulator_ui.cmd with VITE_DEMO_FIXTURES=1).
 */
import { fileURLToPath } from 'node:url'

import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  outputDir: process.env.GEO_SIMULATOR_HUD_QA_OUTPUT_DIR
    ?? fileURLToPath(new URL('../../.local-run/playwright/simulator/hud-qa-results/', import.meta.url)),
  use: {
    baseURL: 'http://127.0.0.1:5176',
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    screenshot: 'only-on-failure',
    video: 'off',
    trace: 'off',
  },
  // No webServer — we connect to the already-running server on 5176
})
