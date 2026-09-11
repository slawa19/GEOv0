import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'
import type { TrustlineFiguresSource } from '../composables/interact/trustlinesSourceState'

/**
 * `F-013-7`, ПРЕЗЕНТАЦИОННАЯ ПОЛОВИНА В ТРЕТЬЕЙ КОПИИ ГАРДА (внешнее кросс-ревью, находка P2).
 *
 * ЧТО БЫЛО ЗАКРЫТО РАНЬШЕ И ЧТО ОСТАВАЛОСЬ ОТКРЫТЫМ. Формулировка находки называет три вещи
 * сразу: сохранить авторитетное ОТСУТСТВИЕ, разрешить СОЗДАНИЕ и перестать ПРЕДЪЯВЛЯТЬ строку
 * снапшота как строку существующей линии. Мутирующая половина закрыта в обоих компонентах;
 * презентационная — только в `TrustlineManagementPanel` (`effectiveData` отдаёт `null`), и не
 * была закрыта в `EdgeDetailPopup`. То есть одно и то же состояние источника два компонента
 * показывали по-разному: панель — `Used — / Limit — / Available —`, попап — `Used 12 UAH /
 * Limit 100 UAH / Available 88 UAH / Status active`, полосу загрузки `12%` и фразу «Cannot close:
 * trustline has outstanding debt (used: 12 UAH)» — ПОЛОЖИТЕЛЬНОЕ УТВЕРЖДЕНИЕ о линии, про
 * которую бэкенд только что ответил, что её нет, и напечатанное двумя элементами ниже
 * уведомления `edge-no-trustline`, которое говорит обратное.
 *
 * ЧТО СУДИТСЯ ЗДЕСЬ. Не кнопка (она выключена и это судит `EdgeDetailPopup.sourceUnavailable.test.ts`),
 * а ТЕКСТ НА ЭКРАНЕ: в состоянии без существующей линии попап не делает о ней ни одного
 * положительного утверждения — ни числом в сетке, ни полосой утилизации, ни фразой про долг.
 *
 * РАЗЛИЧАЮЩАЯ СИЛА ВЫБОРКИ (что она ловит и чего не ловит) — в комментарии над каждым тестом.
 */

type Props = Record<string, unknown>

function mountPopup(overrides: Props = {}) {
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

  // ФИКСТУРА ИЗ САМОЙ НАХОДКИ: ровно те числа, которыми ревьюер предъявил противоречие.
  // Долг НЕНУЛЕВОЙ намеренно: на нулевом долге фраза про долг не печатается и тест, написанный
  // на нулях, не увидел бы самое громкое из ложных утверждений.
  const defaultProps: Props = {
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
    close: () => undefined,
  }

  const component: Component = EdgeDetailPopup
  const app = createApp({ render: () => h(component, { ...defaultProps, ...overrides }) })
  app.mount(host)
  return { app, host }
}

async function withPopup(overrides: Props, fn: (host: HTMLElement) => void | Promise<void>) {
  const { app, host } = mountPopup(overrides)
  await nextTick()
  try {
    await fn(host)
  } finally {
    app.unmount()
    host.remove()
  }
}

/** Значения сетки в порядке шаблона: Used, Limit, Available, Status. */
function gridValues(host: HTMLElement): string[] {
  const grid = host.querySelector('.popup__grid')
  expect(grid, 'сетка чисел исчезла из разметки целиком — это другой компонент, а не другой текст').toBeTruthy()
  return Array.from(grid!.querySelectorAll('.ds-value')).map((el) => (el.textContent ?? '').trim())
}

function utilization(host: HTMLElement) {
  const bar = host.querySelector('[role="progressbar"]') as HTMLElement | null
  const fill = host.querySelector('.popup__util-fill') as HTMLElement | null
  const pct = host.querySelector('[data-testid="edge-utilization-pct"]') as HTMLElement | null
  return {
    label: (pct?.textContent ?? '').trim(),
    valuetext: bar?.getAttribute('aria-valuetext') ?? null,
    valuenow: bar?.getAttribute('aria-valuenow') ?? null,
    width: fill?.style.width ?? null,
  }
}

function debtSentence(host: HTMLElement): string | null {
  const el = host.querySelector('[data-testid="edge-close-blocked"]') as HTMLElement | null
  return el ? (el.textContent ?? '').trim() : null
}

const NO_ROW: TrustlineFiguresSource = { kind: 'no-row' }
const NEVER_ASKED: TrustlineFiguresSource = { kind: 'never-asked' }
const FAILED: TrustlineFiguresSource = { kind: 'failed', message: 'GET /runs/r1/trustlines failed: 503' }
const LOADING: TrustlineFiguresSource = { kind: 'loading' }
const ROW: TrustlineFiguresSource = { kind: 'row' }
const FROZEN: TrustlineFiguresSource = { kind: 'frozen' }

