import { nextTick, ref, type Ref } from 'vue'

const HUD_DROPDOWN_FOCUSABLE = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"]):not([disabled])',
].join(', ')

function getFocusableElements(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(HUD_DROPDOWN_FOCUSABLE)).filter(
    (el) => !el.hasAttribute('disabled') && el.tabIndex >= 0,
  )
}

export function useHudDropdownFocus(open: Ref<boolean>) {
  const detailsRef = ref<HTMLDetailsElement | null>(null)
  const summaryRef = ref<HTMLElement | null>(null)
  const surfaceRef = ref<HTMLElement | null>(null)

  let keyboardOpenPending = false
  let restoreFocusPending = false

  function focusFirstInside(): void {
    const target = getFocusableElements(surfaceRef.value)[0] ?? surfaceRef.value
    target?.focus()
  }

  function restoreSummaryFocus(): void {
    summaryRef.value?.focus()
  }

  function closeAndRestoreFocus(): void {
    open.value = false
    if (detailsRef.value) detailsRef.value.open = false
    restoreFocusPending = true
    void nextTick(() => {
      if (!restoreFocusPending) return
      restoreFocusPending = false
      restoreSummaryFocus()
    })
  }

  function onToggle(event: Event): void {
    const details = event.target instanceof HTMLDetailsElement ? event.target : detailsRef.value
    if (!details) return

    open.value = details.open

    if (details.open) {
      const shouldMoveFocusInside = keyboardOpenPending || document.activeElement === summaryRef.value
      keyboardOpenPending = false
      if (!shouldMoveFocusInside) return
      void nextTick(() => {
        if (!open.value) return
        focusFirstInside()
      })
      return
    }

    keyboardOpenPending = false
    const active = document.activeElement
    const shouldRestoreFocus =
      restoreFocusPending ||
      (active instanceof HTMLElement &&
        (surfaceRef.value?.contains(active) ?? false))

    if (!shouldRestoreFocus) return
    restoreFocusPending = false
    void nextTick(() => {
      restoreSummaryFocus()
    })
  }

  function onDetailsKeydown(event: KeyboardEvent): void {
    const target = event.target instanceof HTMLElement ? event.target : null
    const key = event.key

    if (
      (key === 'Enter' || key === ' ' || key === 'Spacebar' || key === 'ArrowDown') &&
      target != null &&
      (target === summaryRef.value || summaryRef.value?.contains(target) === true)
    ) {
      keyboardOpenPending = true
    }

    if (!open.value) return

    if (key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeAndRestoreFocus()
      return
    }

    if (key !== 'Tab') return

    const focusables = getFocusableElements(surfaceRef.value)
    if (focusables.length === 0) {
      event.preventDefault()
      surfaceRef.value?.focus()
      return
    }

    const active = document.activeElement
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    const activeInsideSurface = active instanceof HTMLElement && (surfaceRef.value?.contains(active) ?? false)

    if (event.shiftKey) {
      if (!activeInsideSurface || active === first) {
        event.preventDefault()
        last.focus()
      }
      return
    }

    if (!activeInsideSurface || active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return {
    detailsRef,
    summaryRef,
    surfaceRef,
    onToggle,
    onDetailsKeydown,
  }
}