import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'
import type { TrustlineFiguresSource } from '../composables/interact/trustlinesSourceState'

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
 * то, что при основании, которого не хватает для действия, он ОТКАЗЫВАЕТСЯ разрешать закрытие линии
 * и говорит почему. Решение «чем обоснованы эти числа» принимает корень (`SimulatorAppRoot.vue`,
 * `interactSelectedLinkFiguresSource`), и это разделение намеренное: у попапа нет доступа ни к
 * состоянию загрузки, ни к неслитому ответу источника, а у корня — к тому, какая кнопка нажата.
 *
 * ЛОВУШКА, РАДИ КОТОРОЙ ЗДЕСЬ ДВА АССЕРТА, А НЕ ОДИН. `closeBlocked` в попапе означает «есть
 * долг» и вычисляется ИЗ ЧИСЕЛ. Когда чисел нет, оно ложно — то есть каскад «нет долга → можно
 * закрывать» разрешает мутацию ровно в тот момент, когда мы не знаем, есть ли долг. Поэтому
 * проверяется и то, что кнопка выключена, и то, что причина названа; «выключил, но молчит» и
 * «сказал, но разрешил» — разные неверные починки, и обе должны краснеть.
 *
 * 2026-09-10, ВТОРОЙ ПРОХОД. Гард закрывал только «упал» и «в полёте». Здесь добавлены остальные
 * положения источника: «не спрашивали», «ответил, и линии у пары нет», «замороженный ответ» и —
 * отдельно — вызывающий, который не передал основание вовсе.
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

  // ВНИМАНИЕ: `figuresSource` здесь НЕ задан по умолчанию намеренно — «вызывающий забыл передать»
  // это отдельный судимый случай (см. последний тест), и общая заготовка не должна его прятать.
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

function closeButton(host: HTMLElement): HTMLButtonElement | null {
  return host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
}

function hasNotice(host: HTMLElement): boolean {
  return !!host.querySelector('[data-testid="edge-source-unavailable"]')
}

async function withPopup(
  overrides: Record<string, unknown>,
  fn: (host: HTMLElement) => void | Promise<void>,
) {
  const { app, host } = mountPopup(overrides)
  await nextTick()
  try {
    await fn(host)
  } finally {
    app.unmount()
    host.remove()
  }
}

const FAILED: TrustlineFiguresSource = { kind: 'failed', message: 'GET /runs/r1/trustlines failed: 503' }

