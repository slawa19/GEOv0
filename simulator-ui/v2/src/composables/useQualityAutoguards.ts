import type { Ref, WatchStopHandle } from 'vue'
import { onMounted, onUnmounted, ref, watch } from 'vue'
import type { Quality } from '../types/uiPrefs'

type TimersLike = {
  setTimeout: (fn: () => void, ms: number) => number
  clearTimeout: (id: number) => void
}

type RafLike = {
  requestAnimationFrame: (fn: (t: number) => void) => number
  cancelAnimationFrame: (id: number) => void
}

function defaultTimers(): TimersLike {
  const w = (globalThis as any).window as any
  if (!w) return { setTimeout: () => 0, clearTimeout: () => undefined } as any
  return { setTimeout: (fn, ms) => w.setTimeout(fn, ms), clearTimeout: (id) => w.clearTimeout(id) }
}

function defaultRaf(): RafLike {
  const w = (globalThis as any).window as any
  if (!w) return { requestAnimationFrame: () => 0, cancelAnimationFrame: () => undefined } as any
  return { requestAnimationFrame: (fn) => w.requestAnimationFrame(fn), cancelAnimationFrame: (id) => w.cancelAnimationFrame(id) }
}

function detectGpuAccelerationLikelyAvailable(): boolean {
  if (typeof document === 'undefined') return true
  try {
    const canvas = document.createElement('canvas')
    const gl = (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')) as WebGLRenderingContext | null
    if (!gl) return false

    const dbg = gl.getExtension('WEBGL_debug_renderer_info') as any
    if (dbg) {
      const renderer = String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) ?? '')
      const r = renderer.toLowerCase()
      if (r.includes('microsoft basic render driver')) return false
      if (r.includes('swiftshader')) return false
      if (r.includes('llvmpipe')) return false
    }

    return true
  } catch {
    return true
  }
}

export function useQualityAutoguards(deps: {
  quality: Ref<Quality>
  isTestMode: Readonly<Ref<boolean>>
  isWebDriver: boolean
  timers?: TimersLike
  raf?: RafLike
}): { gpuAccelLikely: Ref<boolean> } {
  const timers = deps.timers ?? defaultTimers()
  const raf = deps.raf ?? defaultRaf()

  const gpuAccelLikely = ref(true)

  let qualityFpsGuardRafId: number | null = null
  let stopQualityFpsGuardWatch: WatchStopHandle | null = null

  let gpuQualityDowngradeTimer: number | null = null
  let stopGpuTouchedWatch: WatchStopHandle | null = null

  onMounted(() => {
    if (deps.isTestMode.value) return
    if (deps.isWebDriver) return

    let frames = 0
    let startMs = 0
    let guardActive = true
    let qualityTouchedWhileGuardActive = false

    stopQualityFpsGuardWatch?.()
    stopQualityFpsGuardWatch = watch(
      deps.quality,
      () => {
        if (guardActive) qualityTouchedWhileGuardActive = true
      },
      { flush: 'sync' },
    )

    const loop = (t: number) => {
      if (startMs === 0) startMs = t
      frames++

      const elapsed = t - startMs
      if (elapsed < 1800) {
        qualityFpsGuardRafId = raf.requestAnimationFrame(loop)
        return
      }

      guardActive = false
      stopQualityFpsGuardWatch?.()
      stopQualityFpsGuardWatch = null
      if (qualityFpsGuardRafId != null) raf.cancelAnimationFrame(qualityFpsGuardRafId)
      qualityFpsGuardRafId = null

      const fps = (frames * 1000) / Math.max(1, elapsed)
      if (!qualityTouchedWhileGuardActive && fps < 12 && deps.quality.value !== 'low') {
        deps.quality.value = 'low'
      }
    }

    qualityFpsGuardRafId = raf.requestAnimationFrame(loop)

    gpuAccelLikely.value = detectGpuAccelerationLikelyAvailable()
    if (!gpuAccelLikely.value) {
      let touched = false
      stopGpuTouchedWatch?.()
      stopGpuTouchedWatch = watch(
        deps.quality,
        () => {
          touched = true
        },
        { flush: 'sync' },
      )

      if (gpuQualityDowngradeTimer != null) timers.clearTimeout(gpuQualityDowngradeTimer)
      gpuQualityDowngradeTimer = timers.setTimeout(() => {
        stopGpuTouchedWatch?.()
        stopGpuTouchedWatch = null
        if (!touched && deps.quality.value !== 'low') {
          deps.quality.value = 'low'
        }
      }, 400)
    }
  })

  onUnmounted(() => {
    stopQualityFpsGuardWatch?.()
    stopQualityFpsGuardWatch = null
    if (qualityFpsGuardRafId != null) raf.cancelAnimationFrame(qualityFpsGuardRafId)
    qualityFpsGuardRafId = null

    stopGpuTouchedWatch?.()
    stopGpuTouchedWatch = null
    if (gpuQualityDowngradeTimer != null) timers.clearTimeout(gpuQualityDowngradeTimer)
    gpuQualityDowngradeTimer = null
  })

  return { gpuAccelLikely }
}
