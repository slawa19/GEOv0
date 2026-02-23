import { onMounted, onUnmounted, watch, type Ref } from 'vue'

export function useAppLifecycle(opts: {
  layoutMode: Ref<unknown>
  resizeAndLayout: () => void
  persistedPrefs: { loadFromStorage: () => void; dispose: () => void }
  setupDevHook: () => void
  disposeDevHook?: () => void
  sceneState: { setup: () => void; teardown: () => void }
  hideDragPreview: () => void
  /** Optional runtime cleanup for render/FX module-level caches. */
  resetFxRendererCaches?: () => void
  physics: {
    stop: () => void
    updateViewport: (w: number, h: number, alpha?: number) => void
  }
  getLayoutSize: () => { w: number; h: number }
}) {
  // Re-layout deterministically when user switches layout mode.
  watch(opts.layoutMode, () => {
    opts.resizeAndLayout()
  })

  // Keep physics viewport in sync even when resize does not trigger a relayout.
  watch(
    () => {
      const s = opts.getLayoutSize()
      return [s.w, s.h] as const
    },
    ([w, h], [prevW, prevH]) => {
      if (w === prevW && h === prevH) return
      opts.physics.updateViewport(w, h, 0.15)
    },
  )

  onMounted(() => {
    opts.persistedPrefs.loadFromStorage()
    opts.setupDevHook()
    opts.sceneState.setup()
  })

  onUnmounted(() => {
    opts.hideDragPreview()
    opts.persistedPrefs.dispose()
    opts.disposeDevHook?.()
    opts.physics.stop()
    opts.resetFxRendererCaches?.()
    opts.sceneState.teardown()
  })
}
