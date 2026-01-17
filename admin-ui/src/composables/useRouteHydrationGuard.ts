import { computed, ref } from 'vue'
import type { RouteLocationNormalizedLoaded } from 'vue-router'

/**
 * Guard to distinguish route-driven state hydration from user edits.
 *
 * Common pitfall:
 * - On mount: load()
 * - Then route.query watcher applies query -> updates refs
 * - Refs watcher thinks it's user input -> triggers another load() / router.replace()
 *
 * This helper keeps a boolean flag set through the next microtask,
 * so ref watchers can skip route-driven updates reliably.
 */
export function useRouteHydrationGuard(route: RouteLocationNormalizedLoaded, expectedPath: string) {
  const isApplying = ref(false)
  const isActive = computed(() => route.path === expectedPath)

  let depth = 0

  function run<T>(fn: () => T): T | undefined {
    if (!isActive.value) return undefined

    depth += 1
    isApplying.value = true
    try {
      return fn()
    } finally {
      // Keep the guard for the next microtask so watchers triggered by assignments
      // don't interpret them as user-driven edits.
      Promise.resolve().then(() => {
        depth = Math.max(0, depth - 1)
        if (depth === 0) isApplying.value = false
      })
    }
  }

  return { isApplying, isActive, run }
}
