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
 * ЗДЕСЬ ДВА РАЗНЫХ ВОПРОСА, И ИХ НЕЛЬЗЯ СВОДИТЬ К ОДНОМУ (внешнее ревью 013, находка P2).
 *
 *   А. ОТВЕТИЛ ЛИ ИСТОЧНИК про эту пару — `trustlineSourceAnswered`. Отрицательный ответ значит,
 *      что подтвердить числа на экране нечем, и об этом надо сказать оператору. Потребители:
 *      все три уведомления ниже. ЭТО НЕ УКРАШЕНИЕ, И ОДНАЖДЫ БЫЛО ИМ: до 2026-09-11 у функции
 *      не было ни одного вызывающего — каждое уведомление переписывало её набор видов у себя, —
 *      и подмена её тела на `return false` не роняла ни одного из 1024 тестов. Набор видов,
 *      названный в двух местах, это два правила, которые ещё не разошлись.
 *   Б. ЕСТЬ ЛИ СУЩЕСТВУЮЩАЯ ЛИНИЯ, числа которой мы показываем и мутируем —
 *      `canActOnTrustlineFigures`. Потребители: оба компонента (гард мутации И гашение чисел)
 *      и `freezeTrustlineFiguresSource`.
 *
 * Они расходятся ровно в одном состоянии, `no-row`, и именно на нём прежняя редакция ошиблась:
 * источник ОТВЕТИЛ (А истинно), но линии у пары НЕТ (Б ложно). Из этого ответа следует право
 * СОЗДАТЬ линию — и только оно. Обновлять и закрывать нечего; а числа, которые в этот момент
 * показаны, приехали из снапшотного фоллбэка — то есть из источника, который авторитетный ответ
 * только что опроверг. Выдавать их за состояние существующей линии — та же подмена, ради
 * устранения которой заведён этот модуль, только на одно состояние в сторону.
 *
 * - `row`         — источник ответил, и строка для этой пары в ответе есть (А да, Б да);
 * - `no-row`      — источник ответил, и линии у этой пары нет (А да, Б НЕТ; создать её — законное
 *                   действие, обновить или закрыть — нечего);
 * - `frozen`      — замороженный ранее ОТВЕТ (`keepAlive` в edge-detail): вопрос устаревания,
 *                   а не отсутствия (А да, Б да). Заморозка молчания сюда не попадает —
 *                   см. `freezeTrustlineFiguresSource`;
 * - `never-asked` — источник не спрашивали; всё, что на экране, приехало из снапшота;
 * - `loading`     — ответа ещё нет;
 * - `failed`      — ответа не будет, запрос упал.
 *
 * ТРЕТИЙ ВОПРОС, ОРТОГОНАЛЬНЫЙ ПЕРВЫМ ДВУМ: ЖИВОЕ ЛИ ЭТО ОСНОВАНИЕ ИЛИ ЗАМОРОЖЕННАЯ КОПИЯ —
 * признак `frozen` (кросс-ревью, P3). Он ортогонален потому, что заморозка не меняет ни А, ни Б:
 * замороженный `no-row` — всё ещё ответ, по которому нечего обновлять; замороженный `loading` —
 * всё ещё отсутствие ответа. Виду `frozen` он не нужен: тот САМ означает «заморожен ответ со
 * строкой» и существует отдельным видом потому, что у него другое Б (действовать позволено).
 * Спрашивать про заморозку надо `trustlineSourceFrozen`, а не `kind === 'frozen'`.
 */
export type TrustlineFiguresSource =
  | { kind: 'row' }
  | { kind: 'no-row'; frozen?: true }
  | { kind: 'frozen' }
  | { kind: 'never-asked'; frozen?: true }
  | { kind: 'loading'; frozen?: true }
  | { kind: 'failed'; message: string; frozen?: true }

/** Виды, которые означают ОТВЕТ источника (вопрос А). Набор назван ровно один раз — здесь. */
export type AnsweredTrustlineFiguresSource = Extract<
  TrustlineFiguresSource,
  { kind: 'row' | 'no-row' | 'frozen' }
>

/** Виды, у которых есть СУЩЕСТВУЮЩАЯ ЛИНИЯ (вопрос Б). Набор назван ровно один раз — здесь. */
export type ActionableTrustlineFiguresSource = Extract<TrustlineFiguresSource, { kind: 'row' | 'frozen' }>

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

/**
 * ОТВЕТИЛ ЛИ ИСТОЧНИК про эту пару — вопрос А (см. `TrustlineFiguresSource`).
 *
 * Отрицательный ответ означает, что числа на экране не подтвердил никто, и об этом надо сказать
 * словами (`trustlineFiguresNotice`). НЕ путать с вопросом Б: `no-row` — это ответ, и говорить о
 * нём надо иначе, чем о молчании.
 */
