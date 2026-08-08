import { describe, expect, it } from 'vitest'
import { effectScope } from 'vue'

import { useLatestRequest } from './useLatestRequest'

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useLatestRequest', () => {
  it('keeps the latest data and loading state when an older request resolves last', async () => {
    const requests = useLatestRequest()
    const state = { data: 'initial', error: null as string | null, loading: false }

    async function load(input: Promise<string>) {
      const request = requests.begin()
      state.loading = true
      state.error = null
      try {
        const value = await input
        if (!request.isCurrent()) return
        state.data = value
      } catch (error: unknown) {
        if (!request.isCurrent()) return
        state.error = error instanceof Error ? error.message : String(error)
      } finally {
        if (request.isCurrent()) state.loading = false
      }
    }

    const older = deferred<string>()
    const latest = deferred<string>()
    const olderLoad = load(older.promise)
    const latestLoad = load(latest.promise)

    latest.resolve('latest')
    await latestLoad
    expect(state).toEqual({ data: 'latest', error: null, loading: false })

    older.resolve('stale')
    await olderLoad
    expect(state).toEqual({ data: 'latest', error: null, loading: false })
  })

  it('ignores an older rejection and invalidates pending work on scope disposal', async () => {
    const scope = effectScope()
    const requests = scope.run(() => useLatestRequest())
    if (!requests) throw new Error('Expected request owner')

    const older = requests.begin()
    const latest = requests.begin()
    expect(older.isCurrent()).toBe(false)
    expect(latest.isCurrent()).toBe(true)

    scope.stop()
    expect(latest.isCurrent()).toBe(false)

    const rejection = deferred<never>()
    const state = { error: null as string | null, loading: true }
    const task = (async () => {
      try {
        await rejection.promise
      } catch (error: unknown) {
        if (latest.isCurrent()) state.error = error instanceof Error ? error.message : String(error)
      } finally {
        if (latest.isCurrent()) state.loading = false
      }
    })()
    rejection.reject(new Error('stale failure'))
    await task

    expect(state).toEqual({ error: null, loading: true })
  })
})
