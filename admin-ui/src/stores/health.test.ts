import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useHealthStore } from './health'

/**
 * RT-013-3 (F-013-4, P2, `specs/013-frontend-data-honesty/spec.md`) — сторона стора.
 *
 * ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. `spec.md:251-252`: сигнал здоровья строится из ДВУХ мест — развилки
 * в `AppShell.vue:226-242` и состояния в `stores/health.ts:40-41`, — и правка одного оставляет
 * второе. `AppShell.health.test.ts` судит отрисованную ветку; здесь судится то, что стор
 * вообще предлагает экрану различать.
 *
 * ЧТО СЛОМАНО. У стора нет третьего состояния. `error === null` в нём означает не «мы
 * проверили, и всё хорошо», а «ошибки сейчас не записано» — в том числе когда ещё ничего не
 * спрашивали (`health.ts:26-34`), когда ошибку только что стёрли началом нового цикла
 * (`health.ts:40-41`, до трёх await на `:45-47`) и когда бэкенд подтверждённо ответил, что
 * схема не на head (`health.ts:47` кладёт ответ, и никто его не читает).
 *
 * ПОЧЕМУ ОРАКУЛ — СОСТОЯНИЕ, А НЕ ТЕКСТ (контрпроверка `spec.md:224-226`). Подпись тега
 * (`t('app.status.ok')`) одинакова у «не знаем» и у «подтверждённо здорово», поэтому ассерт на
 * текст удовлетворяется дефектом и переживает «починку», которая только переписала слово.
 * Здесь текста нет вовсе: судится значение, из которого экран строит ветку. Переименование
 * подписи на эти ассерты не влияет никак — они останутся красными.
 *
 * ЕСЛИ ПОЧИНКА ВВЕДЁТ ЯВНОЕ ТРЕТЬЕ СОСТОЯНИЕ, `screenWouldReportHealthy` ниже обязана быть
 * обновлена ВМЕСТЕ с `AppShell.vue:228` — она транскрипция развилки, а не независимое правило.
 */

