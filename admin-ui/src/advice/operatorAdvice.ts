import type { RouteLocationRaw } from 'vue-router'
import { toLocationQueryRaw } from '../router/query'

export type AdviceSeverity = 'info' | 'warning' | 'danger'

export type AdviceAction = {
  id: string
  labelKey: string
  to: RouteLocationRaw
}

export type AdviceItem = {
  id: string
  severity: AdviceSeverity
  titleKey: string
  bodyKey: string
  bodyVars?: Record<string, string | number>
  actions?: AdviceAction[]
}

export type TrustlineBottleneckSummary = {
  threshold: string
  total: number
  bottlenecks: number
}

export type GraphAdviceContext = {
  pid?: string | null
  eq?: string | null
  threshold: string

  concentration?: {
    outgoing: { levelType: 'success' | 'warning' | 'danger'; top1: number; top5: number; hhi: number }
    incoming: { levelType: 'success' | 'warning' | 'danger'; top1: number; top5: number; hhi: number }
  } | null

  capacity?: {
    outPct: number
    inPct: number
    bottlenecksCount: number
  } | null
}

export type LiquidityAdviceContext = {
  eq?: string | null
  threshold: string

  trustlinesTotal: number
  bottlenecks: number
  incidentsOverSla: number
}

function severityForCount(n: number): AdviceSeverity {
  if (n >= 10) return 'danger'
  if (n >= 3) return 'warning'
  return 'info'
}

function severityForPct(p: number): AdviceSeverity {
  if (p >= 0.98) return 'danger'
  if (p >= 0.9) return 'warning'
  return 'info'
}

export function buildTrustlinesAdvice(opts: {
  summary: TrustlineBottleneckSummary
  baseQuery: Record<string, unknown>
  equivalent?: string
  creditor?: string
  debtor?: string
}): AdviceItem[] {
  const { summary, baseQuery } = opts
  const baseQueryRaw = toLocationQueryRaw(baseQuery)

  const items: AdviceItem[] = []

  if (summary.bottlenecks >= 3) {
    items.push({
      id: 'TL_MANY_BOTTLENECKS',
      severity: severityForCount(summary.bottlenecks),
      titleKey: 'advice.tl.manyBottlenecks.title',
      bodyKey: 'advice.tl.manyBottlenecks.body',
      bodyVars: {
        n: summary.bottlenecks,
        total: summary.total,
        threshold: summary.threshold,
      },
      actions: [
        {
          id: 'openGraph',
          labelKey: 'advice.actions.openGraph',
          to: { path: '/graph', query: { ...baseQueryRaw, ...(opts.equivalent ? { equivalent: opts.equivalent } : {}), threshold: summary.threshold } },
        },
      ],
    })
  }

  if (opts.creditor && opts.debtor) {
    items.push({
      id: 'TL_EDGE_CONTEXT',
      severity: 'info',
      titleKey: 'advice.tl.edgeContext.title',
      bodyKey: 'advice.tl.edgeContext.body',
      bodyVars: { creditor: opts.creditor, debtor: opts.debtor },
      actions: [
        {
          id: 'openCreditor',
          labelKey: 'advice.actions.openCreditorParticipant',
          to: { path: '/participants', query: { ...baseQueryRaw, q: opts.creditor } },
        },
        {
          id: 'openDebtor',
          labelKey: 'advice.actions.openDebtorParticipant',
          to: { path: '/participants', query: { ...baseQueryRaw, q: opts.debtor } },
        },
      ],
    })
  }

  return items
}

