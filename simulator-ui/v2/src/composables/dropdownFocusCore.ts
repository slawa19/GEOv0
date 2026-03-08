import { nextTick } from 'vue'

const DROPDOWN_FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"]):not([disabled])',
].join(', ')

export function isDropdownOpenKey(key: string): boolean {
  return key === 'Enter' || key === ' ' || key === 'Spacebar' || key === 'ArrowDown'
}

export function getDropdownFocusableElements(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(DROPDOWN_FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute('disabled') && el.tabIndex >= 0,
  )
}

export function focusRelativeDropdownTarget(root: HTMLElement | null, direction: 1 | -1): boolean {
  const focusables = getDropdownFocusableElements(root)
  if (focusables.length === 0) return false

  const active = document.activeElement as HTMLElement | null
  const currentIndex = active != null ? focusables.indexOf(active) : -1
  const nextIndex = currentIndex < 0
    ? 0
    : (currentIndex + direction + focusables.length) % focusables.length

  focusables[nextIndex]?.focus()
  return true
}

export function trapDropdownTabNavigation(root: HTMLElement | null, shiftKey: boolean): boolean {
  const focusables = getDropdownFocusableElements(root)
  if (focusables.length === 0) {
    root?.focus()
    return true
  }

  const active = document.activeElement
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const activeInsideSurface = active instanceof HTMLElement && (root?.contains(active) ?? false)

  if (shiftKey) {
    if (!activeInsideSurface || active === first) {
      last.focus()
      return true
    }
    return false
  }

  if (!activeInsideSurface || active === last) {
    first.focus()
    return true
  }

  return false
}

export function scheduleDropdownFocusRestore(restoreFocus: () => void): void {
  void nextTick(() => {
    restoreFocus()
  })
}