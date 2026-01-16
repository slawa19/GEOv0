import { computed, type Ref } from 'vue'

import type { SelectedInfo } from '../../composables/useGraphVisualization'

export function useGraphFocusMode(input: {
  selected: Ref<SelectedInfo | null>
  focusPid: Ref<string>
  searchQuery: Ref<string>

  focusMode: Ref<boolean>
  focusRootPid: Ref<string>

  extractPidFromText: (text: string) => string | null
}) {
  function suggestPidForFocus(): string {
    if (input.selected.value && input.selected.value.kind === 'node') return input.selected.value.pid
    const p = String(input.focusPid.value || '').trim()
    if (p) return p
    const fromQuery = input.extractPidFromText(input.searchQuery.value)
    return fromQuery || ''
  }

  function setFocusRoot(pid: string) {
    input.focusRootPid.value = String(pid || '').trim()
  }

  function ensureFocusRootPid() {
    if (String(input.focusRootPid.value || '').trim()) return
    setFocusRoot(suggestPidForFocus())
  }

  function useSelectedForFocus() {
    if (!input.selected.value || input.selected.value.kind !== 'node') return
    setFocusRoot(input.selected.value.pid)
    input.focusMode.value = true
    ensureFocusRootPid()
  }

  function clearFocusMode() {
    input.focusMode.value = false
    setFocusRoot('')
  }

  const canUseSelectedForFocus = computed(() => Boolean(input.selected.value && input.selected.value.kind === 'node'))

  return {
    setFocusRoot,
    ensureFocusRootPid,
    useSelectedForFocus,
    clearFocusMode,
    canUseSelectedForFocus,
  }
}
