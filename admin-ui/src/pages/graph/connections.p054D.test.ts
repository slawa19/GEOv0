// 054 wave D regressions for the graph drawer (D-2, D-3, D-4, D-5, D-18, D-19).
// Real composables and components; the API is mocked only at `src/api`. Each assertion states the intended behaviour.
import cytoscape, { type Core } from 'cytoscape'
import ElementPlus from 'element-plus'
import { flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({ graphSnapshot: vi.fn(), graphEgo: vi.fn() }))
vi.mock('../../api', () => ({ api: apiMock }))

import { setLocale } from '../../i18n'
import type { Participant, Trustline } from '../../types/domain'
import { useGraphVisualization, type SelectedInfo } from '../../composables/useGraphVisualization'
import { useGraphData } from '../../composables/useGraphData'
import { useGraphConnections, type ConnectionRow } from './useGraphConnections'
import { useGraphPageWatchers } from './useGraphPageWatchers'
import GraphAnalyticsDrawer from './GraphAnalyticsDrawer.vue'
import GraphConnectionsTable from './GraphConnectionsTable.vue'

afterEach(() => {
  setLocale('en')
})

function tl(from: string, to: string, status: string, used = '0'): Trustline {
  return { equivalent: 'UAH', from, to, limit: '100', used, available: String(100 - Number(used)), status, created_at: 't' }
}

function mountGraphWithConnections(initial: { participants: Participant[]; trustlines: Trustline[] }) {
  const selected = ref<SelectedInfo | null>(null)
  const participants = ref<Participant[]>(initial.participants)
  const trustlines = ref<Trustline[]>(initial.trustlines)
  const statusFilter = ref<string[]>(['active', 'closed'])
  const typeFilter = ref<string[]>([])
  const drawerOpen = ref(false)
  const filteredTrustlines = computed(() =>
    trustlines.value.filter((t) => statusFilter.value.includes(String(t.status))),
  )
  let graph!: ReturnType<typeof useGraphVisualization>
  let conns!: ReturnType<typeof useGraphConnections>
  const participantByPid = computed(() => new Map(participants.value.map((p) => [p.pid, p])))
  const wrapper = mount(defineComponent({
    setup() {
      graph = useGraphVisualization({
        cyRoot: ref(document.createElement('div')),
        createCy: () => cytoscape({ headless: true, styleEnabled: true, elements: [] }),
        threshold: ref('0.10'), typeFilter, minDegree: ref(0), hideIsolates: ref(false),
        participants, filteredTrustlines, selected, drawerOpen, drawerTab: ref('summary'),
        searchQuery: ref(''), focusPid: ref(''), focusMode: ref(false), focusRootPid: ref(''),
        focusDepth: ref<1 | 2>(1), setFocusRoot: () => undefined, showLabels: ref(true),
        labelModeBusiness: ref('name'), labelModePerson: ref('name'), autoLabelsByZoom: ref(false),
        minZoomLabelsAll: ref(1), minZoomLabelsPerson: ref(1), zoom: ref(1), layoutName: ref('grid'),
        layoutSpacing: ref(1), activeConnectionKey: ref(''), extractPidFromText: () => null,
      })
      conns = useGraphConnections({
        getCy: graph.getCy, participantByPid, selected, threshold: ref('0.10'), activeConnectionKey: ref(''),
        clearConnectionHighlight: () => undefined, highlightConnection: () => undefined, goToPid: () => undefined,
      })
      return () => h('div')
    },
  }))
  return { wrapper, graph, conns, selected, participants, trustlines, statusFilter, typeFilter, drawerOpen }
}

const ABC: Participant[] = [
  { pid: 'A', display_name: 'Alice', type: 'person', status: 'active' },
  { pid: 'B', display_name: 'Bob', type: 'person', status: 'active' },
  { pid: 'C', display_name: 'Carol', type: 'person', status: 'active' },
]

