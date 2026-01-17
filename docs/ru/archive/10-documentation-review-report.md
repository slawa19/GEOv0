# GEOv0-PROJECT — отчёт ревью документации (Task B)

**Дата:** 2025‑12‑13
**Область:** RU документация в `docs/ru/` + точечные правки в `docs/ru/archive/` + артефакты контрактов/стенда (`api/openapi.yaml`, `seeds/`)
**Цель:** устранить противоречия/несостыковки после последних правок и восстановить понятную навигацию, особенно вокруг multipath и документов 07/08, а также закрыть «хвост» артефактов (test-scenarios, OpenAPI, seeds, API max-flow, PWA vs Flutter).

---

## 1) Что было найдено (проблемы)

### 1.1. «Потерянные» ключевые документы 07/08
- Документы:
  - [`docs/ru/archive/07-clarifications-and-gaps.md`](docs/ru/archive/07-clarifications-and-gaps.md:1)
  - [`docs/ru/archive/08-decisions-and-defaults-recommendations.md`](docs/ru/archive/08-decisions-and-defaults-recommendations.md:1)
  были в архиве и **не имели понятной точки входа** из основного маршрута чтения RU документации (в частности, из [`docs/ru/00-overview.md`](docs/ru/00-overview.md:1)).

**Риск:** читатель не понимает, где искать «обоснования решений», дефолты и историю спорных мест.

### 1.2. Несостыковка ключей multipath в архиве 08
- В архивном [`docs/ru/archive/08-decisions-and-defaults-recommendations.md`](docs/ru/archive/08-decisions-and-defaults-recommendations.md:1) фигурировали исторические ключи:
  - `routing.default_mode`
  - `routing.full_multipath_enabled`
- В актуальных нормативных документах каноном являются:
  - `routing.multipath_mode`
  - `feature_flags.full_multipath_enabled`
  (см. [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1) и [`docs/ru/02-protocol-spec.md`](docs/ru/02-protocol-spec.md:1)).

**Риск:** разработчики/операторы могут реализовать/включать не те ключи, что ожидаются текущей документацией.

### 1.3. «Битая навигация по смыслу» в deployment
- В [`docs/ru/05-deployment.md`](docs/ru/05-deployment.md:1) YAML конфиг был указан как «[`geo-hub-config.yaml`](docs/ru/config-reference.md:1)» — текст выглядел как ссылка на файл, но вела она на реестр параметров.

**Риск:** ожидание отдельного файла конфигурации в репозитории vs фактическое наличие только примера в документации.

### 1.4. Упоминание внешнего документа, отсутствующего в репозитории (архивный 07)
- В [`docs/ru/archive/07-clarifications-and-gaps.md`](docs/ru/archive/07-clarifications-and-gaps.md:1) ранее фигурировало упоминание внешнего документа с рекомендациями, которого **нет в текущем репозитории**.

**Риск:** читатель воспринимает это как «битую ссылку» и ожидает файл в `docs/ru/`.

### 1.5. Несостыковки уровня «обещание vs канон» (multipath/clearing)
- Часть документов уровня overview/architecture использовала общие формулировки:
  - «multi‑path 2–3 маршрута»
  - «клиринг циклы 3–4» / «3–6»
  без явной привязки к:
  - baseline vs экспериментальному full‑режиму
  - параметрам и дефолтам
  - тому, что именно обязательно доступно в админке

---

## 2) Что исправлено (изменения)

### 2.1. Восстановлен понятный путь к архивным документам 07/08 из активной документации
В [`docs/ru/00-overview.md`](docs/ru/00-overview.md:1) добавлены прямые ссылки на архивные материалы и канонические документы:

- [`docs/ru/archive/07-clarifications-and-gaps.md`](docs/ru/archive/07-clarifications-and-gaps.md:1) — история пробелов/уточнений (контекст «почему не с первого раза»)
- [`docs/ru/archive/08-decisions-and-defaults-recommendations.md`](docs/ru/archive/08-decisions-and-defaults-recommendations.md:1) — варианты решений и рекомендации (история обсуждений)
- [`docs/ru/09-decisions-and-defaults.md`](docs/ru/09-decisions-and-defaults.md:1) — текущие ключевые решения и дефолты MVP (канон)

### 2.2. Зафиксирован канон multipath и помечены устаревшие ключи в архиве
В архивном документе:
- [`docs/ru/archive/08-decisions-and-defaults-recommendations.md`](docs/ru/archive/08-decisions-and-defaults-recommendations.md:1)

добавлена явная пометка:
- ключи `routing.default_mode` и `routing.full_multipath_enabled` — **deprecated/черновик**,
- канон смотреть в:
  - [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)
  - [`docs/ru/02-protocol-spec.md`](docs/ru/02-protocol-spec.md:1)

Тем самым устранено противоречие «архивный ключ = текущий ключ».

### 2.3. Исправлена формулировка про `geo-hub-config.yaml` в deployment
В [`docs/ru/05-deployment.md`](docs/ru/05-deployment.md:1) заменено «похоже на файл» на «пример в документации», чтобы читатель не ожидал, что файл лежит в репозитории.

