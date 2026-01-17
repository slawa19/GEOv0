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

- [`docs/ru/03-architecture.md`](../03-architecture.md:1)

## Быстрый старт (локально)

- Рекомендуемый способ запуска на Windows: `scripts/run_local.ps1` (поднимает backend + Admin UI, управляет портами и записывает `admin-ui/.env.local`).

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

Нормативный гайд по ролям текста и UI-copy: [docs/ru/admin/typography.md](typography.md)

## Спецификации и архив

Рабочие спеки для доработок UI находятся в [docs/ru/admin/specs/README.md](specs/README.md).

