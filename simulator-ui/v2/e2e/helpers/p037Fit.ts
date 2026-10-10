/**
 * "Fits" for a window of the Interact UI on a phone-sized screen (decision 037-B): the shell is wholly in the viewport; no
 * horizontal overflow of the document; each primary control is seen WHOLE (inside the viewport and the shell) with nothing over it,
 * probed at four corners and the centre. Measured in the page BEFORE the next Playwright action (a click scrolls its target into
 * view and would hide exactly the clipping this is after). Shared by the payment-panel and the clearing-panel specs.
 *
 * LIMIT: five points per control - the band closer than 10px to the sides / 3px to the top and bottom, and the gaps between the
 * points, are not probed.
 */
import type { Page } from '@playwright/test'

import { settleShell } from './p037Mock.js'

export type Fit = {
  selector: string
  found: boolean
  rect: { x: number; y: number; w: number; h: number } | null
  insideViewport: boolean
  insideShell: boolean
  uncoveredPoints: number
  of: number
}

export type StepMeasure = {
  step: string
  viewport: { W: number; H: number }
  documentScrollWidth: number
  pageScrollTop: number
  shell: { x: number; y: number; w: number; h: number; inside: boolean } | null
  fits: Fit[]
  /** The scrolling body of the panel: where long content scrolls INSIDE the window. */
  body: { scrollHeight: number; clientHeight: number } | null
}

/** Measure without touching the page: no scroll, no click. */
export async function measure(page: Page, step: string, selectors: string[], panelId = 'manual-payment-panel'): Promise<StepMeasure> {
  // The window manager re-clamps a shell that grew a few frames after the content appeared: measure the step, not the transition.
  await settleShell(page, panelId)
  return await page.evaluate(({ step, selectors, panelId }) => {
    const W = window.innerWidth
    const H = window.innerHeight
    const tol = 0.5
    const panel = document.querySelector(`[data-testid="${panelId}"]`) as HTMLElement | null
    const shellEl = (panel?.closest('.ws-shell') as HTMLElement | null) ?? null
    const sr = shellEl?.getBoundingClientRect() ?? null
    const fits = selectors.map((selector): Fit => {
      const el = document.querySelector(selector) as HTMLElement | null
      if (!el) return { selector, found: false, rect: null, insideViewport: false, insideShell: false, uncoveredPoints: 0, of: 5 }
      const r = el.getBoundingClientRect()
      // Five points per control: four corners and the centre. The HUD theme cuts two corners of buttons at 8px (clip-path), so
      // the corner points sit 10px in from the sides and 3px in from top/bottom. LIMIT: the band closer than that to the edge and
      // the gaps BETWEEN the five points are not probed - a sliver of cover there, or a hole in the middle of a wide control, passes.
      const ix = Math.min(10, r.width / 2 - 1)
      const iy = Math.min(3, r.height / 2 - 1)
      const pts: Array<[number, number]> = [
        [r.left + ix, r.top + iy], [r.right - ix, r.top + iy], [r.left + ix, r.bottom - iy], [r.right - ix, r.bottom - iy],
        [r.left + r.width / 2, r.top + r.height / 2],
      ]
      let uncovered = 0
      for (const [x, y] of pts) {
        if (x < 0 || y < 0 || x >= W || y >= H) continue
        const hit = document.elementFromPoint(x, y)
        if (hit && (hit === el || el.contains(hit))) uncovered += 1
      }
      return {
        selector, found: true,
        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        insideViewport: r.left >= -tol && r.top >= -tol && r.right <= W + tol && r.bottom <= H + tol,
        insideShell: !!sr && r.left >= sr.left - tol && r.top >= sr.top - tol && r.right <= sr.right + tol && r.bottom <= sr.bottom + tol,
        uncoveredPoints: uncovered, of: 5,
      }
    })
    return {
      step,
      viewport: { W, H },
      documentScrollWidth: document.documentElement.scrollWidth,
      pageScrollTop: document.scrollingElement?.scrollTop ?? 0,
      shell: sr && { x: Math.round(sr.x), y: Math.round(sr.y), w: Math.round(sr.width), h: Math.round(sr.height),
        inside: sr.left >= -tol && sr.top >= -tol && sr.right <= W + tol && sr.bottom <= H + tol },
      fits,
      body: (() => { const b = panel?.querySelector<HTMLElement>(':scope > .ds-panel__body'); return b ? { scrollHeight: b.scrollHeight, clientHeight: b.clientHeight } : null })(),
    }
  }, { step, selectors, panelId })
}

export function problems(m: StepMeasure): string[] {
  const out: string[] = []
  if (!m.shell) out.push('no window shell around the payment panel')
  else if (!m.shell.inside) out.push(`shell outside the viewport ${JSON.stringify(m.shell)} in ${m.viewport.W}x${m.viewport.H}`)
  if (m.documentScrollWidth > m.viewport.W) out.push(`document overflows horizontally: ${m.documentScrollWidth} > ${m.viewport.W}`)
  if (m.pageScrollTop !== 0) out.push(`the page itself scrolled (${m.pageScrollTop})`)
  for (const f of m.fits) {
    if (!f.found) { out.push(`${f.selector}: not found`); continue }
    if (!f.insideViewport) out.push(`${f.selector}: not whole inside the viewport ${JSON.stringify(f.rect)}`)
    if (!f.insideShell) out.push(`${f.selector}: not whole inside the shell ${JSON.stringify(f.rect)} (shell ${JSON.stringify(m.shell)})`)
    if (f.uncoveredPoints < f.of) out.push(`${f.selector}: covered or clipped, ${f.uncoveredPoints}/${f.of} points hit it ${JSON.stringify(f.rect)}`)
  }
  return out
}
