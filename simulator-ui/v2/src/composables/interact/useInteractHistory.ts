import { reactive } from 'vue'

import { keptText, type KeptText } from '../../i18n/t'
import type { UiLocale } from '../../i18n/locale'

/**
 * A line of the person's own actions. 046 wave 3 (BACKLOG 046-3): the line is KEPT as its dictionary key and data
 * (`KeptText`), not as a text - it is drawn in the language of the moment it is shown (`historyEntryText`), so a switch
 * re-renders the lines already there. Until wave 3 a line kept the language it was made in.
 */
export type InteractHistoryEntry = {
  id: number
  icon: string
  line: KeptText
  timeText: string
  timestampMs: number
}

/** The text of a history line in `locale` (the language of the interface by default). */
export function historyEntryText(entry: Pick<InteractHistoryEntry, 'line'>, locale?: UiLocale): string {
  return keptText(entry.line, locale)
}

export function useInteractHistory(o?: { max?: number }) {
  const max = typeof o?.max === 'number' && o.max > 0 ? Math.floor(o.max) : 20

  const history = reactive<InteractHistoryEntry[]>([])
  let nextHistoryId = 1

  function pushHistory(icon: string, line: KeptText) {
    const nowMs = Date.now()
    const dt = new Date(nowMs)
    const timeText = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}:${String(dt.getSeconds()).padStart(2, '0')}`

    history.push({ id: nextHistoryId++, icon, line, timeText, timestampMs: nowMs })
    if (history.length > max) history.splice(0, history.length - max)
  }

  return { history, pushHistory }
}
