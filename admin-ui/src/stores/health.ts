import { defineStore } from 'pinia'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { HEALTH_POLL_INTERVAL_MS } from '../constants/timing'
import { t } from '../i18n'

type HealthState = {
  loading: boolean
  error: string | null
  health: Record<string, unknown> | null
  healthDb: Record<string, unknown> | null
  migrations: Record<string, unknown> | null
  _timer: number | null
  _refreshPromise: Promise<void> | null
}

/**
 * Четыре состояния, потому что их четыре, а не два (`F-013-4`, `T1305`).
 *
 * `unknown` — мы ещё ничего не спросили или ни разу не получили ответа. Это НЕ «здоров»: экран,
 * рисующий зелёное до первого ответа, утверждает то, чего не знает.
 * `error`   — попытка была и не удалась.
 * `degraded` — ответ пришёл, HTTP 200, и в нём сказано, что что-то не так. Сегодня это ровно один
 *   случай: `migrations.is_up_to_date === false` (`app/api/v1/admin.py:1390` и путь отказа `:1393`,
 *   оба внутри 200). До этой правки ответ сохранялся и не читался НИКЕМ — проверено грепом.
 * `ok`      — спросили, ответили, и в ответе нет ни одного признака неисправности.
 */
export type HealthStatus = 'unknown' | 'error' | 'degraded' | 'ok'

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
  getters: {
    /**
     * ЕДИНСТВЕННЫЙ источник того, что рисует экран. Развилка живёт здесь, а не в шаблоне, потому
     * что `F-013-4` была построена ровно из двух мест сразу: ветка в `AppShell.vue` и то, чем её
     * кормит этот стор. Починка одного места оставила бы второе.
     */
    status(state): HealthStatus {
      if (state.error) return 'error'
      // Ни одного успешного ответа - сказать нечего. `healthDb` и `migrations` сюда не входят:
      // их отказ выбрасывает через `assertSuccess` и приходит уже как `error`.
      if (state.health === null) return 'unknown'
      if (state.migrations && state.migrations.is_up_to_date === false) return 'degraded'
      return 'ok'
    },
    /** Что именно не так, когда `degraded`. Для подсказки, не для развилки. */
    degradedReason(state): string | null {
      if (state.migrations && state.migrations.is_up_to_date === false) {
        const current = String(state.migrations.current_revision ?? '?')
        const head = String(state.migrations.head_revision ?? '?')
        return t('app.status.migrationsBehindDetail', { current, head })
      }
      return null
    },
  },
  actions: {
    async refresh() {
      // Deduplicate concurrent refreshes (e.g. multiple callers during navigation/HMR).
      if (this._refreshPromise) return this._refreshPromise

      this.loading = true
      // `error` НЕ обнуляется здесь, и это правка `F-013-4`, а не стилистика. Обнуление стояло
      // до трёх await ниже, поэтому известная поломка стиралась в начале КАЖДОГО опроса и экран
      // зеленел на всё время запросов - раз в интервал, бесконечно. Прошлый вердикт живёт до
      // нового: снимается он только успехом, ниже.

      this._refreshPromise = (async () => {
        try {
          this.health = assertSuccess(await api.health())
          this.healthDb = assertSuccess(await api.healthDb())
          this.migrations = assertSuccess(await api.migrations())
          this.error = null
        } catch (e: unknown) {
          this.error = e instanceof Error ? e.message : t('health.loadFailed')
        } finally {
          this.loading = false
          this._refreshPromise = null
        }
      })()

      return this._refreshPromise
    },
    startPolling(intervalMs = HEALTH_POLL_INTERVAL_MS) {
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
