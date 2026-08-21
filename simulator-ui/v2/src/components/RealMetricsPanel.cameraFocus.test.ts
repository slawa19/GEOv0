import { createApp, h, nextTick, ref } from 'vue'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import RealMetricsPanel from './RealMetricsPanel.vue'
import { useAppViewWiring } from '../composables/useAppViewWiring'
import type {
  BottleneckTarget,
  BottlenecksResponse,
  MetricsResponse,
} from '../api/simulatorTypes'
import type { LayoutLinkLike, LayoutNode } from '../types/layout'

/**
 * End-to-end for one sentence of the spec: clicking a bottleneck row moves the camera.
 *
 * Deliberately not a "did it emit an event" test. The panel is mounted for real, the click is a
 * real DOM click, and the camera on the other end is the real `useCamera` state — so the whole
 * chain is exercised, including the part that silently does nothing when it is wired wrong.
 *
 * The failure mode this guards is specific: `useAppViewWiring.focusOnEdge` refuses to move the
 * camera unless it can confirm the edge exists, and it confirms that through the optional
 * `getLayoutLinks` dependency. Leave that dependency out and every row becomes a button that
 * reports success to nobody and changes nothing on screen — no error, no log. The last case below
 * is that exact omission, kept as a live description of what breaking the wiring looks like.
 */

// 1000x600 viewport; one directed edge A → B. Same geometry as `useAppViewWiring.test.ts`.
const NODE_A: LayoutNode = { id: 'A', __x: 100, __y: 100 }
const NODE_B: LayoutNode = { id: 'B', __x: 300, __y: 300 }
const EDGE_A_B: LayoutLinkLike = { __key: 'A→B', source: 'A', target: 'B' }

function makeBottlenecks(target: BottleneckTarget): BottlenecksResponse {
  return {
    api_version: 'v1',
    run_id: 'run-1',
    equivalent: 'UAH',
    items: [
      {
        target,
        score: 0.85,
        reason_code: 'FREQUENT_ABORTS',
        label: 'Alice -> Bob',
        suggested_action: 'Raise trust limit',
      },
    ],
  }
}

const METRICS: MetricsResponse = {
  api_version: 'v1',
  run_id: 'run-1',
  equivalent: 'UAH',
  from_ms: 0,
  to_ms: 5_000,
  step_ms: 5_000,
  series: [],
}

function mountPanelWiredToCamera(opts: {
  target: BottleneckTarget
  /** Omit to reproduce the un-wired case. */
  wireLayoutLinks: boolean
}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  let wiring: ReturnType<typeof useAppViewWiring> | null = null

  const app = createApp({
    setup() {
      wiring = useAppViewWiring({
        canvasEl: ref(null),
        hostEl: ref(null),

        getLayoutNodes: () => [NODE_A, NODE_B],
        getLayoutW: () => 1000,
        getLayoutH: () => 600,
        isTestMode: () => false,

        setClampCameraPan: () => undefined,

        selectedNodeId: ref<string | null>(null),
        setSelectedNodeId: () => undefined,

        getNodeById: () => null,
        getLayoutNodeById: (id) => [NODE_A, NODE_B].find((n) => n.id === id) ?? null,

        getLayoutLinks: opts.wireLayoutLinks ? () => [EDGE_A_B] : undefined,
      })

      // The same handler shape the mount point installs on the surface.
      function onFocusBottleneck(target: BottleneckTarget): void {
        if (target.kind !== 'edge') return
        wiring?.focusOnEdge(target.from, target.to)
      }

      return () =>
        h(RealMetricsPanel, {
          phase: 'ready',
          metrics: METRICS,
          bottlenecks: makeBottlenecks(opts.target),
          onFocusBottleneck,
        })
    },
  })

  app.mount(host)
  if (wiring === null) throw new Error('view wiring did not initialise')

  return { host, app, wiring: wiring as ReturnType<typeof useAppViewWiring> }
}

function focusButton(host: HTMLElement): HTMLButtonElement {
  const btn = host.querySelector('.bnl__actions button')
  expect(btn).toBeTruthy()
  return btn as HTMLButtonElement
}

