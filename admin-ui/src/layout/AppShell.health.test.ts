import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElTag } from 'element-plus'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from './AppShell.vue'
import { useHealthStore } from '../stores/health'
import { setLocale, type Locale } from '../i18n'

/**
 * RT-013-3 (F-013-4, P2, `specs/013-frontend-data-honesty/spec.md`) — сторона экрана.
 *
 * ЧТО СЛОМАНО. У индикатора здоровья в шапке две ветки, а у правды — три состояния.
 * `AppShell.vue:228` — `v-if="healthStore.error"` рисует `<el-tag type="danger">`;
 * `AppShell.vue:237-239` — `v-else` рисует `<el-tag type="success">`. Третьей ветки нет,
 * поэтому «мы не знаем» выводится той же веткой, что и «подтверждено здорово».
 * Единственное, что читает эта развилка, — `healthStore.error`; ни `loading`, ни `health`,
 * ни `migrations.is_up_to_date` она не читает вовсе.
 *
 * ПОЧЕМУ ОРАКУЛ — ВЕТКА, А НЕ ТЕКСТ (контрпроверка `spec.md:224-226`).
 * Неизвестное состояние и подтверждённо здоровое печатают ОДНУ И ТУ ЖЕ строку
 * `t('app.status.ok')`. Значит ассерт на текст удовлетворяется и дефектом (обе ветки дают
 * «ok»), и «починкой», которая только переписала подпись (например на «ok?»): текст стал
 * другим — ассерт позеленел — а веток по-прежнему две, и оператор по-прежнему видит зелёное
 * там, где системе ничего не известно. Поэтому здесь судится ОТРИСОВАННАЯ ВЕТКА: проп `type`
 * у `el-tag` и сравнение с контрольным рендером подтверждённо здоровой системы. Пока третьей
 * ветки нет, `type` в обоих случаях равен `success`, и тест красный; переименование подписи
 * на `type` не влияет и тест зелёным не сделает. НЕ ЗАМЕНЯТЬ ЭТИ АССЕРТЫ НА `.text()`.
 */

const apiMock = vi.hoisted(() => ({
  health: vi.fn(),
  healthDb: vi.fn(),
  migrations: vi.fn(),
  getConfig: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

function ok<T>(data: T) {
  return { success: true as const, data }
}

const HEALTH_OK = { status: 'ok' }
const HEALTH_DB_OK = { status: 'ok', dialect: 'postgresql' }
/** Ответ `/admin/migrations`, у которого HTTP 200 и `is_up_to_date=true` (`app/api/v1/admin.py:1390`). */
const MIGRATIONS_UP_TO_DATE = { current_revision: 'a1b2c3', head_revision: 'a1b2c3', is_up_to_date: true }
/** Тот же 200, но подтверждённо плохое состояние схемы (`app/api/v1/admin.py:1390`, путь отказа `:1393`). */
const MIGRATIONS_BEHIND = { current_revision: 'a1b2c3', head_revision: 'z9y8x7', is_up_to_date: false }

/** Запрос, ответ на который никогда не приходит: состояние «спросили, но ещё не знаем». */
function neverResolves<T>(): Promise<T> {
  return new Promise<T>(() => {})
}

function serveHealthy(): void {
  apiMock.health.mockResolvedValue(ok(HEALTH_OK))
  apiMock.healthDb.mockResolvedValue(ok(HEALTH_DB_OK))
  apiMock.migrations.mockResolvedValue(ok(MIGRATIONS_UP_TO_DATE))
}

type Mounted = { wrapper: VueWrapper; pinia: Pinia; store: ReturnType<typeof useHealthStore> }

/** Ни `flushPromises`, ни `nextTick` здесь нет намеренно: первый кадр обязан быть наблюдаем. */
function mountShell(): Mounted {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      // Корневой маршрут нужен только чтобы стартовая локация memory-history разрешалась
      // без предупреждений роутера: шапка судится сама по себе, страница здесь пустая.
      { path: '/', component: { template: '<div />' } },
      { path: '/dashboard', component: { template: '<div />' } },
    ],
  })
  void router.push('/dashboard')
  const wrapper = mount(AppShell, { global: { plugins: [pinia, router, ElementPlus] } })
  return { wrapper, pinia, store: useHealthStore(pinia) }
}

type Branch = {
  /** Проп `type` у `el-tag` индикатора — та самая отрисованная ветка. */
  type: string
  /** Подпись. Собирается только ради текста падения; ассертов на неё нет и быть не должно. */
  label: string
  /** Сколько тегов состояния вообще нарисовано: третьего элемента сегодня не существует. */
  statusTagCount: number
}

