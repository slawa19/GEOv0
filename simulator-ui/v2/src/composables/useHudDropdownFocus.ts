import { getCurrentScope, nextTick, onScopeDispose, ref, type Ref } from 'vue'

import {
  getDropdownFocusableElements,
  isDropdownOpenKey,
  scheduleDropdownFocusRestore,
  trapDropdownTabNavigation,
} from './dropdownFocusCore'

export function useHudDropdownFocus(open: Ref<boolean>) {
  const detailsRef = ref<HTMLDetailsElement | null>(null)
  const summaryRef = ref<HTMLElement | null>(null)
  const surfaceRef = ref<HTMLElement | null>(null)

  let keyboardOpenPending = false
  let restoreFocusPending = false

  function focusFirstInside(): void {
    const target = getDropdownFocusableElements(surfaceRef.value)[0] ?? surfaceRef.value
    target?.focus()
  }

  function restoreSummaryFocus(): void {
    summaryRef.value?.focus()
  }

  function closeAndRestoreFocus(): void {
    open.value = false
    if (detailsRef.value) detailsRef.value.open = false
    restoreFocusPending = true
    scheduleDropdownFocusRestore(() => {
      if (!restoreFocusPending) return
      restoreFocusPending = false
      restoreSummaryFocus()
    })
  }

  /**
   * The menu is anchored to its summary (`.ds-ov-dropdown`: `left: 0` or `right: 0`), so a summary near an edge of
   * the screen puts a part of the menu outside it: at 1366x768 "Advanced" stuck 49 px out on the right (038 S2). The
   * menu is shifted back by exactly the overflow and no further; a menu that fits is not touched. The gap to the edge
   * is the one its own `max-width` already assumes (`--ds-wm-clamp-pad` a side).
   */
  function clampSurfaceIntoViewport(): void {
    const surface = surfaceRef.value
    if (!surface) return
    surface.style.translate = ''
    const rect = surface.getBoundingClientRect()
    const pad = Number.parseFloat(getComputedStyle(surface).getPropertyValue('--ds-wm-clamp-pad')) || 0
    const viewportWidth = document.documentElement.clientWidth
    let dx = Math.min(0, viewportWidth - pad - rect.right)
    if (rect.left + dx < pad) dx = pad - rect.left
    if (Math.round(dx) !== 0) surface.style.translate = `${Math.round(dx)}px 0`
  }

  /**
   * The focus can fall out of an open menu without the user moving it: a control that becomes disabled while it is
   * focused (the Admin menu's "Refresh" while runs load) is dropped to `body` by the browser. `body` is outside the
   * `<details>` whose key handler holds the policy, so Escape closed nothing and Tab walked off into the page behind
   * the open menu (038 S2). While the menu is open and the focus is NOWHERE, those two keys are the menu's. A focus
   * that sits on any real element is left alone: that element's owner decides.
   */
  function onDocumentKeydown(event: KeyboardEvent): void {
    if (!open.value || (event.key !== 'Escape' && event.key !== 'Tab')) return
    const active = document.activeElement
    if (active && active !== document.body) return
    onDetailsKeydown(event)
  }

  function unbindWhileOpen(): void {
    window.removeEventListener('resize', clampSurfaceIntoViewport)
    document.removeEventListener('keydown', onDocumentKeydown, true)
  }

  if (getCurrentScope()) onScopeDispose(unbindWhileOpen)

  function onToggle(event: Event): void {
    const details = event.target instanceof HTMLDetailsElement ? event.target : detailsRef.value
    if (!details) return

    open.value = details.open

    unbindWhileOpen()
    if (details.open) {
      clampSurfaceIntoViewport()
      window.addEventListener('resize', clampSurfaceIntoViewport)
      document.addEventListener('keydown', onDocumentKeydown, true)
    }

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
      isDropdownOpenKey(key) &&
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

    if (trapDropdownTabNavigation(surfaceRef.value, event.shiftKey)) {
      event.preventDefault()
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