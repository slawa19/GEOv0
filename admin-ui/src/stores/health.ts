import { defineStore } from 'pinia'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'

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
        this.health = assertSuccess(await mockApi.health())
        this.healthDb = assertSuccess(await mockApi.healthDb())
        this.migrations = assertSuccess(await mockApi.migrations())
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