describe('analytics panel → camera', () => {
  it('clicking a bottleneck row frames that edge on the canvas', async () => {
    const { host, app, wiring } = mountPanelWiredToCamera({
      target: { kind: 'edge', from: 'A', to: 'B' },
      wireLayoutLinks: true,
    })
    await nextTick()

    // A known starting point, so "moved" is not confused with "was already there".
    wiring.camera.panX = 7
    wiring.camera.panY = 8
    wiring.camera.zoom = 1.25

    focusButton(host).click()
    await nextTick()

    // The framing the camera owns: the tight axis is Y, so zoom = 440 / 200.
    expect(wiring.camera.zoom).toBeCloseTo(2.2)
    expect(wiring.camera.panX).toBeCloseTo(60)
    expect(wiring.camera.panY).toBeCloseTo(-140)

    // And both ends of the edge really are on screen afterwards.
    const a = wiring.worldToScreen(NODE_A.__x, NODE_A.__y)
    const b = wiring.worldToScreen(NODE_B.__x, NODE_B.__y)
    expect(a.x).toBeCloseTo(280)
    expect(a.y).toBeCloseTo(80)
    expect(b.x).toBeCloseTo(720)
    expect(b.y).toBeCloseTo(520)

    app.unmount()
    host.remove()
  })

  it('leaves the camera alone for an edge the snapshot no longer has', async () => {
    // Both endpoints exist; the edge B → A does not (edge identity is directed).
    const { host, app, wiring } = mountPanelWiredToCamera({
      target: { kind: 'edge', from: 'B', to: 'A' },
      wireLayoutLinks: true,
    })
    await nextTick()

    wiring.camera.panX = 7
    wiring.camera.panY = 8
    wiring.camera.zoom = 1.25

    focusButton(host).click()
    await nextTick()

    expect(wiring.camera.panX).toBe(7)
    expect(wiring.camera.panY).toBe(8)
    expect(wiring.camera.zoom).toBe(1.25)

    app.unmount()
    host.remove()
  })

  it('without the layout-links dependency the row is a dead button — the regression this guards', async () => {
    const { host, app, wiring } = mountPanelWiredToCamera({
      target: { kind: 'edge', from: 'A', to: 'B' },
      wireLayoutLinks: false,
    })
    await nextTick()

    wiring.camera.panX = 7
    wiring.camera.panY = 8
    wiring.camera.zoom = 1.25

    focusButton(host).click()
    await nextTick()

    // Same click, same existing edge, nothing happens and nothing complains. This is what the
    // first case would silently degrade into if `getLayoutLinks` stopped being passed in.
    expect(wiring.camera.panX).toBe(7)
    expect(wiring.camera.panY).toBe(8)
    expect(wiring.camera.zoom).toBe(1.25)

    app.unmount()
    host.remove()
  })

  /**
   * The cases above prove the chain works when it is wired. This one proves the app actually
   * wires it, and it reads the source to do so.
   *
   * That is deliberate, and it is the weakest guard in this file. `getLayoutLinks` is an optional
   * dependency: dropping it from the real `useAppViewWiring` call in `useSimulatorApp` compiles,
   * type-checks, and leaves all 844 unit tests green, because `useSimulatorApp` is mocked wherever
   * the app is mounted and is never instantiated for real anywhere in the suite. The failure it
   * produces is the silent one this whole file exists to prevent — every bottleneck row becomes a
   * button that changes nothing, with no error and no log. Until something can instantiate the
   * real composable, a source assertion is the only thing standing between that regression and a
   * green build, so it is here rather than absent.
   */
  it('the app really passes the layout links into the view wiring', () => {
    // `new URL(rel, import.meta.url)` is not usable here: under happy-dom the global `URL`
    // resolves against the document origin, not the module, and yields an http: URL.
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(resolve(here, '../composables/useSimulatorApp.ts'), 'utf8')

    const callStart = source.indexOf('useAppViewWiring({')
    expect(callStart).toBeGreaterThan(-1)

    // Comments stripped first: a mention of the dependency in prose is not the dependency.
    const optionsBlock = source
      .slice(callStart, source.indexOf('\n  })', callStart))
      .split('\n')
      .filter((line) => !line.trim().startsWith('//'))
      .join('\n')

    // Sanity: we sliced the right call, not some other options object.
    expect(optionsBlock).toMatch(/\bgetLayoutNodeById\s*:/)
    expect(optionsBlock).toMatch(/\bgetLayoutLinks\s*:/)
  })
})