const DRAWER_STUBS = {
  'el-drawer': { template: '<section><slot /></section>' },
  'el-tabs': { template: '<div><slot /></div>' },
  'el-tab-pane': { props: ['name'], template: '<section :data-name="name"><slot /></section>' },
  'el-divider': { template: '<h4 class="divider"><slot /></h4>' },
  GraphConnectionsTable: {
    props: ['rows'],
    template: '<ul><li v-for="r in rows" :key="r.counterparty_pid">{{ r.counterparty_pid }} used {{ r.used }}</li></ul>',
  },
  'el-descriptions': { template: '<dl><slot /></dl>' },
  'el-descriptions-item': { props: ['label'], template: '<div class="desc" :data-label="label"><slot /></div>' },
  'el-select': { props: ['modelValue'], template: '<div class="select" :data-value="modelValue"><slot /></div>' },
  'el-option': { props: ['label', 'value'], template: '<span class="option" :data-value="value">{{ label }}</span>' },
  'el-table': { template: '<table><slot /></table>' },
  'el-table-column': { props: ['label'], template: '<th>{{ label }}</th>' },
  'el-button': true, 'el-card': true, 'el-alert': true, 'el-skeleton': true, TooltipLabel: true,
  CopyIconButton: true, 'el-empty': true, 'el-pagination': true,
}

function mountDrawer(props: Record<string, unknown>) {
  return mount(GraphAnalyticsDrawer, {
    props: {
      modelValue: true, tab: 'connections', eq: 'ALL', connectionsIncomingPage: 1, connectionsOutgoingPage: 1,
      selected: null, availableEquivalents: ['UAH'], money: (v: string) => v,
      metricsLoading: false, metricsError: null, selectedBalanceRows: [],
      selectedConnectionsIncoming: [], selectedConnectionsOutgoing: [],
      selectedConnectionsIncomingPaged: [], selectedConnectionsOutgoingPaged: [], connectionsPageSize: 10,
      ...props,
    },
    global: { stubs: DRAWER_STUBS },
  })
}

/** The heading of the connections section that lists `needle` (the nearest divider before its list). */
function sectionHeadingOf(drawer: ReturnType<typeof mountDrawer>, needle: string): string | null {
  let heading: string | null = null
  for (const el of drawer.get('section[data-name="connections"]').findAll('h4.divider, ul')) {
    if (el.element.tagName === 'H4') heading = el.text()
    else if (el.text().includes(needle)) return heading
  }
  return null
}

function connectionsDrawer(m: ReturnType<typeof mountGraphWithConnections>) {
  const inRows: ConnectionRow[] = m.conns.selectedConnectionsIncoming.value
  const outRows: ConnectionRow[] = m.conns.selectedConnectionsOutgoing.value
  return mountDrawer({
    selected: m.selected.value,
    selectedConnectionsIncoming: inRows, selectedConnectionsOutgoing: outRows,
    selectedConnectionsIncomingPaged: inRows, selectedConnectionsOutgoingPaged: outRows,
  })
}

describe('054 D-3: Connections rows follow the rebuilt graph Core', () => {
  it('a filter rebuild drops the rows of the edges it removed (same node re-opened)', async () => {
    const m = mountGraphWithConnections({ participants: ABC, trustlines: [tl('A', 'B', 'active'), tl('A', 'C', 'closed')] })
    expect(m.graph.initCy()).toBe(true)
    m.selected.value = { kind: 'node', pid: 'A', degree: 2, inDegree: 0, outDegree: 2 }
    const before = m.conns.selectedConnectionsOutgoing.value.map((r) => r.counterparty_pid)
    m.statusFilter.value = ['active']
    await nextTick()
    m.graph.rebuildGraph({ keepPositions: true })
    const cy = m.graph.getCy() as Core
    const coreOut = cy.getElementById('A').outgoers('edge').map((e) => String(e.data('target')))
    m.selected.value = { kind: 'node', pid: 'A', degree: 1, inDegree: 0, outDegree: 1 }
    await nextTick()
    const after = m.conns.selectedConnectionsOutgoing.value.map((r) => r.counterparty_pid)
    expect(before).toEqual(['B', 'C'])
    expect(coreOut).toEqual(['B'])
    expect(after).toEqual(coreOut)
  })

  it('a refresh rebuild shows the new amounts without any selection change', async () => {
    const m = mountGraphWithConnections({ participants: ABC, trustlines: [tl('A', 'B', 'active', '10')] })
    expect(m.graph.initCy()).toBe(true)
    m.selected.value = { kind: 'node', pid: 'A', degree: 1, inDegree: 0, outDegree: 1 }
    expect(m.conns.selectedConnectionsOutgoing.value.map((r) => r.used)).toEqual(['10'])
    m.trustlines.value = [tl('A', 'B', 'active', '40')]
    await nextTick()
    m.graph.rebuildGraph({ fit: false, preserveViewport: true })
    expect(m.conns.selectedConnectionsOutgoing.value.map((r) => r.used)).toEqual(['40'])
  })

  it('destroying the Core empties the rows and a new Core fills them again', async () => {
    const m = mountGraphWithConnections({ participants: ABC, trustlines: [tl('A', 'B', 'active', '10')] })
    expect(m.graph.initCy()).toBe(true)
    m.selected.value = { kind: 'node', pid: 'A', degree: 1, inDegree: 0, outDegree: 1 }
    expect(m.conns.selectedConnectionsOutgoing.value).toHaveLength(1)
    m.graph.destroyCy()
    expect(m.conns.selectedConnectionsOutgoing.value).toEqual([])
    expect(m.graph.initCy()).toBe(true)
    expect(m.conns.selectedConnectionsOutgoing.value.map((r) => r.counterparty_pid)).toEqual(['B'])
  })
})

