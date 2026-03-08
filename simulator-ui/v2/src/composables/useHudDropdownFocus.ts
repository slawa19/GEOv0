import { nextTick, ref, type Ref } from 'vue'

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