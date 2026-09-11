import { describe, expect, it } from 'vitest'

import {
  canActOnTrustlineFigures,
  freezeTrustlineFiguresSource,
  trustlineFiguresNotice,
  trustlineFrozenNotice,
  trustlineNoRowNotice,
  trustlineSourceAnswered,
  trustlineSourceFrozen,
  type TrustlineFiguresSource,
} from './trustlinesSourceState'

/**
 * `F-013-7`, ОСНОВАНИЕ ДЛЯ ЧИСЕЛ КАК МОДУЛЬ. Два сюжета кросс-ревью:
 *
 *  - РАСЩЕПЛЕНИЕ НА ДВА ВОПРОСА БЫЛО НАПОЛОВИНУ ДЕКОРАТИВНЫМ. `trustlineSourceAnswered`
 *    (вопрос А — «ответил ли источник») не имел НИ ОДНОГО потребителя: три уведомления
 *    переписывали его набор видов у себя. Ревьюер подменил его на `return false` и прогнал
 *    весь набор — 114 файлов, 1024 теста, всё зелёное. Символ, неотличимый от собственного
 *    отрицания, — это не механизм, а его описание.
 *  - ЗАМОРОЗКА ГОВОРИЛА ПРО ЖИВОЕ СОСТОЯНИЕ. `freezeTrustlineFiguresSource` пропускала
 *    `loading` без изменений, и замороженное окно навсегда сообщало «данные ещё грузятся» про
 *    копию, которую не обновит ни один запрос; а `no-row` и `never-asked`, будучи заморожены,
 *    оставались наблюдаемо НЕОТЛИЧИМЫ от живых — то есть ровно тем, против чего заведено
 *    уведомление о заморозке.
 */

const ALL_KINDS: ReadonlyArray<readonly [string, TrustlineFiguresSource]> = [
  ['row', { kind: 'row' }],
  ['no-row', { kind: 'no-row' }],
  ['frozen', { kind: 'frozen' }],
  ['never-asked', { kind: 'never-asked' }],
  ['loading', { kind: 'loading' }],
  ['failed', { kind: 'failed', message: 'GET /runs/r1/trustlines failed: 503' }],
]

describe('вопрос А — «ответил ли источник» — задаётся в одном месте', () => {
  /**
   * ЧТО РАЗЛИЧАЕТ ЭТА ВЫБОРКА: `trustlineSourceAnswered` от `return false` и от `return true`
   *   — через её ЕДИНСТВЕННОГО потребителя, `trustlineFiguresNotice`. При `return false`
   *   уведомление «источник не ответил» печатается на `row`, `no-row` и `frozen`; при
   *   `return true` — исчезает на `never-asked`, `loading` и `failed`. Обе половины судятся
   *   ниже, поэтому ни одна из двух вырожденных реализаций не проходит.
   * ЧЕГО НЕ РАЗЛИЧАЕТ, и это надо сказать прямо: НИКАКАЯ выборка не отличит «предикат вызван»
   *   от «его набор видов переписан в потребителе слово в слово» — обе реализации по
   *   построению дают один результат. Различимо другое и именно оно проверено: ПОСЛЕ правки
   *   копия одна, поэтому одна мутация роняет все зависящие тесты, а до правки не роняла ни
   *   одного.
   */
  it.each(ALL_KINDS)('вид «%s»: молчание об отсутствии ответа = источник ответил', (_label, source) => {
    expect(
      trustlineFiguresNotice(source) == null,
      'уведомление «источник не ответил» разошлось с предикатом «источник ответил»: это две ' +
        'копии одного правила, и они уже начали расходиться',
    ).toBe(trustlineSourceAnswered(source))
  })

  it('пропущенное основание читается как «не ответил» обоими', () => {
    expect(trustlineSourceAnswered(null)).toBe(false)
    expect(trustlineSourceAnswered(undefined)).toBe(false)
    expect(trustlineFiguresNotice(undefined)).not.toBeNull()
  })

  /**
   * ВТОРАЯ ПОЛОВИНА, без которой `return true` прошла бы: на каждом НЕответе текст обязан быть,
   * и он обязан называть именно это состояние, а не соседнее.
   */
  it.each([
    ['never-asked', { kind: 'never-asked' } as TrustlineFiguresSource, /has not been requested/i],
    ['loading', { kind: 'loading' } as TrustlineFiguresSource, /loading/i],
    ['failed', { kind: 'failed', message: 'boom' } as TrustlineFiguresSource, /could not be loaded: boom/i],
  ])('вид «%s» назван своими словами', (_label, source, re) => {
    expect(trustlineFiguresNotice(source) ?? '').toMatch(re)
  })

  /**
   * Уведомления про КОНКРЕТНЫЙ ответ — тоже утверждения об ответе, и они не могут возникать
   * там, где ответа нет. Это тот же вопрос А, и потому тот же предикат.
   *
   * ЧТО РАЗЛИЧАЕТ: реализацию, где `no-row`/`frozen`-уведомления сверяются с ответившим
   *   состоянием, от реализации, где они судят только по `kind` и потому «переживают» любую
   *   ошибку в наборе ответивших видов.
   */
  it.each(ALL_KINDS)('вид «%s»: уведомления про конкретный ответ бывают только у ответа', (_label, source) => {
    const answerSpecific = trustlineNoRowNotice(source) != null || trustlineFrozenNotice(source) != null
    if (answerSpecific) {
      expect(
        trustlineSourceAnswered(source),
        'экран рассказывает СОДЕРЖАНИЕ ответа там, где ответа не было',
      ).toBe(true)
    }
  })
})