describe('054 D-2: the Connections headings say who owes whom (from -> to = creditor -> debtor)', () => {
  // Line A -> B: A is the creditor, B owes A 30. Line C -> A: C is the creditor, A owes C 20.
  const lines = [tl('A', 'B', 'active', '30'), tl('C', 'A', 'active', '20')]

  it.each([
    ['en', 'Outgoing (owed to you)', 'Incoming (you owe)'],
    ['ru', 'Исходящие (вам должны)', 'Входящие (вы должны)'],
  ] as const)('%s: the debtor of a line of A is listed under "owed to you", the creditor under "you owe"', async (lang, owedToYou, youOwe) => {
    setLocale(lang)
    const m = mountGraphWithConnections({ participants: ABC, trustlines: lines })
    expect(m.graph.initCy()).toBe(true)
    m.selected.value = { kind: 'node', pid: 'A', degree: 2, inDegree: 1, outDegree: 1 }
    const drawer = connectionsDrawer(m)
    expect(sectionHeadingOf(drawer, 'B used 30')).toBe(owedToYou)
    expect(sectionHeadingOf(drawer, 'C used 20')).toBe(youOwe)
  })
})

describe('054 D-4: leaving focus after an equivalent switch', () => {
  const snap = (eq: string) => ({
    participants: [{ pid: 'A', display_name: 'A', type: 'person', status: 'active', viz_color_key: `net-${eq}` }],
    trustlines: [tl('A', 'B', 'active'), { ...tl('A', 'B', 'active'), equivalent: 'HOUR' }],
    equivalents: [{ code: 'UAH', precision: 2, is_active: true }, { code: 'HOUR', precision: 1, is_active: true }],
  })

  function mountFocusPage() {
    apiMock.graphSnapshot.mockImplementation(async ({ equivalent }: { equivalent?: string }) => snap(String(equivalent)))
    apiMock.graphEgo.mockImplementation(async ({ equivalent }: { equivalent?: string }) => snap(String(equivalent)))
    const eq = ref('UAH')
    const focusMode = ref(false)
    const focusRootPid = ref('')
    const focusDepth = ref<1 | 2>(1)
    const statusFilter = ref<string[]>(['active'])
    let data!: ReturnType<typeof useGraphData>
    mount(defineComponent({
      setup() {
        data = useGraphData({ eq, focusMode, focusRootPid, focusDepth, statusFilter })
        useGraphPageWatchers({
          eq, statusFilter, threshold: ref('0.10'), hideIsolates: ref(true), typeFilter: ref<string[]>([]),
          minDegree: ref(0), focusMode, focusDepth, focusRootPid,
          ensureFocusRootPid: () => { if (!focusRootPid.value) focusRootPid.value = 'A' },
          refreshForFocusMode: data.refreshForFocusMode, refreshSnapshotForEq: data.refreshSnapshotForEq,
          invalidateDataOwnership: data.invalidateDataOwnership, selected: ref<SelectedInfo | null>(null),
          showLabels: ref(true), labelModeBusiness: ref('name'), labelModePerson: ref('name'),
          autoLabelsByZoom: ref(true), minZoomLabelsAll: ref(1), minZoomLabelsPerson: ref(1),
          searchQuery: ref(''), focusPid: ref(''), zoom: ref(1), layoutName: ref('fcose'), layoutSpacing: ref(1),
          applyGraphView: () => true,
          graphViz: {
            rebuildGraph: () => undefined, runLayout: () => undefined, clearConnectionHighlight: () => undefined,
            applySelectedHighlight: () => undefined, applyStyle: () => undefined, updateLabelsForZoom: () => undefined,
            updateSearchHighlights: () => undefined, syncZoomFromControl: () => undefined,
          },
        })
        return () => h('div')
      },
    }))
    return { eq, focusMode, focusRootPid, data: () => data }
  }

  const vizKey = (data: ReturnType<typeof useGraphData>) =>
    (data.participants.value[0] as { viz_color_key?: string } | undefined)?.viz_color_key

  it('reads the snapshot of the current equivalent instead of restoring the old one', async () => {
    const page = mountFocusPage()
    const data = page.data()
    await data.loadData()
    page.focusRootPid.value = 'A'
    page.focusMode.value = true
    await flushPromises()
    await flushPromises()
    page.eq.value = 'HOUR'
    await flushPromises()
    await flushPromises()
    expect(vizKey(data)).toBe('net-HOUR')
    page.focusMode.value = false
    await flushPromises()
    await flushPromises()
    expect(apiMock.graphSnapshot.mock.calls.map((c) => c[0]?.equivalent)).toEqual(['UAH', 'HOUR'])
    expect(vizKey(data)).toBe('net-HOUR')
    expect(data.filteredTrustlines.value.map((t) => t.equivalent)).toEqual(['HOUR'])
  })

  it('control: without an equivalent switch it restores the kept snapshot and sends no request', async () => {
    const page = mountFocusPage()
    const data = page.data()
    await data.loadData()
    page.focusRootPid.value = 'A'
    page.focusMode.value = true
    await flushPromises()
    await flushPromises()
    expect(apiMock.graphEgo).toHaveBeenCalledTimes(1)
    page.focusMode.value = false
    await flushPromises()
    await flushPromises()
    expect(apiMock.graphSnapshot.mock.calls.map((c) => c[0]?.equivalent)).toEqual(['UAH'])
    expect(vizKey(data)).toBe('net-UAH')
  })
})