/**
 * Индикатор здоровья — первый `el-tag` внутри `.status` (`AppShell.vue:226-242`).
 * Форма проверяется, а не предполагается: если разметка съедет, тест упадёт громко,
 * а не тихо начнёт судить чужой тег.
 */
function healthBranch(wrapper: VueWrapper): Branch {
  const status = wrapper.find('.status')
  expect(status.exists(), 'AppShell больше не рисует .status — репродьюсер судит не то место').toBe(true)

  const statusTags = wrapper
    .findAllComponents(ElTag)
    .filter((tag) => (tag.element as HTMLElement).closest('.status') !== null)
  expect(statusTags.length, 'в .status не осталось ни одного el-tag — индикатор здоровья не найден').toBeGreaterThan(0)

  // `expect(...).toBeGreaterThan(0)` убеждает vitest, но не `vue-tsc`: под `noUncheckedIndexedAccess`
  // индексный доступ всё равно даёт `T | undefined`. Проверяем явно, иначе сборка админки красная,
  // а сборка - гейт (`npm --prefix admin-ui run build` включает проверку типов).
  const indicator = statusTags[0]
  if (!indicator) throw new Error('в .status не осталось ни одного el-tag — индикатор здоровья не найден')
  return {
    type: String(indicator.props('type') ?? ''),
    label: indicator.text().trim(),
    statusTagCount: statusTags.length,
  }
}

/**
 * Контроль: как выглядит ветка, когда система подтверждённо здорова — все три ответа
 * получены и `is_up_to_date=true`. Любое неизвестное или подтверждённо плохое состояние
 * обязано отрисоваться ДРУГОЙ веткой, иначе экран не различает правду.
 */
async function confirmedHealthyBranch(labelLocale: Locale = 'en'): Promise<Branch> {
  serveHealthy()
  setLocale(labelLocale)
  const { wrapper } = mountShell()
  await flushPromises()
  await nextTick()
  const branch = healthBranch(wrapper)
  wrapper.unmount()
  return branch
}

function describeBranch(b: Branch): string {
  return `el-tag type=${JSON.stringify(b.type)} (подпись ${JSON.stringify(b.label)}), тегов в .status: ${b.statusTagCount}`
}

const mounted: VueWrapper[] = []

function track(m: Mounted): Mounted {
  mounted.push(m.wrapper)
  return m
}

beforeEach(() => {
  // `startPolling` — синглтон через globalThis (`stores/health.ts:58-71`): не убрав таймер,
  // следующий mount вообще не сделает ни одного запроса.
  delete (globalThis as { __GEO_HEALTH_POLL_TIMER__?: number }).__GEO_HEALTH_POLL_TIMER__
  setLocale('en')
  apiMock.health.mockReset()
  apiMock.healthDb.mockReset()
  apiMock.migrations.mockReset()
  apiMock.getConfig.mockReset()
  apiMock.getConfig.mockResolvedValue(ok({}))
  serveHealthy()
})

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  delete (globalThis as { __GEO_HEALTH_POLL_TIMER__?: number }).__GEO_HEALTH_POLL_TIMER__
  setLocale('en')
})

