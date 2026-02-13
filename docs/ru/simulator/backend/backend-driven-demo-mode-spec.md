# Simulator UI — Backend-driven Demo Mode (one pipeline) + Clearing Viz v2

Дата: **2026-02-05**

## 0. Контекст

Исторически в Simulator UI существовали два независимых источника событий:

- **demo (offline)**: плейлисты JSON (`public/simulator-fixtures/v1/.../events/demo-*.json`) + плеер на фронте.
- **real**: backend SSE (`/api/v1/simulator/runs/{run_id}/events`) + нормализация + единый FX пайплайн.

Это привело к разъезду семантики, таймингов и формата шагов clearing (в т.ч. в демо-фикстурах встречались шаги, где `highlight_edges` и `particles_edges` совпадают/дублируются).

Нужно:

1) **Убрать dual-logic**: визуализация должна работать по **одному** контракту и по **одному** пайплайну.
2) Сохранить удобство “демо-кнопок” для отладки эффектов без запуска полноценных сценариев.
3) Обновить визуализацию клиринга: **подсветка всех узлов цикла** + **одна большая вылетающая сумма очищенного долга**.

## 1. Цели

- Simulator UI всегда получает доменные события через **backend SSE** (одинаково для «обычных» real/fixtures прогонов и для “demo/debug кнопок”).
- Две кнопки (аналог текущего demo HUD):
  - **Single TX**
  - **Run Clearing (full cycle)**
- Новая визуализация клиринга (Clearing Viz v2):
  - подсветка всех узлов цикла во время проигрывания план-степов;
  - по завершении — **одна** крупная анимация/лейбл с `cleared_amount`.
- Удалить устаревший offline demo (фикстуры/плеер/валидации плейлистов), чтобы не было расхождений.

## 2. Non-goals

- Полный «replay/seek» исторических прогонов.
- Поддержка оффлайн-демо без backend.
- Полная симуляция платежной доменной логики в debug-кнопках (это опционально для phase-2).

## 3. Ключевой принцип: one visualization pipeline

- **Один контракт событий**: только формат из `app/schemas/simulator.py`.
- **Один normalizer**: `normalizeSimulatorEvent.ts`.
- **Один FX пайплайн**: функции real-mode FX (tx + clearing) используются всегда.

Любая “демо-отладка” реализуется как **backend actions**, которые генерируют те же события, что и обычный раннер.

## 4. UX / UI поведение

### 4.1. Где находятся кнопки

В UI добавляется панель **FX Debug** (или аналог), отображаемая:

- только в dev (`import.meta.env.DEV`) **или**
- при `?debug=1` (для удобства на shared стендах).

Дополнительно: в UI есть переключатель **Demo UI** (верхний левый угол), который:

- включает `ui=demo` и `debug=1`
- принудительно переводит UI в `mode=real`

Т.е. пользователь может войти в demo/debug без ручного редактирования URL.

### 4.2. Кнопки

1) **Single TX**
   - Триггерит ровно один `tx.updated` (и, при необходимости, `tx.failed` в будущих фазах).
   - Эффект/рендер должен быть идентичен тому, что UI делает при получении `tx.updated` из обычного раннера.

2) **Run Clearing (full cycle)**
  - Триггерит одно событие: `clearing.done` (один цикл, без `clearing.plan`).
  - `clearing.done` содержит `cycle_edges` (authoritative) и `cleared_amount`.
  - FX соответствует real-mode.
  - Clearing Viz v2 применяется поверх этого.

### 4.3. Детерминизм (рекомендуется)

Чтобы эффект был воспроизводим:

- UI может передавать `seed` в action endpoint.
- backend добавляет `debug.seed` в payload (схема `extra="allow"`, это допустимо).

### 4.4. Как UI выбирает run (важно)

Кнопки FX Debug **не создают отдельную визуализацию**. Они просто просят backend эмитить события в текущий run.

Правило:

- Если в UI уже есть активный `runId` (real-mode control plane уже запущен) — actions вызываются для этого `runId`.
- Если `runId` ещё нет, UI делает **автоматический старт** минимального прогона:
  - `POST /api/v1/simulator/runs` с параметрами:
    - `scenario_id = выбранный в UI` (если не выбран — дефолтный preset)
    - `mode = fixtures` (быстрый/дешёвый, для отладки визуальных эффектов)
    - `intensity_percent = текущий ползунок UI` (или дефолт 30)
  - затем подключается к SSE `/runs/{runId}/events` и только после этого запускает action.

