import { reactive } from 'vue'

export type InteractHistoryEntry = {
  id: number
  icon: string
  text: string
  timeText: string
  timestampMs: number
}

export function useInteractHistory(o?: { max?: number }) {
  const max = typeof o?.max === 'number' && o.max > 0 ? Math.floor(o.max) : 20

  const history = reactive<InteractHistoryEntry[]>([])
  let nextHistoryId = 1

  function pushHistory(icon: string, text: string) {
    const nowMs = Date.now()
    const dt = new Date(nowMs)
    const timeText = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}:${String(dt.getSeconds()).padStart(2, '0')}`

    history.push({ id: nextHistoryId++, icon, text, timeText, timestampMs: nowMs })
    if (history.length > max) history.splice(0, history.length - max)
  }

  return { history, pushHistory }
}