describe('RT-013-3 — индикатор здоровья AppShell выдаёт незнание за здоровье', () => {
  /**
   * СОСТОЯНИЕ: экран только что смонтирован. `stores/health.ts:26-34` — начальное состояние
   *   `loading:false, error:null, health:null`; первый кадр шапки рисуется ДО того, как
   *   `onMounted` (`AppShell.vue:73-78`) запустит опрос, и ни одного ответа не получено.
   * ЧЕСТНЫЙ UI: «состояние неизвестно» — отдельная ветка (серый/жёлтый тег либо его отсутствие).
   * ЧТО ВМЕСТО: `<el-tag type="success">` — ровно та же ветка, что у подтверждённо здоровой системы.
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/layout/AppShell.vue:237-239` (`v-else` без третьей ветки),
   *   вход в развилку — `admin-ui/src/layout/AppShell.vue:228`,
   *   источник пустоты — `admin-ui/src/stores/health.ts:26-34`.
   */
  it('экран, не получивший ни одного ответа, докладывает, что система здорова', async () => {
    // Контроль снимается под ДРУГОЙ локалью — это и есть проигранная здесь «починка, которая
    // переписала только подпись»: строки тегов гарантированно разные («ок» против «ok»),
    // а ветка обязана остаться той же. Ассерты ниже сравнивают только `type`, поэтому разница
    // подписей их не спасает; ассерт на текст в этой же расстановке позеленел бы ложно.
    const healthy = await confirmedHealthyBranch('ru')
    setLocale('en')

    apiMock.health.mockImplementation(() => neverResolves())
    apiMock.healthDb.mockImplementation(() => neverResolves())
    apiMock.migrations.mockImplementation(() => neverResolves())

    const { wrapper, store } = track(mountShell())
    const unknown = healthBranch(wrapper)

    expect(store.health, 'предусловие: экран ещё ничего не узнал о здоровье').toBeNull()
    expect(store.healthDb, 'предусловие: экран ещё ничего не узнал о БД').toBeNull()
    expect(store.migrations, 'предусловие: экран ещё ничего не узнал о миграциях').toBeNull()
    expect(
      unknown.label,
      'предусловие контрпроверки: подписи двух рендеров РАЗНЫЕ — оракулом остаётся ветка, не текст',
    ).not.toBe(healthy.label)

    expect(
      unknown.type,
      `экран, которому ничего не ответили, рисует ветку подтверждённого здоровья: ${describeBranch(unknown)}. ` +
        'AppShell.vue:228 читает только healthStore.error, а он null не потому что всё хорошо, ' +
        'а потому что ещё не спрашивали (stores/health.ts:26-34)',
    ).not.toBe('success')

    expect(
      unknown.type,
      `«состояние неизвестно» и «подтверждённо здорово» отрисованы ОДНОЙ веткой: ${describeBranch(unknown)} ` +
        `против контрольной ${describeBranch(healthy)}. Третьего элемента в AppShell.vue:226-242 не существует`,
    ).not.toBe(healthy.type)
  })

  /**
   * СОСТОЯНИЕ: предыдущий цикл опроса закончился ошибкой, начался следующий и ещё висит на
   *   запросах. `stores/health.ts:40-41` ставит `loading = true; error = null` ДО трёх await
   *   на `:45-47`, поэтому ошибка стирается раньше, чем появляется чем её заменить.
   *   `startPolling` (`stores/health.ts:58-71`) повторяет это каждые HEALTH_POLL_INTERVAL_MS.
   * ЧЕСТНЫЙ UI: последнее известное — «сломано», идёт перепроверка. Зелёного здесь быть не может.
   * ЧТО ВМЕСТО: тег снова зелёный на всё время запросов — окно перезеленения на каждом опросе.
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/stores/health.ts:40-41` (сброс до await на `:45-47`) плюс
   *   `admin-ui/src/layout/AppShell.vue:228` — развилка читает только `error` и не читает `loading`.
   */
  it('на каждом опросе экран зеленеет заново поверх уже известной поломки', async () => {
    const healthy = await confirmedHealthyBranch()

    apiMock.health.mockRejectedValueOnce(new Error('health probe failed'))
    const { wrapper, store } = track(mountShell())
    await flushPromises()
    await nextTick()

    const failed = healthBranch(wrapper)
    expect(store.error, 'предусловие: цикл опроса действительно завершился ошибкой').not.toBeNull()
    expect(failed.type, 'предусловие: подтверждённая поломка рисуется веткой danger (AppShell.vue:233)').toBe('danger')

    // Следующий цикл опроса: запросы отправлены, ответов ещё нет.
    apiMock.health.mockImplementation(() => neverResolves())
    void store.refresh()
    await nextTick()

    const reGreened = healthBranch(wrapper)
    expect(
      reGreened.type,
      `известная поломка стёрта началом следующего опроса, экран снова рисует ${describeBranch(reGreened)}. ` +
        'stores/health.ts:41 обнуляет error до await на :45-47, а AppShell.vue:228 больше ничего не читает',
    ).not.toBe('success')

    expect(
      reGreened.type,
      '«перепроверяем после сбоя» и «подтверждённо здорово» отрисованы ОДНОЙ веткой: ' +
        `${describeBranch(reGreened)} против контрольной ${describeBranch(healthy)}`,
    ).not.toBe(healthy.type)
  })

  /**
   * СОСТОЯНИЕ: цикл опроса УЖЕ проходил успешно (`health` наполнен), затем следующий упал, затем
   *   начался третий и висит на запросах.
   * ЧЕСТНЫЙ UI: последнее известное — «сломано», идёт перепроверка.
   * ЧТО ВМЕСТО: экран зеленеет, и это ХУЖЕ соседнего случая выше — там за зелёным не стояло
   *   ничего, а здесь за ним стоит УСТАРЕВШИЙ успешный ответ, который выглядит как основание.
   *
   * ЗАЧЕМ ЭТОТ СЛУЧАЙ ОТДЕЛЬНО, и это измерено, а не предположено. В соседнем тесте выше первый
   * же опрос падает, поэтому `health` остаётся `null`, и при восстановленном дефекте экран
   * попадает в ветку «неизвестно» — ассерт `not.toBe('success')` проходит, дефект не пойман.
   * Проверено мутацией: возврат `error = null` до await роняет один тест из двух. Здесь
   * `health` уже наполнен, поэтому «неизвестно» не спасает, и единственное, что отделяет экран
   * от зелёного, — сохранённый прошлый вердикт.
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/stores/health.ts` — `error` снимается только успехом.
   */
  it('после успешного цикла и последующего сбоя перепроверка не возвращает зелёное', async () => {
    const healthy = await confirmedHealthyBranch()

    apiMock.health.mockResolvedValue(ok(HEALTH_OK))
    apiMock.healthDb.mockResolvedValue(ok(HEALTH_DB_OK))
    apiMock.migrations.mockResolvedValue(ok(MIGRATIONS_UP_TO_DATE))
    const { wrapper, store } = track(mountShell())
    await flushPromises()
    await nextTick()
    expect(
      healthBranch(wrapper).type,
      'предусловие: первый цикл прошёл успешно и экран действительно зелёный',
    ).toBe('success')
    expect(store.health, 'предусловие: успешный ответ сохранён — именно он делает случай отличным от соседнего').not.toBeNull()

    apiMock.health.mockRejectedValueOnce(new Error('health probe failed'))
    await store.refresh()
    await nextTick()
    expect(
      healthBranch(wrapper).type,
      'предусловие: сбой второго цикла виден на экране',
    ).toBe('danger')

    // Третий цикл: запросы отправлены, ответов ещё нет.
    apiMock.health.mockImplementation(() => neverResolves())
    void store.refresh()
    await nextTick()

    const reChecking = healthBranch(wrapper)
    expect(
      reChecking.type,
      `известная поломка стёрта началом перепроверки, и под зелёным лежит устаревший успешный ` +
        `ответ: ${describeBranch(reChecking)}`,
    ).not.toBe('success')
    expect(
      reChecking.type,
      '«перепроверяем после сбоя, имея старый успех» и «подтверждённо здорово» отрисованы ОДНОЙ ' +
        `веткой: ${describeBranch(reChecking)} против контрольной ${describeBranch(healthy)}`,
    ).not.toBe(healthy.type)
  })

  /**
   * СОСТОЯНИЕ: все три запроса вернули HTTP 200, но `/admin/migrations` в этом 200 сообщает
   *   `is_up_to_date=false` — подтверждённо плохое состояние схемы (`app/api/v1/admin.py:1390`;
   *   путь отказа `:1393` отдаёт то же самое, тоже в 200). Стор кладёт ответ в `migrations`
   *   (`stores/health.ts:47`), и НИКТО его для тега не читает.
   * ЧЕСТНЫЙ UI: тег не зелёный — состояние подтверждено плохим, а не неизвестным.
   * ЧТО ВМЕСТО: `<el-tag type="success">`, потому что исключения не было, значит `error === null`.
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/layout/AppShell.vue:228` — развилка знает только про `error`,
   *   при данных, положенных `admin-ui/src/stores/health.ts:47`.
   */
  it('подтверждённо отставшая схема (HTTP 200, is_up_to_date=false) показывается как здоровье', async () => {
    const healthy = await confirmedHealthyBranch()

    apiMock.migrations.mockResolvedValue(ok(MIGRATIONS_BEHIND))
    const { wrapper, store } = track(mountShell())
    await flushPromises()
    await nextTick()

    expect(store.error, 'предусловие: HTTP 200 — исключения нет').toBeNull()
    expect(
      (store.migrations as Record<string, unknown> | null)?.is_up_to_date,
      'предусловие: стор получил подтверждённо плохое состояние миграций',
    ).toBe(false)

    const bad = healthBranch(wrapper)
    expect(
      bad.type,
      `бэкенд подтвердил, что схема не на head, а экран рисует ${describeBranch(bad)}. ` +
        'stores/health.ts:47 сохраняет ответ, но развилка AppShell.vue:228 его не читает',
    ).not.toBe('success')

    expect(
      bad.type,
      '«схема отстала» и «подтверждённо здорово» отрисованы ОДНОЙ веткой: ' +
        `${describeBranch(bad)} против контрольной ${describeBranch(healthy)}`,
    ).not.toBe(healthy.type)
  })
})