describe('RT-013-7p: edge-detail не утверждает ничего о линии, которой нет', () => {
  /**
   * СИТУАЦИЯ: бэкенд ОТВЕТИЛ, и линии у пары alice→bob нет. Числа в пропах приехали из
   *   снапшотного фоллбэка `SimulatorAppRoot.interactSelectedLink`.
   * ЧЕСТНЫЙ UI: ни одно из этих чисел не предъявлено как состояние линии.
   *
   * ЧТО ЭТА ВЫБОРКА РАЗЛИЧАЕТ: реализацию, которая гасит числа, от исходной (печатает 12/100/88)
   *   и от «погасил сетку, но оставил полосу/фразу про долг» — каждый из трёх ассертов падает
   *   на своей половинчатой починке.
   * ЧЕГО НЕ РАЗЛИЧАЕТ: она НЕ отличает «погасить по `canActOnTrustlineFigures`» от «погасить
   *   при `kind === 'no-row'`» — это делают два следующих теста (`never-asked`, `failed`,
   *   `loading`); и НЕ отличает честную починку от «гасить всегда» — это делают контроли `row`
   *   и `frozen` ниже.
   */
  it('на ответе «линии нет» сетка, полоса и фраза про долг молчат', async () => {
    await withPopup({ figuresSource: NO_ROW }, (host) => {
      expect(
        gridValues(host),
        'попап печатает числа снапшота как состояние линии, про которую бэкенд ответил, что её нет',
      ).toEqual(['— UAH', '— UAH', '— UAH', '—'])

      const util = utilization(host)
      expect(
        util.label,
        'полоса утилизации — то же утверждение в графическом виде: 12% значит «занято 12 из 100» ' +
          'у линии, которой нет',
      ).toBe('—%')
      expect(util.valuetext, 'a11y-значение полосы объявляет неизвестное известным').toBe('unknown')
      expect(util.valuenow, 'полоса предъявляет числовое значение').toBeNull()
      expect(util.width, 'заливка полосы рисует долю, которой не из чего считать').toBe('0%')

      expect(
        debtSentence(host),
        'попап УТВЕРЖДАЕТ, что у линии есть непогашенный долг 12 UAH, — про линию, которой по ' +
          'ответу бэкенда не существует; это положительное утверждение о факте, а не отказ в действии',
      ).toBeNull()
    })
  })

  /**
   * СОСЕДСТВО, ИЗ-ЗА КОТОРОГО НАХОДКА НАЗВАНА «ПРОТИВОРЕЧИТ САМОМУ СЕБЕ». Уведомление
   * `edge-no-trustline` обязано остаться: починка «убрать числа, убрав заодно и объяснение»
   * оставляет оператора с четырьмя прочерками без причины.
   *
   * ЧТО РАЗЛИЧАЕТ: «погасил и объяснил» от «погасил молча» и от «спрятал весь попап».
   * ЧЕГО НЕ РАЗЛИЧАЕТ: точную формулировку уведомления — она судится в
   *   `EdgeDetailPopup.sourceUnavailable.test.ts` и в модульных тестах `trustlinesSourceState`.
   */
  it('прочерки объяснены: уведомление про отсутствие линии остаётся рядом', async () => {
    await withPopup({ figuresSource: NO_ROW }, (host) => {
      const notice = host.querySelector('[data-testid="edge-no-trustline"]')
      expect(notice, 'числа погашены молча — оператор видит четыре прочерка без причины').toBeTruthy()
      expect((notice?.textContent ?? '').toLowerCase()).toContain('no trustline')
    })
  })

  /**
   * ОСТАЛЬНЫЕ СОСТОЯНИЯ БЕЗ СУЩЕСТВУЮЩЕЙ ЛИНИИ. Правило одно и то же и живёт в одном предикате
   * (`canActOnTrustlineFigures`), а не в трёх `if`-ах по видам.
   *
   * ЧТО РАЗЛИЧАЕТ: починку по предикату от починки «`if (kind === 'no-row')`» — последняя
   *   оставляет попап утверждающим 12 UAH долга при неспрошенном, летящем и упавшем источнике,
   *   где подтвердить числа вообще нечем.
   * ЧЕГО НЕ РАЗЛИЧАЕТ: порядок ветвей внутри предиката.
   */
  it.each([
    ['не спрашивали', NEVER_ASKED],
    ['в полёте', LOADING],
    ['упал', FAILED],
  ])('на источнике «%s» попап тоже не утверждает ничего о линии', async (_label, source) => {
    await withPopup({ figuresSource: source }, (host) => {
      expect(gridValues(host)).toEqual(['— UAH', '— UAH', '— UAH', '—'])
      expect(utilization(host).label).toBe('—%')
      expect(
        debtSentence(host),
        'числа не подтвердил никто, а попап утверждает по ним наличие долга',
      ).toBeNull()
    })
  })

  /**
   * КОНТРОЛЬ №1 ПРОТИВ ПЕРЕ-ПОЧИНКИ: `row` — живой ответ со строкой. Здесь числа обязаны быть
   * показаны ровно как пришли, полоса — посчитана, а фраза про долг — напечатана: долг есть,
   * и молчать о нём значит выключить предупреждение, ради которого оно написано.
   *
   * ЧТО РАЗЛИЧАЕТ: честную починку от «гасить всегда» и от «гасить по любому предикату, который
   *   ложен на `row`» (например, по `trustlineSourceAnswered` с ошибкой в наборе видов).
   */
  it('КОНТРОЛЬ: на живом ответе со строкой всё показано как есть', async () => {
    await withPopup({ figuresSource: ROW }, (host) => {
      expect(
        gridValues(host),
        'починка выродилась в «не показывать ничего никогда»: живой ответ бэкенда тоже погашен',
      ).toEqual(['12 UAH', '100 UAH', '88 UAH', 'active'])
      expect(utilization(host).label).toBe('12%')
      expect(utilization(host).valuenow).toBe('12')
      expect(
        debtSentence(host),
        'предупреждение о непогашенном долге пропало на линии, у которой долг ЕСТЬ',
      ).toContain('12 UAH')
    })
  })

  /**
   * КОНТРОЛЬ №2 ПРОТИВ ПЕРЕ-ПОЧИНКИ: `frozen` — замороженная копия настоящего ответа. Это вопрос
   * УСТАРЕВАНИЯ, а не отсутствия: числа были ответом бэкенда и остаются им, поэтому гасить их
   * нельзя — иначе окно, оставленное контекстом платежа, теряет тот самый контекст.
   *
   * ЧТО РАЗЛИЧАЕТ: `canActOnTrustlineFigures` (`row | frozen`) от `kind === 'row'`.
   */
  it('КОНТРОЛЬ: на замороженном ответе числа остаются показанными', async () => {
    await withPopup({ figuresSource: FROZEN }, (host) => {
      expect(
        gridValues(host),
        'заморозка — устаревание, а не отсутствие: окно-контекст погасило числа, ради которых оно висит',
      ).toEqual(['12 UAH', '100 UAH', '88 UAH', 'active'])
      expect(utilization(host).label).toBe('12%')
      expect(debtSentence(host)).toContain('12 UAH')
    })
  })
})

