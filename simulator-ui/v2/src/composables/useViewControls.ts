export function useViewControls(opts: {
  worldToScreen: (x: number, y: number) => { x: number; y: number }
  resetCamera: () => void
  clampCameraPan: () => void
}) {
  function worldToCssTranslateNoScale(x: number, y: number) {
    const p = opts.worldToScreen(x, y)
    return `translate3d(${p.x}px, ${p.y}px, 0)`
  }

  function resetView() {
    opts.resetCamera()
    opts.clampCameraPan()
  }

  return {
    worldToCssTranslateNoScale,
    resetView,
  }
}
