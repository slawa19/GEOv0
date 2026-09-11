import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'
import {
  freezeTrustlineFiguresSource,
  type TrustlineFiguresSource,
} from '../composables/interact/trustlinesSourceState'

/**
 * `F-013-7`, ЭКРАННАЯ ПОЛОВИНА НАХОДКИ ПРО ЗАМОРОЗКУ (кросс-ревью, P3).
 *
 * Модульные тесты (`trustlinesSourceState.test.ts`) судят основание; здесь судится то, что из
 * него видно оператору. Оба нужны: основание может быть верным и никуда не доехать — ровно так
 * `trustlineSourceAnswered` и прожил всю прошлую волну.
 *
 * ДОСТИЖИМОСТЬ СОСТОЯНИЯ, а не гипотеза: окно edge-detail замораживается по «Send Payment»
 * (`SimulatorAppRoot.onEdgeDetailSendPayment` → `wmEdgeDetail.allowKeepAlive`), а кнопка
 * выключена только по `busy` МУТАЦИИ, а не по состоянию загрузки trustlines. Значит заморозка
 * в момент летящего запроса — обычный путь, а не угловой случай.
 */

function mountPopup(figuresSource: TrustlineFiguresSource) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const state = reactive({
    phase: 'editing-trustline',
    fromPid: 'alice',
    toPid: 'bob',
    selectedEdgeKey: 'alice→bob',
    edgeAnchor: { x: 10, y: 20 },
    error: null,
    lastClearing: null,
  })

  const component: Component = EdgeDetailPopup
  const app = createApp({
    render: () =>
      h(component, {
        phase: state.phase,
        state,
        unit: 'UAH',
        used: '12',
        reverseUsed: '0',
        limit: '100',
        available: '88',
        status: 'active',
        busy: false,
        forceHidden: false,
        figuresSource,
        close: () => undefined,
      }),
  })
  app.mount(host)
  return { app, host }
}

async function withPopup(source: TrustlineFiguresSource, fn: (host: HTMLElement) => void) {
  const { app, host } = mountPopup(source)
  await nextTick()
  try {
    fn(host)
  } finally {
    app.unmount()
    host.remove()
  }
}

function notices(host: HTMLElement) {
  const pick = (id: string) => {
    const el = host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
    return el ? (el.textContent ?? '').trim() : null
  }
  return {
    unavailable: pick('edge-source-unavailable'),
    noRow: pick('edge-no-trustline'),
    frozen: pick('edge-frozen-figures'),
  }
}

describe('RT-013-7f: замороженное окно отличимо от живого на экране', () => {
  /**
   * ЧТО РАЗЛИЧАЕТ: экран замороженного `loading` от экрана живого `loading`. До правки они
   *   совпадали БУКВАЛЬНО, включая фразу «is still loading from the backend» — то есть
   *   замороженная копия сообщала о запросе, который её не обновит.
   * ЧЕГО НЕ РАЗЛИЧАЕТ: она не судит, ЧТО именно написано про заморозку, — только что про неё
   *   сказано и что про живую загрузку не сказано.
   */
  it('замороженная загрузка не обещает обновления, которого не будет', async () => {
    const live: TrustlineFiguresSource = { kind: 'loading' }
    const frozen = freezeTrustlineFiguresSource(live)

    await withPopup(live, (host) => {
      const n = notices(host)
      expect(n.unavailable ?? '', 'живое окно обязано говорить про идущий запрос').toMatch(/still loading/i)
      expect(n.frozen, 'живое окно объявлено замороженной копией').toBeNull()
    })

    await withPopup(frozen, (host) => {
      const n = notices(host)
      const text = `${n.unavailable ?? ''} ${n.frozen ?? ''}`
      expect(
        text,
        'замороженная копия сообщает «данные ещё грузятся» — навсегда, потому что обновит этот ' +
          'запрос живое состояние, а не снимок',
      ).not.toMatch(/still loading/i)
      expect(n.frozen, 'замороженное окно на экране неотличимо от живого').not.toBeNull()
    })
  })

  /**
   * ВТОРАЯ ПОЛОВИНА. `no-row` и `never-asked`, будучи заморожены, давали ТОТ ЖЕ экран, что
   * живые, — при том что уведомление о заморозке заведено именно против такой неразличимости
   * (она была названа в прошлом ревью как причина его существования, и закрыта только для `row`).
   *
   * ЧТО РАЗЛИЧАЕТ: «заморозку помечают на любом основании» от «помечают только на `row`».
   * ЧЕГО НЕ РАЗЛИЧАЕТ: она не проверяет, что заморозка не ПОВЫСИЛА основание, — это делает
   *   соседний ассерт про сохранившееся уведомление `no-row` и модульный тест.
   */
  it.each([
    ['ответ «линии нет»', { kind: 'no-row' } as TrustlineFiguresSource, 'noRow' as const],
    ['молчание источника', { kind: 'never-asked' } as TrustlineFiguresSource, 'unavailable' as const],
  ])('замороженное «%s» названо замороженным и не теряет исходного смысла', async (_label, live, keep) => {
    await withPopup(live, (host) => {
      expect(notices(host).frozen, 'живое окно объявлено замороженным').toBeNull()
    })

    await withPopup(freezeTrustlineFiguresSource(live), (host) => {
      const n = notices(host)
      expect(n.frozen, 'заморозка не видна на экране — окно выдаёт снимок за живое состояние').not.toBeNull()
      expect(
        n[keep],
        'заморозка стёрла исходное основание: то, что говорилось секунду назад, обязано ' +
          'говориться и теперь',
      ).not.toBeNull()
    })
  })

  /**
   * КОНТРОЛЬ ПРОТИВ «ПОМЕТИТЬ ЗАМОРОЖЕННЫМ ВСЁ»: живой ответ со строкой — ни одного уведомления.
   */
  it('КОНТРОЛЬ: живой ответ со строкой молчит обо всём', async () => {
    await withPopup({ kind: 'row' }, (host) => {
      expect(notices(host)).toEqual({ unavailable: null, noRow: null, frozen: null })
    })
  })
})
