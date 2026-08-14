# 008 — Исполняемые задачи

Легенда: `[x]` выполнено · `[ ]` в работе · `[P]` приостановлено (ждёт владельца) ·
`[N]` не начато · `[B]` заблокировано (назван блокер).

**Это источник ответа на вопрос «что делать дальше».** Статус здесь важнее любой сводки в чате.
Слайс считается выполненным, когда его строка внесена в tracked
[`evidence-index.md`](evidence-index.md) (счёт файлов, вердикты, ID находок, непроверенное) и
оркестратор перепроверил его P1/P2. Machine-local реестры в `plans/` — рабочие артефакты, не
условие завершения (F-PA-7).

**Baseline волны 1 (исторический):** `ea9cde9161c3f7444495ede13871c901abbba811` (2026-08-11).
**Frozen HEAD волн 2–6:** `7cb5149eb03c902977ac0d740d73a933a54e372e` (2026-08-12); разбиение —
[`manifest.md`](manifest.md). Если HEAD уйдёт вперёд до старта волны — перегенерировать манифест
и перепроверить незакрытые P2 (protocol §0 `plan.md`).

## Фаза 0 — governance

- [x] T800-1 Метод, разбиение на блоки, решения владельца — `spec.md`, `plan.md`, `tasks.md`.
- [x] T800-2 Чистота продуктового кода на заморозке волны 1: `git status --short -- app admin-ui simulator-ui api tests` → пусто.
- [x] T800-3 Регистрация программы в `specs/README.md` — выполнено 2026-08-12.
- [x] T800-4 Аудит плана перед возобновлением — `plan-audit-2026-08-12.md`, A1–A8 закрыты, 11 дефектов F-PA-1…F-PA-11.
- [x] T800-5 Коррекция плана по аудиту (авторизована владельцем 2026-08-12): манифест 710 файлов / 0 пропусков / 0 дублей, evidence-индекс, re-baseline P2, синхронизация статусов, новый frozen HEAD. Детали — Changelog `spec.md`.

## Волна 1 — закрыта 2026-08-11 на `ea9cde9`

- [x] T801-A1a 3/3 файла, 3449 строк, 14 находок (2×P2).
- [x] T801-A1b 26 файлов, 4322 строки, 15 находок (3×P2).
- [x] T801-C1 17 файлов, 3378 строк, 11 находок (2×P2); матрица `openapi ↔ realApi ↔ mockApi` по всем 31 методу.
- [x] T802 Сведение: полнота против `git ls-files` — ноль файлов без вердикта; все 7 P2 перепроверены оркестратором.
- [x] T802-STOP Остановка по требованию владельца.
- [x] T802-R Re-baseline всех P2 на HEAD аудита (`plan-audit-2026-08-12.md` A1): 5×LIVE, 1×FIXED_EXTERNALLY (`C-C1-002` → `84ca396`), 1×UNVERIFIED (`C-A1a-003`); `C-A1a-001` подтверждён исполняемо (exit 0 без флага / exit 1 с флагом). Новая находка `C-C1-012` внесена в `spec.md`.

### Открытые вопросы — на решение владельца перед фазой II

1. `C-A1a-003` — граница run-scope мутирующих interact-действий. Продуктовое решение; runtime-репродьюсер — T803-RT3.
2. `C-C1-001` + `C-C1-012` чинятся **только вместе**: включение `include=transactions` без починки `payload` уронит Zod-разбор снапшота (та же механика, что уже сработала у `C-C1-002`).
3. `B-A1b-002` PG-ветка: подтвердить или снять гипотезу `DataError` — T803-RT4.

## NEEDS-RUNTIME — отдельные задачи с точными селекторами

- [N] T803-RT1 `C-A1b-001`: падающий serialization-тест edge-моделей **без** явного `by_alias=True`. Prereq: нет. Gate: `.\scripts\verify_local.ps1 -TaskSlug rt1_alias -BackendOnly -BackendSelector tests/contract/test_openapi_contract.py` + новый тест.
- [N] T803-RT2 `B-A1b-003`: route-level репродьюсер `find_cycles -> RuntimeError` → ответ обязан отличать отказ от пустоты. Gate: targeted selector по `tests/unit`/`tests/contract` для `/admin/clearing/cycles`.
- [N] T803-RT3 `C-A1a-003`: два рана на разных сценариях, foreign PID, наблюдение DB/read-model эффекта. Prereq: решение владельца о границе. Debug-only прогон помечать явно (AGENTS.md §5).
- [N] T803-RT4 `B-A1b-002` PG: disposable Postgres, `-BackendMarker postgres`, длина `String(64)` + класс ошибки + rollback-путь. Prereq: `TEST_DATABASE_URL` на одноразовую DB.

