import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import type { Core } from 'cytoscape'

import type { SelectedInfo } from '../../composables/useGraphVisualization'
import { isRatioBelowThreshold } from '../../utils/decimal'

type ParticipantLike = { display_name?: string | null }

type Direction = 'incoming' | 'outgoing'

export type ConnectionRow = {
  direction: Direction
  counterparty_pid: string
  counterparty_name: string
  equivalent: string
  status: string
  limit: string
  used: string
  available: string
  bottleneck: boolean
}

function normEq(v: string): string {
  return String(v || '').trim().toUpperCase()
}

function isBottleneck(args: { available: string; limit: string; threshold: string }): boolean {
  return isRatioBelowThreshold({ numerator: args.available, denominator: args.limit, threshold: args.threshold })
}

function connectionRowsFromCy(args: {
  pid: string
  cy: Core
  participantByPid: Map<string, ParticipantLike>
  threshold: string
}): { incoming: ConnectionRow[]; outgoing: ConnectionRow[] } {
  const incoming: ConnectionRow[] = []
  const outgoing: ConnectionRow[] = []

  args.cy.edges().forEach((e) => {
    const from = String(e.data('source') || '')
    const to = String(e.data('target') || '')
    if (from !== args.pid && to !== args.pid) return

    const eqCode = normEq(String(e.data('equivalent') || ''))
    const status = String(e.data('status') || '')
    const limit = String(e.data('limit') || '')
    const used = String(e.data('used') || '')
    const available = String(e.data('available') || '')

    const isOut = from === args.pid
    const cp = isOut ? to : from
    const p = args.participantByPid.get(cp)
    const name = String(p?.display_name || '').trim()

    const row: ConnectionRow = {
      direction: isOut ? 'outgoing' : 'incoming',
      counterparty_pid: cp,
      counterparty_name: name,
      equivalent: eqCode,
      status,
      limit,
      used,
      available,
      bottleneck: status === 'active' && isBottleneck({ available, limit, threshold: args.threshold }),
    }

    if (isOut) outgoing.push(row)
    else incoming.push(row)
  })

  const sortKey = (r: ConnectionRow) => `${r.equivalent}|${r.bottleneck ? '0' : '1'}|${r.counterparty_pid}`
  incoming.sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
  outgoing.sort((a, b) => sortKey(a).localeCompare(sortKey(b)))
  return { incoming, outgoing }
}

function pageSlice<T>(items: T[], page: number, pageSize: number): T[] {
  const p = Math.max(1, Math.floor(Number(page) || 1))
  const s = Math.max(1, Math.floor(Number(pageSize) || 25))
  const start = (p - 1) * s
  return (items || []).slice(start, start + s)
}

export function useGraphConnections(opts: {
  getCy: () => Core | null
  participantByPid: ComputedRef<Map<string, ParticipantLike>>
  selected: Ref<SelectedInfo | null>
  threshold: Ref<string>
  activeConnectionKey: Ref<string>

  clearConnectionHighlight: () => void
  highlightConnection: (fromPid: string, toPid: string, eqCode: string) => void
  goToPid: (pid: string) => void
}) {
  const selectedPid = computed(() => {
    if (!opts.selected.value || opts.selected.value.kind !== 'node') return ''
    return opts.selected.value.pid
  })

  const selectedConnectionsIncoming = computed<ConnectionRow[]>(() => {
    const pid = selectedPid.value
    if (!pid) return []
    const cy = opts.getCy()
    if (!cy) return []
    return connectionRowsFromCy({
      pid,
      cy,
      participantByPid: opts.participantByPid.value,
      threshold: opts.threshold.value,
    }).incoming
  })

  const selectedConnectionsOutgoing = computed<ConnectionRow[]>(() => {
    const pid = selectedPid.value
    if (!pid) return []
    const cy = opts.getCy()
    if (!cy) return []
    return connectionRowsFromCy({
      pid,
      cy,
      participantByPid: opts.participantByPid.value,
      threshold: opts.threshold.value,
    }).outgoing
  })

  const connectionsPageSize = ref<number>(25)
  const connectionsIncomingPage = ref<number>(1)
  const connectionsOutgoingPage = ref<number>(1)

  const selectedConnectionsIncomingPaged = computed(() =>
    pageSlice(selectedConnectionsIncoming.value, connectionsIncomingPage.value, connectionsPageSize.value)
  )
  const selectedConnectionsOutgoingPaged = computed(() =>
    pageSlice(selectedConnectionsOutgoing.value, connectionsOutgoingPage.value, connectionsPageSize.value)
  )

  watch(selectedPid, () => {
    connectionsIncomingPage.value = 1
    connectionsOutgoingPage.value = 1
  })

  watch(
    () => selectedConnectionsIncoming.value.length,
    (n) => {
      const maxPage = Math.max(1, Math.ceil(n / Math.max(1, connectionsPageSize.value)))
      if (connectionsIncomingPage.value > maxPage) connectionsIncomingPage.value = 1
    }
  )

  watch(
    () => selectedConnectionsOutgoing.value.length,
    (n) => {
      const maxPage = Math.max(1, Math.ceil(n / Math.max(1, connectionsPageSize.value)))
      if (connectionsOutgoingPage.value > maxPage) connectionsOutgoingPage.value = 1
    }
  )

  function onConnectionRowClick(row: ConnectionRow) {
    if (opts.selected.value && opts.selected.value.kind === 'node') {
      const basePid = opts.selected.value.pid
      const cp = String(row.counterparty_pid || '').trim()
      const eqCode = normEq(row.equivalent)

      const fromPid = row.direction === 'outgoing' ? basePid : cp
      const toPid = row.direction === 'outgoing' ? cp : basePid
      const key = `${eqCode}|${fromPid}->${toPid}`

      if (opts.activeConnectionKey.value === key) {
        opts.clearConnectionHighlight()
      } else {
        opts.clearConnectionHighlight()
        opts.activeConnectionKey.value = key
        opts.highlightConnection(fromPid, toPid, eqCode)
      }
    }

    opts.goToPid(row.counterparty_pid)
  }

  return {
    selectedConnectionsIncoming,
    selectedConnectionsOutgoing,
    selectedConnectionsIncomingPaged,
    selectedConnectionsOutgoingPaged,

    connectionsPageSize,
    connectionsIncomingPage,
    connectionsOutgoingPage,

    onConnectionRowClick,
  }
}
