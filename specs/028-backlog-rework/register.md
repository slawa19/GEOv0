# 028 — реестр пунктов BACKLOG

- **Date:** 2026-10-04, против `main` на `84eb320e`; `specs/BACKLOG.md` — 1722 строки.
- **Что это:** решение по **каждому** пункту `specs/BACKLOG.md`, сгруппированное по его разделам. Спека: [`spec.md`](spec.md).
- **Источник:** триаж четырёх агентов (только чтение, `.local-run/backlog-triage/triage.md`, не коммитится) — это **evidence, не истина** (AGENTS.md §1). Каждый пункт с решением `DO-028`, каждое `CLOSE` и каждая поправка триажа перепроверены автором спеки по коду на `84eb320e`; такие строки помечены «да». Строки «триаж» взяты из триажа после сверки якоря с текстом BACKLOG, код за ними не открывался заново.
- **Колонки:** № · якорь (строка BACKLOG и заголовок/пункт) · суть · статус (LIVE — подтверждено и не исправлено, FIXED — исправлено, OBSOLETE — предмета больше нет, OWNER — нужен выбор владельца, ACCEPTED — терминальное решение владельца, UNCLEAR — не установлено) · evidence `path:line` или коммит · HARM (money / user-visible / ops / dev / none) · COST (S ≤ 30 строк, M ≤ 150, L больше или новая сущность) · решение · обоснование · проверено.
- **Решения:** `DO-028` — делает эта программа (стадия в скобках); `LATER` — живо, но не сейчас, получатель назван; `CLOSE` — снять из BACKLOG с датированной записью (evidence обязателен); `DROP` — живо, но не чинится: наблюдаемой потери нет (§19.2 п. 1), переносится в «Принято и остаётся как есть»; `OWNER` — продуктовый вопрос (раздел «Вопросы владельцу» спеки); `ACCEPTED` — решение владельца, терминально (§17), не переоткрывается.

## A. «Требуют продуктового решения, не кода» (стр. 15–26)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 19, Admin Events | Экрана событий и `GET /admin/events` нет | OWNER | `app/api/v1/admin.py` (маршрута нет), `GET /integrity/audit-log` без фильтров | user-visible | L | OWNER | Состав экрана — продукт (вопрос В-4) | триаж |
| 2 | 20, Admin Transactions/Clearing | Списков для админа нет, `GET /payments` привязан к запрашивающему | OWNER | `app/core/payments/service.py:~2663-2706` | user-visible | M | OWNER | Показывать ли админу чужие платежи — продукт (В-4) | триаж |
| 3 | 21, Liquidity Phase 2 | Осознанно вне MVP | ACCEPTED | запись | none | L | ACCEPTED | Терминальное решение | триаж |
| 4 | 22, `audit.drift` | Событие уходит в `ignored('unknown')` | OWNER | `normalizeSimulatorEvent.ts:568` | none | S | OWNER | Решение по наблюдаемости, потери нет (В-8) | триаж |
| 5 | 23, `/simulator/events/poll` | Закрыто 011 | FIXED | запись зачёркнута, `F-011-7` | none | — | CLOSE | Закрыто 2026-08-23 | да |
| 6 | 24, `/ws` и `event_bus` | Маршрут без потребителя; блокер F-005-1 снят | OWNER | `app/api/v1/websocket.py:19-25` | ops | M | OWNER | keep/deprecate — продукт (В-6) | триаж |
| 7 | 25, `docs/ru/pwa/` | Документ вне канона | OWNER | `docs/ru/pwa/specs/` | dev | S | OWNER | Мёртвый или отложенный — решает владелец (В-6) | триаж |
| 8 | 26, npm-уязвимости | Аудит закрыт срезом | FIXED | PR #103, `ef318c0d` | ops | — | CLOSE | `npm audit` 0 в обоих UI по записи среза | да |
| 9 | 26, пин Node 22.12 | `eslint-visitor-keys@5.0.1` требует ≥22.13, CI и `.nvmrc` — 22.12.0 | LIVE | `.github/workflows/quality.yml:104,690,779,810`; `admin-ui/.nvmrc`; `admin-ui/package-lock.json:1756`, `simulator-ui/v2/package-lock.json:1366`; `admin-ui/package.json:7` | ops | S | DO-028 (S2) | CI запускает зависимость вне её заявленного движка; правка — 6 строк | да |
| 10 | 26, ESLint 9 / flat config | `overrides` держит typescript-eslint 8 под конфигом 13 | LIVE | `admin-ui/package.json` (`overrides`) | dev | L | OWNER | Выбор момента обновления тулчейна (В-7) | триаж |
| 11 | 26, Admin `--sequence.shuffle` | `getComputedStyle is not a function` при случайном порядке | UNCLEAR | запись (3/3 на Vitest 2) | dev | M | LATER | Обычный порядок зелёный; получатель — владелец тестов Admin UI | триаж |
| 12 | 26, холодный таймаут контрактного теста Admin | ≈4,6 с при лимите 5 с | UNCLEAR | запись | ops | S | LATER | Только при повторе в `required-ui` (мера записана) | триаж |

## B. Класс 2 `T2508.1` 025 (стр. 28–31)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 13 | 30, четыре slow-теста красные | Ассерт ждал `True` | FIXED | `tests/integration/test_p019_t1908_lock_removal_experiments_postgres.py:331` ждёт `None`; адаптация `b8e49da0` (027 `T2704`) | dev | — | CLOSE | Предмет исправлен; задача чистки один раз прогоняет модуль `-IncludeExpensive` до снятия строки | да |
| 14 | 31, флейк `locks_off-inject_inject` | `SerializationError` 3/4 | OBSOLETE (вероятно) | `app/config.py:85-107` (только READ COMMITTED); SSI-тесты сняты `b8e49da0` | dev | S | CLOSE | `40001` под RC не возникает; тот же прогон чистки подтверждает | да |

