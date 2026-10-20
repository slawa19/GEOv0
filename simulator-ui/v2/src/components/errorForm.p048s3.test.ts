/**
 * 048 S3 (decision 3, «одна форма ошибки»): what the viewer is shown of a failure, in each place it is shown.
 *
 *   - the windows (payment, trust line, clearing) - their own error block: the sentence; the request id a line of its own
 *     and `data-request-id` (AGENTS.md section 12); the identifiers (HTTP status, code) the block's `title` only;
 *   - the run's error line (top bar) - the same form, and no «Error» / «Ошибка» word before the sentence;
 *   - a failed request is told by the KIND of the failure (`failureRunErrorLine`), never by the transport's English.
 *
 * Read structurally: the elements by test id, absence as "not in the DOM" (never by opacity). What this does NOT see:
 * whether a sentence reads well (the owner's look at the frames), the browser path end to end (`e2e/p048-s3-error-form.spec.ts`).
 */
import { computed, createApp, h, nextTick, reactive, ref, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ClearingPanel from './ClearingPanel.vue'
import ManualPaymentPanel from './ManualPaymentPanel.vue'
import TopBar from './TopBar.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'
import { ApiError } from '../api/http'
import type { RunStatus } from '../api/simulatorTypes'
import { useInteractMode } from '../composables/useInteractMode'
import { provideTopBarContext, type TopBarContext } from '../composables/useTopBarContext'
import { setUiLocale, type UiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import { failureRunErrorLine, renderRunErrorLine, runEndedByErrorLine, runErrorLine, runErrorLineRef, runErrorLineTitle, startRunErrorLine } from '../utils/runErrorClassification'

const PEOPLE = [
  { pid: 'alice', name: 'Алиса', type: 'person', status: 'active' },
  { pid: 'bob', name: 'Борис', type: 'person', status: 'active' },
]
/** What a screen must never show of a failure (decision 3): the word Error, an HTTP status, «Reference», a class name. */
const FORBIDDEN = /\bError\b|Ошибка|HTTP|Reference|ApiError|TypeError|\(ref:|req-/

afterEach(() => {
  setUiLocale('en')
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

async function settle() {
  for (let i = 0; i < 4; i += 1) await nextTick()
}

function mountWith(component: Component, props: Record<string, unknown>, ctx?: TopBarContext): HTMLElement {
  const host = document.createElement('div')
  document.body.appendChild(host)
  createApp({ setup() { if (ctx) provideTopBarContext(ctx); return () => h(component, props) } }).mount(host)
  return host
}

function failedState(phase: string, extra: Record<string, unknown> = {}) {
  return reactive({
    phase, fromPid: 'alice', toPid: 'bob', selectedEdgeKey: null, edgeAnchor: null, lastClearing: null,
    error: t('actionError.TRUSTLINE_EXISTS'), errorRef: 'req-7f3a', errorDetail: 'HTTP 409 TRUSTLINE_EXISTS', ...extra,
  })
}

const WINDOWS: Array<{ name: string; testid: string; mount: () => HTMLElement }> = [
  {
    name: 'payment', testid: 'manual-payment-error',
    mount: () => mountWith(ManualPaymentPanel, {
      phase: 'confirm-payment', state: failedState('confirm-payment'), unit: 'UAH', availableCapacity: '10.00',
      trustlinesLoading: false, paymentTargetsLoading: false, paymentTargetsLastError: null, paymentTargetsMaxHops: 6,
      paymentToTargetIds: new Set(['bob']), trustlines: [], participants: PEOPLE, busy: false, canSendPayment: true,
      trustlinesState: 'answered', confirmPayment: vi.fn(), cancel: vi.fn(), retryPayment: vi.fn(), dismissPaymentResult: vi.fn(),
      discardUnresolvedPayment: vi.fn(), setFromPid: vi.fn(), setToPid: vi.fn(),
    }),
  },
  {
    name: 'trust line', testid: 'trustline-error',
    mount: () => mountWith(TrustlineManagementPanel, {
      phase: 'confirm-trustline-create', state: failedState('confirm-trustline-create'), unit: 'UAH', used: '0', currentLimit: '0',
      available: '0', figuresSource: { kind: 'row' }, participants: PEOPLE, trustlines: [], busy: false,
      confirmTrustlineCreate: vi.fn(), confirmTrustlineUpdate: vi.fn(), confirmTrustlineClose: vi.fn(), cancel: vi.fn(),
      setFromPid: vi.fn(), setToPid: vi.fn(), selectTrustline: vi.fn(),
    }),
  },
  {
    name: 'clearing', testid: 'clearing-error',
    mount: () => mountWith(ClearingPanel, {
      phase: 'clearing-preview', busy: false, equivalent: 'UAH', participants: PEOPLE, confirmClearing: vi.fn(), cancel: vi.fn(),
      state: failedState('clearing-preview', {
        error: null, errorRef: null, errorDetail: null,
        clearingFailure: { text: t('actionError.CLEARING_FAILED'), ref: 'req-7f3a', detail: 'HTTP 409 CLEARING_FAILED' },
      }),
    }),
  },
]

describe('048 S3 / 049 S2: the window error block - the sentence, the request id in data-request-id only, the identifiers in the title', () => {
  for (const locale of ['en', 'ru'] as UiLocale[]) {
    it.each(WINDOWS.map((w) => [w.name, w] as const))(`%s [${locale}]`, async (_name, w) => {
      setUiLocale(locale)
      const host = w.mount()
      await settle()
      const block = host.querySelector<HTMLElement>(`[data-testid="${w.testid}"]`)
      expect(block, 'anti-vacuum: the window shows its failure').not.toBeNull()
      // (§12) the id: in `data-request-id`; 049 S2 (decision 7, reproducer (е)): no longer drawn - red before: the line
      // «request id: req-7f3a» under the sentence.
      expect(block!.getAttribute('data-request-id')).toBe('req-7f3a')
      expect(block!.querySelector('[data-testid="error-request-id"]'), 'no drawn id line').toBeNull()
      expect(block!.textContent, 'nothing of the id is drawn').not.toMatch(/req-/)
      // The sentence holds no id, no HTTP, no «Error», no class name (red before: "… (ref: req-7f3a)" glued in).
      const sentence = block!.querySelector('[data-testid="error-text"]')!.textContent!.trim()
      expect(sentence.length).toBeGreaterThan(5)
      expect(sentence).not.toMatch(FORBIDDEN)
      // The identifiers are the title - not drawn.
      expect(block!.getAttribute('title')).toMatch(/^HTTP 409 /)
      expect(block!.textContent).not.toContain('HTTP 409')
    })
  }

  it('counter-check: a failure without an id has no id line and no data-request-id', async () => {
    const host = mountWith(TrustlineManagementPanel, {
      phase: 'confirm-trustline-create', state: failedState('confirm-trustline-create', { errorRef: null, errorDetail: null }), unit: 'UAH',
      used: '0', currentLimit: '0', available: '0', figuresSource: { kind: 'row' }, participants: PEOPLE, trustlines: [], busy: false,
      confirmTrustlineCreate: vi.fn(), confirmTrustlineUpdate: vi.fn(), confirmTrustlineClose: vi.fn(), cancel: vi.fn(),
      setFromPid: vi.fn(), setToPid: vi.fn(), selectTrustline: vi.fn(),
    })
    await settle()
    const block = host.querySelector('[data-testid="trustline-error"]')!
    expect(block.hasAttribute('data-request-id')).toBe(false)
    expect(block.querySelector('[data-testid="error-request-id"]')).toBeNull()
    expect(block.hasAttribute('title')).toBe(false)
  })
})

describe('048 S3: a refused action writes the shown form into the window state (the composable)', () => {
  it('a 409 with X-Request-ID: the sentence, the id and the identifiers apart; nothing glued; one console line', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ code: 'TRUSTLINE_EXISTS', message: 'Trustline already exists' }),
      { status: 409, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-tl-1' } },
    )))
    try {
      const { useInteractActions } = await import('../composables/useInteractActions')
      const mode = useInteractMode({
        actions: useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('run-1') }),
        runId: computed(() => 'run-1'), equivalent: computed(() => 'UAH'), snapshot: ref(null),
      })
      mode.startTrustlineFlow()
      mode.selectNode('alice')
      mode.selectNode('bob')
      await mode.confirmTrustlineCreate('10')
      expect(mode.state.error).toBe(t('actionError.TRUSTLINE_EXISTS'))
      expect(mode.state.errorRef).toBe('req-tl-1')
      expect(mode.state.errorDetail).toBe('HTTP 409 TRUSTLINE_EXISTS')
      expect(mode.state.error).not.toMatch(FORBIDDEN)
      const lines = warn.mock.calls.filter((c) => c[0] === '[action] failed:')
      expect(lines).toEqual([['[action] failed:', 'HTTP 409 TRUSTLINE_EXISTS: Trustline already exists', '(request id: req-tl-1)']])
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

function topBarContext(over: Partial<TopBarContext>): TopBarContext {
  return {
    apiMode: ref('real'), isTestMode: ref(false), uiTheme: ref('saas'), loadingScenarios: ref(false), scenarios: ref([]),
    selectedScenarioId: ref(''), intensityPercent: ref(30), runId: ref('run-1'),
    runStatus: ref({ run_id: 'run-1', scenario_id: 'sc-1', state: 'running' } as RunStatus),
    sseState: ref('open'), lastError: ref(null), startRetryable: ref(false),
    runStats: ref({ startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} }),
    accessToken: ref(null),
    ...over,
  } as TopBarContext
}

describe('048 S3: the run error line - one sentence, no «Error» before it, the request id apart', () => {
  for (const locale of ['en', 'ru'] as UiLocale[]) {
    it(`a failed request [${locale}]`, async () => {
      setUiLocale(locale)
      const line = failureRunErrorLine(new ApiError('HTTP 500 Internal Server Error for /simulator/runs/r1', { status: 500, requestId: 'req-run-9' }))
      const host = mountWith(TopBar, { detailsOpen: false }, topBarContext({
        lastError: computed(() => renderRunErrorLine(line, locale)),
        lastErrorTitle: computed(() => runErrorLineTitle(line)),
        lastErrorRef: computed(() => runErrorLineRef(line)),
      }))
      await settle()
      const el = host.querySelector<HTMLElement>('[data-testid="top-error"]')!
      expect(el, 'anti-vacuum: the line is shown').not.toBeNull()
      expect(el.getAttribute('data-request-id')).toBe('req-run-9')
      // 049 S2 (decision 7): the id is not drawn (red before: «request id: req-run-9» under the sentence).
      expect(el.querySelector('[data-testid="error-request-id"]')).toBeNull()
      expect(el.textContent).not.toMatch(/req-/)
      const sentence = el.querySelector('[data-testid="error-text"]')!
      expect(sentence.textContent!.trim()).toBe(t('failure.serverFailed', {}, locale))
      expect(sentence.getAttribute('title')).toBe('HTTP 500')
      // Nothing drawn but the icon and the sentence: no «Error» label (red before: `.ds-label` «Error»), no id line (049 S2).
      expect(el.querySelector('.ds-label')).toBeNull()
      const drawn = [...el.querySelectorAll('span')].filter((s) => !s.querySelector('span')).map((s) => s.textContent!.trim()).filter(Boolean)
      expect(drawn).toEqual(['✕', t('failure.serverFailed', {}, locale)])
    })

    // 049 S2 (decision 7, reproducer (е)): a code of the run is no longer printed in brackets - it is the title and the
    // console line. Red before: «The run stopped with an error (REAL_MODE_TICK_FAILED). …».
    it(`a run's code is not drawn in brackets, it is the title [${locale}]`, async () => {
      setUiLocale(locale)
      for (const line of [
        runEndedByErrorLine({ code: 'REAL_MODE_TICK_FAILED', message: 'tick failed', at: '2026-10-20T00:00:00Z' }),
        runErrorLine({ code: 'INTERNAL_ERROR', message: 'boom', at: '2026-10-20T00:00:00Z' }),
      ]) {
        const host = mountWith(TopBar, { detailsOpen: false }, topBarContext({
          lastError: computed(() => renderRunErrorLine(line, locale)),
          lastErrorTitle: computed(() => runErrorLineTitle(line)),
          lastErrorRef: computed(() => runErrorLineRef(line)),
        }))
        await settle()
        const el = host.querySelector<HTMLElement>('[data-testid="top-error"]')!
        const sentence = el.querySelector('[data-testid="error-text"]')!
        const code = line.kind === 'code' ? line.code : ''
        expect(code, 'anti-vacuum: the line carries a code').not.toBe('')
        expect(el.textContent, `${code} is not drawn`).not.toContain(code)
        expect(el.textContent).not.toMatch(/\(\s*[A-Z_]{3,}\s*\)/)
        expect(sentence.getAttribute('title'), 'the code is the title').toContain(code)
        host.remove()
      }
    })
  }

  it('counter-check: a line without an id - no id line, no data-request-id', async () => {
    const host = mountWith(TopBar, { detailsOpen: false }, topBarContext({ lastError: ref(t('runLine.stopped')), lastErrorRef: ref(null) }))
    await settle()
    const el = host.querySelector('[data-testid="top-error"]')!
    expect(el.hasAttribute('data-request-id')).toBe(false)
    expect(el.querySelector('[data-testid="error-request-id"]')).toBeNull()
  })
})

describe('048 S3: a failed request is told by its kind - the sentence of the dictionary, the identifiers apart', () => {
  const cases: Array<[string, unknown, string]> = [
    ['no answer in time', new ApiError('GET /x -> timeout after 8000ms', { status: 0, code: 'TIMEOUT' }), 'failure.noAnswer'],
    ['no connection (fetch rejects)', new TypeError('Failed to fetch'), 'failure.noConnection'],
    ['403', new ApiError('HTTP 403 Forbidden for /simulator/runs/r1', { status: 403, requestId: 'req-403' }), 'failure.forbidden'],
    ['404', new ApiError('HTTP 404 Not Found for /x', { status: 404 }), 'failure.notFound'],
    ['500', new ApiError('HTTP 500 Internal Server Error for /x', { status: 500 }), 'failure.serverFailed'],
    ['409 of another kind', new ApiError('HTTP 409 Conflict for /simulator/runs', { status: 409 }), 'failure.serverFailed'],
    ['a check of the client', new Error('Replay recovery snapshot does not match the active run/equivalent context'), 'failure.viewFailed'],
  ]
  it.each(cases)('%s', (_name, e, key) => {
    const line = failureRunErrorLine(e)
    expect(line).toMatchObject({ kind: 'dict', key })
    for (const locale of ['en', 'ru'] as UiLocale[]) expect(renderRunErrorLine(line, locale)).not.toMatch(FORBIDDEN)
  })
  it('the broken event stream has its own sentence; the id and the identifiers stay apart', () => {
    expect(failureRunErrorLine(new Error('SSE replay recovery buffer exceeded 512 messages'), { client: 'stream' })).toMatchObject({ key: 'failure.streamFailed' })
    const line = failureRunErrorLine(new ApiError('HTTP 403 Forbidden for /simulator/runs/r1', { status: 403, requestId: 'req-403' }))
    expect([runErrorLineRef(line), runErrorLineTitle(line)]).toEqual(['req-403', 'HTTP 403'])
  })
})

describe('048 S3 (adversarial pass F3, F5): the start refusal carries its id; no class name in a title', () => {
  it('a start refused by the limit of runs (409 global_active_limit): the line keeps the request id of its answer', () => {
    const body = (max?: number) => JSON.stringify({ error: { code: 'E008', message: 'x', details: { conflict_kind: 'global_active_limit', ...(max ? { max_active_runs: max } : {}) } } })
    for (const max of [undefined, 1, 10]) {
      const line = startRunErrorLine(new ApiError('HTTP 409 Conflict for /simulator/runs', { status: 409, bodyText: body(max), requestId: 'req-409' }))
      expect(runErrorLineRef(line), `max ${max}`).toBe('req-409')
    }
    expect(runErrorLineRef(startRunErrorLine(new ApiError('HTTP 409', { status: 409, bodyText: body(10) }))), 'counter-check: no id, none').toBeNull()
  })
  it('a failure that is not an answer has no title (its class name is the console line only)', () => {
    const line = failureRunErrorLine(new TypeError('Failed to fetch'))
    expect(runErrorLineTitle(line)).toBeNull()
    expect(line).toMatchObject({ key: 'failure.noConnection', log: 'TypeError: Failed to fetch' })
  })
})

/**
 * §15 Codex on S3 (2026-10-19), items 1, 2, 4: the id of a failed trust-lines read reaches its notices; the window error
 * block is announced (role="alert") and said once; a scene-load failure writes its console line.
 */
describe('048 S3 §15 item 1: a failed trust-lines read keeps its request id up to the notices', () => {
  it('the read answers 500 with X-Request-ID: the source state carries the id; a frozen copy keeps it', async () => {
    const { useInteractActions } = await import('../composables/useInteractActions')
    const { freezeTrustlineFiguresSource, trustlineFiguresNoticeRef } = await import('../composables/interact/trustlinesSourceState')
    vi.stubGlobal('fetch', vi.fn(async (url: string) => String(url).includes('trustlines-list')
      ? new Response(JSON.stringify({ code: 'E000', message: 'down' }), { status: 500, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-tl-list' } })
      : new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    try {
      const mode = useInteractMode({
        actions: useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('run-1') }),
        runId: computed(() => 'run-1'), equivalent: computed(() => 'UAH'), snapshot: ref(null),
      })
      mode.selectEdge('alice->bob')
      await vi.waitFor(() => expect(mode.trustlinesFetchState.value.kind).toBe('failed'))
      const state = mode.trustlinesFetchState.value as { kind: 'failed'; message: string; ref?: string | null }
      expect(state.ref).toBe('req-tl-list')
      expect(state.message).not.toContain('req-tl-list')
      expect(trustlineFiguresNoticeRef(state)).toBe('req-tl-list')
      expect(trustlineFiguresNoticeRef(freezeTrustlineFiguresSource(state)), 'a frozen copy keeps the id').toBe('req-tl-list')
      expect(trustlineFiguresNoticeRef({ kind: 'row' }), 'counter-check: an answer has no failure id').toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('the trust line window and the edge popup keep the id in data-request-id, not as a drawn line (049 S2)', async () => {
    const EdgeDetailPopup = (await import('./EdgeDetailPopup.vue')).default
    const failed = { kind: 'failed' as const, message: 'Trust lines could not be loaded.', ref: 'req-tl-list' }
    const tl = mountWith(TrustlineManagementPanel, {
      phase: 'editing-trustline', state: failedState('editing-trustline', { error: null, errorRef: null, errorDetail: null }), unit: 'UAH',
      used: '0', currentLimit: '0', available: '0', figuresSource: failed, participants: PEOPLE, trustlines: [], busy: false,
      confirmTrustlineCreate: vi.fn(), confirmTrustlineUpdate: vi.fn(), confirmTrustlineClose: vi.fn(), cancel: vi.fn(),
      setFromPid: vi.fn(), setToPid: vi.fn(), selectTrustline: vi.fn(),
    })
    const edge = mountWith(EdgeDetailPopup, {
      phase: 'editing-trustline', state: failedState('editing-trustline', { error: null }), unit: 'UAH', figuresSource: failed, close: vi.fn(),
    })
    await settle()
    for (const [host, id] of [[tl, 'tl-source-unavailable'], [edge, 'edge-source-unavailable']] as const) {
      const notice = host.querySelector(`[data-testid="${id}"]`)
      expect(notice, `${id}: anti-vacuum, the notice is shown`).not.toBeNull()
      expect(notice!.getAttribute('data-request-id'), id).toBe('req-tl-list')
      expect(notice!.querySelector('[data-testid="error-request-id"]'), id).toBeNull()
      expect(notice!.textContent, id).not.toMatch(/req-/)
    }
  })
})

describe('048 S3 §15 item 2: the window error block is announced once', () => {
  it.each(WINDOWS.map((w) => [w.name, w] as const))('%s: role="alert", atomic, no live region around it', async (_name, w) => {
    const host = w.mount()
    await settle()
    const block = host.querySelector<HTMLElement>(`[data-testid="${w.testid}"]`)!
    expect(block.getAttribute('role')).toBe('alert')
    expect(block.getAttribute('aria-atomic')).toBe('true')
    for (let el = block.parentElement; el; el = el.parentElement) {
      const live = el.getAttribute('aria-live') ?? (['alert', 'status', 'log'].includes(el.getAttribute('role') ?? '') ? 'role' : null)
      expect(live, `an ancestor that is a live region would announce it twice: ${el.outerHTML.slice(0, 80)}`).toBeNull()
    }
  })
})

describe('048 S3 §15 item 4: a failed scene load writes its console line', () => {
  it('one line with the technical text and the request id per failed load', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const { useSceneState } = await import('../composables/useSceneState')
    const state = reactive({ loading: false, error: '', errorLine: null, sourcePath: '', snapshot: null, selectedNodeId: null as string | null })
    const noop = () => undefined
    const s = useSceneState({
      eq: ref('UAH'), layoutMode: ref<'admin-force'>('admin-force'), allowEqDeepLink: () => true, isEqAllowed: () => true,
      effectiveEq: computed(() => 'UAH'), state: state as never,
      loadSnapshot: async () => { throw new ApiError('HTTP 503 Service Unavailable for /simulator/x/graph/preview', { status: 503, requestId: 'req-scene-1' }) },
      clearScheduledTimeouts: noop, resetCamera: noop, resetLayoutKeyCache: noop, resetOverlays: noop, resizeAndLayout: noop,
      ensureRenderLoop: noop, setupResizeListener: noop, teardownResizeListener: noop, stopRenderLoop: noop,
    })
    await s.loadScene()
    const lines = warn.mock.calls.filter((c) => c[0] === '[scene] load failed:')
    expect(lines).toEqual([['[scene] load failed:', 'HTTP 503 Service Unavailable for /simulator/x/graph/preview', '(request id: req-scene-1)']])
    expect(state.error, 'the sentence is drawn, not the technical text').toBe(t('failure.serverFailed'))
  })
})
