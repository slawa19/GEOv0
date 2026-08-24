import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage.vue'
import TrustlinesPage from './TrustlinesPage.vue'
import type { Trustline } from '../types/domain'

/**
 * RT-012-6 (F-012-7, дополнен `T1211`) — репродьюсер на реальных денежных вызывающих `admin-ui`.
 *
 * `DashboardPage.vue` и `TrustlinesPage.vue` печатали каждую сумму своей таблицы двумя знаками,
 * какому бы эквиваленту строка ни принадлежала. Верная форма уже существовала рядом, в
 * `LiquidityPage.vue`: резолвить точность по коду эквивалента самой строки. Репродьюсер судит
 * отрисованные ячейки, а не функцию, поэтому остаётся верным для любой починки, которая даёт
 * правильные знаки, а не только для одной сигнатуры.
 *
 * `T1211` — ВТОРОЙ ДЕФЕКТ, КОТОРЫЙ ЭТОТ ТЕСТ ПРОПУСКАЛ. Выборка состояла из одной суммы `12.3`
 * при точностях 1 и 2: для HOUR она уже ровно в точности, для UAH требуется только добивка.
 * Обе строки удовлетворяются и НЕВЕРНОЙ реализацией «точность как максимум с округлением
 * половины вверх», при которой `0.05 HOUR` — сумма, принимаемая дверью и хранимая
 * `Numeric(20, 8)` точно, — показывается оператору как `0.1`. Это изменение величины, а не
 * написания, и старый оракул «ровно `precision` знаков» его закреплял.
 *
 * Правило: `Equivalent.precision` — МИНИМУМ знаков, никогда не максимум (`app/utils/money.py`).
 * Ниже каждая строка несёт собственную сумму, выбранную так, чтобы занять свою позицию
 * относительно объявленной точности, а оракул — точная строка. Полнота выборки охраняется
 * отдельным ассертом-часовым: он воспроизводит снятую реализацию и требует, чтобы хотя бы одна
 * строка на ней краснела.
 *
 * `seeds/equivalents.json` поставляет HOUR с precision 1 рядом с UAH с precision 2.
 */

type Row = { code: string; precision: number; limit: string; expected: string; why: string }

const ROWS_SPEC: Row[] = [
  // scale > precision: величина точнее объявленного разрешения. Старая реализация печатала `0.1`.
  { code: 'HOUR', precision: 1, limit: '0.05', expected: '0.05', why: 'величина точнее precision' },
  // scale < precision: добивка до объявленного минимума.
  { code: 'UAH', precision: 2, limit: '12.3', expected: '12.30', why: 'добивка до precision' },
  // Та же сумма, что у UAH, при другой точности: строки обязаны различаться.
  { code: 'SAT', precision: 0, limit: '12.3', expected: '12.3', why: 'precision 0 не усекает' },
]

const PRECISION_BY_EQUIVALENT: Record<string, number> = Object.fromEntries(
  ROWS_SPEC.map((r) => [r.code, r.precision]),
)

const SPEC_BY_EQUIVALENT = new Map(ROWS_SPEC.map((r) => [r.code, r]))

const apiMock = vi.hoisted(() => ({
  listTrustlines: vi.fn(),
  listEquivalents: vi.fn(),
  trustlineBottlenecks: vi.fn(),
  listAuditLog: vi.fn(),
  listIncidents: vi.fn(),
  participantsStats: vi.fn(),
  health: vi.fn(),
  healthDb: vi.fn(),
  migrations: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

function ok<T>(data: T) {
  return { success: true as const, data }
}

function paginated<T>(items: T[]) {
  return ok({ items, page: 1, per_page: 20, total: items.length })
}

function trustline(spec: Row): Trustline {
  return {
    equivalent: spec.code,
    from: `FROM_${spec.code}`,
    to: `TO_${spec.code}`,
    limit: spec.limit,
    used: '0',
    available: spec.limit,
    status: 'active',
    created_at: '2026-08-24T00:00:00Z',
  }
}

const ROWS = ROWS_SPEC.map(trustline)

async function mountPage(component: object, path: string): Promise<VueWrapper> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path, component }],
  })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(component, { global: { plugins: [pinia, router, ElementPlus] } })
  await flushPromises()
  await nextTick()
  await flushPromises()
  return wrapper
}

/**
 * Reads the rendered limit cell of every row, keyed by the row's equivalent.
 * The equivalent is the first column on both pages and the limit is the fourth;
 * the shape is asserted rather than assumed, so a column move fails loudly.
 */
function renderedLimitByEquivalent(wrapper: VueWrapper): Map<string, string> {
  const byEquivalent = new Map<string, string>()

  for (const row of wrapper.findAll('tbody tr')) {
    const cells = row.findAll('td').map((cell) => cell.text().trim())
    if (cells.length < 4) continue

    const equivalent = cells[0]
    const limitCell = cells[3]
    // `cells.length < 4` уже отсеян выше; guard существует только ради строгой индексации
    // TypeScript и не может изменить вердикт ни одного ассерта ниже.
    if (equivalent === undefined || limitCell === undefined) continue
    if (!(equivalent in PRECISION_BY_EQUIVALENT)) continue

    const spec = SPEC_BY_EQUIVALENT.get(equivalent) as Row
    expect(
      Number(limitCell),
      `Column layout changed: the fourth cell of the ${equivalent} row reads "${limitCell}", which is `
        + `not the trust limit ${spec.limit}. This reproducer is reading the wrong column and must be `
        + 're-anchored before its verdict means anything.',
    ).toBeCloseTo(Number(spec.limit), 6)

    byEquivalent.set(equivalent, limitCell)
  }

  expect(
    [...byEquivalent.keys()].sort(),
    'Both equivalents must reach the rendered table, otherwise there is nothing to compare.',
  ).toEqual(Object.keys(PRECISION_BY_EQUIVALENT).sort())

  return byEquivalent
}

