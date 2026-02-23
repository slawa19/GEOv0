import { onMounted, onUnmounted, ref, watch, type Ref, type WatchSource } from 'vue'

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
   * Disarm on Interact ESC event (default: `geo:interact-esc`).
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
  const escEventName = options.esc?.eventName ?? 'geo:interact-esc'
  const escConsume = options.esc?.consume ?? true

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

  onMounted(() => {
    if (!escEnabled) return
    window.addEventListener(escEventName, onEsc)
  })

  onUnmounted(() => {
    if (escEnabled) {
      window.removeEventListener(escEventName, onEsc)
    }
    clearAutoDisarmTimer()
  })

  return { armed, arm, disarm, confirmOrArm }
}