## Волна 2 — ядро симулятора и плоскость данных simulator-ui

Состав слайсов — по манифесту (правила 8, 9, 11, 12).

- [N] T803-A1c Каркас backend: `app/main.py`, `app/config.py`, `app/db/**`, `app/utils/**` — 29 файлов (~1.6k LOC). Добавлен по F-PA-2: раньше не был назначен никому.
- [N] T803-A2a `app/core/simulator/` real-конвейер — 17 файлов (манифест, правило 8).
- [N] T803-A2b `app/core/simulator/` остальное — 24 файла (правило 9, catch-all).
- [N] T803-B1 `simulator-ui/v2/src/api/**` + `realEventPipeline.ts` + `useSimulatorRealMode.ts` — 9 файлов.
- [N] T803-SUM Сведение, строка в `evidence-index.md`, ручная перепроверка P1/P2.

## Волна 3 — денежное ядро и состояние UI

- [N] T804-A3 Денежное ядро **read-only** — 22 файла (правило 10). Программы 002–006 закрыты; находки идут в `specs/BACKLOG.md` (решение владельца 2026-08-11 о read-only сохраняется).
- [N] T804-B2 Состояние симулятора — 11 файлов (правило 13). Вопросы для проверки: единственность источника состояния после декомпозиции WM; вердикт — только после evidence.
- [N] T804-C2 Граф admin-ui — 24 файла (правило 16).
- [N] T804-SUM Сведение, строка в индексе, перепроверка.

## Волна 4 — представление

- [N] T805-B3 `simulator-ui/v2/src` остальное — 146 файлов (правило 14, catch-all). Для `dev/`, `demo/`, `legacyReference/` — нейтральные вопросы (источник истины? runtime-импорты? ownership?), не предрешённые вердикты: 006 уже доказала использование `src/demo/patches.ts` в real mode.
- [N] T805-C3 `admin-ui/src` остальное — 39 файлов (правило 17, catch-all).
- [N] T805-SUM Сведение, строка в индексе, перепроверка.

## Волна 5 — тесты и E2E (suite-wide)

Формат — матрица «группа / наблюдаемое поведение / tier / owner source / failure paths / риск»;
пофайловый вердикт только для delete/merge, крупных файлов, policy guards, marker-несущих
slow/Postgres тестов и подозрений на no-op (F-PA-5). Уникальная роль волны: conftest и shared
state, таксономия маркеров и anti-vacuum, ownership фикстур, изоляция, сквозные дубли.

- [N] T806-A4 `tests/**` — 185 tracked entries, 179 модулей `test_*.py`; маркеры: 2 slow, 13 postgres.
- [N] T806-B4 Simulator unit/component — 100 файлов (вкл. snapshots).
- [N] T806-C4 Admin unit/component — 34 файла (вкл. `src/test/setup.ts`).
- [N] T806-B5 Simulator Playwright — 16 файлов: e2e specs + 2 конфига (добавлено по F-PA-6). Read-only инвентаризация; browser-запуск только для подтверждения runtime-находки.
- [N] T806-C5 Admin Playwright — 6 файлов: `e2e/`, `e2e-real/`, 2 конфига (F-PA-6).
- [N] T806-SUM Сведение, строка в индексе, перепроверка.

## Волна 6 — сквозная сборка

- [N] T807-D1 Триангуляция контрактов: `openapi.yaml` ↔ backend ↔ simulator-ui ↔ admin-ui.
- [N] T807-D2 Сводный список удаления: reference scan, `git log -- <path>`, safe delete / verify first / keep. Rollback — revertable-коммиты и полный SHA; обязательный tag не требуется (A6).
- [N] T807-D3 Сводка дублей, пересекающих границы блоков.

## Приёмка

- [N] T808 Единый fresh-context adversarial-проход по сведённым реестрам (self-refutation слайсов и ручная проверка оркестратора его не заменяют и не дублируют — F-PA-8).
- [N] T809 Внешнее ревью другой системой по `docs/external-review-runbook.md`. При сбое — `UNVERIFIED`, а не «находок нет».
- [N] T810 Триаж владельца: `fix now` / `в спеку` / `удалить` / `принято как есть`.

## Фаза II — правки

- [B] T811-0 **Блокер:** триаж T810 и решения по трём открытым вопросам волны 1.
- [N] T811-1 Пакет «мёртвый код»: один класс — один revertable коммит.
- [N] T811-2 Пакет «дубли внутри файла и локальные чистки».
- [N] T811-3 Targeted gates после каждого пакета (матрица «tier ↔ тип правки» — `plan.md` §6); milestone `verify_local.ps1` перед merge.
- [N] T812 Перенос итога: подтверждённое → `## Findings` + `evidence-index.md`, узкие правки → `specs/BACKLOG.md`, закрытие программы.
