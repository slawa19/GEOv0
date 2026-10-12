/**
 * 039 slice B - the owner's "before / after" frames. NOT a gate and NOT a test of anything: files are
 * `*.p039-frames.ts`, which no counted project collects (`qa-stand` takes `*.qa.ts`, `qa-perf` `*.qaperf.ts`, the
 * reproducers `*.p039-repro.ts`). A run of this config is DEBUG-ONLY (AGENTS.md section 5): it writes pictures.
 *
 * The browser and the server are the reproducers' own (`playwright.p039-repro.config.ts`), so a frame is taken the
 * way the stand sees the product: Chromium without a GPU, Vite dev server out of test mode, the real pipeline over a
 * mocked backend.
 *
 *   P039_FRAMES_DIR=<abs dir> P039_FRAMES_PHASE=before|after PW_E2E_PORT=<free port> \
 *     npx playwright test --config=e2e/qa/p039-repro/playwright.p039-frames.config.ts
 */
import { defineConfig } from '@playwright/test'

import base from './playwright.p039-repro.config.js'

export default defineConfig({
  ...base,
  testMatch: '**/*.p039-frames.ts',
  use: { ...base.use, viewport: { width: 1366, height: 768 } },
})
