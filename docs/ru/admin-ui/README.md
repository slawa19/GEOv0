# GEO v0.1 — Admin UI (RU)

Этот раздел — **каноническая** документация по админке (операторской консоли) и правилам UI для текущей реализации в этом репозитории.

## Технологический стек (источник истины)

Admin UI — отдельное приложение в каталоге `admin-ui/`:

- Vue 3 + TypeScript
- Vite
- Element Plus
- Pinia

Источник истины по версиям/зависимостям:

- `admin-ui/package.json`

Каноническое описание стека проекта целиком (backend + Admin UI):

- [../03-architecture.md](../03-architecture.md)

## Быстрый старт (локально)

- Рекомендуемый способ запуска на Windows: `scripts/run_local.ps1` (поднимает backend + Admin UI, управляет портами и записывает `admin-ui/.env.local`).

## Режимы API (mock vs real)

Admin UI поддерживает два режима:

- **mock** (по умолчанию) — UI работает через mock API слой и читает детерминированные JSON фикстуры из `admin-ui/public/admin-fixtures/v1/...`.
- **real** — UI обращается к backend по HTTP.

Переключение управляется env-переменными Vite:

- `VITE_API_MODE=mock|real`
- `VITE_API_BASE_URL=http://127.0.0.1:18000` (дефолт локального runner-а)
- `VITE_API_BASE_URL=http://127.0.0.1:8000` (дефолт Docker Compose)

Важно: `scripts/run_local.ps1 start` автоматически пишет/обновляет `admin-ui/.env.local` и включает **real** режим.

Техническая заметка (EN) по интеграции с реальным API: `admin-ui/docs/real-api-integration.md`.

## Навигация (ключевые экраны)

- `Dashboard` — здоровье системы + базовые операционные списки.
- `Liquidity analytics` — triage по снимку сети (bottlenecks, net positions, советы оператору).
- `Trustlines`/`Graph` — drill-down и расследование конкретных рёбер/узлов.

## Fixtures (fixtures-first режим)

Admin UI в режиме моков читает JSON фикстуры из:

- `admin-ui/public/admin-fixtures/v1/…`

Источник истины (каноничные фикстуры):

- `admin-fixtures/v1/datasets/*.json`

Синхронизация и валидация:

- sync: `admin-ui/scripts/sync-fixtures.mjs`
- validate: `admin-ui/scripts/validate-fixtures.mjs`

Типовой цикл разработки фикстур:

1) Сгенерировать каноничные фикстуры:

```powershell
python admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100
```

2) Синхронизировать в Admin UI:

```powershell
npm --prefix admin-ui run sync:fixtures
```

3) Провалидировать:

```powershell
npm --prefix admin-ui run validate:fixtures
```

Важные инварианты данных:

- TrustLine direction: `from → to` = creditor → debtor (risk limit), *не наоборот*.
- Направление долгов обратное: debt = debtor → creditor.
- `policy.daily_limit` в MVP: informational-only (не enforced) до отдельного решения/задачи.

Конвенции представления чисел:

- Денежные значения в фикстурах и API предпочтительно передавать как **decimal string** (UI не должен зависеть от float).

## Типографика и текстовые стили

Нормативный гайд по ролям текста и UI-copy: [typography.md](typography.md)

## Роутинг и query-фильтры

Правило для двухсторонней синхронизации `route.query ↔ refs` (без двойных `load()`/"мелькания"): [docs/route-query-sync.md](docs/route-query-sync.md)

## Роли в UI (важно)

В шапке UI есть переключатель роли (`admin` / `operator` / `auditor`). Это **режим отображения/UX** (хранится в `localStorage` под ключом `admin-ui.role`):

- роль может скрывать/дизейблить некоторые «опасные» действия (например verify/repair в Integrity, freeze/unfreeze),
- **но это не авторизация** и не security boundary.

При работе в `real` режиме права и доступ должны enforce-иться на backend (например через `X-Admin-Token` и серверные проверки), даже если UI что-то «запрещает».

## Спецификации и архив

Рабочие спеки для доработок UI находятся в [specs/README.md](specs/README.md).

---

Примечание: материалы админки были перенесены из исторического пути `docs/ru/admin/*` в домен `docs/ru/admin-ui/*`.
