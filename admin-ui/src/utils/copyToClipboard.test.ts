import { describe, expect, it, vi } from 'vitest'

import { copyToClipboard } from './copyToClipboard'

describe('copyToClipboard', () => {
  it('uses navigator.clipboard.writeText when available', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })

    const writeText = vi.fn<[string], Promise<void>>().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: ({ writeText } as unknown) as Clipboard,
      configurable: true,
    })

    const exec = vi.fn<[string], boolean>(() => true)
    Object.defineProperty(document, 'execCommand', {
      value: (exec as unknown) as Document['execCommand'],
      configurable: true,
    })

    const res = await copyToClipboard('hello')

    expect(res).toEqual({ ok: true })
    expect(writeText).toHaveBeenCalledWith('hello')
    expect(exec).not.toHaveBeenCalled()
  })

  it('falls back to execCommand when clipboard API is unavailable', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })

    const exec = vi.fn<[string], boolean>(() => true)
    Object.defineProperty(document, 'execCommand', {
      value: (exec as unknown) as Document['execCommand'],
      configurable: true,
    })

    const res = await copyToClipboard('  x  ')

    expect(res).toEqual({ ok: true })
    expect(exec).toHaveBeenCalledWith('copy')
    expect(document.body.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('returns an error when execCommand fails', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })

    const exec = vi.fn<[string], boolean>(() => false)
    Object.defineProperty(document, 'execCommand', {
      value: (exec as unknown) as Document['execCommand'],
      configurable: true,
    })

    const res = await copyToClipboard('x')

    expect(res.ok).toBe(false)
    if (!res.ok) expect(res.error).toBeTruthy()
  })
})
