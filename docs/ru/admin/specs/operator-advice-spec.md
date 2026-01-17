# Operator Advice (Admin UI) — спецификация

**Статус:** к реализации сейчас (MVP+)

## 0) Зачем это нужно

Экраны диагностики (Graph analytics / Trustlines) уже показывают **факты** (bottlenecks, загрузка лимитов, концентрация), но оператору в момент инцидента нужны ещё и **рекомендации**:
- что это означает в терминах сети;
- какие следующие шаги (куда кликнуть, что проверить, какие действия возможны);
- какие решения вероятно помогут (повышение лимитов, локальная кампания, включение/настройка clearing и т.п.).

Цель доработки: добавить **детерминированный слой советов** поверх существующих данных, без «магии» и без обучения — правила прозрачны, воспроизводимы и объяснимы.

## 1) Scope

### 1.1. Где показываем советы (UI placement)

1) **Network Graph → Drawer → Summary**
- Панель `Operator advice` сверху вкладки Summary.
- Контекст: выбранный узел/ребро + выбранный `Equivalent` + порог `threshold`.

2) **Trustlines**
- Панель `Operator advice` над таблицей.
- Контекст: текущие фильтры (equivalent/creditor/debtor/status) и `threshold`.

> На первом шаге советы **read-only**: никаких новых «силовых» действий. Кнопки в советах ведут на существующие экраны/фильтры.

### 1.2. Не-цели (пока)
- Автоматическое изменение лимитов/статусов (только навигация + текст рекомендаций).
- Исторические тренды (period/time-series) — советы работают по Snapshot данным.
- NLP/LLM: никаких генеративных текстов; всё по фиксированным правилам.

## 2) Модель данных

`AdviceItem` (UI-элемент рекомендации):
- `id`: стабильный код правила.
- `severity`: `info | warning | danger`.
- `title` + `body`: i18n keys (RU/EN), тело допускает переменные.
- `actions[]`: набор кнопок «быстрых шагов» (переходы по роутам с query).

Ключевой принцип: **объяснимость**. Каждая рекомендация должна содержать:
- триггер (почему сработало);
- что проверить;
- какие варианты действий обычно помогают.

## 3) Набор правил (MVP)

Ниже — минимальный набор правил, который уже даёт пользу и не требует новых API.

### 3.1. Bottlenecks (узкие рёбра trustline)

**Rule TL_MANY_BOTTLENECKS (Trustlines page)**
- Trigger: в текущем списке trustlines есть `bottleneck_count >= 3` (где `available/limit < threshold` и status=active).
- Severity:
  - `warning` при 3–9
  - `danger` при >= 10
- Advice:
  - это прямой предиктор отказов платежей;
  - проверьте топ-3 ребра (кто creditor→debtor) и «соседние связи» в Graph.
- Actions:
  - открыть `Graph` (с тем же `equivalent` и `threshold` в query, если поддерживается),
  - сузить `Trustlines` фильтром по `creditor`/`debtor` (по выбранному ребру — если есть selection),
  - открыть `Participants` для ключевых PID.

**Rule GRAPH_NODE_HAS_BOTTLENECKS (Graph drawer, node selected)**
- Trigger: `selectedCapacity.bottlenecks.length > 0`.
- Severity: `warning` (или `danger`, если >= 5).
- Advice:
  - у участника есть локальные ограничения входящих/исходящих лимитов;
  - уточнить, он «поглотитель» или «поставщик» ликвидности.
- Actions:
  - открыть `Trustlines` как creditor (out bottlenecks),
  - открыть `Trustlines` как debtor (in bottlenecks).

### 3.2. Capacity near limit (почти исчерпан лимит)

**Rule GRAPH_CAPACITY_NEAR_LIMIT**
- Trigger: `selectedCapacity.out.pct >= 0.90` и/или `selectedCapacity.inc.pct >= 0.90`.
- Severity:
  - `warning` при 0.90–0.97
  - `danger` при >= 0.98
- Advice:
  - вероятны массовые отказы по маршрутам через этого участника;
  - стоит проверить bottlenecks и рассмотреть повышение лимитов/clearing.
- Actions:
  - открыть `Trustlines` по PID (creditor/debtor),
  - открыть `Audit log` по PID (последние изменения trustlines/политик).

### 3.3. Concentration risk (концентрация ликвидности)

**Rule GRAPH_CONCENTRATION_HIGH**
- Trigger: `selectedConcentration.outgoing.level.type !== success` или `incoming.level.type !== success`.
- Severity: `warning/danger` берётся из level.
- Advice:
  - высокая концентрация делает сеть хрупкой (single-point-of-failure);
  - полезно планировать диверсификацию trustlines.
- Actions:
  - открыть `Trustlines` для ключевого PID (если выбран узел),
  - открыть `Participants` для узла (проверить статус/роль).

## 4) UX требования

- Советы должны быть **контекстными**: если нет выбранного equivalent — показываем только те правила, которым этого не нужно.
- Текст не должен выглядеть как «приказ»; формат: *наблюдение → смысл → следующий шаг*.
- Действия не должны ломать текущие фильтры: роут-переходы сохраняют `route.query`, добавляя свои параметры.

## 5) Acceptance criteria

- На `Trustlines` при наличии bottlenecks появляется панель с советом + счётчиком `bottleneck_count`.
- В `Graph → Drawer → Summary` при выборе узла появляются советы по bottlenecks/capacity/concentration (если применимо).
- Все user-facing строки — через i18n (RU/EN).
- Логика детерминирована и чистая: одинаковые входные данные → одинаковые советы.

## 6) Будущее расширение (после MVP)

- Периодные правила (рост bottlenecks за 7/30 дней, churn).
- Интеграция с `Events`/`Transactions` для советов по частым abort/timeout.
- «Runbook links» (ссылки на внутренние SOP/процедуры сообщества).
