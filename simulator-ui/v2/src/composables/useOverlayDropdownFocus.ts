import { nextTick, watch, type Ref } from 'vue'

import {
  focusRelativeDropdownTarget,
  getDropdownFocusableElements,
  isDropdownOpenKey,
  scheduleDropdownFocusRestore,
  trapDropdownTabNavigation,
} from './dropdownFocusCore'

export function useOverlayDropdownFocus(
  open: Ref<boolean>,
  triggerRef: Ref<HTMLElement | null>,
  surfaceRef: Ref<HTMLElement | null>,
) {
  let keyboardOpenPending = false

  function focusInside(): void {
    const selected = surfaceRef.value?.querySelector<HTMLElement>('[data-dropdown-selected="1"]:not([disabled])') ?? null
    const target = selected ?? getDropdownFocusableElements(surfaceRef.value)[0] ?? surfaceRef.value
    target?.focus()
  }

  function restoreTriggerFocus(): void {
    triggerRef.value?.focus()
  }

  function openFromKeyboard(): void {
    keyboardOpenPending = true
    open.value = true
  }

  function closeAndRestoreFocus(): void {
    open.value = false
    keyboardOpenPending = false
    scheduleDropdownFocusRestore(() => {
      restoreTriggerFocus()
    })
  }

  function onTriggerKeydown(event: KeyboardEvent): void {
    const key = event.key
    if (isDropdownOpenKey(key)) {
      event.preventDefault()
      event.stopPropagation()
      openFromKeyboard()
    }
  }

  function onSurfaceKeydown(event: KeyboardEvent): void {
    if (!open.value) return

    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeAndRestoreFocus()
      return
    }

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      focusRelativeDropdownTarget(surfaceRef.value, event.key === 'ArrowDown' ? 1 : -1)
      return
    }

    if (event.key !== 'Tab') return

    if (trapDropdownTabNavigation(surfaceRef.value, event.shiftKey)) {
      event.preventDefault()
    }
  }

  watch(open, (isOpen) => {
    if (!isOpen) {
      keyboardOpenPending = false
      return
    }

    if (!keyboardOpenPending) return

    keyboardOpenPending = false
    void nextTick(() => {
      if (!open.value) return
      focusInside()
    })
  })

  return {
    onTriggerKeydown,
    onSurfaceKeydown,
    closeAndRestoreFocus,
  }
}