describe('RT-013-7 (третья копия): edge-detail не закрывает линию по числам без основания', () => {
  /**
   * СИТУАЦИЯ: авторитетный источник линий не ответил, а показанные числа приехали из снапшота
   *   через молчаливый фоллбэк `SimulatorAppRoot.vue`. Долга по этим числам нет.
   * ЧЕСТНЫЙ UI: закрытие линии недоступно, и сказано почему.
   * ЧТО БЫЛО: кнопка активна, потому что `closeBlocked` считает «долга нет» — по данным, которых
   *   у нас на самом деле нет.
   */
  it('выключает закрытие линии, когда источник упал', async () => {
    await withPopup({ figuresSource: FAILED }, (host) => {
      expect(
        closeButton(host)?.disabled,
        'кнопка закрытия линии активна при упавшем источнике: решение принимается по числам ' +
          'снапшота, а `closeBlocked` («есть долг») на пустых данных ложно — то есть каскад ' +
          'разрешает мутацию именно тогда, когда мы не знаем, есть ли долг',
      ).toBe(true)
    })
  })

  /**
   * Вторая половина: «выключил и промолчал» — тоже неверная починка. Оператор обязан видеть, что
   * это не свойство линии, а отсутствие ответа.
   */
  it('называет причину, а не просто гасит кнопку', async () => {
    await withPopup({ figuresSource: FAILED }, (host) => {
      expect(
        hasNotice(host),
        'попап выключил действие и не сказал почему — оператор прочитает это как свойство линии, ' +
          'а не как отсутствие ответа от бэкенда',
      ).toBe(true)
    })
  })

  /**
   * СОСТОЯНИЕ «НЕ СПРАШИВАЛИ» — то самое начальное положение `useInteractDataCache`, в котором
   * панель находится до первого запроса: загрузки нет, ошибки нет, список пуст. Старый предикат
   * `(loading || error) && нет строки` его НЕ покрывал, и по нему всё было разрешено — при том что
   * подтвердить числа было решительно нечем.
   */
  it('выключает закрытие линии, когда источник ещё не спрашивали', async () => {
    await withPopup({ figuresSource: { kind: 'never-asked' } as TrustlineFiguresSource }, (host) => {
      expect(
        closeButton(host)?.disabled,
        '«у источника не спрашивали» отличается от «источник ответил» только состоянием источника: ' +
          'числа на экране в обоих случаях одни и те же, и в первом их не подтвердил никто',
      ).toBe(true)
      expect(hasNotice(host), 'состояние «не спрашивали» не названо оператору').toBe(true)
    })
  })

  /**
   * ГРАНИЦА, КОТОРУЮ ЛЕГКО ПЕРЕЙТИ В ОБЕ СТОРОНЫ.
   *
   * ЧТО ЗДЕСЬ ИЗМЕНИЛОСЬ 2026-09-10 И ПОЧЕМУ (внешнее ревью программы 013, находка P2). Прежняя
   * редакция требовала, чтобы при `no-row` закрытие линии ОСТАВАЛОСЬ ДОСТУПНЫМ, и обосновывала
   * это тем, что пустой ответ — настоящий ответ. Ответ он и есть; но разрешает он ровно одно —
   * СОЗДАТЬ линию. Закрывать в этом состоянии нечего: линии у пары нет, а `used`/`limit` в попапе
   * в этот момент приехали из снапшота — из того самого источника, который ответ и опроверг.
   * `closeBlocked` («есть долг») на них ложно, и прежний вердикт разрешал мутацию именно там, где
   * предмета мутации не существует.
   *
   * ЧТО СОХРАНЕНО: `no-row` НЕ РАВЕН МОЛЧАНИЮ, и говорить о нём надо другими словами. Поэтому
   * `edge-source-unavailable` здесь обязан отсутствовать (источник ответил), а отдельное
   * сообщение `edge-no-trustline` — присутствовать. Про то, что создание при `no-row` не гасится,
   * судит корневой тест RT-013-7d2: в попапе кнопки создания нет.
   */
  it('на ответившем источнике без строки для пары закрывать нечего — и это сказано отдельными словами', async () => {
    await withPopup({ figuresSource: { kind: 'no-row' } as TrustlineFiguresSource }, (host) => {
      expect(
        closeButton(host)?.disabled,
        'закрытие предлагается при ответе «линии у этой пары нет»: закрывать нечего, а решение ' +
          'принимается по снапшотным числам, которые этот же ответ опроверг',
      ).toBe(true)
      expect(
        hasNotice(host),
        'ОТВЕТ источника объявлен его ОТСУТСТВИЕМ: бэкенд ответил, и это разные состояния',
      ).toBe(false)
      const noRow = host.querySelector('[data-testid="edge-no-trustline"]') as HTMLElement | null
      expect(noRow, 'действие выключено молча: оператор прочитает это как отказ, а не как факт').toBeTruthy()
      expect((noRow?.textContent ?? '').toLowerCase()).toContain('no trustline')
    })
  })

  /**
   * `keepAlive`: попап показывает ЗАМОРОЖЕННУЮ линию — ответ, снятый раньше. Это вопрос
   * устаревания, а не отсутствия, и блокировать там нечего.
   */
  it('на замороженной линии (keepAlive) действие остаётся доступным, но живой она не объявляется', async () => {
    await withPopup({ figuresSource: { kind: 'frozen' } as TrustlineFiguresSource }, (host) => {
      expect(closeButton(host)?.disabled, 'заморозка — это устаревание, а не отсутствие').toBe(false)
      expect(hasNotice(host)).toBe(false)
      // Внешнее ревью 013 (P3): без этого ассерта `frozen` наблюдаемо совпадает с `row`, и
      // «заморозили настоящий ответ» ничем не отличается от «показываем живой».
      expect(
        host.querySelector('[data-testid="edge-frozen-figures"]'),
        'замороженная копия предъявлена как живой ответ',
      ).toBeTruthy()
    })
  })

  /**
   * FAIL-OPEN DEFAULT НА FAIL-CLOSED ГАРДЕ. Раньше основание было НЕОБЯЗАТЕЛЬНЫМ пропом со
   * значением по умолчанию «всё в порядке»: вызывающий, забывший его передать, молча получал
   * разрешённую мутацию. Так смонтирован, например, legacy-снимок разметки. Пропущенное основание
   * обязано читаться как «оснований нет».
   */
  it('вызывающий, не передавший основание, получает запрет, а не разрешение', async () => {
    await withPopup({}, (host) => {
      expect(
        closeButton(host)?.disabled,
        'проп с основанием необязателен и по умолчанию разрешает мутацию — это fail-open умолчание ' +
          'на fail-closed гарде: достаточно забыть один атрибут, чтобы гарда не стало',
      ).toBe(true)
      expect(hasNotice(host)).toBe(true)
    })
  })

  /**
   * КОНТРОЛЬ, без которого ассерты выше удовлетворялись бы починкой «выключить всё всегда».
   * При отвечающем источнике со строкой и нулевом долге закрытие обязано быть доступно.
   */
  it('на отвечающем источнике закрытие доступно и сообщения нет', async () => {
    await withPopup({ figuresSource: { kind: 'row' } as TrustlineFiguresSource }, (host) => {
      expect(
        closeButton(host)?.disabled,
        'закрытие выключено при живом источнике и нулевом долге — починка выродилась в «запретить всё»',
      ).toBe(false)
      expect(hasNotice(host)).toBe(false)
    })
  })
})
