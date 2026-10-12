/**
 * The value of a CSS custom property at `scope`, trimmed; `fallback` when there is no DOM or the property is empty.
 *
 * NOT CACHED (039 slice B, 2026-10-12). The reader used to keep the first answer per name for ever and always asked
 * the document root, while the theme is set on the app's own root element: a token a theme overrides was never seen,
 * and no value could follow a theme change. Call it where a theme is resolved - on mount and on a theme change
 * (`vizMapping.ts` `resolveVizColors`) - never per node or per frame: `getComputedStyle` is not a hot-path call.
 *
 * `scope`: the element the cascade is read at. `null` / `undefined` reads the document root, which sees only what
 * the common `:root` declares.
 */
export function readCssVar(scope: Element | null | undefined, name: `--${string}`, fallback: string): string {
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback
  const el = scope ?? document.documentElement
  if (!el) return fallback
  const value = String(getComputedStyle(el).getPropertyValue(name) || '').trim()
  return value.length > 0 ? value : fallback
}
