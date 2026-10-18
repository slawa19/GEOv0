import type { UiLocale } from './locale'

/**
 * 046 wave 1, §15 Codex P2 (2026-10-18): a line of text that is KEPT in state (the run's error line, `real.lastError`)
 * was stored already translated, so a language switch left it in the old language next to a label and a button in the
 * new one. A line made through `localizedLine` remembers how it was made - its render, a function of the language over
 * the structured data of its cause (a key, the server's number of places, ...) - and `relocalizeLine` makes it again in
 * another language. The holder of the state calls `relocalizeLine` on a switch (`useSimulatorRealMode.ts`).
 *
 * The line is found by its exact text: the holder keeps a string, and a string that equals a made line IS that line
 * (two renders that make the same text make the same line in every language too, or they would not be one text).
 * A text no render made (a server's raw `CODE: message`, a transport error) is not found and stays as it is.
 *
 * Bounded: the last `MAX_LINES` made lines are remembered, the oldest forgotten first. Process-wide: a line made in one
 * place is found from another, which is the point (the run status pipeline makes the line, the run mode keeps it).
 *
 * NOT seen here: a text kept in state that was translated without this module - the action windows' errors, history
 * lines and toasts (wave 2) - see BACKLOG 046-3.
 */
export type LineRender = (locale: UiLocale) => string

const MAX_LINES = 64
const lines = new Map<string, LineRender>()

/** Make the line in `locale` and remember how; returns its text. */
export function localizedLine(render: LineRender, locale: UiLocale): string {
  const text = render(locale)
  if (text) {
    lines.delete(text)
    lines.set(text, render)
    while (lines.size > MAX_LINES) {
      const oldest = lines.keys().next().value
      if (oldest === undefined) break
      lines.delete(oldest)
    }
  }
  return text
}

/** The same line in `locale`, if `text` is a line this module made; otherwise null (keep the text as it is). */
export function relocalizeLine(text: string | null | undefined, locale: UiLocale): string | null {
  if (!text) return null
  const render = lines.get(text)
  return render ? localizedLine(render, locale) : null
}
