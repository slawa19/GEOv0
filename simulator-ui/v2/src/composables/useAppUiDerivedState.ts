import { computed, type ComputedRef, type Ref } from 'vue'

import { isDemoScene, type SceneId } from '../scenes'
import { clamp } from '../utils/math'
import type { Quality } from '../types/uiPrefs'

export function useAppUiDerivedState(deps: {
  eq: Ref<string>
  scene: Ref<SceneId>
  quality: Ref<Quality>
  apiMode: ComputedRef<'demo' | 'real'>
  isDemoFixtures: ComputedRef<boolean>
  isTestMode: ComputedRef<boolean>
  isWebDriver: boolean
  getCameraZoom: () => number
}) {
  const effectiveEq = computed(() => {
    // Demo fixtures are shipped for UAH only; avoid silently requesting missing files.
    if (deps.apiMode.value === 'demo' && deps.isDemoFixtures.value) return 'UAH'
    // Scene E must use canonical clearing cycles (UAH) per spec.
    if (deps.scene.value === 'E') return 'UAH'
    // Keep Playwright / test-mode stable even if query params are present.
    if (deps.isTestMode.value) return 'UAH'
    return deps.eq.value
  })

  const dprClamp = computed(() => {
    if (deps.isTestMode.value) return 1
    if (deps.quality.value === 'low') return 1
    if (deps.quality.value === 'med') return 1.5
    return 2
  })

  const showDemoControls = computed(() => !deps.isTestMode.value && isDemoScene(deps.scene.value))
  const showResetView = computed(() => !deps.isTestMode.value)

  const overlayLabelScale = computed(() => {
    // Keep e2e screenshots stable and avoid surprises in test-mode.
    if (deps.isTestMode.value || deps.isWebDriver) return 1
    // When zoomed out (z<1), make overlay text visually smaller so it "moves away" with nodes.
    // Clamp to avoid becoming unreadable or comically large.
    const z = Math.max(0.01, deps.getCameraZoom())
    return clamp(z, 0.65, 1)
  })

  return { effectiveEq, dprClamp, showDemoControls, showResetView, overlayLabelScale }
}
