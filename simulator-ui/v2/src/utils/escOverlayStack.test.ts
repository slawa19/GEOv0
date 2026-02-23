import { describe, expect, it, vi } from 'vitest'

import { handleEscOverlayStack } from './escOverlayStack'

function makeEvent(opts: { key: string; target?: unknown } = { key: 'Escape' }) {
  return {
    key: opts.key,
    target: (opts.target ?? null) as any,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  } as any as KeyboardEvent
}

function makeDeps(overrides: Partial<Parameters<typeof handleEscOverlayStack>[1]> = {}) {
  return {
    isNodeCardOpen: () => false,
    closeNodeCard: vi.fn(),

    isInteractActive: () => true,
    cancelInteract: vi.fn(),

    isFormLikeTarget: () => false,
    dispatchInteractEsc: () => true,

    ...overrides,
  }
}

describe('utils/escOverlayStack', () => {
  it('returns false for non-ESC key', () => {
    const ev = makeEvent({ key: 'Enter' })
    const deps = makeDeps()

    expect(handleEscOverlayStack(ev, deps)).toBe(false)
    expect(ev.preventDefault).not.toHaveBeenCalled()
    expect(deps.closeNodeCard).not.toHaveBeenCalled()
    expect(deps.cancelInteract).not.toHaveBeenCalled()
  })

  it('returns false for ESC when interact is not active (and NodeCard is closed)', () => {
    const ev = makeEvent({ key: 'Escape' })
    const deps = makeDeps({ isInteractActive: () => false })

    expect(handleEscOverlayStack(ev, deps)).toBe(false)
    expect(ev.preventDefault).not.toHaveBeenCalled()
    expect(deps.closeNodeCard).not.toHaveBeenCalled()
    expect(deps.cancelInteract).not.toHaveBeenCalled()
  })

  it('ESC closes NodeCardOverlay first', () => {
    const ev = makeEvent({ key: 'Escape' })
    const deps = makeDeps({ isNodeCardOpen: () => true })

    expect(handleEscOverlayStack(ev, deps)).toBe(true)
    expect(ev.preventDefault).toHaveBeenCalledTimes(1)
    expect(deps.closeNodeCard).toHaveBeenCalledTimes(1)
    expect(deps.cancelInteract).not.toHaveBeenCalled()
  })

  it('ESC cancels interact when active and not consumed by nested overlays', () => {
    const ev = makeEvent({ key: 'Escape' })
    const deps = makeDeps({ dispatchInteractEsc: () => true, isFormLikeTarget: () => false })

    expect(handleEscOverlayStack(ev, deps)).toBe(true)
    expect(ev.preventDefault).toHaveBeenCalledTimes(1)
    expect(deps.cancelInteract).toHaveBeenCalledTimes(1)
  })

  it('ESC does not cancel interact when consumed by nested overlays', () => {
    const ev = makeEvent({ key: 'Escape' })
    const deps = makeDeps({ dispatchInteractEsc: () => false, isFormLikeTarget: () => false })

    expect(handleEscOverlayStack(ev, deps)).toBe(true)
    expect(ev.preventDefault).toHaveBeenCalledTimes(1)
    expect(deps.cancelInteract).not.toHaveBeenCalled()
  })

  it('ESC does not cancel interact when focus is in a form-like element', () => {
    const ev = makeEvent({ key: 'Escape', target: {} })
    const deps = makeDeps({ dispatchInteractEsc: () => true, isFormLikeTarget: () => true })

    expect(handleEscOverlayStack(ev, deps)).toBe(true)
    expect(ev.preventDefault).not.toHaveBeenCalled()
    expect(deps.cancelInteract).not.toHaveBeenCalled()
  })
})
