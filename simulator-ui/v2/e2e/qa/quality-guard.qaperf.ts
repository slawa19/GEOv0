/**
 * 038 S3 - CHARACTERIZATION of the start-up FPS guard (`useQualityAutoguards.ts`, policy 3.2). The product is not
 * changed by this slice; these tests record what it does.
 *
 * The guard counts animation frames for 1800 ms after mount and puts the quality on `low` under 12 fps, unless the
 * user touched the quality. On the stand the OTHER guard always fires first: software rendering is recognised through
 * WebGL and the quality goes to `low` after 400 ms. To see the FPS guard alone the page is told that WebGL exists (a
 * stub context with no renderer info - the heuristic then reads "GPU likely available"), and a low frame rate is
 * forced by delivering every animation frame eight display frames late (about 7.5 fps on a 60 Hz display).
 *
 * Real time, but the outcome is discrete (`low` or `med`) and far from the 12 fps edge on both sides; the measured
 * rate is asserted, so a machine too slow to hold 12 fps ungoverned fails the control instead of passing by accident.
 * Not judged: the 12 fps and 1800 ms constants themselves (unit-level), and whether `low` is enough on a slow machine.
 */
import { chromium, expect, test, type Page } from '@playwright/test'

import { boot, pretendGpu, ringSnapshot } from './stand.js'

const NO_GPU_ARGS = ['--disable-gpu', '--disable-accelerated-2d-canvas', '--disable-gpu-compositing']
const devHook = process.env.QA_STAND_SERVER !== 'preview'

const fakeWebGl = pretendGpu

/** `requestAnimationFrame` callbacks run only on every `late + 1`-th display frame; the measured rate is kept for the test. */
const governor = (late: number) => (page: Page) => page.addInitScript((late) => {
  const inner = window.requestAnimationFrame.bind(window)
  const innerCancel = window.cancelAnimationFrame.bind(window)
  const live = new Map<number, number>()
  const stat = { frameAt: [] as number[] }
  ;(window as unknown as { __qaGovernor: typeof stat }).__qaGovernor = stat
  let seq = 0
  window.requestAnimationFrame = (cb: FrameRequestCallback) => {
    const id = (seq += 1)
    // All callers are delivered on the same display frames (every `late + 1`-th of a 60 Hz grid), as on a slow machine.
    const hop = () => live.set(id, inner((t) => {
      if (Math.round(t / (1000 / 60)) % (late + 1) !== 0) return hop()
      live.delete(id)
      // One entry per display frame that ran a callback (several callers share a frame and its timestamp).
      if (stat.frameAt[stat.frameAt.length - 1] !== t && stat.frameAt.length < 5000) stat.frameAt.push(t)
      cb(t)
    }))
    hop()
    return -id
  }
  window.cancelAnimationFrame = (id: number) => {
    const h = live.get(-id)
    if (h === undefined) return innerCancel(id)
    innerCancel(h)
    live.delete(-id)
  }
}, late)

async function flags(page: Page) {
  return page.evaluate(() => {
    const r = document.querySelector<HTMLElement>('.root')!
    const at = (window as unknown as { __qaGovernor?: { frameAt: number[] } }).__qaGovernor?.frameAt ?? []
    // The rate over the first 1800 ms of frames - the window the guard itself judges (it starts at mount, a little later).
    const inWindow = at.filter((t) => t - at[0]! <= 1800)
    const startupFps = inWindow.length < 2 ? 0 : Math.round(((inWindow.length - 1) * 1000) / (inWindow[inWindow.length - 1]! - inWindow[0]!))
    return { quality: r.dataset.quality, gpu: r.dataset.gpu, webdriver: navigator.webdriver, startupFps }
  })
}

/** The guard has decided 1800 ms after mount; wait for well past that on the page's own clock, not by sleeping in the test. */
const guardWindowOver = (page: Page) => page.waitForFunction(() => performance.now() > 6000, undefined, { timeout: 30_000 })

test('FPS guard: with WebGL present and about 7.5 fps at start-up the quality goes to low', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false, devHook, beforeLoad: async (p) => { await fakeWebGl(p); await governor(7)(p) } })
  await guardWindowOver(page)
  const f = await flags(page)
  console.log('[038 guard] governed', JSON.stringify(f))
  expect(f.gpu, 'precondition: the GPU heuristic was told a GPU is there, so it is not what lowered the quality').toBe('1')
  expect(f.startupFps, 'precondition: the governor held the start-up frame rate under the 12 fps of the guard, and frames did come').toBeGreaterThan(0)
  expect(f.startupFps).toBeLessThan(12)
  expect(f.quality, 'under 12 fps in the first 1800 ms the FPS guard lowers the quality').toBe('low')
})

test('FPS guard, control: with WebGL present and an ungoverned frame rate the quality stays med', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false, devHook, beforeLoad: async (p) => { await fakeWebGl(p); await governor(0)(p) } })
  await guardWindowOver(page)
  const f = await flags(page)
  console.log('[038 guard] control', JSON.stringify(f))
  expect(f.gpu).toBe('1')
  expect(f.startupFps, 'precondition: this machine holds the start-up frame rate well above 12 fps ungoverned - otherwise the control says nothing').toBeGreaterThan(24)
  expect(f.quality, 'nothing lowers the quality when frames come at the display rate: the first test is the guard\'s doing, not a default').toBe('med')
})

test('FPS guard is off under WebDriver on purpose: the same 7.5 fps leaves the quality at med', async ({ baseURL }) => {
  // The default Playwright browser: navigator.webdriver === true (`useQualityAutoguards.ts`: `if (deps.isWebDriver) return`).
  // This is why the stand clears the flag: a WebDriver browser does not get the product's no-GPU degradation at all.
  const browser = await chromium.launch({ args: NO_GPU_ARGS })
  try {
    const page = await browser.newPage({ baseURL, viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 })
    await boot(page, { snapshot: ringSnapshot(), clock: false, devHook, allowWebdriver: true, beforeLoad: async (p) => { await fakeWebGl(p); await governor(7)(p) } })
    await guardWindowOver(page)
    const f = await flags(page)
    console.log('[038 guard] webdriver', JSON.stringify(f))
    expect(f.webdriver).toBe(true)
    expect(f.startupFps, 'precondition: the frame rate was as low as in the first test').toBeLessThan(12)
    expect(f.quality, 'no guard ran: neither the FPS guard nor the GPU heuristic').toBe('med')
  } finally {
    await browser.close()
  }
})
