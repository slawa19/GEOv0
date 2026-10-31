import { expect, test } from '@playwright/test'

/**
 * MM1-S1: the `bloom` node underlay must leave visible pixels in a REAL Canvas 2D.
 *
 * The `fx-dot` beam-head halo is MEASURED and printed, not judged: it is empty (alpha-0 shape) and stays so on purpose
 * until a decision - a visible halo breaks the beam contract of 039 slice C on the QA stand (see `fillShadowOnly`).
 *
 * A canvas shadow is built from the alpha of the shape that casts it, so a shape filled with alpha 0 casts no shadow:
 * a mock of the 2D context records `shadowBlur` and a fill and cannot tell an empty sprite from a glowing one. This
 * spec therefore rasterises the sprites in Chromium (the Vite dev server serves `/src/...` as modules, so the browser
 * imports the very module the app draws with) and counts pixels with alpha > 0 by `getImageData`.
 *
 * What it does NOT check: the look (softness, colour balance) and the node/beam composition on the scene - that is
 * the visual baseline of `scenes.spec.ts`. `rim` is measured as the working reference the halo is compared with.
 */

type SpriteStats = { w: number; h: number; lit: number; alphaSum: number; maxAlpha: number }

test('MM1-S1: the bloom glow sprite rasterises visible halo pixels in Chromium', async ({ page }) => {
  await page.goto('/')

  // A string, not a function: the test runner must not transpile `import()` for Node.
  const stats = (await page.evaluate(`(async () => {
    const m = await import('/src/render/glowSprites.ts')
    const t = m.__testing
    t._cacheClear()
    const measure = (sprite, ringMinR) => {
      const ctx = sprite.getContext('2d')
      const d = ctx.getImageData(0, 0, sprite.width, sprite.height).data
      const cx = sprite.width / 2, cy = sprite.height / 2
      let lit = 0, alphaSum = 0, maxAlpha = 0
      for (let y = 0; y < sprite.height; y++) {
        for (let x = 0; x < sprite.width; x++) {
          if (ringMinR > 0 && Math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= ringMinR) continue
          const a = d[(y * sprite.width + x) * 4 + 3]
          if (a > 0) { lit++; alphaSum += a; if (a > maxAlpha) maxAlpha = a }
        }
      }
      return { w: sprite.width, h: sprite.height, lit, alphaSum, maxAlpha }
    }
    const node = { shape: 'circle', w: 20, h: 20, r: 10, rr: 0, color: '#22d3ee', blurPx: 16, lineWidthPx: 0 }
    const bloomCircle = measure(t._getGlowSprite({ ...node, kind: 'bloom' }), 0)
    const bloomRect = measure(t._getGlowSprite({ ...node, kind: 'bloom', shape: 'rounded-rect', w: 28, h: 20, rr: 5 }), 0)
    const rim = measure(t._getGlowSprite({ ...node, kind: 'rim', blurPx: 3, lineWidthPx: 2 }), 0)
    // fx-dot: r 4, the crisp core is 0.55 r; count only the ring OUTSIDE the core (+1 px of antialiasing).
    const dotR = 4
    const fxDotHalo = measure(t._getGlowSprite({ kind: 'fx-dot', color: '#22d3ee', r: dotR, blurPx: 8 }), dotR * 0.55 + 1)
    return { bloomCircle, bloomRect, rim, fxDotHalo }
  })()`)) as Record<'bloomCircle' | 'bloomRect' | 'rim' | 'fxDotHalo', SpriteStats>

  // Printed so the measurement is open in the run log, not only its verdict.
  console.log(`MM1-S1 sprite stats: ${JSON.stringify(stats)}`)

  // Counter-check: the reference sprite is lit, so a zero below is the sprite, not the measuring.
  expect(stats.rim.lit, 'rim (reference) lit pixels').toBeGreaterThan(0)
  expect(stats.bloomCircle.lit, 'bloom circle lit pixels').toBeGreaterThan(0)
  expect(stats.bloomRect.lit, 'bloom rounded-rect lit pixels').toBeGreaterThan(0)
})
