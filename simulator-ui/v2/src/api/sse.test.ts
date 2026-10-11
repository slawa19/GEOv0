import { afterEach, describe, expect, it, vi } from 'vitest'
import { connectSse, parseSseChunk, type SseParserState } from './sse'

describe('parseSseChunk', () => {
  it('parses id/event/data and ignores keep-alive comments', () => {
    const st: SseParserState = { buffer: '' }

    const input = [
      ': keep-alive\n\n',
      'id: evt_1\n',
      'event: simulator.event\n',
      'data: {"a":1}\n\n',
    ].join('')

    const { messages } = parseSseChunk(st, input)
    expect(messages).toEqual([{ id: 'evt_1', event: 'simulator.event', data: '{"a":1}' }])
    expect(st.buffer).toBe('')
  })

  it('handles frames split across chunks', () => {
    const st: SseParserState = { buffer: '' }

    const a = 'id: evt_2\nevent: simulator.event\ndata: {"x":'
    const b = '2}\n\n'

    expect(parseSseChunk(st, a).messages).toEqual([])
    expect(parseSseChunk(st, b).messages).toEqual([{ id: 'evt_2', event: 'simulator.event', data: '{"x":2}' }])
  })

  it('joins multiple data lines with newline', () => {
    const st: SseParserState = { buffer: '' }

    const input = 'id: evt_3\ndata: line1\ndata: line2\n\n'
    const { messages } = parseSseChunk(st, input)

    expect(messages).toEqual([{ id: 'evt_3', event: undefined, data: 'line1\nline2' }])
  })

  it('normalizes CRLF', () => {
    const st: SseParserState = { buffer: '' }

    const input = 'id: evt_4\r\nevent: simulator.event\r\ndata: {}\r\n\r\n'
    const { messages } = parseSseChunk(st, input)
    expect(messages[0]?.id).toBe('evt_4')
  })
})

/**
 * 034 S8, F-034-D14 (reproducer of T3404): the SSE transport reports a refused connection with its HTTP status,
 * so the caller can tell 404 (the run is gone) and 410 (the replay cursor is gone) from everything else without
 * reading the message - which carries the answer's body.
 */
describe('connectSse: a refused connection', () => {
  function refuse(status: number, statusText: string, body: string) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status, statusText, text: async () => body, body: null })),
    )
  }

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  // T3404: red on origin/main because of F-034-D14; S8 removes `.fails` together with the fix.
  it.fails.each([
    [404, 'Not Found'],
    [410, 'Gone'],
    [500, 'Internal Server Error'],
  ])('HTTP %i rejects with an error that carries the status', async (status, statusText) => {
    refuse(status, statusText, '{"detail":"x"}')

    const error: unknown = await connectSse({ url: 'http://sim.test/events', onMessage: () => undefined }).then(
      () => null,
      (e: unknown) => e,
    )

    expect(error).toBeInstanceOf(Error)
    expect((error as { status?: unknown }).status, 'the status is only inside the message text').toBe(status)
  })

  it('CONTRACT: the message still names the transport, the status and the body', async () => {
    refuse(500, 'Internal Server Error', 'upstream said HTTP 404')

    await expect(connectSse({ url: 'http://sim.test/events', onMessage: () => undefined })).rejects.toThrow(
      'SSE HTTP 500 Internal Server Error: upstream said HTTP 404',
    )
  })
})
