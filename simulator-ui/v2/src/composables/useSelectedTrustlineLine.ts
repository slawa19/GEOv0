import { computed, type ComputedRef } from 'vue'

import type { TrustlineInfo } from '../api/simulatorTypes'
import type { GraphLink } from '../types'
import {
  freezeTrustlineFiguresSource,
  resolveTrustlineFiguresSource,
  type TrustlineFiguresSource,
  type TrustlinesFetchState,
} from './interact/trustlinesSourceState'

/**
 * 034 S5b, F-034-16: the selected trustline line and the BASIS of its figures, moved out of `SimulatorAppRoot.vue`
 * (`interactSelectedLink`, `interactSelectedLinkFiguresSource`, `wmEdgeDetailFiguresSource`) so that the rule can be
 * tested without a mount. It is a MOVE: the six states of the basis (`trustlinesSourceState.ts`) and the ban on
 * mutating by unconfirmed figures are kept as they were; nothing is replaced by "snapshot or REST".
 *
 * Why the decision lives here and the state of the source in the cache (`useInteractDataCache`): only the cache
 * knows the life cycle of the request (`trustlinesFetchState`, `findAnsweredTrustline` that looks at the UNMERGED
 * answer); which pair is selected and which surface is being answered is known only to the one who holds both - and
 * a component sees only the merged list, in which a row taken from the snapshot is indistinguishable from a row the
 * backend answered. That is why the basis is handed to the components as a prop.
 */

export type SelectedLineInputs = {
  fromPid: string | null
  toPid: string | null
  /** The MERGED list of the cache (the answer, else the snapshot-derived rows). */
  trustlines: TrustlineInfo[] | null | undefined
  /** Links of the snapshot on screen (may be stale, but always available in fixtures / topology-only views). */
  snapshotLinks: GraphLink[] | null | undefined
}

/**
 * The line whose figures are SHOWN. It prefers the REST row only when the list is non-empty, otherwise it silently
 * takes the snapshot: fine for viewing, never for a mutating control (`F-013-7`) - that decision is
 * `selectedTrustlineFiguresSource`.
 */
export function selectedTrustlineLink(i: SelectedLineInputs): GraphLink | null {
  const from = i.fromPid
  const to = i.toPid
  if (!from || !to) return null

  // NEW-3: prefer backend-fetched trustlines (Interact Mode cache) as the source of truth.
  // This keeps EdgeDetailPopup and TrustlineManagementPanel consistent.
  const tls = i.trustlines
  if (Array.isArray(tls) && tls.length > 0) {
    const tl = tls.find((t) => t.from_pid === from && t.to_pid === to) ?? null
    if (tl) {
      return {
        source: from,
        target: to,
        trust_limit: tl.limit,
        used: tl.used,
        reverse_used: tl.reverse_used,
        available: tl.available,
        status: tl.status ?? undefined,
        close_requested_at: tl.close_requested_at ?? null,
      }
    }
  }

  // Fallback: snapshot link (may be stale, but always available in fixtures/topology-only views).
  for (const l of i.snapshotLinks ?? []) {
    if (l.source === from && l.target === to) return l
  }
  return null
}

/**
 * ЧЕМ ОБОСНОВАНЫ числа выбранной линии (`F-013-7`, расширено 2026-09-10): `row` / `no-row` / `never-asked` /
 * `loading` / `failed`. `answeredRow` обязан быть строкой из НЕСЛИТОГО ответа источника
 * (`findAnsweredTrustline`), а не из слитого списка, иначе строка снапшота сошла бы за ответ бэкенда.
 */
export function selectedTrustlineFiguresSource(i: {
  fetchState: TrustlinesFetchState | null | undefined
  answeredRow: TrustlineInfo | null
}): TrustlineFiguresSource {
  return resolveTrustlineFiguresSource(i.fetchState, i.answeredRow != null)
}

/**
 * То же для окна edge-detail, с одной поправкой: в режиме `keepAlive` попап показывает ЗАМОРОЖЕННУЮ линию -
 * снимок, снятый до того, как interact-состояние ушло на ДРУГУЮ пару, поэтому живое состояние источника о ней
 * ничего не знает. Замораживается ОСНОВАНИЕ ВМЕСТЕ С ЧИСЛАМИ (внешнее ревью 013, P3): `frozen` получается только
 * из того, что действительно БЫЛО ответом (`freezeTrustlineFiguresSource`); молчание остаётся молчанием.
 */
export function edgeDetailFiguresSource(
  live: TrustlineFiguresSource,
  wm: { state: string; frozenLink: GraphLink | null; frozenFiguresSource: TrustlineFiguresSource | null },
): TrustlineFiguresSource {
  if (wm.state === 'keepAlive' && wm.frozenLink != null) {
    return freezeTrustlineFiguresSource(wm.frozenFiguresSource)
  }
  return live
}

export type SelectedLineDeps = {
  fromPid: () => string | null
  toPid: () => string | null
  trustlines: () => TrustlineInfo[] | null | undefined
  fetchState: () => TrustlinesFetchState | null | undefined
  findAnsweredTrustline: (from: string | null, to: string | null) => TrustlineInfo | null
  snapshotLinks: () => GraphLink[] | null | undefined
  windowState: () => string
  frozenLink: () => GraphLink | null
  frozenFiguresSource: () => TrustlineFiguresSource | null
}

export function useSelectedTrustlineLine(deps: SelectedLineDeps): {
  selectedLink: ComputedRef<GraphLink | null>
  figuresSource: ComputedRef<TrustlineFiguresSource>
  edgeDetailFiguresSource: ComputedRef<TrustlineFiguresSource>
} {
  const selectedLink = computed(() =>
    selectedTrustlineLink({
      fromPid: deps.fromPid(),
      toPid: deps.toPid(),
      trustlines: deps.trustlines(),
      snapshotLinks: deps.snapshotLinks(),
    }),
  )
  const figuresSource = computed(() =>
    selectedTrustlineFiguresSource({
      fetchState: deps.fetchState(),
      answeredRow: deps.findAnsweredTrustline(deps.fromPid(), deps.toPid()),
    }),
  )
  const edgeDetail = computed(() =>
    edgeDetailFiguresSource(figuresSource.value, {
      state: deps.windowState(),
      frozenLink: deps.frozenLink(),
      frozenFiguresSource: deps.frozenFiguresSource(),
    }),
  )
  return { selectedLink, figuresSource, edgeDetailFiguresSource: edgeDetail }
}