describe('054 D-5: the selection follows a rebuilt graph', () => {
  it('node details follow a refreshed snapshot', async () => {
    const m = mountGraphWithConnections({ participants: ABC, trustlines: [tl('A', 'B', 'active')] })
    expect(m.graph.initCy()).toBe(true)
    ;(m.graph.getCy() as Core).getElementById('A').emit('tap') // the real node tap handler fills `selected`
    await nextTick()
    expect(m.selected.value?.kind === 'node' && m.selected.value.status).toBe('active')
    m.participants.value = ABC.map((p) => (p.pid === 'A' ? { ...p, status: 'suspended' } : p))
    m.trustlines.value = [tl('A', 'B', 'active'), tl('C', 'A', 'active')]
    await nextTick()
    m.graph.rebuildGraph({ fit: false, preserveViewport: true })
    const coreStatus = String((m.graph.getCy() as Core).getElementById('A').data('status'))
    expect(coreStatus).toBe('suspended')
    expect(m.selected.value).toMatchObject({ kind: 'node', pid: 'A', status: coreStatus, degree: 2, inDegree: 1, outDegree: 1 })
  })

  it('a node the view no longer draws keeps the selection, with the data of the participant and no edges in view', async () => {
    const m = mountGraphWithConnections({ participants: ABC, trustlines: [tl('A', 'B', 'active')] })
    expect(m.graph.initCy()).toBe(true)
    ;(m.graph.getCy() as Core).getElementById('A').emit('tap')
    await nextTick()
    m.participants.value = ABC.map((p) => (p.pid === 'A' ? { ...p, status: 'suspended' } : p))
    m.typeFilter.value = ['business']
    await nextTick()
    m.graph.rebuildGraph({ keepPositions: true })
    expect((m.graph.getCy() as Core).getElementById('A').empty()).toBe(true)
    expect(m.selected.value).toMatchObject({ kind: 'node', pid: 'A', status: 'suspended', degree: 0, inDegree: 0, outDegree: 0 })
  })

  it('a participant gone from the data clears the selection and closes the drawer', async () => {
    const m = mountGraphWithConnections({ participants: ABC, trustlines: [tl('A', 'B', 'active')] })
    expect(m.graph.initCy()).toBe(true)
    ;(m.graph.getCy() as Core).getElementById('A').emit('tap')
    m.drawerOpen.value = true
    await nextTick()
    m.participants.value = ABC.filter((p) => p.pid !== 'A')
    m.trustlines.value = []
    await nextTick()
    m.graph.rebuildGraph({ fit: false, preserveViewport: true })
    expect(m.selected.value).toBeNull()
    expect(m.drawerOpen.value).toBe(false)
  })

  it('edge details follow a refreshed snapshot, and an edge gone from the view clears the selection', async () => {
    const m = mountGraphWithConnections({ participants: ABC, trustlines: [tl('A', 'B', 'active', '10')] })
    expect(m.graph.initCy()).toBe(true)
    ;(m.graph.getCy() as Core).edges().first().emit('tap')
    await nextTick()
    expect(m.selected.value).toMatchObject({ kind: 'edge', from: 'A', to: 'B', used: '10' })
    m.trustlines.value = [tl('C', 'B', 'active'), tl('A', 'B', 'active', '40')]
    await nextTick()
    m.graph.rebuildGraph({ fit: false, preserveViewport: true })
    expect(m.selected.value).toMatchObject({ kind: 'edge', from: 'A', to: 'B', used: '40', available: '60' })
    m.trustlines.value = [tl('C', 'B', 'active')]
    await nextTick()
    m.graph.rebuildGraph({ fit: false, preserveViewport: true })
    expect(m.selected.value).toBeNull()
  })
})

