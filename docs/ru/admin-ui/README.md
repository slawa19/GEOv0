# GEO v0.1 — Admin UI (RU)

```text
Статус: Stable
Область: admin-ui
Последнее обновление: 2026-10-07
```

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

## Backend и режимы API

Admin UI всегда обращается к backend по HTTP: режим mock, его фикстуры и переключатель режимов удалены 2026-10-07 (программа 032, срез S4). Переменной `VITE_API_MODE` больше нет.

Настройка — env-переменные Vite:

- `VITE_API_BASE_URL=http://127.0.0.1:18000` (дефолт локального runner-а и dev-сервера)
- `VITE_API_BASE_URL=http://127.0.0.1:8000` (дефолт Docker Compose)
- `VITE_ADMIN_TOKEN` — `ADMIN_TOKEN` backend-а (либо localStorage `admin-ui.adminToken`). Dev-сервер без токена использует dev-токен backend-а по умолчанию; production-сборка без токена отказывает в admin-запросах с 401 `ADMIN_TOKEN_MISSING`.

`scripts/run_local.ps1 start` сам поднимает backend, сидирует базу и пишет/обновляет `admin-ui/.env.local`. Без runner-а базу сидируют рецептом сообщества: `python scripts/seed_db.py --source recipe --community riverside-town-50`.

Техническая заметка (EN) по интеграции с реальным API: `admin-ui/docs/real-api-integration.md`.

## Навигация (ключевые экраны)

- `Dashboard` — счётчики участников, строка на эквивалент (линии и суммы внутри эквивалента), предупреждение об удержаниях, последние записи аудита.
- `Integrity` — проверки инвариантов и удержания эквивалентов (отметка и действие «Снять» с причиной).
- Экраны `Liquidity analytics` и `Incidents`, советы оператору и аналитика участника на графе удалены 2026-10-07 (программа 032 S5, решение владельца).
- `Trustlines`/`Graph` — drill-down и расследование конкретных рёбер/узлов.

## Данные

Фикстур в Admin UI нет: `admin-fixtures/`, `admin-ui/public/admin-fixtures/` и скрипты `sync:fixtures` / `validate:fixtures` удалены 2026-10-07 (032 S4). Данные приходят с backend-а; база наполняется рецептом сообщества (`seeds/communities/<id>/`, см. [seeds/README.md](../seeds/README.md)).

Важные инварианты данных:

- TrustLine direction: `from → to` = creditor → debtor (risk limit), *не наоборот*.
- Направление долгов обратное: debt = debtor → creditor.
- `policy.daily_limit` в MVP: informational-only (не enforced) до отдельного решения/задачи.

Конвенции представления чисел:

- Денежные значения в API предпочтительно передавать как **decimal string** (UI не должен зависеть от float).

## Типографика и текстовые стили

Нормативный гайд по ролям текста и UI-copy: [typography.md](typography.md)

## Роутинг и query-фильтры

Правило для двухсторонней синхронизации `route.query ↔ refs` (без двойных `load()`/"мелькания"): [docs/route-query-sync.md](docs/route-query-sync.md)

## Роли в UI

Переключателя роли (`admin` / `operator` / `auditor`) и режима «только чтение» в UI нет: они существовали только в mock-режиме и удалены 2026-10-07 (032 S4). RBAC не реализован (зафиксированное решение программы 022); доступ — это admin-токен (`X-Admin-Token`), его проверяет backend.

## Спецификации и архив

Рабочие спеки для доработок UI находятся в [specs/README.md](specs/README.md).

## Проверка текущей реализации

Phase 4 operator path включает контекстную аналитику, детерминированные советы,
синхронизацию latest-request и keyboard-доступную навигацию графа. Проверяйте его
behavioral unit-тестами и Admin Playwright smoke, а не статусом старой spec.

```powershell
npm --prefix admin-ui run test
npm --prefix admin-ui run build
```

Required repository milestone запускается через `scripts/verify_local.ps1`.
Playwright output по умолчанию находится под `.local-run/playwright/admin/` и не
является fixture.

Каждый Admin e2e идёт против реального засеянного backend-а (mock-режима нет): локальный вход — `scripts/verify_admin_e2e.ps1 -TaskSlug <slug>` (`-Smoke` — только блокирующий smoke). Скрипт создаёт одноразовую базу `geov0_dev_<slug>-<id>`, мигрирует, сидирует `riverside-town-50`, запускает uvicorn и Playwright с `ADMIN_E2E_BACKEND_ORIGIN` / `ADMIN_E2E_TOKEN`; Playwright без этих двух переменных не стартует. Деградации (500/403/401, пусто, integrity warning/critical, медленный ответ) тесты создают через `page.route`. Ручная проверка в браузере: [manual-smoke-real-mode.md](manual-smoke-real-mode.md).

---

Примечание: материалы админки были перенесены из исторического пути `docs/ru/admin/*` в домен `docs/ru/admin-ui/*`.
