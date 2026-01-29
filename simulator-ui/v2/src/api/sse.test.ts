import { describe, expect, it } from 'vitest'
import { parseSseChunk, type SseParserState } from './sse'

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
