export type SseParsedMessage = {
  id?: string
  event?: string
  data?: string
}

export type SseParserState = {
  buffer: string
}

export function parseSseChunk(state: SseParserState, chunk: string): { messages: SseParsedMessage[] } {
  // Normalize CRLF → LF to keep parsing predictable.
  state.buffer += chunk.replace(/\r\n/g, '\n')

  const messages: SseParsedMessage[] = []

  while (true) {
    const sep = state.buffer.indexOf('\n\n')
    if (sep < 0) break

    const rawFrame = state.buffer.slice(0, sep)
    state.buffer = state.buffer.slice(sep + 2)

    if (!rawFrame.trim()) continue

    let id: string | undefined
    let event: string | undefined
    const dataLines: string[] = []

    for (const line of rawFrame.split('\n')) {
      if (!line) continue
      if (line.startsWith(':')) continue // comment / keep-alive

      if (line.startsWith('id:')) {
        id = line.slice(3).trim()
        continue
      }

      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
        continue
      }

      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
        continue
      }

      // Ignore unknown fields.
    }

    const data = dataLines.length ? dataLines.join('\n') : undefined
    if (id === undefined && event === undefined && data === undefined) continue
    messages.push({ id, event, data })
  }

  return { messages }
}

export type SseConnectOpts = {
  url: string
  headers?: Record<string, string>
  lastEventId?: string | null
  signal?: AbortSignal
  onMessage: (msg: SseParsedMessage) => void
}

export async function connectSse(opts: SseConnectOpts): Promise<void> {
  const headers = new Headers(opts.headers)
  headers.set('Accept', 'text/event-stream')
  if (opts.lastEventId) headers.set('Last-Event-ID', opts.lastEventId)

  const res = await fetch(opts.url, { headers, signal: opts.signal })
  if (!res.ok) {
    const bodyText = await res.text().catch(() => '')
    throw new Error(`SSE HTTP ${res.status} ${res.statusText}: ${bodyText}`)
  }

  if (!res.body) throw new Error('SSE response has no body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const state: SseParserState = { buffer: '' }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    if (!value) continue

    const chunk = decoder.decode(value, { stream: true })
    const { messages } = parseSseChunk(state, chunk)
    for (const m of messages) opts.onMessage(m)
  }
}
