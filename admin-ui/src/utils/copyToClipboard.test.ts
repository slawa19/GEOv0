import { describe, expect, it, vi } from 'vitest'

import { copyToClipboard } from './copyToClipboard'

describe('copyToClipboard', () => {
  it('uses navigator.clipboard.writeText when available', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })

    const writeText = vi.fn().mockResolvedValue(undefined)
    ;(navigator as any).clipboard = { writeText }

    const exec = vi.fn(() => true)
    ;(document as any).execCommand = exec

    const res = await copyToClipboard('hello')

    expect(res).toEqual({ ok: true })
    expect(writeText).toHaveBeenCalledWith('hello')
    expect(exec).not.toHaveBeenCalled()
  })

  it('falls back to execCommand when clipboard API is unavailable', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    ;(navigator as any).clipboard = undefined

    const exec = vi.fn(() => true)
    ;(document as any).execCommand = exec

    const res = await copyToClipboard('  x  ')

    expect(res).toEqual({ ok: true })
    expect(exec).toHaveBeenCalledWith('copy')
    expect(document.body.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('returns an error when execCommand fails', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    ;(navigator as any).clipboard = undefined

    const exec = vi.fn(() => false)
    ;(document as any).execCommand = exec

    const res = await copyToClipboard('x')

    expect(res.ok).toBe(false)
    if (!res.ok) expect(res.error).toBeTruthy()
  })
})