describe('вопрос Б — «есть ли что мутировать» — расходится с А ровно на `no-row`', () => {
  /**
   * КОНТРОЛЬ ПРОТИВ СЛИЯНИЯ ДВУХ ВОПРОСОВ ОБРАТНО В ОДИН. Если бы `canActOnTrustlineFigures`
   * и `trustlineSourceAnswered` совпадали на всех видах, расщепление было бы декоративным
   * целиком, а не наполовину.
   */
  it('`no-row` — ответ (А), но мутировать нечего (Б)', () => {
    const noRow: TrustlineFiguresSource = { kind: 'no-row' }
    expect(trustlineSourceAnswered(noRow)).toBe(true)
    expect(canActOnTrustlineFigures(noRow)).toBe(false)
  })

  it.each(ALL_KINDS)('вид «%s»: Б влечёт А, обратное неверно', (_label, source) => {
    if (canActOnTrustlineFigures(source)) {
      expect(trustlineSourceAnswered(source), 'мутировать позволено по тому, чего нам не отвечали').toBe(true)
    }
  })
})

describe('заморозка: копия наследует основание и НЕ выдаёт себя за живое состояние', () => {
  /**
   * СИТУАЦИЯ: окно edge-detail заморожено (`keepAlive`) в момент, когда запрос trustlines был
   *   ещё в полёте. `interact.mode.busy` — busy МУТАЦИИ, а не состояние загрузки, поэтому
   *   «Send Payment» в этот момент нажимается, и такое окно достижимо.
   * ЧТО БЫЛО: замороженная копия навсегда сообщала «Trustline data is still loading from the
   *   backend» — про копию, которую не обновит ни один запрос: тот запрос, когда ответит,
   *   обновит живое состояние, а не этот снимок.
   *
   * ЧТО РАЗЛИЧАЕТ ВЫБОРКА: реализацию, которая помечает замороженное основание, от исходной
   *   (`loading` проходит насквозь) и от починки «превратить заморозку `loading` в `frozen`» —
   *   последняя ловится соседним ассертом про `canActOnTrustlineFigures`: это было бы
   *   повышение происхождения, ровно тот P3, который уже закрывали.
   * ЧЕГО НЕ РАЗЛИЧАЕТ: точных слов; судится отсутствие утверждения о живом запросе и наличие
   *   утверждения о заморозке.
   */
  it('замороженный `loading` не утверждает, что данные всё ещё грузятся', () => {
    const frozenLoading = freezeTrustlineFiguresSource({ kind: 'loading' })

    expect(trustlineSourceFrozen(frozenLoading), 'факт заморозки потерян').toBe(true)
    expect(
      trustlineFrozenNotice(frozenLoading),
      'замороженное окно ничем не отличается от живого: именно эту неразличимость уведомление ' +
        'о заморозке и заведено убирать',
    ).not.toBeNull()

    const text = `${trustlineFiguresNotice(frozenLoading) ?? ''} ${trustlineFrozenNotice(frozenLoading) ?? ''}`
    expect(
      text,
      'копия объявлена «всё ещё загружающейся»: её не обновит ни один запрос, и это утверждение ' +
        'о живом состоянии в окне, которое живым не является',
    ).not.toMatch(/still loading/i)
    expect(text, 'заморозка не названа словами').toMatch(/frozen/i)

    expect(
      canActOnTrustlineFigures(frozenLoading),
      'заморозка ПОВЫСИЛА происхождение: «запрос был в полёте» превратилось в «бэкенд ответил»',
    ).toBe(false)
  })

  /**
   * ВТОРАЯ ПОЛОВИНА НАХОДКИ: замороженные `no-row` и `never-asked` были наблюдаемо неотличимы
   * от живых. Разница существенная: живое окно обновится, замороженное — нет, и оно может
   * описывать вообще другую пару, чем выбранная сейчас.
   */
  it.each([
    ['no-row', { kind: 'no-row' } as TrustlineFiguresSource],
    ['never-asked', { kind: 'never-asked' } as TrustlineFiguresSource],
    ['failed', { kind: 'failed', message: 'boom' } as TrustlineFiguresSource],
  ])('замороженный «%s» отличим от живого «%s»', (_label, live) => {
    const frozen = freezeTrustlineFiguresSource(live)

    expect(trustlineFrozenNotice(live), 'живое окно объявлено замороженным').toBeNull()
    expect(
      trustlineFrozenNotice(frozen),
      'замороженное окно и живое дают один и тот же экран — заморозка не проверяется ничем',
    ).not.toBeNull()

    // Основание не повышено и не понижено: тот же вид, тот же смысл, плюс факт заморозки.
    expect(frozen.kind, 'заморозка подменила основание другим').toBe(live.kind)
    expect(canActOnTrustlineFigures(frozen)).toBe(canActOnTrustlineFigures(live))
    expect(trustlineSourceAnswered(frozen)).toBe(trustlineSourceAnswered(live))
  })

  it('замороженный `failed` сохраняет причину отказа', () => {
    const frozen = freezeTrustlineFiguresSource({ kind: 'failed', message: 'GET /trustlines: 503' })
    expect(trustlineFiguresNotice(frozen) ?? '').toContain('GET /trustlines: 503')
  })

  it('замороженный `no-row` по-прежнему говорит, что линии нет', () => {
    const frozen = freezeTrustlineFiguresSource({ kind: 'no-row' })
    expect(trustlineNoRowNotice(frozen) ?? '').toMatch(/no trustline/i)
  })

  /**
   * КОНТРОЛИ, без которых всё выше удовлетворяется починкой «считать замороженным всё подряд».
   */
  it('КОНТРОЛЬ: живые основания не объявляются замороженными', () => {
    for (const [label, source] of ALL_KINDS) {
      if (label === 'frozen') continue
      expect(trustlineSourceFrozen(source), `живое основание «${label}» объявлено замороженным`).toBe(false)
    }
    expect(trustlineSourceFrozen(null)).toBe(false)
  })

  it('КОНТРОЛЬ: заморозка настоящего ответа со строкой по-прежнему даёт `frozen`', () => {
    const frozen = freezeTrustlineFiguresSource({ kind: 'row' })
    expect(frozen.kind).toBe('frozen')
    expect(canActOnTrustlineFigures(frozen), 'устаревание превращено в отсутствие').toBe(true)
    expect(trustlineFiguresNotice(frozen), 'замороженный ОТВЕТ объявлен отсутствием ответа').toBeNull()
    expect(trustlineFrozenNotice(frozen)).not.toBeNull()
  })

  it('КОНТРОЛЬ: повторная заморозка идемпотентна', () => {
    expect(freezeTrustlineFiguresSource({ kind: 'frozen' }).kind).toBe('frozen')
  })

  it('КОНТРОЛЬ: заморозили неизвестно что — оснований нет', () => {
    const frozen = freezeTrustlineFiguresSource(null)
    expect(canActOnTrustlineFigures(frozen)).toBe(false)
    expect(trustlineSourceAnswered(frozen)).toBe(false)
    expect(trustlineFiguresNotice(frozen)).not.toBeNull()
  })
})
