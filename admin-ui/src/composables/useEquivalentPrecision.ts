import { computed, ref, type ComputedRef, type Ref } from 'vue'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'
import type { Equivalent } from '../types/domain'
import { formatDecimalFixed } from '../utils/decimal'

/**
 * Единственный источник точности денежной ячейки admin-ui (F-012-7).
 *
 * Точность объявляет эквивалент, а не место вывода: `Equivalent.precision` разрешён контрактом
 * в диапазоне 0..18, и `seeds/equivalents.json` уже поставляет `HOUR` с `precision: 1` рядом с
 * `UAH` с `precision: 2`. Печатая фиксированные два знака, страница приписывает величине
 * разрешение, которого у единицы нет.
 *
 * Форма взята с `LiquidityPage.vue`, где она уже была верной: резолвим `precision` по коду
 * эквивалента строки и передаём его в `formatDecimalFixed`; при неизвестной точности печатаем
 * прочерк, а не число с выдуманным числом знаков.
 */
export const PRECISION_UNAVAILABLE = '—'

export function normalizeEquivalentCode(value: unknown): string {
  return String(value ?? '').trim().toUpperCase()
}

export function buildPrecisionByEquivalent(
  items: readonly Equivalent[] | null | undefined,
): Map<string, number> {
  const byCode = new Map<string, number>()
  for (const item of items || []) {
    const code = normalizeEquivalentCode(item?.code)
    const precision = Number(item?.precision)
    if (code && Number.isInteger(precision) && precision >= 0) byCode.set(code, precision)
  }
  return byCode
}

export function precisionForEquivalent(
  precisionByEquivalent: ReadonlyMap<string, number>,
  equivalent: unknown,
): number | null {
  const code = normalizeEquivalentCode(equivalent)
  if (!code) return null
  const precision = precisionByEquivalent.get(code)
  return precision === undefined ? null : precision
}

/**
 * Печатает величину с объявленной точностью. `null`/`undefined` — «точность неизвестна»:
 * прочерк честнее, чем число с произвольным количеством знаков.
 */
export function formatMoneyWithPrecision(
  value: string,
  precision: number | null | undefined,
): string {
  if (precision === null || precision === undefined) return PRECISION_UNAVAILABLE
  return formatDecimalFixed(value, precision)
}

/** Формат денежной ячейки по коду эквивалента самой строки. */
export function formatMoneyByEquivalent(
  value: string,
  equivalent: unknown,
  precisionByEquivalent: ReadonlyMap<string, number>,
): string {
  return formatMoneyWithPrecision(value, precisionForEquivalent(precisionByEquivalent, equivalent))
}

export type EquivalentPrecisionSource = {
  equivalents: Ref<Equivalent[]>
  /** Запрос каталога завершён — успехом или отказом. До этого «точность неизвестна» ещё не вывод. */
  catalogueSettled: Ref<boolean>
  precisionByEquivalent: ComputedRef<Map<string, number>>
  precisionOf: (equivalent: unknown) => number | null
  money: (value: string, equivalent: unknown) => string
  hasUnknownPrecision: (equivalents: readonly unknown[]) => boolean
  loadEquivalentPrecision: () => Promise<void>
}

/**
 * Загружает каталог эквивалентов и отдаёт денежный форматтер, привязанный к нему.
 *
 * `include_inactive: true` — намеренно: деактивированный эквивалент не удаляет уже существующие
 * трастлайны и долги, и его строки обязаны печататься с собственной точностью, а не прочерком.
 */
export function useEquivalentPrecision(): EquivalentPrecisionSource {
  const equivalents = ref<Equivalent[]>([])
  const catalogueSettled = ref(false)

  const precisionByEquivalent = computed(() => buildPrecisionByEquivalent(equivalents.value))

  function precisionOf(equivalent: unknown): number | null {
    return precisionForEquivalent(precisionByEquivalent.value, equivalent)
  }

  function money(value: string, equivalent: unknown): string {
    return formatMoneyWithPrecision(value, precisionOf(equivalent))
  }

  function hasUnknownPrecision(codes: readonly unknown[]): boolean {
    return (codes || []).some((code) => precisionOf(code) === null)
  }

  async function loadEquivalentPrecision(): Promise<void> {
    try {
      const res = assertSuccess(await api.listEquivalents({ include_inactive: true }))
      equivalents.value = (res.items || []) as Equivalent[]
    } finally {
      catalogueSettled.value = true
    }
  }

  return {
    equivalents,
    catalogueSettled,
    precisionByEquivalent,
    precisionOf,
    money,
    hasUnknownPrecision,
    loadEquivalentPrecision,
  }
}
