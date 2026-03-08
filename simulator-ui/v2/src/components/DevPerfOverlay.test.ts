import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createApp, h, nextTick, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DevPerfOverlay from './DevPerfOverlay.vue'

const devPerfOverlayComponent: Component = DevPerfOverlay
const devPerfOverlaySource = readFileSync(resolve(process.cwd(), 'src/components/DevPerfOverlay.vue'), 'utf8')
const overlaysSource = readFileSync(resolve(process.cwd(), 'src/ui-kit/designSystem.overlays.css'), 'utf8')
const tokensSource = readFileSync(resolve(process.cwd(), 'src/ui-kit/designSystem.tokens.css'), 'utf8')

afterEach(() => {
  vi.restoreAllMocks()
})

describe('DevPerfOverlay', () => {
  it('renders diagnostics using the shared dev-overlay contract', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({
      render: () =>
        h(devPerfOverlayComponent, {
          enabled: true,
          perf: {
            lastFps: 60,
            fxBudgetScale: 1,
            maxParticles: 1000,
            renderQuality: 'high',
            dprClamp: 2,
            canvasCssW: 1280,
            canvasCssH: 720,
            canvasPxW: 2560,
            canvasPxH: 1440,
            canvasDpr: 2,
          },
        }),
    })

    try {
      app.mount(host)
      await nextTick()

      const overlay = host.querySelector('.ds-ov-dev-perf') as HTMLElement | null
      expect(overlay).toBeTruthy()
      expect(overlay?.textContent ?? '').toContain('Perf probe')
      expect(overlay?.textContent ?? '').toContain('FPS(last): 60')
      expect(host.querySelector('.ds-ov-dev-perf__row')).toBeTruthy()
      expect(host.querySelector('.ds-ov-dev-perf__pre')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('uses tokenized dev-overlay spacing instead of local hardcoded paddings and gaps', () => {
    expect(devPerfOverlaySource).not.toContain('gap: 10px;')
    expect(devPerfOverlaySource).not.toContain('padding: 10px 12px 6px 12px;')
    expect(devPerfOverlaySource).toContain('ds-ov-dev-perf__row')
    expect(devPerfOverlaySource).toContain('ds-ov-dev-perf__warn')
    expect(devPerfOverlaySource).toContain('ds-ov-dev-perf__pre')

    expect(overlaysSource).toContain('.ds-ov-dev-perf__row {')
    expect(overlaysSource).toContain('gap: var(--ds-ov-dev-row-gap);')
    expect(overlaysSource).toContain('padding: var(--ds-ov-dev-row-pad-top) var(--ds-ov-dev-row-pad-x) var(--ds-ov-dev-row-pad-bottom);')
    expect(overlaysSource).toContain('.ds-ov-dev-perf__warn {')
    expect(tokensSource).toContain('--ds-ov-dev-row-gap: 10px;')
    expect(tokensSource).toContain('--ds-ov-dev-row-pad-top: 10px;')
    expect(tokensSource).toContain('--ds-ov-dev-pre-pad-bottom: 12px;')
  })

  it('shows the GPU warning note when the fallback renderer is detected', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const originalCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation(((tagName: string) => {
      const element = originalCreateElement(tagName)
      if (tagName !== 'canvas') return element

      return Object.assign(element, {
        getContext() {
          return {
            getExtension(name: string) {
              if (name !== 'WEBGL_debug_renderer_info') return null
              return {
                UNMASKED_VENDOR_WEBGL: 'UNMASKED_VENDOR_WEBGL',
                UNMASKED_RENDERER_WEBGL: 'UNMASKED_RENDERER_WEBGL',
              }
            },
            getParameter(name: string) {
              if (name === 'UNMASKED_VENDOR_WEBGL') return 'Microsoft'
              if (name === 'UNMASKED_RENDERER_WEBGL') return 'Microsoft Basic Render Driver'
              return null
            },
          }
        },
      })
    }) as typeof document.createElement)

    const app = createApp({
      render: () =>
        h(devPerfOverlayComponent, {
          enabled: true,
          perf: {
            lastFps: null,
            fxBudgetScale: null,
            maxParticles: null,
            renderQuality: null,
            dprClamp: null,
            canvasCssW: null,
            canvasCssH: null,
            canvasPxW: null,
            canvasPxH: null,
            canvasDpr: null,
          },
        }),
    })

    try {
      app.mount(host)
      await nextTick()
      expect(host.querySelector('.ds-ov-dev-perf__warn')?.textContent ?? '').toContain('Hardware acceleration appears disabled')
    } finally {
      app.unmount()
      host.remove()
    }
  })
})