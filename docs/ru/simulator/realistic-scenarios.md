# Реалистичные сценарии симулятора (realistic-v2) — правила и чек‑лист (RU)

Цель: зафиксировать **требования к входным данным `scenario.json`**, которые делают прогон в **Real Mode** реалистичным и воспроизводимым:
- суммы (UAH) выходят на десятки/сотни,
- клиринг возникает регулярно (потому что есть естественные циклы),
- появляется заметный P2P,
- нет «обхода лимитов» за счёт неверной семантики trustlines.

Этот документ — **единая точка истины** по правилам *реалистичности сценариев*.
- Движок/алгоритмы: см. `docs/ru/simulator/backend/behavior-model-spec.md`.
- Приёмочные критерии real mode (метрики/контракты): см. `docs/ru/simulator/backend/acceptance-criteria.md`.

---

## 1) Инварианты (семантика)

### 1.1 Trustline direction (критично)
- `trustlines[].from → trustlines[].to` = **creditor → debtor**.
- Платёж `payer → payee` возможен только если существует маршрут по trustlines, и на каждом шаге используется лимит кредитора к должнику.

Практическое следствие:
- Для прямого платежа `A → B` (1 hop) требуется активная trustline **`B → A`** с достаточным остатком лимита
  (получатель кредитует отправителя; платёж увеличивает `debt[A → B]`, и он ограничен `limit[B → A]`).
- Для multi-hop `A → C` через `B` требуется цепочка trustlines **`B → A`** и **`C → B`** (путь платежа `A → B → C`).

### 1.2 Clearing возникает только на циклах долгов
Чтобы клиринг был не редкостью, в trustlines‑графе должны естественно появляться циклы вида:
- Household → Retail → Producer → Household

Ключевая идея: сценарий должен содержать «замыкающее ребро» от household к producer (как кредитная линия creditor→debtor),
чтобы платежи producer→household вообще были достижимы.

---

## 2) Требования realistic-v2 к `scenario.json`

### 2.1 Эквиваленты
- Для realistic‑v2 рекомендуется **UAH‑only**: `equivalents: ["UAH"]`.
- Цель: убрать «шум» мультивалютности и сделать метрики/клиринг читаемыми.

### 2.2 Роли участников (groups + behavior profiles)
Реалистичность достигается сочетанием:
- реалистичного **спроса** (частоты попыток),
- реалистичной **структуры кредита** (trustlines),
а не «подкруткой» движка в обход лимитов.

Требование к realistic‑v2:
- у участников должны быть осмысленные `groupId` (например: households/retail/producers/anchors/services — конкретные названия зависят от сценария),
- и задан `behaviorProfileId`,
- а в `behaviorProfiles[].props` должны быть заполнены минимум:
  - `tx_rate`
  - `recipient_group_weights`
  - `amount_model.UAH` (min/max/p50/p90 или эквивалентная модель)

Конкретные стартовые значения удобно брать из:
- `plans/simulator-realistic-scenario-v2-2026-01-30.md` (как инженерный “рецепт” параметров)

### 2.3 Trustlines: гарантированные циклы
Требование: в графе trustlines должны формироваться циклы не “случайно”, а структурно.

Минимальный набор проверок по данным:
- Есть связи household ↔ retail (в смысле достижимости платежей).
- Есть связи retail ↔ producer.
- Есть «замыкание»: **household (creditor) → producer (debtor)** хотя бы для подмножества участников.
- Лимиты на ребрах, замыкающих цикл, должны быть порядка сотен UAH (ориентир: 300..500), чтобы клиринг был заметен.

### 2.4 Суммы в real mode
- По умолчанию в real mode действует backward‑compatible cap: `SIMULATOR_REAL_AMOUNT_CAP=3.00`.
- Для realistic‑v2 запускать с `SIMULATOR_REAL_AMOUNT_CAP>=500`.

---

## 3) Чек‑лист «реалистичность данных» (быстрая проверка)

Перед тем как считать сценарий realistic‑v2 готовым:
1) `equivalents=["UAH"]` (или UAH доминирует и метрики не расползаются).
2) Участники размечены группами/ролями; профили поведения не пустые.
3) В trustlines есть минимум один структурный цикл household→retail→producer→household (лучше несколько независимых).
4) Нет ошибок направления trustlines (creditor→debtor) — это самая частая причина «платежи не ходят / клиринг не появляется».

---

## 4) Каноничные сценарии в репозитории

- Основной realistic‑v2 сценарий: `fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json`
- Компактный realistic‑v2: `fixtures/simulator/riverside-town-50-realistic-v2/scenario.json`
- Демо клиринга (ручной режим, не realistic‑v2 по профилям): `fixtures/simulator/clearing-demo-10/scenario.json`

---

## 5) Связанные документы (не каноничные по данным)

- Обзор сценариев и движка: `docs/ru/simulator/scenarios-and-engine.md`
- Спека модели поведения и требований к realistic‑v2: `docs/ru/simulator/backend/behavior-model-spec.md` (раздел про scenario requirements)
- Приёмочные критерии: `docs/ru/simulator/backend/acceptance-criteria.md` (INT-01a и др.)
