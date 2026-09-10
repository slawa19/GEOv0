import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import IncidentsPage from './IncidentsPage.vue'

/**
 * RT-013-5 (`F-013-5`, P3, решение владельца **B**) — экран делит пополам множество, вторая
 * половина которого пуста по построению.
 *
 * ЧТО ОТДАЁТ БЭКЕНД. `GET /admin/incidents` возвращает ТОЛЬКО уже просроченные транзакции:
 * `app/api/v1/admin.py:1011` считает `cutoff = now - sla`, а `:1019` фильтрует
 * `Transaction.updated_at < cutoff`. Значит у каждой возвращённой строки `age_seconds` заведомо
 * не меньше `sla_seconds` — это не рассуждение, это запинено бэкендовым тестом
 * `tests/unit/test_admin_incidents_list.py:84-85` (`sla_seconds == 60`, `age_seconds >= 60`).
 *
 * ЧТО ДЕЛАЕТ ЭКРАН. `IncidentsPage.vue` применяет к этому множеству ТОТ ЖЕ предикат
 * (`isOverSla`, `age_seconds > sla_seconds`) и печатает результат отдельным счётчиком
 * «SLA breaches: N», как будто это подмножество. Оно им не является: N всегда равно длине
 * списка. Оператор читает «из показанных N нарушают SLA» и делает вывод, которого в числе нет.
 *
 * ПОЧЕМУ ЭТО НЕ КОСМЕТИКА. Тавтологический счётчик неотличим от настоящего до тех пор, пока не
 * сравнишь его с длиной списка, а вывод из него делают как из настоящего. Это ровно тот класс,
 * ради которого заведена 013: экран показывает значение там, где значения нет.
 *
 * ЧЕГО ЭТОТ ТЕСТ НЕ ПРОВЕРЯЕТ. Он не судит бэкенд — правило `updated_at < cutoff` верное и
 * менять его 013 не имеет права (`## Owner surface`). Судится только то, что экран приписывает
 * этому множеству структуру, которой у него нет.
 */

const SLA_SECONDS = 60

/** Три строки ровно той формы, которую отдаёт бэкенд: каждая уже просрочена. */
const INCIDENTS = [
  { tx_id: 'TX_A', state: 'PREPARE_IN_PROGRESS', initiator_pid: 'PID_A', equivalent: 'UAH', age_seconds: 300, sla_seconds: SLA_SECONDS },
  { tx_id: 'TX_B', state: 'PREPARE_IN_PROGRESS', initiator_pid: 'PID_B', equivalent: 'UAH', age_seconds: 180, sla_seconds: SLA_SECONDS },
  { tx_id: 'TX_C', state: 'COMMIT_IN_PROGRESS', initiator_pid: 'PID_C', equivalent: 'HOUR', age_seconds: 61, sla_seconds: SLA_SECONDS },
]

const apiMock = vi.hoisted(() => ({
  listIncidents: vi.fn(),
  abortTx: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

function ok<T>(data: T) {
  return { success: true as const, data }
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
}

async function mountIncidents() {
  const router = makeRouter()
  await router.push('/')
  await router.isReady()
  const wrapper = mount(IncidentsPage, {
    global: { plugins: [ElementPlus, router] },
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

describe('RT-013-5: экран не делит множество, которое неделимо', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    apiMock.listIncidents.mockResolvedValue(ok({ items: INCIDENTS, total: INCIDENTS.length, page: 1, per_page: 20 }))
  })

  /**
   * СИТУАЦИЯ: бэкенд вернул три инцидента — то есть три уже просроченных платежа.
   * ЧЕСТНЫЙ UI: одно утверждение — «показаны N застрявших платежей, все за пределами SLA».
   * ЧТО ВМЕСТО: отдельный счётчик «SLA breaches: 3» рядом со списком из 3 строк, поданный как
   *   подмножество.
   * ЯКОРЬ РЕШЕНИЯ: `admin-ui/src/pages/IncidentsPage.vue` — `isOverSla` и `overSlaCount`.
   *
   * ОРАКУЛ — ЧИСЛО ПРОТИВ ДЛИНЫ СПИСКА, А НЕ ТЕКСТ ПОДПИСИ. Ассерт на подпись удовлетворялся бы
   * переименованием; здесь красным остаётся любой экран, который продолжает печатать счётчик,
   * тождественно равный длине, как будто он о чём-то говорит.
   */
  it('не печатает счётчик нарушений SLA, тождественно равный числу строк', async () => {
    const wrapper = await mountIncidents()
    const header = wrapper.text()

    // ЧАСОВОЙ НА САМ ТЕСТ, добавлен 2026-09-10 после того, как он это пропустил. Первая редакция
    // судила только шапку, и когда починка сломала рендер СТРОКИ (второй вызов снятого предиката
    // остался в `:class`), тест остался зелёным, а страница падала с unhandled rejection.
    // Отсутствие счётчика на не отрисовавшейся странице не значит ничего.
    for (const row of INCIDENTS) {
      expect(header, `страница не отрисовала строку ${row.tx_id} — судить её шапку бессмысленно`).toContain(row.tx_id)
    }

    const breaches = header.match(/SLA breaches:\s*(\d+)|Нарушений SLA:\s*(\d+)/)
    const printed = breaches ? Number(breaches[1] ?? breaches[2]) : null

    expect(
      printed,
      `экран печатает счётчик нарушений SLA (${printed}) рядом со списком из ${INCIDENTS.length} строк, ` +
        'каждая из которых просрочена по построению запроса (app/api/v1/admin.py:1011,1019). ' +
        'Число не может отличаться от длины списка ни при каком ответе сервера, то есть не несёт ' +
        'информации, а читается как подмножество',
    ).toBeNull()
  })

  /**
   * СИТУАЦИЯ: та же выдача.
   * ЧЕСТНЫЙ UI: сказать, что все показанные строки просрочены — один раз, как свойство списка.
   * ЧТО ВМЕСТО: этого утверждения на экране нет вовсе; вместо него счётчик-подмножество.
   *
   * Этот ассерт — вторая половина: без него «починка» могла бы просто убрать счётчик и оставить
   * оператора вообще без указания, что список означает.
   */
  it('говорит, что весь список уже за пределами SLA', async () => {
    const wrapper = await mountIncidents()
    expect(
      wrapper.text(),
      'на экране нет утверждения о том, что показанные строки уже просрочены — а это единственное, ' +
        'что про это множество правда, и единственное, что оператору нужно знать',
    ).toMatch(/past SLA|за пределами SLA/i)
  })
})
