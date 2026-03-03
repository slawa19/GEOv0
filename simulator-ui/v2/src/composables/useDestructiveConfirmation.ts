import { onUnmounted, ref, watch, type Ref, type WatchSource } from 'vue'

import { useWindowContainerEl } from './windowManager/windowContainerContext'

export type DestructiveConfirmationDisarmTrigger = {
  /**
   * A watch source that, when changed, may trigger `disarm()`.
   * Example: `() => props.phase`, `open`, `() => props.busy`.
   */
  source: WatchSource<unknown>
  /** Optional predicate: disarm only when it returns true for the new value. */
  when?: (value: unknown, oldValue: unknown) => boolean
}

export type UseDestructiveConfirmationOptions = {
  /**
   * Auto-disarm after N ms once armed.
   * Keep `null/undefined` to preserve current behavior where no auto-disarm exists.
   */
  autoDisarmMs?: number | null

  /**
   * Disarm on ESC key event from the *window container element*.
   * Default: `keydown`.
   * The handler will `preventDefault()` only when armed (to consume ESC like before).
   */
  esc?: {
    enabled?: boolean
    eventName?: string
    consume?: boolean
  }

  /**
   * Additional change triggers that should disarm (e.g. phase change, busy=true, popup close).
   */
  disarmOn?: DestructiveConfirmationDisarmTrigger[]
}

export type UseDestructiveConfirmationApi = {
  armed: Ref<boolean>
  arm: () => void
  disarm: () => void
  /**
   * Implements the common UX:
   * - first call arms (returns false)
   * - second call confirms (disarms, calls `confirm`, returns true)
   */
  confirmOrArm: (confirm: () => unknown | Promise<unknown>) => Promise<boolean>
}

/**
 * Small UX helper for 2-step destructive confirmations (arm → confirm).
 *
 * IMPORTANT: keep behavior identical to the pre-refactor implementation:
 * - no implicit auto-disarm unless explicitly enabled via `autoDisarmMs`
 * - ESC disarms and consumes the event only when armed
 */
export function useDestructiveConfirmation(options: UseDestructiveConfirmationOptions = {}): UseDestructiveConfirmationApi {
  const armed = ref(false)

  const escEnabled = options.esc?.enabled ?? true
  const escEventName = options.esc?.eventName ?? 'keydown'
  const escConsume = options.esc?.consume ?? true

  // TODO-ESC: bind ESC handling to the per-window container element (provided by WindowShell).
  const injectedContainerEl = useWindowContainerEl()
  const containerEl = injectedContainerEl ?? ref<HTMLElement | null>(null)

  let autoDisarmTimer: ReturnType<typeof setTimeout> | null = null

  function clearAutoDisarmTimer() {
    if (!autoDisarmTimer) return
    clearTimeout(autoDisarmTimer)
    autoDisarmTimer = null
  }

  function disarm() {
    armed.value = false
    clearAutoDisarmTimer()
  }

  function arm() {
    armed.value = true

    const ms = options.autoDisarmMs
    if (!ms || ms <= 0) return

    clearAutoDisarmTimer()
    autoDisarmTimer = setTimeout(() => {
      armed.value = false
      autoDisarmTimer = null
    }, ms)
  }

  async function confirmOrArm(confirm: () => unknown | Promise<unknown>): Promise<boolean> {
    if (!armed.value) {
      arm()
      return false
    }

    disarm()
    await confirm()
    return true
  }

  function onEsc(ev: Event) {
    const kev = ev as KeyboardEvent
    const k = kev?.key
    if (k !== 'Escape' && k !== 'Esc') return

    if (!armed.value) return
    disarm()
    if (escConsume) ev.preventDefault()
  }

  for (const t of options.disarmOn ?? []) {
    watch(t.source, (value, oldValue) => {
      if (t.when && !t.when(value, oldValue)) return
      disarm()
    })
  }

  let boundEl: HTMLElement | null = null
  const stopEscWatch = escEnabled
    ? watch(
        containerEl,
        (nextEl, prevEl) => {
          if (prevEl) {
            prevEl.removeEventListener(escEventName, onEsc as EventListener)
            if (boundEl === prevEl) boundEl = null
          }

          if (nextEl) {
            nextEl.addEventListener(escEventName, onEsc as EventListener)
            boundEl = nextEl
          }
        },
        { immediate: true },
      )
    : null

  onUnmounted(() => {
    stopEscWatch?.()
    if (boundEl) {
      boundEl.removeEventListener(escEventName, onEsc as EventListener)
      boundEl = null
    }
    clearAutoDisarmTimer()
  })

  return { armed, arm, disarm, confirmOrArm }
}

