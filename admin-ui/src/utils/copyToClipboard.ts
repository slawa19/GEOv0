export type CopyResult = { ok: true } | { ok: false; error: string }

export async function copyToClipboard(text: string): Promise<CopyResult> {
  const value = String(text ?? '')

  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return { ok: true }
    }
  } catch (e: any) {
    // Fall back to execCommand below.
  }

  try {
    const el = document.createElement('textarea')
    el.value = value

    // Prevent page scroll/jump.
    el.style.position = 'fixed'
    el.style.top = '0'
    el.style.left = '0'
    el.style.opacity = '0'
    el.style.pointerEvents = 'none'

    document.body.appendChild(el)
    el.focus()
    el.select()

    const ok = document.execCommand('copy')
    document.body.removeChild(el)

    if (!ok) return { ok: false, error: 'Copy failed' }
    return { ok: true }
  } catch (e: any) {
    return { ok: false, error: e?.message || 'Copy failed' }
  }
}
