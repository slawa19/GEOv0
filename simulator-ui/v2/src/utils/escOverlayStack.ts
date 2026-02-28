export type EscOverlayStackDeps = {
  isNodeCardOpen: () => boolean
  closeNodeCard: () => void

  isInteractActive: () => boolean
  cancelInteract: () => void

  isFormLikeTarget: (t: EventTarget | null) => boolean

  /**
   * Give overlays/panels a chance to consume ESC (e.g. disarm a destructive confirmation).
   * Convention: a listener calls `preventDefault()` on the custom event to stop the global cancel.
   */
  dispatchInteractEsc: () => boolean
}

/**
 * ESC overlay stack (formalized):
 * 1) Close NodeCardOverlay first.
 * 2) If not in active Interact phase: do nothing.
 * 3) If a nested overlay consumes ESC (via cancelable `geo:interact-esc`): stop.
 * 4) If focus is inside a form element: do not cancel the Interact flow.
 * 5) Otherwise cancel the Interact flow.
 */
export function handleEscOverlayStack(ev: KeyboardEvent, deps: EscOverlayStackDeps): boolean {
  const k = ev.key
  const isEsc = k === 'Escape' || k === 'Esc'
  if (!isEsc) return false

  // Step 1: close NodeCard first (always highest priority).
  if (deps.isNodeCardOpen()) {
    ev.preventDefault()
    deps.closeNodeCard()
    return true
  }
  
  // Step 2: Interact-only beyond this point.
  if (!deps.isInteractActive()) return false

  // Step 3: if focus is inside a form-like element (input, select, textarea…),
  // yield to native ESC behavior — do NOT cancel the Interact flow.
  if (deps.isFormLikeTarget(ev.target)) return true

  // Step 4: prevent default navigation/browser ESC for non-form targets.
  ev.preventDefault()

  // Step 5: allow nested overlays to consume ESC.
  const notCanceled = deps.dispatchInteractEsc()
  if (!notCanceled) return true

  // Step 6: cancel active flow.
  deps.cancelInteract()
  return true
}