export function trustlineSourceAnswered(
  source: TrustlineFiguresSource | null | undefined,
): source is AnsweredTrustlineFiguresSource {
  if (source == null) return false
  return source.kind === 'row' || source.kind === 'no-row' || source.kind === 'frozen'
}

/**
 * ЕСТЬ ЛИ СУЩЕСТВУЮЩАЯ ЛИНИЯ, по числам которой позволено действовать, — вопрос Б.
 *
 * `no-row` СЮДА НЕ ВХОДИТ, и это содержание находки P2 внешнего ревью 013. «Бэкенд ответил, и
 * линии у этой пары нет» — настоящий ответ, но предмет обновления и закрытия он не создаёт, а
 * числа, показанные в этом состоянии, происходят из снапшота, который этот же ответ опроверг.
 * Право СОЗДАТЬ линию из него следует и здесь не гасится: создание не читает эти числа
 * (`TrustlineManagementPanel.createValid`), поэтому через этот предикат оно не проходит вовсе.
 */
export function canActOnTrustlineFigures(
  source: TrustlineFiguresSource | null | undefined,
): source is ActionableTrustlineFiguresSource {
  if (source == null) return false
  return source.kind === 'row' || source.kind === 'frozen'
}

/**
 * ЗАМОРОЗКА НЕ ПОВЫШАЕТ ПРОИСХОЖДЕНИЕ ЧИСЕЛ (внешнее ревью 013, находка P3).
 *
 * `keepAlive` в edge-detail снимает КОПИЮ показанной линии, чтобы окно осталось контекстом, пока
 * interact-состояние ушло на другую пару. Копия наследует основание того, что копировали: если
 * замораживали настоящий ответ — получается `frozen` (устаревание, а не отсутствие); если
 * замораживали молчание, отказ или ответ «линии нет» — основание остаётся ТЕМ ЖЕ, и окно обязано
 * говорить о нём ровно теми же словами, что и секунду назад.
 *
 * Прежняя редакция выдавала `frozen` по одному лишь факту наличия замороженной линии — то есть
 * сам жест «Send Payment» превращал молчание бэкенда в его ответ.
 */
export function freezeTrustlineFiguresSource(
  frozen: TrustlineFiguresSource | null | undefined,
): TrustlineFiguresSource {
  // Fail closed: заморозили неизвестно что — значит, основания нет. Заморожено оно всё равно.
  if (frozen == null) return { kind: 'never-asked', frozen: true }
  // Набор «row | frozen» не переписывается здесь заново: это ровно вопрос Б.
  if (canActOnTrustlineFigures(frozen)) return { kind: 'frozen' }
  switch (frozen.kind) {
    case 'no-row':
      return { kind: 'no-row', frozen: true }
    case 'never-asked':
      return { kind: 'never-asked', frozen: true }
    case 'loading':
      return { kind: 'loading', frozen: true }
    case 'failed':
      return { kind: 'failed', message: frozen.message, frozen: true }
  }
}

/**
 * ЗАМОРОЖЕНО ЛИ ТО, ЧТО ПОКАЗАНО, — один факт, названный в одном месте (кросс-ревью, P3).
 *
 * `kind: 'frozen'` описывает заморозку ОТВЕТА СО СТРОКОЙ и потому занимает целый вид: у него
 * другое поведение и в вопросе Б (действовать позволено), и в тексте. Заморозка всех остальных
 * оснований вида не меняет — иначе она повышала бы происхождение, — и держится отдельным
 * признаком. Спрашивать «заморожено ли» надо через эту функцию, а не через `kind`: проверка
 * `kind === 'frozen'` отвечает на вопрос про ОДИН из способов быть замороженным, и именно
 * поэтому замороженные `no-row` и `never-asked` были неотличимы от живых.
 */
export function trustlineSourceFrozen(source: TrustlineFiguresSource | null | undefined): boolean {
  if (source == null) return false
  if (source.kind === 'frozen') return true
  return 'frozen' in source && source.frozen === true
}

/**
 * Текст про ОТСУТСТВИЕ ОТВЕТА (вопрос А). Сформулирован как факт О ДАННЫХ, а не как политика про
 * кнопки: одна и та же панель открыта и в фазе создания, где Create остаётся доступным, потому
 * что не читает эти числа.
 *
 * `null` — когда источник ответил. Про `no-row` здесь молчание НАМЕРЕННОЕ: источник ответил, и
 * называть его ответ отсутствием ответа — значит повторить ту же подмену этажом выше. Этот случай
 * говорит `trustlineNoRowNotice`.
 */
