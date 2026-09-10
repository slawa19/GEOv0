import type { TrustlineInfo } from '../../api/simulatorTypes'

/**
 * `F-013-7` — состояние источника trustlines КАК СОСТОЯНИЕ, а не как два булева флага.
 *
 * До этого модуля источник описывался парой `trustlinesLoading` / `trustlinesLastError`, и
 * четыре разных положения дел сходились в одно значение «оба false»:
 *
 *   1. у источника ещё ничего не спрашивали (начальное состояние `useInteractDataCache`);
 *   2. запрос выполняется;
 *   3. запрос упал;
 *   4. источник ответил — и ответ может быть пустым.
 *
 * (1) и (4) внешне неразличимы: в обоих `trustlines` пуст, оба флага сняты. Именно эту пару
 * находка называет словом «неразличимы»: «пустой авторитетный результат и отсутствие либо отказ
 * источника». Разница между ними решающая — (4) это ОТВЕТ («у этой пары линии нет», оператор
 * вправе её создать), а (1) это МОЛЧАНИЕ, и мутировать по числам, которые в этот момент показывает
 * снапшот, нельзя.
 */
export type TrustlinesFetchState =
  | { kind: 'never-asked' }
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  /** Источник ответил для текущих (run, equivalent). Пустой ответ — тоже ответ. */
  | { kind: 'answered' }

/**
 * Чем обоснованы числа, показанные для ОДНОЙ пары (from → to).
 *
 * Первые три — основания действовать, остальные три — нет:
 *
 * - `row`         — источник ответил, и строка для этой пары в ответе есть;
 * - `no-row`      — источник ответил, и линии у этой пары нет (создать её — законное действие);
 * - `frozen`      — замороженный ранее ответ (`keepAlive` в edge-detail): вопрос устаревания,
 *                   а не отсутствия;
 * - `never-asked` — источник не спрашивали; всё, что на экране, приехало из снапшота;
 * - `loading`     — ответа ещё нет;
 * - `failed`      — ответа не будет, запрос упал.
 */
export type TrustlineFiguresSource =
  | { kind: 'row' }
  | { kind: 'no-row' }
  | { kind: 'frozen' }
  | { kind: 'never-asked' }
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }

/**
 * Свести состояние источника и наличие АВТОРИТЕТНОЙ строки к основанию для одной пары.
 *
 * `hasAnsweredRow` обязан считаться по тому списку, который источник реально прислал, а НЕ по
 * `trustlines` из `useInteractDataCache`: тот при отсутствии ответа молча подставляет строки,
 * собранные из снапшота (`useInteractDataCache.ts`, `trustlines` computed), и найденная в нём
 * «строка» ничего не подтверждает. Поэтому `never-asked` проверяется ПЕРВЫМ: если источник
 * молчал, никакая строка не может быть его ответом.
 */
export function resolveTrustlineFiguresSource(
  fetchState: TrustlinesFetchState | null | undefined,
  hasAnsweredRow: boolean,
): TrustlineFiguresSource {
  // Fail closed: у вызывающего, который не сказал ничего, нет основания действовать.
  if (fetchState == null) return { kind: 'never-asked' }
  if (fetchState.kind === 'never-asked') return { kind: 'never-asked' }
  if (hasAnsweredRow) return { kind: 'row' }
  if (fetchState.kind === 'answered') return { kind: 'no-row' }
  return fetchState
}

/** Есть ли основание ВЫПОЛНИТЬ мутацию по показанным числам. */
export function canActOnTrustlineFigures(source: TrustlineFiguresSource | null | undefined): boolean {
  if (source == null) return false
  return source.kind === 'row' || source.kind === 'no-row' || source.kind === 'frozen'
}

/**
 * Текст для оператора. Сформулирован как факт О ДАННЫХ, а не как политика про кнопки: одна и та же
 * панель открыта и в фазе создания, где Create остаётся доступным, потому что не читает эти числа.
 *
 * `null` — когда основание есть и говорить не о чем.
 */
export function trustlineFiguresNotice(source: TrustlineFiguresSource | null | undefined): string | null {
  const resolved: TrustlineFiguresSource = source ?? { kind: 'never-asked' }
  switch (resolved.kind) {
    case 'row':
    case 'no-row':
    case 'frozen':
      return null
    case 'never-asked':
      return 'Trustline data for this pair has not been requested from the backend; the figures on the graph come from the snapshot and nothing has confirmed them for this pair.'
    case 'loading':
      return 'Trustline data is still loading from the backend; the figures for this pair are not available yet.'
    case 'failed':
      return `Trustline data could not be loaded: ${resolved.message}. The figures for this pair are not available, and what the graph shows may be out of date.`
  }
}

/** Ищет строку пары в списке, который источник ДЕЙСТВИТЕЛЬНО прислал (`null` — не присылал). */
export function findAnsweredRow(
  answered: TrustlineInfo[] | null | undefined,
  from: string | null | undefined,
  to: string | null | undefined,
): TrustlineInfo | null {
  if (!answered || !from || !to) return null
  return answered.find((tl) => tl.from_pid === from && tl.to_pid === to) ?? null
}
