# ARCHIVED: GEO Hub Admin Console — Fixture Pack для статического прототипа (Variant B)

Каноническая точка входа по Admin UI и фикстурам: [docs/ru/admin/README.md](../../README.md).

---

# GEO Hub Admin Console — Fixture Pack для статического прототипа (Variant B)

**Версия:** 0.2
**Дата:** 2026‑01‑13
**Статус:** рабочая спецификация для прототипирования UI без backend

Связано с:
- `docs/ru/admin/specs/admin-ui-spec.md` (экраны/состояния/401/403)
- `docs/ru/admin/specs/archive/admin-console-minimal-spec.md` (MVP scope)
- `api/openapi.yaml` (контракт — ориентир)

---

## 1. Цель

Создать **единый, детерминированный** набор данных и сценариев ("fixture pack"), чтобы:
- собрать статическую SPA админку с моками (без backend);
- можно было легко сделать 2+ независимых варианта UI (другими агентами) и сравнить;
- UI можно было затем "переключить" на реальный API с минимальными изменениями.

Ключевой принцип: **данные и сценарии — общие**, меняется только слой UI/архитектура фронтенда.

---

## 2. Где лежат данные

Каноничный пакет:
- `admin-fixtures/v1/datasets/*.json` — исходные датасеты (много записей).
- Минимально ожидаемые файлы датасетов:
  - `health.json`, `health-db.json`
  - `config.json`, `feature-flags.json`
  - `participants.json`, `trustlines.json`, `audit-log.json`, `incidents.json`
  - `equivalents.json`, `migrations.json`, `integrity-status.json`
  - `debts.json`, `clearing-cycles.json`, `transactions.json`
- `admin-fixtures/v1/scenarios/*.json` — сценарии поведения (ошибки/401/403/empty/slow).
- `admin-fixtures/v1/api-snapshots/*.json` — примеры готовых ответов (для UI без мок‑сервера), опционально.
- `admin-fixtures/tools/generate_fixtures.py` — единый детерминированный генератор (каноничные community seeds).
  - seeds: `greenfield-village-100` | `riverside-town-50`
  - общие утилиты: `admin-fixtures/tools/seedlib.py`, `admin-fixtures/tools/adminlib.py`

  Копия для runtime Admin UI (синхронизируется из канона):
  - `admin-ui/public/admin-fixtures/v1/` — то, что реально грузит `mockApi` в браузере.
  - sync-скрипт: `admin-ui/scripts/sync-fixtures.mjs` (команда `npm run sync:fixtures`).

---

## 3. Детерминизм (нормативно)

Чтобы сравнение разных UI было честным:
- **Запрещён случайный рандом** в датасетах.
- Все `id`/`pid`/`code`/`tx_id` фиксированы.
- Все timestamps фиксированы относительно базовой даты (например, 2026‑01‑11T00:00:00Z).
- Сортировка во всех списках должна быть стабильной.

---

## 4. Envelope и ошибки (нормативно)

UI в прототипе должен работать так же, как с реальным API.

### 4.1. Успешный ответ

```json
{ "success": true, "data": { "...": "..." } }
```

### 4.2. Ошибка

```json
{
  "success": false,
  "error": {
    "code": "FORBIDDEN",
    "message": "Недостаточно прав",
    "details": {"hint": "X-Admin-Token is missing"}
  }
}
```

Рекомендуемые `code`:
- `UNAUTHORIZED` (401)
- `FORBIDDEN` (403)
- `VALIDATION_ERROR` (422)
- `INTERNAL_ERROR` (500)

---

## 5. Пагинация (нормативно)

Для таблиц (trustlines, participants, audit-log, incidents) пакет должен позволять проверить:
- 3+ страниц при `per_page=20`.

Рекомендованный формат `data` для списков:

```json
{
  "items": [ {"...": "..."} ],
  "page": 1,
  "per_page": 20,
  "total": 123
}
```

---

## 6. Требования к числам trustlines (нормативно)

Для trustlines:
- `limit`, `used`, `available` — **decimal string**.
- UI **не должен** использовать float для расчёта порогов.
- В пакете должны быть кейсы:
  - `available/limit < threshold` (например, 0.10) — подсветка.
  - `limit == 0` — подсветка не применяется.

---

## 7. Сценарии (нормативно)

Минимальный набор сценариев:
- `happy` — всё работает.
- `empty` — списки пустые.
- `error500` — серверная ошибка на выбранных endpoints.
- `admin_forbidden403` — все `/admin/*` возвращают 403.
- `integrity_unauthorized401` — `/integrity/*` возвращают 401.
- `slow` — искусственные задержки (например, 800–1500ms).

Сценарий задаётся в UI через переключатель (например, query `?scenario=happy`) или dev‑настройку.

Примечание: в текущем Admin UI сценарий реально выбирается через query `?scenario=...` (и UI-селектор в шапке в fixtures-first режиме).

---

## 8. Состав датасетов (минимум)

Рекомендованные минимумы (чтобы хватало для UX-демо; seed-датасеты могут быть меньше по некоторым сущностям):
- `participants`: 50 или 100 (поддерживаемые seed-наборы; это проверяет валидатор).
- `trustlines`: 120+ (3+ equivalents, разные статусы, есть узкие места).
- `audit-log`: 150+ (разные action/object_type, есть before/after).
- `incidents`: желательно 10+ (для демо таблицы/фильтров; в некоторых seed-наборах может быть меньше).
- `equivalents`: базовый набор фиксирован и валидируется (см. ниже).

