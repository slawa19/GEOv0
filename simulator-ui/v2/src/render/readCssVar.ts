const cssVarCache = new Map<string, string>()

export function readCssVar(name: `--${string}`, fallback: string): string {
  const cached = cssVarCache.get(name)
  if (cached !== undefined) return cached

  if (typeof window === 'undefined' || typeof document === 'undefined') {
    cssVarCache.set(name, fallback)
    return fallback
  }

  const raw = getComputedStyle(document.documentElement).getPropertyValue(name)
  const value = String(raw || '').trim()
  const resolved = value.length > 0 ? value : fallback
  cssVarCache.set(name, resolved)
  return resolved
}