Так достигается ключевое UX требование: «нажать кнопку и сразу увидеть эффект», без ручного старта сценария.

Ограничение безопасности: FX Debug панель показывается только в dev или при `?debug=1`, но **actions** дополнительно защищены backend-guardrail (см. 5.1).

## 5. Backend API: actions для debug/demo

Добавляется группа endpoints (control plane) для запуска “одиночных” событий внутри существующего run.

### 5.1. Общие правила

- Все endpoints требуют `require_participant_or_admin` (как прочие simulator endpoints).
- Рекомендуемый guardrail:
  - `SIMULATOR_ACTIONS_ENABLE=1` включает endpoints;
  - по умолчанию в prod = выключено.
- Endpoint должен быть **idempotent-friendly** (напр., поддержка `client_action_id`), но строгая идемпотентность не обязательна для FX-debug.

Ошибки (рекомендация, минимально достаточно):

- `404` если `run_id` не найден.
- `409` если run в терминальном состоянии (`stopped`/`error`) и actions больше не принимаются.
- `403` если `SIMULATOR_ACTIONS_ENABLE != 1` (или аналогичный флаг окружения).

Примечание: даже если run в `paused`, actions **разрешены** (они не обязаны зависеть от tick-loop).

### 5.2. POST /api/v1/simulator/runs/{run_id}/actions/tx-once

**Назначение:** сгенерировать единичный `tx.updated`.

Request (JSON):

- `equivalent: string` (обяз.)
- `from?: string` (опц.; если не задано — backend выбирает сам)
- `to?: string` (опц.)
- `amount?: string` (опц.; major units, например "12.50")
- `ttl_ms?: int` (опц.; default 1200)
- `intensity_key?: "low"|"mid"|"high"` (опц.; default "mid")
- `seed?: string|number` (опц.)

Response (JSON):

- `ok: true`
- `emitted_event_id: string`

Дополнительно (рекомендуется):

- `client_action_id?: string` (если был передан)

**Семантика события:**

- event payload должен соответствовать `SimulatorTxUpdatedEvent`.
- `edges[]` должен содержать хотя бы одно ребро (можно 1-hop для минимального UX).
- Важно: при сериализации использовать `model_dump(mode="json", by_alias=True)` для корректного ключа `from`.

Рекомендованный минимальный payload (пример):

```json
{
  "equivalent": "UAH",
  "from": "P1",
  "to": "P2",
  "amount": "12.50",
  "ttl_ms": 1200,
  "intensity_key": "mid",
  "seed": "fx-debug-001"
}
```

Примечание: `node_patch/edge_patch` можно добавлять позже (phase-2) для более “реалистичной” динамики used/available.

### 5.3. POST /api/v1/simulator/runs/{run_id}/actions/clearing-once

**Назначение:** сгенерировать `clearing.done` для одного цикла (без `clearing.plan`).

Request (JSON):

- `equivalent: string` (обяз.)
- `seed?: string|number` (опц.)
- `cycle_edges?: Array<{from: string, to: string}>` (опц.; если не задано — backend выбирает цикл сам)
- `cleared_amount?: string` (опц.; major units; если не задано — backend выбирает/считает сам)

Response (JSON):

- `ok: true`
- `plan_id: string`
- `done_event_id: string`

Дополнительно (рекомендуется):

- `client_action_id?: string` (если был передан)

**Семантика `clearing.done`:**

- Содержит `plan_id`, `cleared_amount`, `cycle_edges`.
- `node_patch/edge_patch` опциональны (phase-2).

### 5.4. Где живёт логика генерации

Рекомендуемая архитектура:

- В runtime/run record добавить очередь “manual actions” (in-memory, best-effort).
- Runner (fixtures или real) в tick-loop:
  - забирает actions;
  - эмитит события через `SseBroadcast` так же, как обычные доменные события.

Это гарантирует:

- одинаковую последовательность event_id;
- попадание в replay buffer;
- одинаковый формат SSE.

Примечание по реализации: если actions будут эмититься напрямую (без очереди в tick-loop), это тоже допустимо, если:

- event_id монотонно растёт по run (как и для обычных событий),
- событие попадает в ring-buffer (для replay по `Last-Event-ID`).

