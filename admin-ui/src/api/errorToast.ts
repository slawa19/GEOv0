import { formatApiError } from './errorFormat'

let lastToastAt = 0
let lastToastMsg = ''

export async function toastApiError(
  e: unknown,
  opts?: {
    fallbackTitle?: string
    dedupeMs?: number
  },
): Promise<void> {
  const { title, hint } = formatApiError(e)
  const msg = (hint ? `${title} — ${hint}` : title) || opts?.fallbackTitle || 'Request failed'

  const now = Date.now()
  const dedupeMs = Math.max(0, opts?.dedupeMs ?? 2000)
  if (msg === lastToastMsg && now - lastToastAt < dedupeMs) return
  lastToastAt = now
  lastToastMsg = msg

  // Best-effort toast: do not crash the app if UI layer isn't available.
  try {
    const mod = await import('element-plus')
    const ElMessage = (mod as unknown as { ElMessage?: { error?: (msg: string) => void } }).ElMessage
    if (typeof ElMessage?.error === 'function') {
      ElMessage.error(msg)
      return
    }
  } catch {
    // ignore
  }

  // eslint-disable-next-line no-console
  console.error(msg, e)
}

export function __resetApiErrorToastForTests(): void {
  lastToastAt = 0
  lastToastMsg = ''
}
