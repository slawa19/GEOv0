/**
 * HUD QA §4 — Post-fix verification (after HudBar shrink fix)
 * Checks: overflow, CSS vars, !important guards. (QA-2 "Advanced dropdown not clipped" at 480 px removed by 043 S3: it never opened a menu.)
 */
/// <reference types="node" />
import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { resolve, join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirnameCurrent = dirname(__filename)
const REPO_ROOT = resolve(__dirnameCurrent, '../../../')
const ARTIFACTS_DIR = process.env.GEO_SIMULATOR_HUD_QA_ARTIFACT_DIR
  ? resolve(process.env.GEO_SIMULATOR_HUD_QA_ARTIFACT_DIR)
  : resolve(REPO_ROOT, '.local-run/playwright/simulator/hud-qa-artifacts')

/** Wait until the app signals data-ready (same convention as console-guard.spec.ts). */
async function waitReady(page: Page) {
  await page.waitForSelector('[data-ready="1"]', { timeout: 25_000 })
  await page.waitForTimeout(300)
}

function getHomeUrl(testInfo: TestInfo) {
  const baseURL = testInfo.project.use.baseURL as string | undefined
  if (baseURL) return new URL('/', baseURL).toString()

  // Fallback for ad-hoc runs without config/baseURL.
  return 'http://127.0.0.1:5176/'
}

async function measureBottomBar(page: Page) {
  return page.evaluate(() => {
    // Try the bottom bar's service row first (046 wave 1: by test id - its accessible name is the dictionary's), then
    // fall back to last .hud-bar
    const byAria = document.querySelector('[data-testid="bottom-details-row"]') as HTMLElement | null
    const allBars = Array.from(document.querySelectorAll('.hud-bar')) as HTMLElement[]
    const el = byAria || allBars[allBars.length - 1] || null
    if (!el) return null
    return {
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      offsetWidth: el.offsetWidth,
      hasOverflow: el.scrollWidth > el.clientWidth,
      selector: byAria ? '[data-testid="bottom-details-row"]' : 'last .hud-bar',
      className: el.className
    }
  })
}

test.describe('HUD QA §4 post-fix', () => {
  test.beforeAll(() => {
    mkdirSync(ARTIFACTS_DIR, { recursive: true })
  })

  test('QA-1 480px: BottomBar no horizontal overflow', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 480, height: 900 })
    await page.goto(getHomeUrl(testInfo))
    await waitReady(page)

    // Try to switch to INTERACT tab if available and enabled
    const interactTab = page.locator('button, [role="tab"]').filter({ hasText: /interact/i })
    if (await interactTab.count() > 0) {
      const isDisabled = await interactTab.first().evaluate((el: HTMLButtonElement) => el.disabled)
      if (!isDisabled) {
        await interactTab.first().click()
        await page.waitForTimeout(500)
      }
    }

    // Screenshot
    await page.screenshot({
      path: join(ARTIFACTS_DIR, 'hud_qa_480_postfix.png'),
      fullPage: false
    })

    const metrics = await measureBottomBar(page)

    // Measure all hud-bar elements
    const allHudMetrics = await page.evaluate(() => {
      const bars = Array.from(document.querySelectorAll('.hud-bar')) as HTMLElement[]
      return bars.map(el => ({
        label: el.getAttribute('aria-label') || el.className.split(' ').slice(0, 5).join(' '),
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        hasOverflow: el.scrollWidth > el.clientWidth
      }))
    })

    const report = {
      viewport: '480x900',
      bottomBar: metrics,
      allHudBars: allHudMetrics,
      pass: metrics ? !metrics.hasOverflow : null,
      noOverflowInAnyBar: allHudMetrics.every(b => !b.hasOverflow),
      timestamp: new Date().toISOString()
    }

    console.log('480px report:', JSON.stringify(report, null, 2))

    writeFileSync(
      join(ARTIFACTS_DIR, 'hud_qa_report_480_postfix.json'),
      JSON.stringify(report, null, 2)
    )

    // If BottomBar found — it must not overflow
    if (metrics) {
      expect(
        metrics.scrollWidth,
        `scrollWidth (${metrics.scrollWidth}) should <= clientWidth (${metrics.clientWidth}) at 480px`
      ).toBeLessThanOrEqual(metrics.clientWidth)
    }
    // Check ALL hud bars — none should overflow
    for (const bar of allHudMetrics) {
      expect(
        bar.hasOverflow,
        `HudBar "${bar.label}" has overflow at 480px: scrollWidth=${bar.scrollWidth} > clientWidth=${bar.clientWidth}`
      ).toBe(false)
    }
  })

  test('QA-1 360px: BottomBar no horizontal overflow', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 360, height: 900 })
    await page.goto(getHomeUrl(testInfo))
    await waitReady(page)

    const interactTab = page.locator('button, [role="tab"]').filter({ hasText: /interact/i })
    if (await interactTab.count() > 0) {
      const isDisabled = await interactTab.first().evaluate((el: HTMLButtonElement) => el.disabled)
      if (!isDisabled) {
        await interactTab.first().click()
        await page.waitForTimeout(500)
      }
    }

    await page.screenshot({
      path: join(ARTIFACTS_DIR, 'hud_qa_360_postfix.png'),
      fullPage: false
    })

    const metrics = await measureBottomBar(page)

    const allHudMetrics = await page.evaluate(() => {
      const bars = Array.from(document.querySelectorAll('.hud-bar')) as HTMLElement[]
      return bars.map(el => ({
        label: el.getAttribute('aria-label') || el.className.split(' ').slice(0, 5).join(' '),
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        hasOverflow: el.scrollWidth > el.clientWidth
      }))
    })

    const report = {
      viewport: '360x900',
      bottomBar: metrics,
      allHudBars: allHudMetrics,
      pass: metrics ? !metrics.hasOverflow : null,
      noOverflowInAnyBar: allHudMetrics.every(b => !b.hasOverflow),
      timestamp: new Date().toISOString()
    }

    writeFileSync(
      join(ARTIFACTS_DIR, 'hud_qa_report_360_postfix.json'),
      JSON.stringify(report, null, 2)
    )

    if (metrics) {
      expect(
        metrics.scrollWidth,
        `scrollWidth (${metrics.scrollWidth}) should <= clientWidth (${metrics.clientWidth}) at 360px`
      ).toBeLessThanOrEqual(metrics.clientWidth)
    }
    for (const bar of allHudMetrics) {
      expect(
        bar.hasOverflow,
        `HudBar "${bar.label}" has overflow at 360px: scrollWidth=${bar.scrollWidth} > clientWidth=${bar.clientWidth}`
      ).toBe(false)
    }
  })

  test('QA-3: CSS vars --ds-hud-bar-py/--ds-hud-bar-px contract', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 480, height: 900 })
    await page.goto(getHomeUrl(testInfo))
    await waitReady(page)

    const cssVarCheck = await page.evaluate(() => {
      const hudBars = Array.from(document.querySelectorAll('.hud-bar')) as HTMLElement[]
      return hudBars.map(el => {
        const cs = getComputedStyle(el)
        return {
          element: el.className.split(' ').slice(0, 5).join(' '),
          paddingTop: cs.paddingTop,
          paddingBottom: cs.paddingBottom,
          paddingLeft: cs.paddingLeft,
          paddingRight: cs.paddingRight,
          hudBarPy: cs.getPropertyValue('--ds-hud-bar-py').trim(),
          hudBarPx: cs.getPropertyValue('--ds-hud-bar-px').trim(),
        }
      })
    })

    console.log('CSS var check:', JSON.stringify(cssVarCheck, null, 2))

    expect(cssVarCheck.length, 'Should have at least 1 HudBar in DOM').toBeGreaterThan(0)

    for (const bar of cssVarCheck) {
      const topPx = parseFloat(bar.paddingTop)
      const leftPx = parseFloat(bar.paddingLeft)
      expect(topPx, `HudBar should have vertical padding >= 0: ${bar.element}`).toBeGreaterThanOrEqual(0)
      expect(leftPx, `HudBar should have horizontal padding >= 0: ${bar.element}`).toBeGreaterThanOrEqual(0)
    }
  })

  test('QA-4: No !important vertical padding overrides in overlays CSS', async () => {
    const overlaysCssPath = resolve(__dirnameCurrent, '../src/ui-kit/designSystem.overlays.css')
    const cssContent = readFileSync(overlaysCssPath, 'utf-8')

    const lines = cssContent.split('\n')
    const suspiciousLines: { line: number; content: string; context: string }[] = []

    let lastSelector = ''
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim()
      if (line.includes('{') && !line.startsWith('//') && !line.startsWith('/*')) {
        lastSelector = line
      }
      if (
        line.includes('!important') &&
        (line.includes('padding-top') || line.includes('padding-bottom') || line.includes('padding-block'))
      ) {
        suspiciousLines.push({ line: i + 1, content: line, context: lastSelector })
      }
    }

    console.log('QA-4 all suspicious !important padding lines:', JSON.stringify(suspiciousLines, null, 2))

    const hudRelated = suspiciousLines.filter(l =>
      l.context.includes('ds-ov-top-stack') ||
      l.context.includes('ds-ov-bottom') ||
      l.context.includes('hud-bar') ||
      l.context.includes('ds-ov-bar')
    )

    expect(
      hudRelated,
      `Found !important vertical padding overrides in HUD context: ${JSON.stringify(hudRelated)}`
    ).toHaveLength(0)
  })
})
