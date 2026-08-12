# 008 — Исполняемые задачи

Легенда: `[x]` выполнено · `[ ]` в работе · `[!]` не начато или заблокировано.

**Это источник ответа на вопрос «что делать дальше».** Статус здесь важнее любой сводки в чате.
Слайс считается выполненным, только когда в `plans/review-2026-08-11-three-surfaces/` лежат оба его
файла (`-inventory.md` и `-findings.md`) и оркестратор перепроверил его P1/P2.

Замороженный HEAD программы: `ea9cde9` (2026-08-11).

## Фаза 0 — governance

- [x] T800-1 Метод, разбиение на блоки, решения владельца — `spec.md`, `plan.md`, `tasks.md`.
- [x] T800-2 Проверить чистоту продуктового кода на заморозке: `git status --short -- app admin-ui simulator-ui api tests` → пусто.
- [ ] T800-3 Зарегистрировать программу в `specs/README.md` (реестр + порядок работ). **Отложено:** файл сейчас untracked и редактируется владельцем; строку вставляет владелец при коммите своих спек.

## Волна 1 — периметр API и плоскости данных

- [x] T801-A1a `app/api/v1/simulator.py`, `integrity.py`, `websocket.py` — 3/3 файла, 3449/3449 строк, 14 находок (2×P2). Вердикты: `clean` 2, `keep` 1.
- [x] T801-A1b `app/api/v1/admin.py` + остальные роуты + `deps.py`, `router.py` + `app/schemas/**` — 26 файлов, 4322 строки, 15 находок (3×P2). Вердикты: `keep` 15, `clean` 11.
- [x] T801-C1 `admin-ui/src/api/**`, `stores/`, `types/`, `constants/` — 17 файлов, 3378 строк, 11 находок (2×P2). Вердикты: `keep` 10, `clean` 6, `delete` 1. Матрица `openapi ↔ realApi ↔ mockApi` построена по всем 31 методу.
- [x] T802 Сведение волны 1. Полнота: пофайловое сопоставление с `git ls-files` — **ноль файлов без строки вердикта** в трёх слайсах. Все 7 P2 перепроверены оркестратором по коду; `C-A1b-001` — интроспекцией pydantic. Дерево продуктового кода после волны чисто.
- [x] T802-STOP **Остановка по требованию владельца** достигнута 2026-08-11: волна 1 закрыта, правок ноль, все артефакты в untracked `plans/`. Владелец доводит незаконченные спеки и коммитит не относящиеся к программе изменения.

### Открытые вопросы волны 1 — на решение владельца перед волной 2

1. `C-A1a-003` — граница run-scope для мутирующих interact-действий: чтение scoped, запись глобальна. Требует продуктового решения, не правки.
2. `C-C1-001` + `C-C1-002` чинятся **только вместе**: починка первого в одиночку роняет страницу Graph целиком через Zod.
3. `B-A1b-002`, PG-ветка: гипотеза `DataError` по `String(64)` не воспроизводилась — нужен Postgres-прогон или снятие гипотезы.

## Волна 2 — ядро симулятора и плоскость данных simulator-ui

- [!] T803-A2a `app/core/simulator/` real-конвейер: `real_*`, `sse_broadcast`, `runtime_impl`, `run_lifecycle` (~7k).
- [!] T803-A2b `app/core/simulator/` данные: `storage`, `snapshot_builder`, `artifacts`, `metrics_*`, `scenario_*`, `*_patch*` (~6k).
- [!] T803-B1 `simulator-ui/v2/src/api/**`, `realEventPipeline.ts`, `useSimulatorRealMode.ts` (~5k).
- [!] T803-SUM Сведение и ручная перепроверка волны 2.

## Волна 3 — денежное ядро и состояние UI

- [!] T804-A3 Денежное ядро **read-only**: `payments/`, `clearing/`, `balance/`, `trustlines/`, `participants/`, `invariants.py`, `integrity.py`, `recovery.py`, `auth/`, `admin/` (7k). Находки дописываются в 002/003/004 и BACKLOG; правки запрещены.
- [!] T804-B2 `useSimulatorApp.ts`, `useSceneState`, `useLayoutCoordinator`, `windowManager/**`, `interact/**` (~9k).
- [!] T804-C2 `admin-ui` граф: `pages/graph/**`, `useGraphVisualization.ts`, `useGraphAnalytics.ts`, `advice/` (~8.5k).
- [!] T804-SUM Сведение и ручная перепроверка волны 3.

## Волна 4 — представление

- [!] T805-B3 `simulator-ui/v2/src`: `components/**`, `ui-kit/**`, `render/**`, `layout/**`, `utils/**`, `dev/`, `demo/`, `legacyReference/` (~14k).
- [!] T805-C3 `admin-ui/src`: остальные `pages/**`, `i18n/`, `content/`, `ui/`, `layout/`, `router/`, `utils/` (~8k).
- [!] T805-SUM Сведение и ручная перепроверка волны 4.

## Волна 5 — тесты

- [!] T806-A4 `tests/**` — 165 файлов: гарды, проходящие вхолостую, маркерные ловушки, тесты тонкой проводки (§11 AGENTS.md).
- [!] T806-B4 `simulator-ui/v2/src/**/*.test.ts` (25.4k), включая `SimulatorAppRoot.interact.test.ts` (4201).
- [!] T806-C4 `admin-ui/src/**/*.test.ts` (5.8k).
- [!] T806-SUM Сведение и ручная перепроверка волны 5.

## Волна 6 — сквозная сборка

- [!] T807-D1 Триангуляция контрактов: `openapi.yaml` ↔ backend ↔ simulator-ui ↔ admin-ui.
- [!] T807-D2 Сводный список удаления: reference scan, `git log -- <path>`, назначение safe delete / verify first / keep, baseline-тег.
- [!] T807-D3 Сводка дублей, пересекающих границы блоков.

## Приёмка

- [!] T808 Adversarial-проход отдельным агентом по сведённым реестрам трёх поверхностей: пропущенное, братья багов, ложные находки.
- [!] T809 Внешнее ревью Codex по поверхностям (`docs/external-review-runbook.md` §3). При сбое — состояние `UNVERIFIED`, а не «находок нет».
- [!] T810 Триаж владельца: `fix now` / `в спеку` / `удалить` / `принято как есть`.

## Фаза II — правки

- [!] T811-1 Пакет «мёртвый код»: удаления после reference scan, один класс — один revertable коммит.
- [!] T811-2 Пакет «дубли внутри файла и локальные чистки».
- [!] T811-3 Targeted gates после каждого пакета; milestone `verify_local.ps1` перед merge.
- [!] T812 Перенос итога: подтверждённые находки → `## Findings` в `spec.md`, узкие правки → `specs/BACKLOG.md`, закрытие программы.
