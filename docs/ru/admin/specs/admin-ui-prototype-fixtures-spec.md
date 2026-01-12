# GEO Hub Admin Console — Fixture Pack для статического прототипа (Variant B)

**Версия:** 0.1  
**Дата:** 2026‑01‑11  
**Статус:** рабочая спецификация для прототипирования UI без backend

Связано с:
- `docs/ru/admin/specs/admin-ui-spec.md` (экраны/состояния/401/403)
- `docs/ru/admin/specs/admin-console-minimal-spec.md` (MVP scope)
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
- `admin-fixtures/v1/scenarios/*.json` — сценарии поведения (ошибки/401/403/empty/slow).
- `admin-fixtures/v1/api-snapshots/*.json` — примеры готовых ответов (для UI без мок‑сервера), опционально.
- `admin-fixtures/tools/generate_admin_fixtures.py` — генератор (детерминированный).
- Seed-генераторы (каноничные датасеты для графа):
  - `admin-fixtures/tools/generate_seed_greenfield_village_100.py`
  - `admin-fixtures/tools/generate_seed_riverside_town_50.py`
  - общий модуль: `admin-fixtures/tools/seedlib.py`

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

---

## 8. Состав датасетов (минимум)

Нормативные минимумы (чтобы хватало для UX):
- `participants`: 60+ (разные статусы: active/frozen/banned).
- `trustlines`: 120+ (3+ equivalents, разные статусы, есть узкие места).
- `audit-log`: 150+ (разные action/object_type, есть before/after).
- `incidents`: 25+ зависших tx.
- `equivalents`: 10+ (часть inactive).

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
- `python admin-fixtures/tools/generate_admin_fixtures.py`

