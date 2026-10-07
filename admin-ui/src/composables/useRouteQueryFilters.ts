import { watch, type Ref } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

import { readQueryString, toLocationQueryRaw } from '../router/query'
import { useRouteHydrationGuard } from './useRouteHydrationGuard'

export type QueryFilter = {
  model: Ref<string>
  /** Wire -> model. Default: trimmed. */
  fromQuery?: (raw: string) => string
  /** Model -> wire; `''` leaves the key out of the URL. Default: trimmed. */
  toQuery?: (value: string) => string
  /**
   * `true`: a route that does not carry the key leaves the filter as it is (the page chose a value itself, e.g. the
   * first equivalent of the graph). Default `false`: no key means the value of `fromQuery('')`.
   */
  keepWhenAbsent?: boolean
  /** `false`: a knob that only changes how the loaded data is shown; moving it never reports a change. Default `true`. */
  reloads?: boolean
}

/**
 * Page filters <-> route query (032 S7, D-10), once instead of five hand-written copies. The URL is how a screen is
 * linked to (`/participants?status=suspended`) and how a filter survives a reload.
 *
 * - `applyRoute()` moves the filters to what the route says (on mount, and whenever the route's own filter keys
 *   change). It returns whether a filter that `reloads` moved. While it applies, the filter watchers stay quiet: they
 *   cannot tell the route's update from the operator's edit otherwise (`useRouteHydrationGuard`).
 * - An edit of a filter is written to the query with `router.replace`, keeping the other keys, only while the page is
 *   the current route (a late edit must not navigate away from the page the operator has moved to), and only when the
 *   URL would change.
 * - The route is never allowed to rewrite what the operator is typing: a filter whose wire form already equals the
 *   route's is left alone (`two ` and the echoed `two` are the same filter), so a trailing space survives.
 *
 * `onRouteChange` / `onUserChange` say that a filter that `reloads` was moved by the route / by the operator; the page
 * decides what that costs (a request now, a debounced request, nothing).
 */
export function useRouteQueryFilters(options: {
  route: RouteLocationNormalizedLoaded
  router: Router
  path: string
  filters: Record<string, QueryFilter>
  onRouteChange?: () => void
  onUserChange?: () => void
}) {
  const { route, router } = options
  const entries = Object.entries(options.filters).map(([key, filter]) => ({
    key,
    model: filter.model,
    fromQuery: filter.fromQuery ?? ((raw: string) => raw.trim()),
    toQuery: filter.toQuery ?? ((value: string) => value.trim()),
    keepWhenAbsent: filter.keepWhenAbsent ?? false,
    reloads: filter.reloads ?? true,
  }))

  const { isApplying, isActive, run } = useRouteHydrationGuard(route, options.path)

  function applyRoute(): boolean {
    const reloadingChange = run(() => {
      let moved = false
      for (const f of entries) {
        const wire = readQueryString(route.query[f.key])
        if (f.keepWhenAbsent && wire === '') continue
        const next = f.fromQuery(wire)
        if (f.model.value === next || f.toQuery(f.model.value) === wire) continue
        f.model.value = next
        if (f.reloads) moved = true
      }
      return moved
    })
    return Boolean(reloadingChange)
  }

  function syncToRoute() {
    // After the operator has left the page the route is already the next page's: do not navigate it.
    if (!isActive.value) return
    const query: Record<string, unknown> = { ...route.query }
    let differs = false
    for (const f of entries) {
      const wire = f.toQuery(f.model.value)
      if (wire !== '') query[f.key] = wire
      else delete query[f.key]
      if (readQueryString(route.query[f.key]) !== wire) differs = true
    }
    if (differs) void router.replace({ query: toLocationQueryRaw(query) })
  }

  watch(
    () => entries.map((f) => route.query[f.key]),
    () => {
      if (applyRoute()) options.onRouteChange?.()
    },
  )

  for (const f of entries) {
    watch(f.model, () => {
      if (isApplying.value) return
      syncToRoute()
      if (f.reloads) options.onUserChange?.()
    })
  }

  return { applyRoute, isApplying }
}