export function trustlineFiguresNotice(source: TrustlineFiguresSource | null | undefined): string | null {
  const resolved: TrustlineFiguresSource = source ?? { kind: 'never-asked' }
  // ВОПРОС А ЗАДАЁТСЯ ОДИН РАЗ И ЗДЕСЬ. Раньше на этом месте стоял собственный список видов
  // (`row | no-row | frozen`) — вторая копия того же правила, из-за которой `trustlineSourceAnswered`
  // не имел ни одного потребителя и был неотличим от `return false`.
  if (trustlineSourceAnswered(resolved)) return null
  switch (resolved.kind) {
    case 'never-asked':
      return 'Trustline data for this pair has not been requested from the backend; the figures on the graph come from the snapshot and nothing has confirmed them for this pair.'
    case 'loading':
      // ЗАМОРОЖЕННАЯ ЗАГРУЗКА — НЕ ЗАГРУЗКА (кросс-ревью, P3). Запрос, который был в полёте в
      // момент заморозки, обновит ЖИВОЕ состояние, а не этот снимок; обещать «ещё грузится»
      // значит утверждать про окно то, чего с ним никогда не случится.
      return resolved.frozen === true
        ? 'Trustline data for this pair was still being requested when this window was frozen; that request will not update these figures, and nothing has confirmed them for this pair.'
        : 'Trustline data is still loading from the backend; the figures for this pair are not available yet.'
    case 'failed':
      return `Trustline data could not be loaded: ${resolved.message}. The figures for this pair are not available, and what the graph shows may be out of date.`
  }
}

/**
 * Текст про ЗАМОРОЖЕННЫЙ ОТВЕТ.
 *
 * ПОЧЕМУ ОН НУЖЕН (внешнее ревью 013, находка P3). Без него `frozen` и `row` наблюдаемо
 * НЕРАЗЛИЧИМЫ: оба разрешают действие и оба молчат. То есть «заморозили настоящий ответ» нельзя
 * отличить от «показываем живой ответ» — а это разные вещи: замороженная копия не обновляется,
 * пока окно висит контекстом чужого потока, и может относиться к ДРУГОЙ паре, чем живое
 * interact-состояние. Пока разница ничем не сказана, она и не проверяется ничем: заморозка,
 * сделанная как попало, остаётся зелёной.
 *
 * `null` — во всех остальных состояниях.
 */
export function trustlineFrozenNotice(source: TrustlineFiguresSource | null | undefined): string | null {
  // ЧТО ИЗМЕНИЛОСЬ (кросс-ревью, P3). Здесь стояло `source?.kind !== 'frozen'`, то есть
  // уведомление о заморозке доставалось ОДНОМУ из способов быть замороженным — заморозке ответа
  // со строкой. Замороженные `no-row`, `never-asked`, `loading` и `failed` оставались наблюдаемо
  // НЕОТЛИЧИМЫ от живых — ровно та неразличимость, против которой это уведомление и заведено.
  if (!trustlineSourceFrozen(source)) return null
  if (source?.kind === 'frozen') {
    return (
      'These figures are a frozen copy of an earlier backend answer, kept as context for the ' +
      'action in progress; they are not being refreshed and may describe a different pair than the ' +
      'one now selected.'
    )
  }
  // Про ОТВЕТ здесь не говорится ни слова: его в этих основаниях нет (либо он есть, но
  // означает «линии нет»), и что именно было основанием — говорят соседние уведомления.
  return (
    'The figures in this window are a frozen copy kept as context for the action in progress; ' +
    'they are not being refreshed and may describe a different pair than the one now selected.'
  )
}

/**
 * Текст про ОТВЕТ «линии у этой пары нет» (вопрос Б при истинном А).
 *
 * ОТДЕЛЬНЫЙ ОТ `trustlineFiguresNotice` НАМЕРЕННО. «Бэкенд не ответил» и «бэкенд ответил, что
 * линии нет» — разные факты, у них разные последствия (в первом случае ждать, во втором создать),
 * и сливать их в одно сообщение значило бы воспроизвести находку в тексте после того, как её
 * убрали из кнопок.
 *
 * `null` — во всех остальных состояниях.
 */
export function trustlineNoRowNotice(source: TrustlineFiguresSource | null | undefined): string | null {
  // Сначала вопрос А тем же предикатом: рассказывать СОДЕРЖАНИЕ ответа можно только там, где
  // ответ был. Это не перестраховка, а отказ от третьей копии набора видов.
  if (!trustlineSourceAnswered(source)) return null
  if (source.kind !== 'no-row') return null
  return (
    'The backend answered for this pair: there is no trustline. ' +
    'The figures on the graph come from the snapshot and do not describe an existing line — ' +
    'there is nothing to update or close here; create the line instead.'
  )
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