describe('054 D-18: the drawer prints labels, not raw codes', () => {
  it('node card: participant status and type in the operator language', () => {
    setLocale('ru')
    const drawer = mountDrawer({
      tab: 'summary',
      selected: { kind: 'node', pid: 'A', status: 'suspended', type: 'business', degree: 0, inDegree: 0, outDegree: 0 },
    })
    const items = drawer.findAll('.desc').map((d) => d.text())
    expect(items).toContain('заморожен')
    expect(items).toContain('организация')
    expect(items).not.toContain('suspended')
    expect(items).not.toContain('business')
  })

  it('balance columns are in Russian', () => {
    setLocale('ru')
    const drawer = mountDrawer({
      tab: 'balance',
      selected: { kind: 'node', pid: 'A', degree: 0, inDegree: 0, outDegree: 0 },
      selectedBalanceRows: [{
        equivalent: 'UAH', outgoing_limit: '1', outgoing_used: '1', incoming_limit: '1', incoming_used: '1',
        total_debt: '1', total_credit: '1', net: '0',
      }],
    })
    const headers = drawer.get('section[data-name="balance"]').findAll('th').map((th) => th.text())
    for (const raw of ['Out limit', 'Out used', 'In limit', 'In used']) expect(headers).not.toContain(raw)
    expect(headers).toEqual(expect.arrayContaining(['Лимит исходящих', 'Использовано исходящих', 'Лимит входящих', 'Использовано входящих']))
  })

  it('connections table: the trustline status in the operator language', async () => {
    setLocale('ru')
    const row: ConnectionRow = {
      direction: 'outgoing', counterparty_pid: 'B', counterparty_name: 'Bob', equivalent: 'UAH', status: 'closed',
      limit: '100', used: '0', available: '100', bottleneck: false,
    }
    const table = mount(GraphConnectionsTable, {
      props: { page: 1, rows: [row], pagedRows: [row], pageSize: 10, money: (v: string) => v },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()
    const cells = table.findAll('tbody tr td').map((td) => td.text().trim())
    expect(cells[0]).toContain('B')
    expect(cells).toContain('закрыт')
    expect(cells).not.toContain('closed')
  })
})

describe('054 D-19: the drawer equivalent can go back to all equivalents', () => {
  it('offers ALL next to the equivalents, also after one was picked', () => {
    const drawer = mountDrawer({
      tab: 'summary', eq: 'HOUR', availableEquivalents: ['EUR', 'HOUR'],
      selected: { kind: 'node', pid: 'A', degree: 0, inDegree: 0, outDegree: 0 },
    })
    const options = drawer.findAll('.select .option').map((o) => [o.attributes('data-value'), o.text()])
    expect(options).toEqual([['ALL', 'All equivalents'], ['EUR', 'EUR'], ['HOUR', 'HOUR']])
  })
})
