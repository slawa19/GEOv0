import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'

/**
 * RT-013-7, третья копия (`F-013-7`) — мутирующая кнопка в edge-detail.
 *
 * ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ РАСШИРЕНИЕ ТЕСТА ПАНЕЛИ. Находка `F-013-7` названа по одному
 * контролу, а предикат «предпочитаем REST только если массив непустой, иначе молча снапшот»
 * питает ТРИ мутирующих места: `TrustlineManagementPanel`, этот попап и — через
 * `wmEdgeDetailEffectiveLink` — его же в режиме keepAlive. Закрыть два из трёх и объявить находку
 * закрытой — это ровно та форма отказа, которую репозиторий записал как «закрыта не та половина
 * находки». Здесь закрывается третья.
 *
 * ЧТО ЗДЕСЬ СУДИТСЯ. Не источник данных — попап его не выбирает, он получает готовые числа. Судится
 * то, что при `sourceUnavailable` он ОТКАЗЫВАЕТСЯ разрешать закрытие линии и говорит почему.
 * Решение «источник не ответил» принимает корень (`SimulatorAppRoot.vue`,
 * `interactLinkSourceUnavailable`), и это разделение намеренное: у попапа нет доступа к состоянию
 * загрузки, а у корня — к тому, какая кнопка нажата.
 *
 * ЛОВУШКА, РАДИ КОТОРОЙ ЗДЕСЬ ДВА АССЕРТА, А НЕ ОДИН. `closeBlocked` в попапе означает «есть
 * долг» и вычисляется ИЗ ЧИСЕЛ. Когда чисел нет, оно ложно — то есть каскад «нет долга → можно
 * закрывать» разрешает мутацию ровно в тот момент, когда мы не знаем, есть ли долг. Поэтому
 * проверяется и то, что кнопка выключена, и то, что причина названа; «выключил, но молчит» и
 * «сказал, но разрешил» — разные неверные починки, и обе должны краснеть.
 */

// Тот же способ монтирования, что и у соседнего `EdgeDetailPopup.test.ts`: в этом проекте нет
// `@vue/test-utils`, компоненты поднимаются голым `createApp` в отдельный host-элемент.
function mountPopup(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const state = reactive({
    phase: 'editing-trustline',
    fromPid: 'alice',
    toPid: 'bob',
    selectedEdgeKey: 'alice→bob',
    edgeAnchor: { x: 100, y: 200 },
    error: null,
    lastClearing: null,
  })

  const defaultProps: Record<string, unknown> = {
    phase: state.phase,
    state,
    unit: 'UAH',
    used: '0',
    reverseUsed: '0',
    limit: '100',
    available: '100',
    status: 'active',
    busy: false,
    forceHidden: false,
    close: () => undefined,
  }

  const component: Component = EdgeDetailPopup
  const app = createApp({ render: () => h(component, { ...defaultProps, ...overrides }) })
  app.mount(host)
  return { app, host }
}

describe('RT-013-7 (третья копия): edge-detail не закрывает линию по числам из снапшота', () => {
  /**
   * СИТУАЦИЯ: авторитетный источник линий не ответил, а показанные числа приехали из снапшота
   *   через молчаливый фоллбэк `SimulatorAppRoot.vue:650-655`. Долга по этим числам нет.
   * ЧЕСТНЫЙ UI: закрытие линии недоступно, и сказано почему.
   * ЧТО БЫЛО: кнопка активна, потому что `closeBlocked` считает «долга нет» — по данным, которых
   *   у нас на самом деле нет.
   */
  it('выключает закрытие линии, когда источник не ответил', async () => {
    const { app, host } = mountPopup({ sourceUnavailable: true })
    await nextTick()
    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null

    expect(
      btn?.disabled,
      'кнопка закрытия линии активна при недоступном источнике: решение принимается по числам ' +
        'снапшота, а `closeBlocked` («есть долг») на пустых данных ложно — то есть каскад ' +
        'разрешает мутацию именно тогда, когда мы не знаем, есть ли долг',
    ).toBe(true)

    app.unmount()
    host.remove()
  })

  /**
   * Вторая половина: «выключил и промолчал» — тоже неверная починка. Оператор обязан видеть, что
   * это не свойство линии, а отсутствие ответа.
   */
  it('называет причину, а не просто гасит кнопку', async () => {
    const { app, host } = mountPopup({ sourceUnavailable: true })
    await nextTick()
    expect(
      !!host.querySelector('[data-testid="edge-source-unavailable"]'),
      'попап выключил действие и не сказал почему — оператор прочитает это как свойство линии, ' +
        'а не как отсутствие ответа от бэкенда',
    ).toBe(true)

    app.unmount()
    host.remove()
  })

  /**
   * КОНТРОЛЬ, без которого оба ассерта выше удовлетворялись бы починкой «выключить всё всегда».
   * При отвечающем источнике и нулевом долге закрытие обязано быть доступно, а сообщения — не быть.
   */
  it('на отвечающем источнике закрытие доступно и сообщения нет', async () => {
    const { app, host } = mountPopup({ sourceUnavailable: false })
    await nextTick()
    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
    expect(
      btn?.disabled,
      'закрытие выключено при живом источнике и нулевом долге — починка выродилась в «запретить всё»',
    ).toBe(false)
    expect(!!host.querySelector('[data-testid="edge-source-unavailable"]')).toBe(false)

    app.unmount()
    host.remove()
  })
})