## 6. Frontend изменения (удаление старого demo)

### 6.1. Удалить offline demo pipeline

Удалить/вычистить:

- `useDemoPlayer.ts` + тесты
- `useDemoActions.ts` + тесты
- `useDemoPlaybackControls.ts`
- `useAppDemoPlayerSetup.ts`
- `demo/playlistValidation.ts` и привязанные проверки
- demo playlists/сцены, которые требуют загрузки `demo-*.json`
- `public/simulator-fixtures/v1/**/events/demo-*.json` (и мета-доки, относящиеся к demo events)

Разрешено оставить **только** то, что используется для snapshot fixtures (если они остаются нужны для e2e/screenshot). Если snapshot fixtures не нужны — удалить целиком `public/simulator-fixtures/v1`.

### 6.2. Всегда использовать backend

UI должен:

- получать snapshot через backend (`GET /api/v1/simulator/.../graph/snapshot` или `.../scenarios/{id}/graph/preview`);
- получать события только через backend SSE;
- в dev/debug режиме дергать новые action endpoints.

`?mode=demo` считается устаревшим и удаляется.

Рекомендуемая замена:

- “режимы” UI больше не означают «offline demo vs real». Всегда backend.
- быстрый режим для визуальной отладки = `run.mode = fixtures` (создаётся через `POST /runs`).

## 7. Clearing Viz v2 (новые требования визуализации)

### 7.1. Подсветка всех узлов цикла

Требование: во время проигрывания clearing цикла подсвечиваются **все узлы**, участвующие в цикле.

Реализация (frontend-only):

- При получении `clearing.done`:
  - вычислить `cycleNodeIds` как объединение всех `from/to` из `cycle_edges`.
  - подсветить рёбра из `cycle_edges` и узлы из `cycleNodeIds` на короткое TTL.

Тайминг (рекомендация):

- старт: при `clearing.done`
- стоп: через 900–1500ms (best-effort)
- Для визуала:
  - либо новый “node glow” overlay (предпочтительно, т.к. это подсветка «во времени», а не одноразовый burst),
  - либо серия `spawnNodeBursts` с низкой амплитудой и повтором.

Критически: подсветка должна быть **одна на план**, а не на каждое ребро (иначе получится “месиво”).

### 7.2. Одна большая вылетающая сумма cleared_amount

Требование: по `clearing.done` показывается **одна** крупная анимация суммы очищенного долга.

Рекомендованный UX:

- Текст: форматированный `cleared_amount` + `equivalent`.
- Позиция старта: центр цикла (центроид позиций `cycleNodeIds` по layout), fallback — центр канваса.
- Анимация: движение вверх на 60–120px + fade-out за 900–1400ms.
- Размер/вес: заметно крупнее tx-лейблов.

Ограничение: если `cleared_amount` отсутствует — лейбл не показывать (или показывать “Cleared” без суммы — отдельное решение).

## 8. Acceptance criteria

- В UI отсутствует код-путь, который грузит `demo-*.json` и проигрывает их на фронте.
- Две кнопки “Single TX” и “Run Clearing” работают через backend actions и дают события в SSE.
- Кнопки работают даже если пользователь ещё не стартовал run руками: UI автоматически создаёт `fixtures` run и подключается к SSE.
- Визуальные эффекты для tx/clearing совпадают с real pipeline (т.е. UI обрабатывает один и тот же тип событий).
- Clearing Viz v2:
  - во время цикла подсвечены все узлы цикла;
  - на завершении показывается ровно 1 крупный лейбл суммы.

## 9. План внедрения (итерации)

1) Backend: добавить action endpoints + минимальную генерацию событий (без patches), guarded env.
2) Frontend: добавить FX Debug панель и вызовы endpoints.
3) Frontend: реализовать Clearing Viz v2 (node highlight + big label).
4) Удалить legacy demo artifacts (фикстуры/плеер/тесты/режимы), обновить docs и ссылки.
5) (Опционально) Phase-2: добавить `node_patch/edge_patch` для “реалистичных” изменений графа.

## 10. Примечания по сериализации (Pydantic alias)

В simulator schemas используются поля `from_` с alias `from`.

Везде, где backend делает `model_dump(mode="json")` для событий с `from_`, нужно обязательно ставить `by_alias=True`, иначе UI получит `from_` и сломает нормализацию.