Guardrails валидации (фактическая проверка в репозитории):
- `admin-ui/scripts/validate-fixtures.mjs` сверяет канон и public-copy, проверяет схемы, и ожидает ровно `UAH/EUR/HOUR`.
- `participants.length` должен быть 50 или 100 (или задаётся `EXPECTED_PARTICIPANTS`).

### 8.1. Инциденты (формат incidents.json)

Чтобы упростить прототипирование, формат `admin-fixtures/v1/datasets/incidents.json` допускает два варианта:
- массив: `[ { ...incident... }, ... ]`
- объект: `{ "items": [ { ...incident... }, ... ] }`

Текущий стек (`mockApi` и `validate-fixtures`) поддерживает оба варианта, но канонично рекомендуется `{ items: [...] }`.

---

## 9. Генерация

Генератор должен:
- создавать датасеты в `admin-fixtures/v1/datasets/`;
- опционально создавать несколько `api-snapshots` (page1/page2) для быстрых прототипов без логики пагинации.

В `api-snapshots` хранятся envelope-ответы для типовых запросов, например:
- `health.get.json`, `health.db.get.json`
- `admin.migrations.get.json`
- `admin.config.get.json`, `admin.feature-flags.get.json`
- `integrity.status.get.json`
- `admin.participants.page1.per20.json`
- `admin.trustlines.page1.per20.json`
- `admin.audit-log.page1.per20.json`
- `admin.incidents.page1.per20.json`

Команда (Windows/pwsh):
- `python admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100`
- `python admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50`

Опционально (хранить несколько паков без путаницы):
- сгенерировать pack в `admin-fixtures/packs/<seed_id>/v1`:
  - `python admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100 --pack`
- и активировать его как текущий канон (копирование в `admin-fixtures/v1`):
  - `python admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100 --pack --activate`

---

## 10. Дополнительные датасеты для метрик и аналитики

### 10.1. `debts.json`

**Назначение:** Расчёт баланса/нетто-позиции участников и "top counterparties". Строится из долгов (`debtor→creditor`), а не из trustlines.

**Схема:**
```json
[
  {
    "equivalent": "UAH",
    "debtor": "PID_...",
    "creditor": "PID_...",
    "amount": "123.45"
  }
]
```

**Источник данных:**
- Для прототипа производится детерминированно из `trustline.used`:
  - trustline `from → to` (creditor→debtor)
  - debt: `debtor=to`, `creditor=from`, `amount=used`

### 10.2. `clearing-cycles.json`

**Назначение:** Вкладка `Cycles (Clearing)` в drawer участника — показывает готовые циклы для взаимозачёта.

**Схема:**
```json
{
  "equivalents": {
    "UAH": {
      "cycles": [
        [
          { "debtor": "PID_A", "creditor": "PID_B", "equivalent": "UAH", "amount": "10.00" },
          { "debtor": "PID_B", "creditor": "PID_C", "equivalent": "UAH", "amount": "10.00" },
          { "debtor": "PID_C", "creditor": "PID_A", "equivalent": "UAH", "amount": "10.00" }
        ]
      ]
    }
  }
}
```

**Важно:**
- Граф trustlines (creditor→debtor) и граф debts (debtor→creditor) — **разные направления**.
- В UI важно явно подписывать, что cycles отображают долги.

### 10.3. `transactions.json`

**Назначение:** История платежей для отображения в детальных просмотрах участников и общей таблице.

---

## 11. Метаданные генерации

Файл `admin-fixtures/v1/_meta.json` содержит информацию о последней генерации:
- `generated_at` — timestamp генерации
- `counts` — количество записей по каждому датасету
- `seed_id` — идентификатор seed (например, `greenfield-village-100`)
- `generator` — какой скрипт/entrypoint генерировал pack

Используется для:
- быстрой проверки актуальности фикстур;
- обнаружения расхождений после запуска разных генераторов.

Guardrails:
- Admin UI валидатор сравнивает canonical `admin-fixtures/v1/_meta.json` и синхронизированную копию `admin-ui/public/admin-fixtures/v1/_meta.json`.
- `seed_id` должен быть из allow‑list (чтобы случайно не подменить демо‑сообщество).

---

## 12. Рабочий цикл разработки

1. **Сгенерировать canonical fixtures:**
   ```bash
  python admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100
   ```

  Альтернатива для параллельной работы с несколькими сообществами (packs + explicit activation):

  ```bash
  python admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50 --pack
  python admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50 --pack --activate
  ```

2. **Синхронизировать в admin-ui:**
   ```bash
   cd admin-ui
   npm run sync:fixtures
   ```

3. **Проверить корректность:**
   ```bash
   npm run validate:fixtures
   ```

Примечание: `npm run dev` и `npm run build` в `admin-ui/` уже включают sync+validate как pre-steps.

4. **Дорабатывать UI**, используя `mockApi` + новые датасеты.

---

## Changelog

- **v0.2 (2026-01-13):** Добавлены секции 10 (debts.json, clearing-cycles.json, transactions.json), 11 (_meta.json), 12 (рабочий цикл).
- **v0.1 (2026-01-11):** Начальная версия спецификации.