## C–E. Измеритель 025, helper 020, закрывающее ревью 024 (стр. 33–47)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 15 | 35, пределы `scripts/test_asset/` | Справочный инструмент недосчитывает | ACCEPTED | решение §19.4 2026-10-03 | dev | — | ACCEPTED | Терминально; число не доказательство | триаж |
| 16 | 39, `single_dfs_service_class()` | Осиротевший helper | LIVE | `scripts/p020_experimental_detectors.py:561` | dev | S | LATER | Снимается вместе с решением о модуле 020 (стр. 239) | триаж |
| 17 | 45, R-024-6 `/admin/liquidity/summary` без фильтра | Итоги и нетто-позиции складывают разные эквиваленты | LIVE | `app/api/v1/admin.py:772-860`; UI уже прячет денежные итоги и нетто без эквивалента — `admin-ui/src/pages/LiquidityPage.vue:155`, `:505` | user-visible (только внешний клиент API) | M | OWNER | **Поправка триажа (DO → OWNER):** ни один экран репозитория эту сумму не показывает; починка меняет контракт. Вместе со стр. 1197 — вопрос В-2 | да |
| 18 | 46, `verification_passed = null` на HTTP | Контракт не утверждён тестом | LIVE | `app/api/v1/integrity.py:389`; `tests/integration/test_integrity_endpoints.py:54` | dev | S | LATER | **Поправка триажа (DO → LATER):** страховка от гипотетической регрессии, потери нет; при следующей правке integrity API | да |
| 19 | 47, флейк `test_p015_step5b_criterion_b[intent_flow]` | Маршрут по настенным 500 мс упал на CI | LIVE | `app/config.py:198`; переопределения в `tests/unit/test_p015_step5b_criterion_b.py` нет; тест в обязательном тире | ops | S | DO-028 (S2) | Ложно-красный обязательный гейт (CI PR #100); AGENTS §11 запрещает зависимость от реального времени | да |

## F. Закрывающее ревью 026 (стр. 49–59)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 20 | 53, multipath через pending-пару в сервисе | Пробел evidence | LIVE | `tests/integration/test_p026_s3_pending_transit_postgres.py:157` | dev | M | DROP | Нет наблюдаемой потери: ядро ограничено `pending_pair_capacity` (`capacity.py:37`), путь через `Book` покрыт | триаж |
| 21 | 54, pending-линия и дрейф/импорт | Нет контртеста | LIVE | `trust_drift_engine.py:379-380` фильтр стоит | dev | S | DROP | Нет потери: фильтр в коде, тест лишь страховка | триаж |
| 22 | 55, commit-unknown после автозакрытия | Не исполнен | LIVE | `test_p026_s4_tick_close_publication_postgres.py:109` | dev | M | DROP | Нет потери: пробел evidence, не дефект | триаж |
| 23 | 56, знаковое `available` на настоящем долге | Долг вставлен прямо | LIVE | `tests/unit/test_p026_s2_signed_available_in_simulator.py:43` | dev | S | DROP | Нет потери: API и Admin покрыты настоящим путём | триаж |
| 24 | 57, PATCH против платежа/клиринга | Нет отдельного расписания | LIVE | `trustlines/service.py:549-555` (`FOR UPDATE`) | dev | M | DROP | Нет потери: гонку закрывает блокировка строки 027 | триаж |
| 25 | 58, клиринг-завершение при снятом согласии | Нет совместного отрицательного случая | LIVE | — | dev | S | DROP | Нет потери: положительный путь и проверки согласия есть | триаж |
| 26 | 59, PATCH замороженной линии | Протокол требует `active`, код отказывает только `closed` | LIVE | `app/core/trustlines/service.py:570`; `docs/ru/02-protocol-spec.md` ~:355 | user-visible | S | OWNER | Часть вопроса «что значит заморозка» (В-1) | да |

## G. Дубли сущностей 015–026 (стр. 61–73)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 27 | 65, G1 итоговые столбцы журнала | Не сравниваются ни одним путём | LIVE | `app/db/journal_tables.py:175`, `:214-218` | dev | M | LATER | Решение консультации: в ближайшей авторизованной правке журнала, одной миграцией, §15 | триаж |
| 28 | 66, G2 перекрытые индексы | Логическое перекрытие | LIVE | `journal_tables.py:231`, `:339` | none | S | DROP | Нет потери; без замера планов не строить | триаж |
| 29 | 67, G3 DDL журнала дважды | `create_all` и миграция 029 | LIVE | `tests/conftest.py`; паритетный тест | dev | M | LATER | Уходит вместе с путём `create_all` (тестовый актив) | триаж |
| 30 | 68, G4 копии настроек в `RealTick` | Тестовая половина сделана | LIVE | `app/core/simulator/tick.py:134-139` | dev | S | DROP | Нет потери: продовые копии читают настройки при создании, поведение верно | триаж |
| 31 | 69, G5 одно множество видов дважды | Сейчас совпадают | LIVE | `app/core/ledger/book.py:629`; `reconciliation.py:177` | dev | S | LATER | Одна константа при правке политики видов, §15 | триаж |
| 32 | 70, G6 маркеры «не проверено» дважды | Расхождения сейчас нет | LIVE | `app/api/v1/integrity.py:40`; `app/core/integrity.py:90-114` | dev | S | LATER | При следующем изменении этого вывода | триаж |
| 33 | 71, G7 мёртвый/повторённый код | Пин v3 снят | LIVE | `app/core/clearing/flow_planner.py:149`; `runner.py:138` | dev | S | LATER | Причина «не трогать» исчезла; по одной чистке при касании файла | триаж |
| 34 | 72, G8 дубли UI | — | LIVE | `EdgeDetailPopup.vue:157`; `graphPageHelpers.ts:30` | dev | S | DROP | Нет потери | триаж |
| 35 | 73, G9 оставить | `принято` | ACCEPTED | запись | none | — | ACCEPTED | Терминально | триаж |

## H–K. Адверсариал и §15-ревью стадий 026 (стр. 75–98)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 36 | 77, обнаружение закрытия через патчи | Закрытая линия висит в сценарии рана и метрике `active_trustlines` | LIVE | `sse_broadcast.py:755`; `real_payment_planner.py` ~:120 | user-visible | L | LATER | Новый механизм (топология из БД после commit); §19.4 применён в 026. Получатель — владелец рана симулятора | триаж |
| 37 | 79, `bump_topology_epoch` — мера | Две формы, одна при одном ране | LIVE | `models.py:267`; `simulator.py:890` | user-visible | L | LATER | Тот же механизм, что № 36. Вторая форма достижима при ≥2 ранах — см. № 54 (resume обходит лимит) | триаж |
| 38 | 87, checksum без `close_requested_at` | Новая форма меняет сравнение чекпойнтов | LIVE | `app/core/integrity.py:35`, `:61` | none | M | DROP | Нет потери: запрос и завершение закрытия пишутся в аудит | триаж |
| 39 | 88, Interact `TRUSTLINE_CLOSED` вложенным конвертом | 409 приходит `{error:{…}}`, OpenAPI объявляет плоский `SimulatorActionError` | LIVE | перевод только `TRUSTLINE_CLOSE_REQUESTED` — `app/api/v1/simulator.py:1456-1463`; отказ сервиса — `app/core/trustlines/service.py:570-574`, `:683-687`; схема 409 — `api/openapi.yaml:3254`, `:3307` | user-visible | S | DO-028 (S1) | Ответ нарушает собственный канон; под READ COMMITTED `FOR UPDATE` сервиса перечитывает строку после ожидания, путь достижим | да |
| 40 | 92, `total_available` падает при лимите ниже долга | Сумма знаковых `available` | LIVE | `admin.py` (`available_expr`); `LiquidityPage.vue:153` | user-visible | S | OWNER | Поле задокументировано ровно как эта сумма; смена — контракт (В-2) | триаж |
| 41 | 93, подсказка bottleneck | Закрыто 2026-10-02 | FIXED | `admin-ui/src/i18n/en.ts:100` | none | — | CLOSE | Текст исправлен | да |
| 42 | 94, стенд инжекта и повтор `40001` | Повтор SSI | OBSOLETE | `app/config.py:85-107` (RC) | dev | — | CLOSE | Под RC `40001` нет; тест остаётся поведенческим | да |
| 43 | 98, метаданные `debt_growth` | Сделано `T2603.1` | FIXED | `app/core/payments/service.py:1995` | none | — | CLOSE | Ключ пишется | да |

## L. Класс 2 `T2415.2` 024 (стр. 100–112)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 44 | 104, классификатор `NO_ROUTE`/`INSUFFICIENT_CAPACITY` | Не видит хоп зачёта | LIVE | `app/api/v1/simulator.py:1604-1651` | user-visible | S | LATER | Меняется только текст отказа, код 409 тот же; при следующей правке классификации | триаж |
| 45 | 105, Simulator UI «доступно» | Остаток прямой линии | LIVE | `useInteractMode.ts:280-285` | user-visible | M | LATER | Итерация Simulator UI (022, не авторизована) | триаж |
| 46 | 107, §6.5.2 псевдокод | Политика не тому узлу | LIVE | `docs/ru/02-protocol-spec.md` §6.5.2 | dev | S | LATER | Зарезервированный сетевой режим; при его правке | триаж |
| 47 | 108, решение о `FOR SHARE` | Закрыто, 027 заменила на `FOR UPDATE` | FIXED | запись зачёркнута; `money_boundary.py:119` | none | — | CLOSE | Закрыто | да |
| 48 | 109, политика сценария мимо валидатора | `can_be_intermediate: "false"` читается как разрешение | LIVE | `real_scenario_seeder.py:257-259` копирует как есть; `capacity.py:53` `bool(...)`; валидатор `app/utils/validation.py:550-562` | money (кто вправе быть посредником) | S | DO-028 (S1) | Тот же класс, что исправленный `"0.0"` (024 `T2415.3`, решение владельца B). Сценарные фикстуры несут только `bool` (2817 линий), правка их не ломает | да |
| 49 | 110, дрейф доверия против платежа: взаимоблокировка | После 027 — `FOR UPDATE` по одной линии не в порядке `id` | LIVE | `app/core/simulator/trust_drift_engine.py:380`, `:566`; закрывающее ревью 027 ссылается на эту строку как на живую (`BACKLOG.md:1719`) | ops | M | LATER | **Поправка триажа (CLOSE → LIVE):** механизм сменился, порядок захвата — нет. Деньги целы: `40P01` пропускает тик дрейфа. Получатель — владелец тика | да |
| 50 | 111, PATCH/закрытие → 500 на `40001` | SSI | OBSOLETE | `trustlines/service.py:549-555`, RC | none | — | CLOSE | Одна строка под `FOR UPDATE`, `40001` под RC не возникает | да |
| 51 | 112, `/payments/max-flow` завышает оценку | Перераспределение потока игнорирует политику | LIVE | `router.py` ~:592 | user-visible | L | DROP | Нет потери: оценка, деньги идут через ядро, которое перепроверяет | триаж |

## M–O. Флейк p017, адверсариал Ш6, матрица SQLSTATE (стр. 114–131)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 52 | 116, флейк `test_disconnecting_reports_what_it_terminated` | Причина не установлена | UNCLEAR | `test_p017_t1701_schema_provisioning_postgres.py:403-408` | dev | S | LATER | Сначала воспроизвести; одно падение за всё время | триаж |
| 53 | 122, restart не запускает писатель `events.ndjson` | Перезапущенный ран теряет события в артефакте | LIVE | `start_events_writer` только в `create` — `app/core/simulator/run_lifecycle.py:306`; `restart` `:452-512` его не зовёт; `stop` гасит — `:428`; вызов идемпотентен — `artifacts.py:231-233` | user-visible | S | DO-028 (S1) | Артефакт `events.ndjson` перезапущенного рана неполон без сигнала | да |
| 54 | 123, `resume` из `error` без лимитов | Два `running` рана у владельца | LIVE | `run_lifecycle.py:337-353` не вызывает `_enforce_active_run_limit_locked` (`:100`) и проверку владельца (ср. `restart` `:455-470`) | user-visible | S | DO-028 (S1) | **Поправка триажа (LATER → DO):** обходит `SIMULATOR_MAX_ACTIVE_RUNS=1` по умолчанию и тем делает достижимой вторую форму № 37 | да |
| 55 | 124, отмена `resume`/`restart` во время heartbeat | Ран без heartbeat | UNCLEAR | расписание не прослежено | ops | M | LATER | Не подтверждено; сначала стенд | триаж |
| 56 | 125, `flush_pending_storage` `or -1` | Тик 0 пропускается | LIVE | `app/core/simulator/tick.py:1618`, `:1622` | dev | S | LATER | Узкое окно (неудачный хвост + restart), метрика не деньги | триаж |
| 57 | 131, матрица SQLSTATE прячет 14 ответов | Маска `~` | LIVE | `tests/unit/test_p024_t2415_sqlstate_policy_matrix.py:128-129` | dev | S | LATER | Слабость проверки; при следующем касании файла | триаж |

## P. Класс 2 Ш4 024 (стр. 133–140)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 58 | 137, `.env` перебивает окружение через псевдоним | Приоритет псевдонима, не источника | LIVE | `app/config.py:283` | ops | S | DROP | Нет потери: обход — одно имя в одном месте; деньги не зависят | триаж |
| 59 | 138, `NotFoundException` несёт `E001` | Код «нет маршрута» на 404 | LIVE | `app/utils/exceptions.py:59` | user-visible | M | LATER | Техническое решение принято ревью (`E009`); меняет код на проводе — отдельный срез `Contract: yes` с §15. HTTP 404 уже различает | триаж |
| 60 | 139, логирование не сконфигурировано | WARNING- теряется, `exc_info` без редакции | LIVE | `app/main.py:182-196` | ops | M | LATER | Срез наблюдаемости §12, отдельной спекой | триаж |
| 61 | 140, `/admin/config` mutable без эффекта | UI правит 12 ключей впустую | LIVE | `admin.py:319-342,457-458`; `ConfigPage.vue:163` | user-visible | M | LATER | Нужно согласованно с Admin UI; получатель — 022 | триаж |

## Q–V. Класс 2 Ш3 024, 023, 021 (стр. 142–173)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 62 | 146, непересекающиеся пары конфликтуют | Взято в 027 | FIXED | программа 027 закрыта 2026-10-04 | none | — | CLOSE | Закрыто 027 (N=10: 200/200) | да |
| 63 | 147, перерасход бюджета Ш3 | Учёт | ACCEPTED | запись | dev | — | CLOSE | Действия не требует, 024 закрыта | триаж |
| 64 | 148, коммит без строки аудита при не-DB сбое | `except` пишет событие и коммитит | LIVE | `payments/service.py:~2004-2010`; клиринг ~:2360 | ops | S | LATER | Денежный эффект верен; путь — только инъекция сбоя, реального входа не найдено (§19.2 п. 6) | триаж |
| 65 | 152, отмена при освобождении аренды теряет отчёт | Структурно | LIVE | `app/core/clearing/runner.py:66-69` | ops | M | LATER | Долг закоммичен верно; страдает отчёт | триаж |
| 66 | 153, докстринг `flow_planner` «NOT WIRED» | Ложная проза: модуль — единственный планировщик клиринга | LIVE | `app/core/clearing/flow_planner.py:1-4`; импорт `app/core/clearing/runner.py:61`; пин хеша снят (`44954ebb`) | dev | S | DO-028 (S4) | Неверная посылка в коде денежного пути (§15 «неверная посылка опаснее отсутствующей»); правка текста | да |
| 67 | 157, провал коммита хвоста тика | Исправлено 024 `T2416.3` | FIXED | `app/core/simulator/tick.py:165-172` (`on_commit`) | none | — | CLOSE | Пометка — только после коммита | да |
| 68 | 163, объём клиринга при таймауте | Закрыто PR #75 | FIXED | запись зачёркнута | none | — | CLOSE | Закрыто | да |
| 69 | 164, холодный старт процесса | Не проверен | UNCLEAR | `evidence/2026-09-28-d-cold-spawn.txt` | ops | M | LATER | Только контейнерный путь; при касании запуска процесса хаба | триаж |
| 70 | 168, изоляция периодического клиринга по псевдоключу | Форма (б) — обнаружение | LIVE | `real_scenario_seeder.py:30`; `app/core/clearing/runner.py:454-468` | none | M | DROP | Нет потери: периодический клиринг выключен по умолчанию, гарантия — раздельные базы. Опора проверки на `simulator_runs` чинится № 187 | да |
| 71 | 172, комментарии на `real_tick_*` | Перенаправлены 024 `T2411` | FIXED | `tick.py:3` исторический; `UNFINISHED.md:101` датированный | dev | — | CLOSE | Остаток намеренный | триаж |
| 72 | 173, тайминг стенда `spawn` | Закрыто PR #77 | FIXED | запись зачёркнута | none | — | CLOSE | Закрыто | да |

## W–X. Схемная гигиена и артефакты (стр. 175–212)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 73 | 175, `trustlines.policy` nullable | Никто не пишет `null` | LIVE | `app/db/models/trustline.py:21` | none | M | DROP | Нет потери; `nullable=False` — новая миграция без выигрыша | триаж |
| 74 | 196, content type выгрузки артефакта | `events.ndjson` как `text/plain` | LIVE | `app/api/v1/simulator.py:2940` `FileResponse(path)` | user-visible | S | LATER | Потребителя, которого это ломает, нет; правка меняет заголовок и канон — вместе с ближайшей правкой артефактов | триаж |

## Y. Ревью ядра 2026-09-27 — остаток вне 024 (стр. 214–239)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 75 | 223, П1 ёмкость против встречного долга | Решено 2026-09-29 | FIXED | 024 `T2415.3` | none | — | CLOSE | Развилка закрыта решением владельца и кодом | да |
| 76 | 224, П2 настоящие участники в ране | Решено 2026-10-03 «оставить» | ACCEPTED | запись | none | — | ACCEPTED | Терминально; строка переносится в раздел принятого | да |
| 77 | 225, П3 baseline существующих эквивалентов | Решено «пересоздать демо», не исполнено | OWNER | запись; `scripts/take_reconciliation_baseline.py` | ops | S | OWNER | Исполнение — оператор по подтверждению целевой базы (В-9) | триаж |
| 78 | 226, инверсия `core → schemas` | `принято` | ACCEPTED | — | none | — | ACCEPTED | Терминально | триаж |
| 79 | 227, `chk_transaction_state` | `принято` | ACCEPTED | — | none | — | ACCEPTED | Терминально | триаж |
| 80 | 228, dialect-ветки миграций | `принято` | ACCEPTED | — | none | — | ACCEPTED | Применённые миграции неизменяемы | триаж |
| 81 | 229, `naming_convention` индексов | ORM `ix_*` против `idx_*` | LIVE | `test_p018_b_schema_parity_postgres.py:47` | dev | S | DROP | Нет потери; существующие не трогать (см. № 164) | триаж |
| 82 | 230, повтор `tx_id` после 4xx → 200 `ABORTED` | Закреплено тестом | OWNER | `test_p015_t1523_replay_after_a_hold_or_an_abort.py:288` | user-visible | — | OWNER | Вопрос протокола, прил. E (В-5) | триаж |
| 83 | 231, `max_hop_usage: "NaN"` → 500 | `Decimal('NaN') < 0` бросает `InvalidOperation`; `"Infinity"` и `inf` принимаются | LIVE | `app/utils/validation.py:614`; исполнено 2026-10-04: `'NaN'`, `nan`, `'sNaN'` → `InvalidOperation`; `'Infinity'`, `inf` → приняты | user-visible | S | DO-028 (S1) | Достижимый 500 на подписанном вводе; **дополнение к триажу:** бесконечность проходит валидатор и хранится | да |
| 84 | 232, ротация refresh-токена не атомарна | Отзыв в памяти процесса без Redis | LIVE | `app/core/auth/service.py:108-129` | user-visible | M | LATER | Периметр безопасности, §15; демо-инсталляция одного процесса | триаж |
| 85 | 233, Redis недоступен → plain-text 500 | `rate_limit` без защиты | LIVE | `app/api/deps.py:~69-73`; `REDIS_ENABLED=False` по умолчанию | ops | S | LATER | Только при включённом Redis | триаж |
| 86 | 234, `/integrity/verify` и `/audit-log` любому участнику | Политика доступа | OWNER | `app/api/v1/integrity.py:234-240` | user-visible | S | OWNER | Выбор политики доступа (В-5) | триаж |
| 87 | 235, у `integrity_checkpoints` нет TTL | Рост со временем | LIVE | удалений в `app` нет | ops | M | LATER | §12; рост только во времени, замера нет | триаж |
| 88 | 236, инициатор `CLEARING` — первый должник | Протокол §7.3 — хаб | OWNER | `clearing/service.py:1906-1915` | none | M | OWNER | Вопрос протокола (В-5) | триаж |
| 89 | 237, утечка SSE-подписки | INFERENCE | UNCLEAR | `sse_broadcast.py` | ops | M | LATER | «Измерить, не чинить вслепую» | триаж |
| 90 | 238, пять валидаторов naive→UTC | Закрыто `T2411` | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 91 | 239, обязательный гейт тестирует детектор 020 | Условие переноса `_CONSENTS` выполнено | LIVE | `tests/integration/test_p020_experimental_detectors_postgres.py`, `tests/unit/test_p020_rank_bound_diagnostic.py` | dev | M | LATER | Удаление — `T2504.2` 025 (не авторизовано); вместе с № 16 | да |

## Z–AD. Остатки 021, флейк p019, стадия 0 и Ш2 024, super-smoke (стр. 241–283)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 92 | 245, повторный засев копит закрытые линии | +1 линия и аудит на ран | LIVE | `app/core/trustlines/service.py:783` | ops | S | LATER | Поведение закреплено тестом намеренно; при следующем касании засева | триаж |
| 93 | 246, OpenAPI не описывает `checkpoint_scope`, `initial_status` | Канон отстаёт от метаданных аудита | LIVE | `grep -c` по `api/openapi.yaml` = 0; пишет `app/core/trustlines/service.py:117`, `:811` | dev | S | DO-028 (S4) | Потребитель принимает общую сумму транзакции за переход одной операции; канон — авторитет №1 | да |
| 94 | 247, гард «ровно одно присваивание» | Принимает дубль | LIVE | `test_p021_simulator_writes_trust_lines_only_through_the_service.py:48-49` | dev | S | LATER | При правке гардов | триаж |
| 95 | 252, флейк p019 staged_refusal | Закрыто `614fad5`, `973f4bc` | FIXED | запись | dev | — | CLOSE | Закрыто 2026-09-28 | да |
| 96 | 258, соседний characterization с тем же узором | Падений в CI не было | LIVE | `test_p019_refusal_classes_characterization_postgres.py:346,356` | dev | S | LATER | Нет наблюдения; при первом падении — правка по образцу `614fad5` | триаж |
| 97 | 264, TOCTOU в сидере | `принято, §19.2 п. 6` | ACCEPTED | — | none | — | ACCEPTED | Терминально | триаж |
| 98 | 265, ран адоптирует участников чужого рана | Ключей рана нет | LIVE | `inject_executor.py` | none | M | DROP | Нет потери: решение П2 2026-10-03 — участники симулятора полноценные | триаж |
| 99 | 266, статус участника после подключения WS | Не перепроверяется | LIVE | `app/api/v1/websocket.py:35-41` | none | S | DROP | Нет потери: у `/ws` нет потребителя (№ 6) | триаж |
| 100 | 274, mock Admin отказывает в удалении из-за чекпойнтов | — | OBSOLETE | `admin-ui/src/api/mockApi.ts:384` всегда `integrity_checkpoints: 0`, условие `:1416` не срабатывает | none | — | CLOSE | Расхождения с бэкендом нет | да |
| 101 | 278, поправка механизма super-smoke | Устаревшие стенды part2/part3 | UNCLEAR | `external_connection_bind_unsupported` в `app` больше нет (grep пуст); part2 строит сессию с `bind=` — `tests/integration/test_simulator_super_smoke.py:825` | dev | M | DO-028 (S2) | Входит в № 188: первый шаг — один прогон после 027, затем правка стендов | да |
| 102 | 282, манифест тестов 021 | Закрыто `T2109` | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 103 | 283, «выживающие контракты» 021 п. 3 | Закрыто `1d8a2cd1` | FIXED | запись | none | — | CLOSE | Закрыто | триаж |

## AE–AF. «Требуют отдельной спеки» и «Узкие правки» (стр. 285–309)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 104 | 289, launcher runtime helpers | 15 дублей, 5 идентичных | LIVE | `scripts/run_local.ps1`, `run_full_stack.ps1`, `run_real_simulator.ps1` | dev | L | DROP | Нет потери: новый межскриптовый модуль; безопасность lifecycle покрыта тиром инструментов | триаж |
| 105 | 295, trustline timestamps `[x]` | — | FIXED | `app/schemas/trustline.py:22` | none | — | CLOSE | Закрыто 2026-08-11 | да |
| 106 | 296, TODO-ESC `[x]` | — | FIXED | `c3db303` | none | — | CLOSE | Закрыто | да |
| 107 | 297, M20 `??` `[x]` | — | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 108 | 298, histogram precision fallback | `?? 2` остался только в неиспользуемом файле | LIVE | `admin-ui/src/pages/graph/tabs/BalanceTab.vue:71-72`; импортёров нет (`grep -rn BalanceTab admin-ui/src`); в Drawer fallback снят (`GraphAnalyticsDrawer.vue:202` — комментарий) | dev | S | DO-028 (S4) | Удалить мёртвый файл — последний носитель класса M20; перед удалением проверить динамические импорты (§14) | да |
| 109 | 299, participant timestamps без UTC | Причина — SQLite | OBSOLETE | `app/db/models/participant.py:17-18` `DateTime(timezone=True)`; SQLite удалён 017 | none | — | CLOSE | Наивных значений больше нет | да |
| 110 | 300, bottleneck float/decimal `[x]` | — | FIXED | `02feee7` | none | — | CLOSE | Закрыто | да |
| 111 | 301, непроверенные касты `[x]` | — | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 112 | 302, дублирование политики в движке `[x]` | — | OBSOLETE | `app/core/payments/engine.py` удалён | none | — | CLOSE | Закрыто, файла нет | да |
| 113 | 303, мёртвые экспорты `[x]` | — | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 114 | 304, `tmp_*` скрипты `[x]` | — | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 115 | 305, trust-drift мутация до коммита `[x]` | — | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 116 | 306, 53 теста лаунчера `[x]` | — | FIXED | запись | none | — | CLOSE | Закрыто | триаж |
| 117 | 308, `.snap` / `.gitattributes` `[x]` | — | FIXED | `.gitattributes` | none | — | CLOSE | Закрыто 2026-09-20 | триаж |
| 118 | 309, subprocess декодирует по локали | `text=True` без `encoding=` | LIVE | AST-скан 2026-10-04 — **16 мест**: `tests/migrated_schema.py:171`, `tests/contract/test_p011_responses_conform_to_the_canon.py:1502`, `tests/integration/test_p015_step5c_hold_races_postgres.py:706`, `…/test_p017_t1702_mode_b_fixture_postgres.py:171`, `…/test_p018_b_migration_029_postgres.py:41`, `…/test_p019_migration_030_postgres.py:45`, `…/test_p019_migration_031_postgres.py:49`, `tests/unit/test_alembic_postgres_only.py:30`, `…/test_deployment_config.py:160`, `…/test_p015_b4_entries_and_money.py:518`, `…/test_p020_experimental_detectors_are_not_imported_by_production.py:59`, `tooling-tests/portable/test_p017_s1_demo_fixture_generator_needs_no_database.py:63`, `tooling-tests/powershell/test_the_tier_refuses_a_database_that_is_not_postgres.py:45`, `:176`, `scripts/measure_p021_trust_line_batches.py:637`, `scripts/test_asset/analyze.py:126` | ops | S | DO-028 (S2) | **Поправка триажа (9–10 мест → 16):** класс уже выстрелил на ru-RU (`aa2353b`), два места — в разделе powershell, т. е. ровно тот ребёнок, что падал; CI en-US этого не видит | да |

## AG–AL. 023, 020, 019 (стр. 311–379)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 119 | 311, дефект `(T)` раннера `[x]` | — | FIXED | `cddabb6` | none | — | CLOSE | Закрыто 2026-09-27 | да |
| 120 | 321–325, F2, F4, F5, F6, F7 раннеров 023 | — | OBSOLETE | раннеры удалены `44954ebb` (`scripts/measure_p023_planner_acceptance*.py` нет) | none | — | CLOSE | Предмета в дереве нет | да |
| 121 | 333–339, пп. 1–3, 6, 7 диагностики `find_cycles` | `LIMIT` до дедупликации и т. п. | LIVE | `app/core/clearing/service.py:778`, `:906`, `:1397` | none | M | DROP | Нет потери: только диагностика `GET /clearing/cycles`, денежного пути нет; пп. 4–5 закрыты удалением `auto_clear` | да |
| 122 | 347–349, находки закрывающего ревью 020 | Числа, маркеры, P3 | FIXED | записи 2026-09-28 | dev | — | CLOSE | Все три закрыты записями | триаж |
| 123 | 351, режим `execute_clearing_with_amount` без вхождения | Удалять после миграции тестов | LIVE | `app/core/clearing/service.py:1578`; ~77 тестовых файлов | dev | L | LATER | **Поправка триажа (OWNER → LATER):** развилка техническая, не продуктовая; 024 закрыта, получатель — следующая программа тестового актива | триаж |
| 124 | 355–366, аренда Redis `/clearing/auto` | Закрыто PR #74 | FIXED | `2df5703`; `app/core/clearing/runner.py:445-448` | none | — | CLOSE | Оговорка о реальном Redis остаётся в спеке 023 | да |
| 125 | 370–378, неизвестный коммит клиринга | Закрыто 020 | FIXED | `d448284`, `b959fe7`, `40bbbdc` | none | — | CLOSE | Закрыто | да |
| 126 | 379, остаток `_drain_task` у трёх вызывающих | Интерлок | OBSOLETE | `_rollback_before_interlock`, `_close_checked_out_connection`, `_release_interlock_session` в `clearing/service.py` нет (grep пуст) | none | — | CLOSE | Механизм удалён 027 | да |

## AM–AN. 015 `T1509`, visual E2E (стр. 381–418)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 127 | 406, SQLite без FK удержания | — | OBSOLETE | `app/config.py:48` только `postgresql+asyncpg`; `app/db/sqlite_transaction_control.py` удалён | none | — | CLOSE | Пути SQLite нет | да |
| 128 | 407, RU-протокол «сверки нет» | — | FIXED | `docs/ru/02-protocol-spec.md:1908-1916` описывает сверку | none | — | CLOSE | Документ исправлен | триаж |
| 129 | 418, `Simulator visual E2E` красный с 2026-09-14 | Два сценария trustline | LIVE | прогоны `34802959906`, `35557881677`, `36374399886` (последний 2026-09-28, `2 failed / 22 passed`); оба сценария на моках (`mockRealInteractApp`), `ECONNREFUSED :18000` — шум прокси; `simulator-ui/v2/e2e/manual-operations-interact.spec.ts:623` ждёт `tl-limit-too-low`, удалённый `3b7b328a` (026 `T2602`, лимит ниже долга разрешён) | ops | M | DO-028 (S2) | **Поправка триажа (OWNER → DO):** причина не «неизвестна» — сценарии расходятся с продуктом после решений 026; провалившаяся проверка не может стоять без владельца (§18) | да |

## AO. Класс 2 `T1548` 015 (стр. 421–441)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 130 | 436, staged-гонка вставки | Переписано 019/027 | UNCLEAR | `app/core/payments/service.py:1634-1642` ловит `IntegrityError` под savepoint; прежние якоря не соответствуют | user-visible | S | LATER | Проба 2026-09-21 не сохранена; закрывать только стендом коллизии `tx_id` в staged-режиме | триаж |
| 131 | 437, ответ на гонку зависит от окружения | 23505 против 40001 | OBSOLETE (механизм) | RC — `app/config.py:85-107` | none | — | DROP | Нет потери: ни одна ветка не двигает деньги дважды; ветка `40001` под RC не возникает | триаж |
| 132 | 438, `test_payment_timeouts.py` подделывает коммит | — | LIVE | `tests/unit/test_payment_timeouts.py:188-194` | dev | S | DROP | Нет потери: покрытие держит ячейка 7 `T1523` настоящим коммитом | да |
| 133 | 439, SQLite-тир и гонка вставки | — | OBSOLETE | SQLite-тира нет | none | — | CLOSE | Предмет исчез | да |
| 134 | 440, `seed_db.py` пишет `PAYMENT` без отпечатка | Предупреждение на будущее | LIVE | `scripts/seed_db.py:529` | dev | S | DROP | Нет потери: повтор их `tx_id` недостижим (запись сама это говорит) | триаж |
| 135 | 441, юнит-тесты SQLite-тира на PostgreSQL | — | OBSOLETE | один тир (AGENTS §5) | none | — | CLOSE | Тира нет | да |

## AP–AR. Проглатывание исключений, M20, пробелы покрытия (стр. 444–620)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 136 | 463–489, пп. 1–5 и `integrity.py:284` | Закрыто 2026-08-11/12 | FIXED | записи `[x]` | none | — | CLOSE | Закрыто; заметка про `integrity.py:284` — справка | триаж |
| 137 | 492–525, `engine.py:538` проглоченный `rollback()` | — | OBSOLETE | `app/core/payments/engine.py` удалён | none | — | CLOSE | Механизм удалён | да |
| 138 | 526–613, M20 разбор и «не является дефектом» | — | FIXED / ACCEPTED | таблица 13 `[x]`; раздел «не поднимать заново» | none | — | CLOSE | Закрыто; список «не является дефектом» переносится в раздел принятого одной строкой-ссылкой | триаж |
| 139 | 615–620, пробелы покрытия без владельца | Отсылка к 006 | OWNER | `specs/006-verification-integrity/spec.md` | dev | L | OWNER | 006 не авторизована; судьба отсылки — владелец (В-8) | триаж |

## AS–AT. Долги 011 и 012 (стр. 622–656)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 140 | 630, `F-011-8` политика не валидируется в симуляторе | Тот же дефект, что № 48 | LIVE | см. № 48 | money | S | DO-028 (S1) | Закрывается той же правкой, что № 48 | да |
| 141 | 631, `F-011-9` метки без `Z` в `list[Any]` графа | Два формата одной метки | LIVE | `app/schemas/graph.py:52-54`; `app/api/v1/admin.py:276`, `:605` | user-visible | S | LATER | Ревьюер — `DESCRIBE_ONLY`; потребитель — Admin UI, который разбирает оба формата | триаж |
| 142 | 632, generated-схема SSE | Схем событий нет в `app.openapi()` | LIVE | `api/openapi.yaml:1196-1218` | dev | M | LATER | Канон в `openapi.yaml` есть; способ публикации — отдельная спека | триаж |
| 143 | 647, форматтеры симулятора не читают `precision` | 19 производств строки | LIVE | `app/api/v1/simulator.py:824-826`, `:2097-2106` | user-visible | M | LATER | Тот же класс, что № 170 (`T1513`); нужен репродьюсер потери цифр | триаж |
| 144 | 648, написание клиента уезжает в снапшот | Экспонента недостижима | LIVE | `app/api/v1/simulator.py:867`, `:885`, `:894` | none | S | DROP | Нет потери: величина хранится точно, меняется только написание | триаж |

## AU. Долги `T1211` 012 (стр. 658–735)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 145 | 665, §1 примеры документации не сверяются | Нужна разметка блоков | LIVE | `docs/ru/02-protocol-spec.md` (16 тестов читают только код) | dev | L | DROP | Нет потери: пример `limit` исправлен `2790e28`, остаток — новый механизм | триаж |
| 146 | 686, §2 RU §5.2 неполон | Порог «ниже долга» снят 026 | OBSOLETE | `app/core/trustlines/service.py:608-610`; RU уже описывает | none | — | CLOSE | Посылка устарела; EN/PL заморожены | триаж |
| 147 | 704, дрейф EN/PL §5.1 и §6.2 | Переводы | ACCEPTED | Translation freeze (AGENTS §13) | none | — | ACCEPTED | Переводы не нормативны | триаж |
| 148 | 709, §3 пример `ERROR` §9.5 числами | `"limit": 1000.00` в деталях | LIVE | `docs/ru/02-protocol-spec.md:1733-1734` | dev | S | DO-028 (S4) | Нормативный RU показывает деньги числом вопреки правилу 012; правка двух строк, только RU | да |
| 149 | 729, §4a тест stale run context не проверяет гард | — | LIVE | `useSimulatorRealMode.test.ts:308`; гард `useSimulatorRealMode.ts:442` | dev | S | LATER | Слабый тест без дефекта продукта; при правке файла | триаж |
| 150 | 730, §4b scenario-change вотчер не покрыт | — | LIVE | `useSimulatorRealMode.ts:1318,1406,1420` | dev | S | LATER | Вместе с № 149 | триаж |

## AV–AX. 007, 013, без владельца (стр. 737–1043)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 151 | 754, 007 §1 переполнение `NUMERIC(20,8)` на `total_debt` | Тик метрик теряется целиком, `GET /metrics` повторяет старую точку | LIVE | один `executemany` на все ключи — `app/core/simulator/storage.py:359` в savepoint `:388`; `_measured_value` без проверки представимости — `:207-223`; читатель выбрасывает `NULL` — `app/core/simulator/metrics_bottlenecks.py:215-216` | user-visible | S | DO-028 (S1) | Уверенное ложное утверждение о данных (класс `F-007-1`); путь есть (сумма двух долгов у потолка), починка по поправке 2026-08-25 — без смены контракта | да |
| 152 | 864, 007 §2 аннотации `float` | Объекта нет | FIXED | `app/core/simulator/storage.py:231` объявляет `Optional[Decimal или float]`; модулей `real_tick_*` нет | none | — | CLOSE | Предмет снят | триаж |
| 153 | 889, 007 §3 мёртвый `utc_now` | — | LIVE | `metrics_bottlenecks.py:55,65` | dev | S | LATER | Попутно при № 151, если файл открыт; отдельно не делается | триаж |
| 154 | 899, 013 строгий декодер гасит страницу | Сценарий рассинхрона версий | LIVE | `admin-ui/src/api/realApi.ts:230-231`, `:568` | user-visible | S | DROP | Нет потери: сервер четвёртого имени не шлёт; ослабление гарда 013 вредно | триаж |
| 155 | 925, 013 платёж без `from/to` как «не участвовал» | Нет отката на `initiator_id` | LIVE | `app/core/admin/metrics.py:674-678` | user-visible | S | LATER | Существование таких платежей не измерено; сначала замер | триаж |
| 156 | 962, `api/openapi.yaml` без владельца | — | OWNER | нигде не записано | dev | S | OWNER | Строка в AGENTS §8 (В-8) | триаж |
| 157 | 987, `HOUR` precision 1 против 2 | Два набора | LIVE | `seeds/equivalents.json`; `admin-fixtures/v1/datasets/equivalents.json` | user-visible | S | OWNER | Какая точность у часа — продукт (В-3) | триаж |
| 158 | 1016, `MISSED-3` | Тест жив и разобран 025 | FIXED | `tests/integration/test_trustlines_list_filters_pagination.py` | dev | — | CLOSE | Адресат не нужен | триаж |
| 159 | 1030, `api-snapshots` никто не читает | 13 generated-файлов | LIVE | генератор `admin-fixtures/tools/generate_admin_fixtures.py:33,611` | dev | S | OWNER | Удалить / подключить / пометить — решение по generated-копии (В-6) | триаж |

## AY–AZ. Оценка направления 2026-09-11, FK, изоляция (стр. 1050–1180)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 160 | ~1110, ремонт `/integrity` удаляет долг (`F-015-6`) | — | OBSOLETE | ремонтных эндпоинтов в `app/api/v1/integrity.py` нет; удалены `91b6f794` | money | — | CLOSE | Эндпоинтов нет | да |
| 161 | ~1116, допуск дрейфа в один квант | — | OBSOLETE | `engine.py` удалён; сверка — `app/core/ledger/reconciliation.py` | money | — | CLOSE | Механизм заменён 018 | да |
| 162 | ~1122, `existing_fp is not None` обходит конфликт | — | FIXED | `app/core/payments/service.py:783-804` отказывает при отсутствующем отпечатке (`T1548`) | money | — | CLOSE | Закрыто 015 | да |
| 163 | ~1093, публичный тест дубликата не смотрит на долг | Не было в триаже | FIXED | `tests/integration/test_payments_idempotency.py:23-76` сверяет долги, операции и записи журнала | dev | — | CLOSE | **Дополнение к триажу:** пункт пропущен триажем, закрыт | да |
| 164 | 1145, имена FK зависят от способа создания базы | Нет `naming_convention` | LIVE | `migrations/versions/025_debts_participant_fk_restrict.py:83`; `naming_convention` в `app` нет | ops | S | LATER | Вред — только будущей миграции, удаляющей FK по имени; дешёвый вариант — правило «через отражение» в доке при первой такой миграции | да |
| 165 | 1163, тир не в той изоляции, что приложение | — | OBSOLETE | `app/config.py:85-107` (только RC); тир берёт уровень из настроек | dev | — | CLOSE | Расхождения нет | да |

## BA. Отложено из 015 (стр. 1182–1217)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 166 | 1194, `T1507` `/integrity` API, OpenAPI, протокол | EN называет zero-sum проверкой | LIVE (только EN) | `api/openapi.yaml:6020-6068` уже снимает | dev | S | DROP | Нет потери: EN заморожен | триаж |
| 167 | 1195, `T1510` политика и `max_paths` в предсказательных маршрутах | Цитата устарела | UNCLEAR | `router.py:458-548` | none | M | DROP | Остаток не сформулирован через потерю (§19.2 п. 1) | триаж |
| 168 | 1196, `T1511` починка ремонта | — | OBSOLETE | `91b6f794` | money | — | CLOSE | Предмет удалён | да |
| 169 | 1197, `T1512` профиль участника складывает эквиваленты | `GET /participants/me` и `public_stats` | LIVE | `app/core/participants/service.py:96-104`, `:113-150` (сумма по всем эквивалентам и `quantize(0.00)` на `:110`); потребителей в `admin-ui/src`, `simulator-ui/v2/src` нет | user-visible (внешний клиент API) | M | OWNER | **Поправка триажа (DO → OWNER):** ни один экран репозитория не показывает эти поля; правка меняет `ParticipantStats` в каноне. **Дополнение:** округление до 2 знаков искажает `HOUR`/8-знаковые суммы. Вопрос В-2 | да |
| 170 | 1198, `T1513` форма денежной строки на выходе | Не измерено | LIVE (непроверено) | `to_money_str` не используется в `api/v1/trustlines.py`, `payments/capacity.py` | user-visible | M | LATER | Нужен репродьюсер на `0.05 HOUR`; вместе с № 143 | триаж |
| 171 | 1199, `T1514` PID вне сценария | Защита есть | UNCLEAR | `inject_executor.py:113-116`, `:616` | none | S | DROP | Остаток не сформулирован через потерю | триаж |
| 172 | 1200, `T1515` планировщик обещает больше ядра | Реализм | UNCLEAR | `real_payment_planner.py:601-613` | none | M | DROP | Нет потери: ядро отказывает | триаж |
| 173 | 1201, `T1517` подпись не связывает тип операции | — | LIVE | `payments/service.py:1170-1181` | none | S | DROP | Нет потери: не воспроизведена, `tx_id` уникален | триаж |
| 174 | 1202, `T1518` четыре строки расхождений с протоколом | Цитаты устарели | UNCLEAR | `trustlines/service.py:351,658,743` | dev | M | DROP | Нет потери; спор о документе, нужен новый аудит, если возьмут | триаж |
| 175 | 1203, `T1519` мёртвый код | Удалено или оставлено осознанно | FIXED | `validate_idempotency_key`, `PaymentDetail` в `app` нет (grep) | dev | — | CLOSE | Закрыто | да |
| 176 | 1204, `T1521` два режима округления | — | LIVE | `admin.py:1690,2060` (`ROUND_HALF_UP`); `reconciliation.py:857` (`ROUND_DOWN`) | none | M | DROP | Нет потери: расхождения на живом пути не измерено | триаж |
| 177 | 1205–1208, `T1531`, `T1536`, `T1538`, `T1539` журнал в процессе | — | OBSOLETE | `app/core/ledger/journal.py` удалён `2b8b0228` (018) | none | — | CLOSE | Механизм удалён (4 строки BACKLOG) | да |
| 178 | 1209, `T1541` CI строит схему трижды | ~17 с на плановом job | UNCLEAR | цитата устарела | ops | S | DROP | Нет потери: секунды на расписании | триаж |
| 179 | 1210, `T1542` DDL `debts` в EN | — | ACCEPTED | EN заморожен | none | — | DROP | Нет потери: перевод не нормативен | триаж |
| 180 | 1211, `T1547` real-mode без opt-in | — | LIVE | `app/config.py:272` | none | S | DROP | Нет потери: real-mode — запрошенный оператором режим; решение П2 | триаж |
| 181 | 1212, `T1552` необъявленный 400 | — | FIXED | `app/api/v1/clearing.py:116-141` (409/E008) | user-visible | — | CLOSE | Закрыто `T1544` | да |
| 182 | 1213, `T1554` подмена `checksum_after` | — | OBSOLETE | `payments/service.py:1992`, `clearing/service.py:2005` пишут `""` | none | — | CLOSE | Контрольной точки в операции нет | да |
| 183 | 1214, `T1555` перебор эквивалентов инжекта | — | FIXED | `real_runner_impl.py:481-483` — только эквиваленты `inject_debt` | none | — | CLOSE | Сужено | да |
| 184 | 1215, `T1556` распределённый лок цикла целостности | — | OBSOLETE | `reconciliation.py:1286` «no advisory lock» | ops | — | CLOSE | Advisory-локов нет | да |
| 185 | 1216, `T1549`-1 `[201, 500]` на создании линии | — | OBSOLETE | `trustlines/service.py:317-323` → 409 `CONCURRENT_TRUSTLINE_CREATE`; `xfail` в `test_p1_trustline_reopen_postgres.py` нет | user-visible | — | CLOSE | Дефект был специфичен для SERIALIZABLE | да |
| 186 | 1217, `T1549`-2 тесты на RC и SERIALIZABLE | — | OBSOLETE | `test_payment_commit_advisory_locks_postgres.py` удалён; приложение на RC | dev | — | CLOSE | Вопрос отпал | да |

## BB–BF. 017 (стр. 1223–1388)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 187 | 1227, `simulator_runs.seed` int32, сид — 32 бита без знака | ≈ половина ранов не сохраняется | LIVE | `int.from_bytes(seed_material[:4])` — `app/core/simulator/run_lifecycle.py:190-191`; `Integer` — `app/db/models/simulator_storage.py:30`, `migrations/versions/013_simulator_storage_mvp.py:32`; отказ проглатывается — `app/core/simulator/storage.py:89-93` | user-visible | S | DO-028 (S1) | **Дополнение к триажу:** строки `simulator_runs` читает проверка изоляции периодического клиринга (`app/core/clearing/runner.py:465`) — потерянная строка прячет real-ран от неё. Сид нигде не питает генератор (только `storage.py:63`, `artifacts.py:76`) — маска безопасна | да |
| 188 | 1241, super-smoke без сервиса Postgres | Расписанный job заведомо красный | LIVE | `.github/workflows/quality.yml:733-764`, `runs-on: windows-latest` (`:736`) — контейнер-сервисов на Windows-раннере нет; прогон `36374399886`: pytest exit `4` | ops | M | DO-028 (S2) | **Дополнение к триажу:** нужен перенос job на ubuntu, а не только строка `services:`. Блокер SSI снят 027, блокер `seed` — № 187 | да |
| 189 | 1261, архивный legacy-генератор сломан | Вызывает несуществующие функции | LIVE | `scripts/_archive/generate_simulator_seed_scenarios_legacy.py:19-25` | dev | S | LATER | Архив защищён §3; удаление — отдельный cleanup-slice с reference scan | триаж |
| 190 | 1271, документы симулятора о входе генератора | Закрыто тем же PR | FIXED | запись | dev | — | CLOSE | Закрыто | триаж |
| 191 | 1277, заморозка участника не мешает транзиту | Роутер и ядро статус участника не читают | LIVE | `app/core/payments/router.py:104-114`; репродьюсер ревьюера (стр. 1348) | money (возможный) | S–M | OWNER | Если транзит запрещён — класс 1 на денежном пути. Решение первым (В-1) | да |
| 192 | 1289, статус линии `frozen` недостижим продуктом | «Заморозка» — два разных исхода | LIVE | писатели `trustlines/service.py:121`, `inject_executor.py:1021`; админ меняет только `participants.status` (`admin.py:925-940`) | user-visible | M | OWNER | Тот же вопрос В-1 | триаж |
| 193 | 1307, лаунчер: владение по PID | Закрыто | FIXED | `d392532e`; `run_full_stack.ps1:400-437` | ops | — | CLOSE | Дочерний слушатель признаётся по дереву | да |
| 194 | 1324, где живёт переносной PostgreSQL | Ждёт «да» | OWNER | `docs/ru/backend/postgres-local-portable.md:55-73` | dev | S | OWNER | Один абзац; рекомендация «не менять» (В-8) | триаж |
| 195 | 1338, F2 сброс dev-базы по пространству имён | — | LIVE | `scripts/dev_database.py:66` | dev | L | DROP | Нет потери: чинится только метаданными владения — новая сущность | триаж |
| 196 | 1339, F6 `GeneratorExit` прячет отказ очистки | Обёртки нет | LIVE (гипотеза) | `tests/migrated_schema.py:628-650` | none | S | DROP | Нет потери: нет входа (§19.2 п. 6) | триаж |
| 197 | 1340, F7 гарды не смотрят `needs:` | — | LIVE | `tooling-tests/conftest.py:46`, `:275` | ops | M | DROP | Нет потери: ловится ревью workflow; дубль № 209 | триаж |
| 198 | 1341, F8 нет независимого знаменателя | Гард над гардом | LIVE | `scripts/seed_recipe.py:828-895` | dev | M | DROP | Нет потери | триаж |
| 199 | 1342, F9 исключение `quality.yml` шире фикстуры | — | LIVE (сузилось) | `tooling-tests/portable/test_p017_t1701_…:163-200` | dev | S | DROP | Нет потери: пути в продукте нет | триаж |
| 200 | 1346, готовность советует `reset-db` при проваленной сверке | Не было в триаже | LIVE | `scripts/dev_database.py:554` — `"… readiness check(s): {failed}. Reset it: .\scripts\run_local.ps1 reset-db"`; `reconciliation_passed` — `scripts/seed_recipe.py:1150` | money (evidence) | S | DO-028 (S2) | **Дополнение к триажу:** совет уничтожает единственное свидетельство денежного расхождения (журнал, долги, baseline); §9 — целостность есть гейт. Правка сообщения | да |
| 201 | 1348, репродьюсер транзита через замороженного | Дополнение к № 191 | LIVE | запись | money (возможный) | — | OWNER | Входит в В-1 | триаж |
| 202 | 1352, super-smoke красный на старте тира | — | LIVE | см. № 188 | ops | — | DO-028 (S2) | Закрывается № 188 | да |
| 203 | 1358, строки без жертв в базе тира | — | LIVE | `integrity_audit_log`, `simulator_runs` | none | S | DROP | Нет потери: нет сигнала — нет починки (§9) | триаж |
| 204 | 1362, гард `T1525` | — | OBSOLETE | `tests/conftest.py:410` — «the deleted T1525 engine guard» | none | — | CLOSE | Гарда нет | да |
| 205 | 1370, F2 ложноотрицательный путь `T1525` | — | OBSOLETE | то же | none | — | CLOSE | Гарда нет | да |
| 206 | 1371, F6 посылка о `scratch_db` | — | OBSOLETE | SQLite удалён 017 стадией 3 | none | — | CLOSE | Условие выполнено | триаж |
| 207 | 1373, Windows PowerShell 5.1 в CI | — | FIXED | `quality.yml:130-142`; тир перебирает `pwsh` и `powershell.exe` | ops | — | CLOSE | Ось измеряется в `required-ui` | триаж |
| 208 | 1381, демо-фикстуры EUR/HOUR: знак дважды | Генератор пишет модуль | UNCLEAR | рантайм отдаёт знаковые атомы — `app/core/simulator/net_balance_utils.py:79-99` (`atoms_to_net_sign` читает знак с атомов); закоммиченные файлы с этим согласованы | user-visible (демо) | S | LATER | **Поправка триажа (OWNER → LATER):** вопрос технический — по контракту рантайма подозрителен генератор, а не файлы; сверить генератор до любой регенерации | да |

## BG–BH. 025 (стр. 1389–1443)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 209 | 1391, пределы тира инструментов | `--help`, `needs:`, `shell:` | LIVE | `pytest.ini:41`; `tooling-tests/conftest.py:40-56` | ops | S | DROP | Нет потери: нужна умышленная перенастройка | триаж |
| 210 | 1410, (a) стенды решённых развилок | 020/T1908 | LIVE | файлы на месте; T1908 адаптирован `b8e49da0` | dev | L | LATER | Новая авторизация тестового актива; сначала пересчитать «красные» | триаж |
| 211 | 1424, (b) неавторизованные стадии 025 | `T2503`, `T2505`–`T2509`, `T2511`, `T2512`, G3 | OWNER | `VERDICT-025: NARROW` | dev | L | OWNER | Новое «да» владельца с ответами §19.2 (В-7) | триаж |
| 212 | 1438, (c1) лаунчеры не под Linux `pwsh` | — | LIVE | `quality.yml:130-142` | ops | S | DROP | Нет потери: логика проверяется на Windows двумя хостами | триаж |
| 213 | 1439, (c2) раздел powershell до UI-гейтов | Маскирует | LIVE | `quality.yml:135-142` | ops | S | DROP | Нет потери: оставлено сознательно, раздел ~70 с | триаж |
| 214 | 1440, (c3) гарды продукта не читают `tooling-tests/` | — | LIVE | три гарда | dev | S | DROP | Нет потери: сигнала нет | триаж |
| 215 | 1441, (c4) число тира — не состав | — | LIVE | `tooling-tests/conftest.py` (`EXPECTED_CASES`) | dev | M | DROP | Нет потери: гард над гардом | триаж |
| 216 | 1442, (c5) MOVE-OUT внутри KEEP/MIXED | — | LIVE | названные тесты | dev | M | LATER | Следующий срез тестового актива | триаж |
| 217 | 1443, (c6) `viz_rules.net_sign_from_atoms`, `collect_magnitudes` без вызывающих | Дубль | LIVE | `app/core/simulator/viz_rules.py:44`, `:115`; вызовов нет | dev | S | LATER | Мёртвый дубль; `app/` — отдельным касанием | да |

## BI–BJ. Принятые риски и «Принято и остаётся как есть» (стр. 1446–1689)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 218 | 1448, 013 принят риск | 2026-09-11 | ACCEPTED | запись | — | — | ACCEPTED | Терминально | триаж |
| 219 | 1512, 012 `T1212`–`T1214` | 2026-09-10 | ACCEPTED | запись | — | — | ACCEPTED | Терминально | триаж |
| 220 | 1578, 012 финальная дельта | 2026-08-25 | ACCEPTED | запись | — | — | ACCEPTED | Терминально | триаж |
| 221 | 1614, 011 второй fix-delta | 2026-08-24 | ACCEPTED | запись | — | — | ACCEPTED | Терминально | триаж |
| 222 | 1643, ревью при достижимом credential | 2026-08-14 | ACCEPTED | запись | — | — | ACCEPTED | Терминально | триаж |
| 223 | 1671, неверная запись credential-free (исправлено) | Самоисправленная | ACCEPTED | запись | — | — | ACCEPTED | История, остаётся в разделе | триаж |
| 224 | 1680, «Принято и остаётся как есть» (3 пункта) | 2026-08-11 | ACCEPTED | запись | — | — | ACCEPTED | Терминально | триаж |

## BK–BN. 017 стадия 3, 019 стадия 4, 027 (стр. 1691–1720)

| № | Якорь | Суть | Статус | Evidence | HARM | COST | Решение | Обоснование | Пров. |
|---|---|---|---|---|---|---|---|---|---|
| 225 | 1693–1696, четыре зачёркнутых пункта | Закрыто | FIXED | запись `chore(017): close the stage-3 review remainder` | dev | — | CLOSE | Закрыто (4 строки BACKLOG) | триаж |
| 226 | 1697, половина `T1541` — bootstrap entrypoint | — | LIVE | `docker/docker-entrypoint.sh:25-26` | ops | S | DROP | Нет потери: сигнала нет | триаж |
| 227 | 1698, замер CI с промахом кэша | — | UNCLEAR | нужен прогон CI | ops | S | DROP | Нет потери: замер ради замера | триаж |
| 228 | 1704, повтор `pay()` видит устаревший `Equivalent` | — | LIVE | `app/core/payments/service.py:1147-1150` | none | S | DROP | Нет потери: привязывающее чтение отказывает до записи долга | триаж |
| 229 | 1706, то же, недетерминированный путь отказа | — | LIVE | то же | none | S | DROP | Тот же дефект; критерий «20 повторов» записан для того, кто возьмёт | триаж |
| 230 | 1712, создание линии в инжекте без таймаута | Обещание 027 «ожидания инжекта ограничены» не держится | LIVE | `app/core/trustlines/service.py:463` без `timeout_ms`; `app/core/money_boundary.py:108-110` (`timeout_ms is None` — без предела); предблокировка — только участники `inject_debt` (`app/core/simulator/real_runner_impl.py:488-491`) | user-visible (зависание тика) | S | DO-028 (S3) | Новый путь ожидания без предела, введённый fix-delta 027; правка — передать бюджет | да |
| 231 | 1713, `lock_timeout` на каждое ожидание, не на набор | Накопление ожиданий | LIVE | `app/core/money_boundary.py:106-117` | ops | M | LATER | Держатели строк сами ограничены своими бюджетами, ожидание конечно; абсолютный дедлайн — новый механизм (`statement_timeout`/дедлайн корутины). Получатель — владелец денежного ядра | да |
| 232 | 1719, Q3 стенда принимает `committed >= 1` | Контракт — 40/40 | LIVE | `tests/integration/test_p027_disjoint_concurrency_stand_postgres.py:745`; `cycle_debts_left` измеряется на `:725`, не утверждается; `specs/027-concurrent-payments/spec.md:272` | dev | S | DO-028 (S3) | Регрессионный критерий слабее записанного результата; два ассерта, новой сущности нет | да |
| 233 | 1720, inject-тест принимает любую раннюю ошибку | `except Exception` | LIVE | `tests/integration/test_p027_t2706_fix_delta_postgres.py:127-137` | dev | S | DO-028 (S3) | Этот тест — репродьюсер № 230; без свидетеля захвата он проходит на немедленном `RuntimeError` | да |
