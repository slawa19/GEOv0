import { onMounted, onUnmounted, readonly, ref, type Ref } from 'vue'

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

export function useReducedMotionPreference(): Readonly<Ref<boolean>> {
  const prefersReducedMotion = ref(false)
  let mediaQuery: MediaQueryList | null = null

  const syncPreference = (event?: MediaQueryListEvent): void => {
    prefersReducedMotion.value = event?.matches ?? mediaQuery?.matches ?? false
  }

  onMounted(() => {
    if (typeof window.matchMedia !== 'function') return

    mediaQuery = window.matchMedia(REDUCED_MOTION_QUERY)
    syncPreference()

    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', syncPreference)
    } else {
      mediaQuery.addListener(syncPreference)
    }
  })

  onUnmounted(() => {
    if (!mediaQuery) return

    if (typeof mediaQuery.removeEventListener === 'function') {
      mediaQuery.removeEventListener('change', syncPreference)
    } else {
      mediaQuery.removeListener(syncPreference)
    }
    mediaQuery = null
  })

  return readonly(prefersReducedMotion)
}