beforeEach(() => {
  for (const mock of Object.values(apiMock)) mock.mockReset()

  apiMock.listTrustlines.mockResolvedValue(paginated(ROWS))
  apiMock.trustlineBottlenecks.mockResolvedValue(ok({ items: ROWS, threshold: '0.10' }))
  apiMock.listEquivalents.mockResolvedValue(
    paginated(
      Object.entries(PRECISION_BY_EQUIVALENT).map(([code, precision]) => ({
        code,
        precision,
        active: true,
      })),
    ),
  )
  apiMock.listAuditLog.mockResolvedValue(paginated([]))
  apiMock.listIncidents.mockResolvedValue(paginated([]))
  apiMock.participantsStats.mockResolvedValue(
    ok({ participants_by_status: {}, participants_by_type: {} }),
  )
  apiMock.health.mockResolvedValue(ok({ status: 'healthy' }))
  apiMock.healthDb.mockResolvedValue(ok({ status: 'healthy' }))
  apiMock.migrations.mockResolvedValue(ok({ current: 'head' }))
})

describe.each([
  { name: 'DashboardPage bottlenecks table', component: DashboardPage, path: '/' },
  { name: 'TrustlinesPage table', component: TrustlinesPage, path: '/trustlines' },
])('RT-012-6: $name renders money by the row equivalent', ({ component, path }) => {
  it('sentinel: the sample must redden under the implementation T1211 removed', () => {
    // Отрицательный контроль — снятая `formatDecimalFixed`: ровно `precision` знаков,
    // округление половины вверх. Если ни одна строка на ней не краснеет, выборка подобрана
    // согласиться и весь файл ничего не доказывает.
    const asMaximumHalfUp = (value: string, digits: number): string => {
      const [int = '0', frac = ''] = value.split('.')
      const scaled = BigInt(int + frac.padEnd(Math.max(frac.length, digits), '0'))
      const drop = Math.max(0, frac.length - digits)
      const div = 10n ** BigInt(drop)
      const q = scaled / div + (drop > 0 && (scaled % div) * 2n >= div ? 1n : 0n)
      const s = q.toString().padStart(digits + 1, '0')
      return digits === 0 ? s : `${s.slice(0, s.length - digits)}.${s.slice(s.length - digits)}`
    }

    const distinguishing = ROWS_SPEC.filter(
      (r) => asMaximumHalfUp(r.limit, r.precision) !== r.expected,
    )
    expect(
      distinguishing.map((r) => r.code),
      'Ни одна строка не отличает «минимум знаков» от снятого «максимум знаков»: выборка '
        + 'подобрана согласиться, и оба ассерта ниже пройдут без починки.',
    ).not.toHaveLength(0)

    expect(
      distinguishing.some(
        (r) => Number(asMaximumHalfUp(r.limit, r.precision)) !== Number(r.expected),
      ),
      'Различающие строки есть, но все они про написание. Нужна хотя бы одна, где старая '
        + 'реализация показывала оператору другое ЧИСЛО.',
    ).toBe(true)
  })

  it('shows each amount with the digits its equivalent declares, and never fewer than it needs', async () => {
    const wrapper = await mountPage(component, path)
    const rendered = renderedLimitByEquivalent(wrapper)

    for (const spec of ROWS_SPEC) {
      const cell = rendered.get(spec.code) as string

      expect(
        cell,
        `Строка — трастлайн в ${spec.code}, ${spec.code} объявляет precision ${spec.precision}, `
          + `лимит ${spec.limit} (${spec.why}). Оператор обязан увидеть "${spec.expected}", `
          + `а видит "${cell}". Точность объявляет минимум знаков: недостающие знаки меняют саму `
          + 'величину, а не её написание.',
      ).toBe(spec.expected)
    }

    wrapper.unmount()
  })

  it('reacts to the equivalent: one amount at two precisions must not render identically', async () => {
    const wrapper = await mountPage(component, path)
    const rendered = renderedLimitByEquivalent(wrapper)

    const uah = SPEC_BY_EQUIVALENT.get('UAH') as Row
    const sat = SPEC_BY_EQUIVALENT.get('SAT') as Row

    expect(
      uah.limit === sat.limit && uah.precision !== sat.precision,
      'Counter-check premise: обе строки обязаны нести ОДНУ сумму при РАЗНОЙ точности, '
        + 'иначе различие вывода ничего не доказывает.',
    ).toBe(true)

    const asUah = rendered.get('UAH') as string
    const asSat = rendered.get('SAT') as string

    expect(
      asUah,
      `Обе строки несут лимит ${uah.limit}; UAH (precision ${uah.precision}) печатается как `
        + `"${asUah}", SAT (precision ${sat.precision}) — как "${asSat}". Одинаковый вывод значит, `
        + 'что страница форматирует любой эквивалент одинаково, и знаки не говорят оператору '
        + 'ничего о единице.',
    ).not.toBe(asSat)

    wrapper.unmount()
  })
})
