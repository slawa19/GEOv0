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
}

export const useHealthStore = defineStore('health', {
  state: (): HealthState => ({
    loading: false,
    error: null,
    health: null,
    healthDb: null,
    migrations: null,
    _timer: null,
  }),
  actions: {
    async refresh() {
      this.loading = true
      this.error = null
      try {
        this.health = assertSuccess(await api.health())
        this.healthDb = assertSuccess(await api.healthDb())
        this.migrations = assertSuccess(await api.migrations())
      } catch (e: any) {
        this.error = e?.message || 'Failed to load health'
      } finally {
        this.loading = false
      }
    },
    startPolling(intervalMs = 15000) {
      if (this._timer) return
      void this.refresh()
      this._timer = window.setInterval(() => void this.refresh(), intervalMs)
    },
    stopPolling() {
      if (!this._timer) return
      window.clearInterval(this._timer)
      this._timer = null
    },
  },
})