### 2.4. Нейтрализовано упоминание отсутствующего внешнего документа в архивном 07
В [`docs/ru/archive/07-clarifications-and-gaps.md`](docs/ru/archive/07-clarifications-and-gaps.md:1) убрано упоминание конкретного имени несуществующего файла; оставлена нейтральная ремарка, что внешний документ с рекомендациями отсутствует в репозитории.

### 2.5. Синхронизировано описание multipath/clearing на уровне архитектуры
В [`docs/ru/03-architecture.md`](docs/ru/03-architecture.md:1) уточнено:
- baseline: limited multipath (2–3 маршрута) со ссылкой на канон параметров,
- full multipath: опционально/экспериментально, включается только feature flag,
- clearing: разделено на триггерный 3–4 и периодический 5–6 (опционально) со ссылкой на канон.

### 2.6. Устранена несогласованность клиентского стека (Flutter vs PWA) в MVP
- В [`docs/ru/03-architecture.md`](docs/ru/03-architecture.md:1) и [`docs/ru/00-overview.md`](docs/ru/00-overview.md:1) зафиксировано: **primary клиент для MVP — Web Client (PWA)**, Flutter — отложено на следующие этапы.

### 2.7. Добавлен Max Flow API endpoint
- В [`docs/ru/04-api-reference.md`](docs/ru/04-api-reference.md:1) добавлен endpoint `GET /payments/max-flow` и уточнена разница с `GET /payments/capacity`.

### 2.8. Добавлены тестовые сценарии MVP
- Создан документ [`docs/ru/08-test-scenarios.md`](docs/ru/08-test-scenarios.md:1) (20+ сценариев + топ‑5 для e2e).

### 2.9. Создана OpenAPI спецификация
- Создан файл [`api/openapi.yaml`](api/openapi.yaml:1) (OpenAPI 3.0), включающий endpoints `capacity` и `max-flow` и базовые сущности.

### 2.10. Добавлены seed-данные для локального стенда
- Созданы файлы:
  - [`seeds/equivalents.json`](seeds/equivalents.json:1)
  - [`seeds/participants.json`](seeds/participants.json:1)
  - [`seeds/trustlines.json`](seeds/trustlines.json:1)

---

## 3) Контроль требований (multipath)

Пользовательское требование выполнено на уровне документации:

- Baseline multipath: **limited** по умолчанию:
  - [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)
  - [`docs/ru/02-protocol-spec.md`](docs/ru/02-protocol-spec.md:1)

- Full multipath: **опция для perf/benchmark**, выключена по умолчанию и включается через feature flag:
  - `feature_flags.full_multipath_enabled: false` (дефолт)
  - канон: [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)

- Параметры вынесены в отдельный документ:
  - [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)

- Управление параметрами из минимальной админки:
  - [`docs/ru/admin/specs/archive/admin-console-minimal-spec.md`](docs/ru/admin/specs/archive/admin-console-minimal-spec.md:1) включает `feature_flags.full_multipath_enabled` и `routing.*` как runtime‑mutable и подчёркивает важность `routing.max_paths_per_payment`.

---

## 4) Что остаётся открытым (требует owner/PO или отдельного решения)

1) **Единая политика для «концептуальных» документов в `docs/ru/concept/`**
   Там встречаются альтернативные формулировки (например, про клиентов, про подтверждения клиринга и т.п.).
   Рекомендуется отдельным проходом: «привести концепт‑доки в соответствие канону или пометить как исторические/альтернативные».

2) **Рекомендуется добавить явный раздел “Архив” в обзоре** (опционально).
   Сейчас ссылки на stub‑07/08 добавлены в таблицу, но можно выделить отдельную секцию «Архивные документы и обоснования», если захотите ещё более прозрачную навигацию.

---

## 5) Список затронутых файлов

**Созданы:**
- [`docs/ru/08-test-scenarios.md`](docs/ru/08-test-scenarios.md:1)
- [`docs/ru/09-decisions-and-defaults.md`](docs/ru/09-decisions-and-defaults.md:1)
- [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)
- [`docs/ru/admin/specs/archive/admin-console-minimal-spec.md`](docs/ru/admin/specs/archive/admin-console-minimal-spec.md:1)
- [`docs/ru/10-documentation-review-report.md`](docs/ru/10-documentation-review-report.md:1)
- [`api/openapi.yaml`](api/openapi.yaml:1)
- [`seeds/equivalents.json`](seeds/equivalents.json:1)
- [`seeds/participants.json`](seeds/participants.json:1)
- [`seeds/trustlines.json`](seeds/trustlines.json:1)

**Изменены:**
- [`docs/ru/00-overview.md`](docs/ru/00-overview.md:1)
- [`docs/ru/03-architecture.md`](docs/ru/03-architecture.md:1)
- [`docs/ru/04-api-reference.md`](docs/ru/04-api-reference.md:1)
- [`docs/ru/05-deployment.md`](docs/ru/05-deployment.md:1)
- [`docs/ru/archive/07-clarifications-and-gaps.md`](docs/ru/archive/07-clarifications-and-gaps.md:1)
- [`docs/ru/archive/08-decisions-and-defaults-recommendations.md`](docs/ru/archive/08-decisions-and-defaults-recommendations.md:1)