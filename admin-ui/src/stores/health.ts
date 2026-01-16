import { defineStore } from 'pinia'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'

type HealthState = {
  loading: boolean
  error: string | null
  health: Record<string, unknown> | null
  healthDb: Record<string, unknown> | null
  migrations: Record<string, unknown> | null
  _timer: number | null
  _refreshPromise: Promise<void> | null
}

type GeoHealthPollGlobal = {
  __GEO_HEALTH_POLL_TIMER__?: number
}

function getHealthPollGlobal(): GeoHealthPollGlobal {
  return globalThis as unknown as GeoHealthPollGlobal
}

export const useHealthStore = defineStore('health', {
  state: (): HealthState => ({
    loading: false,
    error: null,
    health: null,
    healthDb: null,
    migrations: null,
    _timer: null,
    _refreshPromise: null,
  }),
  actions: {
    async refresh() {
      // Deduplicate concurrent refreshes (e.g. multiple callers during navigation/HMR).
      if (this._refreshPromise) return this._refreshPromise

      this.loading = true
      this.error = null

      this._refreshPromise = (async () => {
        try {
          this.health = assertSuccess(await api.health())
          this.healthDb = assertSuccess(await api.healthDb())
          this.migrations = assertSuccess(await api.migrations())
        } catch (e: unknown) {
          this.error = e instanceof Error ? e.message : 'Failed to load health'
        } finally {
          this.loading = false
          this._refreshPromise = null
        }
      })()

      return this._refreshPromise
    },
    startPolling(intervalMs = 15000) {
      // Keep polling singleton across HMR/module reloads.
      const g = getHealthPollGlobal()
      if (typeof g.__GEO_HEALTH_POLL_TIMER__ === 'number') {
        this._timer = g.__GEO_HEALTH_POLL_TIMER__
        return
      }

      void this.refresh()

      const id = window.setInterval(() => void this.refresh(), intervalMs)
      g.__GEO_HEALTH_POLL_TIMER__ = id
      this._timer = id
    },
    stopPolling() {
      const g = getHealthPollGlobal()
      const id = typeof g.__GEO_HEALTH_POLL_TIMER__ === 'number' ? g.__GEO_HEALTH_POLL_TIMER__ : this._timer
      if (typeof id !== 'number') return

      window.clearInterval(id)
      this._timer = null
      delete g.__GEO_HEALTH_POLL_TIMER__
    },
  },
})

// Ensure leaked timers are cleaned up when HMR swaps this module.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    const g = getHealthPollGlobal()
    const id = g.__GEO_HEALTH_POLL_TIMER__
    if (typeof id === 'number') window.clearInterval(id)
    delete g.__GEO_HEALTH_POLL_TIMER__
  })
}