export function buildGraphDrawerAdvice(opts: {
  ctx: GraphAdviceContext
  baseQuery: Record<string, unknown>
}): AdviceItem[] {
  const { ctx, baseQuery } = opts
  const baseQueryRaw = toLocationQueryRaw(baseQuery)
  const items: AdviceItem[] = []

  const pid = (ctx.pid || '').trim() || null
  const eq = (ctx.eq || '').trim() || null

  if (ctx.capacity && pid) {
    const { outPct, inPct, bottlenecksCount } = ctx.capacity

    if (bottlenecksCount > 0) {
      items.push({
        id: 'GRAPH_NODE_HAS_BOTTLENECKS',
        severity: bottlenecksCount >= 5 ? 'danger' : 'warning',
        titleKey: 'advice.graph.nodeBottlenecks.title',
        bodyKey: 'advice.graph.nodeBottlenecks.body',
        bodyVars: { n: bottlenecksCount },
        actions: [
          {
            id: 'trustlinesAsCreditor',
            labelKey: 'advice.actions.openTrustlinesAsCreditor',
            to: { path: '/trustlines', query: { ...baseQueryRaw, ...(eq ? { equivalent: eq } : {}), creditor: pid, threshold: ctx.threshold } },
          },
          {
            id: 'trustlinesAsDebtor',
            labelKey: 'advice.actions.openTrustlinesAsDebtor',
            to: { path: '/trustlines', query: { ...baseQueryRaw, ...(eq ? { equivalent: eq } : {}), debtor: pid, threshold: ctx.threshold } },
          },
        ],
      })
    }

    const outSev = severityForPct(outPct)
    const inSev = severityForPct(inPct)
    const maxSev: AdviceSeverity = outSev === 'danger' || inSev === 'danger' ? 'danger' : outSev === 'warning' || inSev === 'warning' ? 'warning' : 'info'

    if (maxSev !== 'info') {
      items.push({
        id: 'GRAPH_CAPACITY_NEAR_LIMIT',
        severity: maxSev,
        titleKey: 'advice.graph.capacityNearLimit.title',
        bodyKey: 'advice.graph.capacityNearLimit.body',
        bodyVars: {
          outPct: Math.round(outPct * 100),
          inPct: Math.round(inPct * 100),
        },
        actions: [
          {
            id: 'openAuditLog',
            labelKey: 'advice.actions.openAuditLog',
            to: { path: '/audit-log', query: { ...baseQueryRaw, q: pid } },
          },
        ],
      })
    }
  }

  if (ctx.concentration && pid) {
    const outType = ctx.concentration.outgoing.levelType
    const inType = ctx.concentration.incoming.levelType
    const level: AdviceSeverity = outType === 'danger' || inType === 'danger' ? 'danger' : outType === 'warning' || inType === 'warning' ? 'warning' : 'info'

    if (level !== 'info') {
      items.push({
        id: 'GRAPH_CONCENTRATION_HIGH',
        severity: level,
        titleKey: 'advice.graph.concentrationHigh.title',
        bodyKey: 'advice.graph.concentrationHigh.body',
        bodyVars: {
          outTop1: Math.round(ctx.concentration.outgoing.top1 * 100),
          inTop1: Math.round(ctx.concentration.incoming.top1 * 100),
        },
        actions: [
          {
            id: 'openTrustlines',
            labelKey: 'advice.actions.openTrustlines',
            to: { path: '/trustlines', query: { ...baseQueryRaw, ...(eq ? { equivalent: eq } : {}), creditor: pid, threshold: ctx.threshold } },
          },
        ],
      })
    }
  }

  return items
}

export function buildLiquidityAdvice(opts: {
  ctx: LiquidityAdviceContext
  baseQuery: Record<string, unknown>
}): AdviceItem[] {
  const { ctx, baseQuery } = opts
  const baseQueryRaw = toLocationQueryRaw(baseQuery)
  const items: AdviceItem[] = []

  const eq = (ctx.eq || '').trim() || null
  const thr = String(ctx.threshold || '').trim() || '0.10'

  if (ctx.bottlenecks >= 3) {
    items.push({
      id: 'LIQ_MANY_BOTTLENECKS',
      severity: severityForCount(ctx.bottlenecks),
      titleKey: 'advice.liq.manyBottlenecks.title',
      bodyKey: 'advice.liq.manyBottlenecks.body',
      bodyVars: { n: ctx.bottlenecks, total: ctx.trustlinesTotal, threshold: thr },
      actions: [
        {
          id: 'openTrustlines',
          labelKey: 'advice.actions.openTrustlines',
          to: { path: '/trustlines', query: { ...baseQueryRaw, ...(eq ? { equivalent: eq } : {}), threshold: thr } },
        },
        {
          id: 'openGraph',
          labelKey: 'advice.actions.openGraph',
          to: { path: '/graph', query: { ...baseQueryRaw, ...(eq ? { equivalent: eq } : {}), threshold: thr } },
        },
      ],
    })
  }

  if (ctx.incidentsOverSla > 0) {
    items.push({
      id: 'LIQ_INCIDENTS_OVER_SLA',
      severity: ctx.incidentsOverSla >= 5 ? 'danger' : 'warning',
      titleKey: 'advice.liq.incidentsOverSla.title',
      bodyKey: 'advice.liq.incidentsOverSla.body',
      bodyVars: { n: ctx.incidentsOverSla },
      actions: [
        {
          id: 'openIncidents',
          labelKey: 'advice.actions.openIncidents',
          to: { path: '/incidents', query: { ...baseQueryRaw } },
        },
      ],
    })
  }

  return items
}