/**
 * КОНТРОЛЬ №3 ПРОТИВ ПЕРЕ-ПОЧИНКИ, И ОН В ДРУГОМ КОМПОНЕНТЕ. Третья вещь, названная в
 * формулировке находки, — «разрешить поток СОЗДАНИЯ». Гашение чисел не должно его задеть:
 * создание не читает `used`/`limit`/`available` вовсе (`TrustlineManagementPanel.createValid`).
 *
 * Почему контроль стоит здесь, а не только в `TrustlineManagementPanel.test.ts`: мутация,
 * сделанная ради этой находки, проверяется прогоном ОДНОГО файла, и если контроль живёт в
 * соседнем файле, «починка сломала создание» покажется зелёной ровно в том прогоне, которым
 * находку объявят закрытой.
 */
describe('RT-013-7p (контроль): создание линии при `no-row` остаётся доступным', () => {
  it('Create активен, когда источник ответил «линии нет»', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-trustline-create',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null,
      edgeAnchor: null,
      error: null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as Component, {
          phase: 'confirm-trustline-create',
          state,
          unit: 'UAH',
          used: '12',
          currentLimit: '100',
          available: '88',
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          figuresSource: NO_ROW,
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })
    app.mount(host)
    await nextTick()

    const input = host.querySelector('#tl-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()
    input!.value = '250'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    const btn = Array.from(host.querySelectorAll('button')).find(
      (b) => (b.textContent ?? '').trim() === 'Create',
    ) as HTMLButtonElement | undefined
    expect(btn, 'кнопка создания исчезла из потока создания').toBeTruthy()
    expect(
      btn!.disabled,
      'гашение чисел задело создание: ответ «линии нет» — это основание СОЗДАТЬ её, и именно ' +
        'это право находка требует сохранить',
    ).toBe(false)

    app.unmount()
    host.remove()
  })
})