const apiMock = vi.hoisted(() => ({
  health: vi.fn(),
  healthDb: vi.fn(),
  migrations: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

function ok<T>(data: T) {
  return { success: true as const, data }
}

const HEALTH_OK = { status: 'ok' }
const HEALTH_DB_OK = { status: 'ok', dialect: 'postgresql' }
/** HTTP 200 и `is_up_to_date=true` (`app/api/v1/admin.py:1390`). */
const MIGRATIONS_UP_TO_DATE = { current_revision: 'a1b2c3', head_revision: 'a1b2c3', is_up_to_date: true }
/** Тот же HTTP 200, но подтверждённо плохая схема (`app/api/v1/admin.py:1390`, путь отказа `:1393`). */
const MIGRATIONS_BEHIND = { current_revision: 'a1b2c3', head_revision: 'z9y8x7', is_up_to_date: false }

function neverResolves<T>(): Promise<T> {
  return new Promise<T>(() => {})
}

type HealthStore = ReturnType<typeof useHealthStore>

/**
 * Транскрипция развилки экрана.
 *
 * КАК БЫЛО, и почему тест был красным: `AppShell.vue` читал РОВНО одно поле стора,
 * `healthStore.error`, и при пустом значении уходил в `v-else`, где стоял
 * `<el-tag type="success">`. Ни `loading`, ни `health`, ни `migrations` в развилку не входили,
 * поэтому «не спрашивали» и «спросили, всё хорошо» рисовались одинаково.
 *
 * КАК СТАЛО (`T1305`, 2026-09-10): развилка переехала в геттер `status` стора, и шаблон читает
 * только его. Транскрипция обновлена ВМЕСТЕ с шаблоном — ровно как требовал заголовок этого
 * файла: она следует за развилкой, а не живёт своей жизнью. Если развилка снова переедет,
 * переезжает и эта строка; если её оставить позади, тест начнёт судить несуществующий экран.
 */
function screenWouldReportHealthy(store: HealthStore): boolean {
  return store.status === 'ok'
}

/**
 * Что стор на самом деле подтвердил: получены все три ответа и миграции на head.
 * Это не «мнение теста» — ровно эти три поля стор и наполняет (`health.ts:45-47`),
 * а `is_up_to_date` — единственное поле ответа, которое утверждает исправность
 * (`app/api/v1/admin.py:1390`).
 */
function confirmedHealthy(store: HealthStore): boolean {
  if (store.health === null || store.healthDb === null || store.migrations === null) return false
  return (store.migrations as Record<string, unknown>).is_up_to_date === true
}

function stateDump(store: HealthStore): string {
  return JSON.stringify({
    loading: store.loading,
    error: store.error,
    health: store.health,
    healthDb: store.healthDb,
    migrations: store.migrations,
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  delete (globalThis as { __GEO_HEALTH_POLL_TIMER__?: number }).__GEO_HEALTH_POLL_TIMER__
  apiMock.health.mockReset()
  apiMock.healthDb.mockReset()
  apiMock.migrations.mockReset()
  apiMock.health.mockResolvedValue(ok(HEALTH_OK))
  apiMock.healthDb.mockResolvedValue(ok(HEALTH_DB_OK))
  apiMock.migrations.mockResolvedValue(ok(MIGRATIONS_UP_TO_DATE))
})

afterEach(() => {
  delete (globalThis as { __GEO_HEALTH_POLL_TIMER__?: number }).__GEO_HEALTH_POLL_TIMER__
})

describe('RT-013-3 — стор здоровья не отличает «не знаем» от «подтверждённо здорово»', () => {
  /**
   * СОСТОЯНИЕ: стор только что создан, `refresh()` не вызывался ни разу
   *   (`admin-ui/src/stores/health.ts:26-34`: `loading:false, error:null, health:null`).
   * ЧЕСТНЫЙ СТОР: отдаёт «неизвестно» — состояние, отличимое от подтверждённого здоровья.
   * ЧТО ВМЕСТО: отдаёт `error === null`, то есть ровно то же, что и после успешной проверки,
   *   и экран уходит в зелёную ветку.
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/stores/health.ts:26-34` (начальное состояние без «неизвестно»),
   *   потребитель — `admin-ui/src/layout/AppShell.vue:228`.
   */
  it('стор, у которого ничего не спрашивали, выглядит для экрана как подтверждённо здоровый', () => {
    const store = useHealthStore()

    expect(apiMock.health, 'предусловие: ни одного запроса ещё не сделано').not.toHaveBeenCalled()
    expect(confirmedHealthy(store), 'предусловие: стору ничего не подтверждено').toBe(false)

    expect(
      screenWouldReportHealthy(store),
      `стор, который ничего не спрашивал, отдаёт экрану сигнал «всё чисто»: ${stateDump(store)}. ` +
        'AppShell.vue:228 читает только error, а null здесь означает «не смотрели», ' +
        'а не «проверили и хорошо» (stores/health.ts:26-34)',
    ).toBe(false)
  })

  /**
   * СОСТОЯНИЕ: первый цикл опроса завершился ошибкой; начался следующий и висит на запросах.
   *   `admin-ui/src/stores/health.ts:40-41` выполняет `this.loading = true; this.error = null`
   *   ДО трёх await на `:45-47`, поэтому известная поломка стирается раньше, чем появляется
   *   чем её заменить. `startPolling` (`:58-71`) повторяет это на каждом опросе.
   * ЧЕСТНЫЙ СТОР: последнее известное — «сломано»; новый цикл ничего ещё не узнал и не имеет
   *   права выдавать сигнал «всё чисто».
   * ЧТО ВМЕСТО: `error` обнулён, `loading` в развилку экрана не входит — окно перезеленения.
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/stores/health.ts:40-41`; потребитель — `AppShell.vue:228`.
   */
  it('начало нового опроса стирает известную поломку раньше, чем узнаёт что-либо новое', async () => {
    const store = useHealthStore()

    apiMock.health.mockRejectedValueOnce(new Error('health probe failed'))
    await store.refresh()

    expect(store.error, 'предусловие: цикл действительно завершился ошибкой').not.toBeNull()
    const knownFailure = store.error

    apiMock.health.mockImplementation(() => neverResolves())
    void store.refresh()

    expect(
      store.error,
      `известная поломка ${JSON.stringify(knownFailure)} стёрта началом следующего опроса, ` +
        `хотя ни одного нового ответа ещё не пришло: ${stateDump(store)}. ` +
        'stores/health.ts:41 обнуляет error до await на :45-47',
    ).not.toBeNull()

    expect(
      screenWouldReportHealthy(store),
      `во время перепроверки после подтверждённого сбоя стор отдаёт экрану сигнал «всё чисто»: ${stateDump(store)}. ` +
        'loading=true развилку AppShell.vue:228 не интересует, поэтому тег зелёный всё время запросов',
    ).toBe(false)
  })

  /**
   * СОСТОЯНИЕ: все три запроса вернули HTTP 200, а `/admin/migrations` в этом 200 сообщает
   *   `is_up_to_date=false` — подтверждённо плохая схема (`app/api/v1/admin.py:1390`, тот же
   *   ответ на пути отказа `:1393`). `admin-ui/src/stores/health.ts:47` кладёт ответ в
   *   `migrations`, и ни один потребитель сигнала здоровья его не читает.
   * ЧЕСТНЫЙ СТОР: сигнал «всё чисто» не выдаётся — состояние подтверждено плохим.
   * ЧТО ВМЕСТО: исключения не было, значит `error === null`, значит для экрана «здорово».
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/stores/health.ts:47` — ответ сохранён и не участвует в сигнале;
   *   потребитель — `admin-ui/src/layout/AppShell.vue:228`.
   */
  it('подтверждённо отставшая схема в HTTP 200 не мешает стору отдавать сигнал «всё чисто»', async () => {
    const store = useHealthStore()

    apiMock.migrations.mockResolvedValue(ok(MIGRATIONS_BEHIND))
    await store.refresh()

    expect(store.error, 'предусловие: HTTP 200 — исключения нет').toBeNull()
    expect(
      (store.migrations as Record<string, unknown> | null)?.is_up_to_date,
      'предусловие: стор получил подтверждённо плохое состояние миграций',
    ).toBe(false)
    expect(confirmedHealthy(store), 'предусловие: подтверждённого здоровья нет').toBe(false)

    expect(
      screenWouldReportHealthy(store),
      `бэкенд подтвердил, что схема не на head, а стор отдаёт экрану сигнал «всё чисто»: ${stateDump(store)}. ` +
        'stores/health.ts:47 сохраняет ответ, но в сигнал здоровья он не входит',
    ).toBe(false)
  })
})
