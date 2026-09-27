# arch-layering — общая архитектура backend (слои и границы)

HEAD: 4119ace. Прочитано целиком: `app/main.py` (897), `app/config.py` (459), `app/api/deps.py` (360), `app/api/router.py` (20), `app/api/v1/health.py` (172), `app/api/v1/websocket.py` (96), `app/api/v1/auth.py` (110), `app/utils/*` (11 модулей, 1246), `app/schemas/*` (14 модулей, 1839), `app/db/*` (base, session, types, journal_tables, journal_triggers, reconciliation_tables, models/* — 1618), `docs/ru/03-architecture.md` §1–2.3, `specs/README.md:54-163`, front matter + Problem 016/021/023, заголовки `specs/BACKLOG.md`. Точечно (только цитируемые якоря): `app/api/v1/admin.py:296-520`, `app/api/v1/simulator.py:112-140, 355-405`, `app/api/v1/payments.py:40-100`, `app/api/v1/clearing.py:30-55`, `app/core/balance/service.py:25-60`, `app/core/payments/service.py:193-200, 560-580`, `app/core/simulator/__init__.py`, `app/core/simulator/session.py`, `tests/contract/test_openapi_contract.py:1-120, 1380-1410`, `tests/unit/test_trustline_timestamps.py`. Не прочитано: остальное в `app/api/v1/admin.py` (2329) и `app/api/v1/simulator.py` (3053), внутренности `app/core/*` (зоны других агентов), `migrations/`, `docs/ru/config-reference.md`.

Инструменты: AST-граф импортов `scratchpad/review/import_graph.py` (Tarjan по модулям, top-level и с ленивыми), перепись читателей полей `Settings` `scratchpad/review/settings_usage.py`, `ruff 0.1.14` по зоне (`F401,F811,F841,ARG,PLR0912,PLR0913,PLR0915,C901,E402`: 6×PLR0912, 5×C901 — `_declare_auth_statuses` 23, 3×PLR0915; F401/F811/F841 — ноль), `git grep`.

## Summary

Слоёв четыре, и направление в целом выдержано: `api → core → db`, `utils` и `schemas` — листья; **циклов импортов нет** (Tarjan пуст и по top-level, и с учётом 56 ленивых импортов). Инверсии три и все «мягкие»: `core` импортирует `schemas` (9 модулей ядра + 11 симулятора — сервисы принимают и возвращают API-DTO), `utils/validation.py:6` тянет `schemas/equivalent.py`, а `db/models/equivalent.py:5` через `utils.validation` — Pydantic-модуль. Ленивые импорты в массе не от циклов, а от привычки; единственная структурная причина — `app/core/simulator/__init__.py:1` эйджерно поднимает весь runtime (это же названо причиной, почему денежный рендерер живёт в `utils/money.py:3-12`).

Главный диагноз зоны — не слои, а **три места, где одна ответственность размазана**: (1) `main.py` — 38 % файла (`:553-897`) это пост-обработка OpenAPI, ещё 240 строк — супервизор фоновых задач и планировщик целостности/сверки, и 023 собирается добавить туда третий цикл; (2) конфигурация — одна `Settings` с хорошими guardrail'ами, но 38 вызовов `getattr(settings, KEY, default)` с расходящимися мёртвыми дефолтами и одним фантомным ключом, плюс 9 чтений `os.environ` мимо `Settings` (6 переменных, одна — продуктовый флаг Interact-режима), плюс `LOG_LEVEL`, который никто не читает, но который админка предлагает менять; (3) наблюдаемость — логгер один (`logging.getLogger(__name__)` в 15 модулях), но нигде не сконфигурирован, request-id не попадает ни в записи лога, ни в конверт ошибки, а `auth.py:177` пишет в аудит сырой заголовок мимо валидатора.

Остатки 017 в зоне — не код, а проза: `db/types.py`, `db/journal_tables.py`, `db/models/debt.py`, `equivalent.py` рассуждают о SQLite в настоящем времени, а пять валидаторов «наивный timestamp → UTC» в `schemas/` — мёртвая компенсация, которую держит один unit-тест; строка BACKLOG:81 предлагает её *расширить*, что после 017 перевёрнуто.

Первым делом я бы сделал не рефакторинг, а три узкие правки без спеки (§13 «только спека — нормальный случай» здесь даже не нужна): вынести супервизор в `utils/background_jobs.py` и цикл целостности в `core/integrity.py` **до** того, как 023 положит рядом второй цикл; заменить `getattr(settings, …)` на прямой доступ и завести `SIMULATOR_ACTIONS_ENABLE` в `Settings`; сконфигурировать логирование из `LOG_LEVEL` с request-id в записи или убрать `LOG_LEVEL` из `/admin/config`.

## Findings

| ID | Sev | Category | path:line | Одной строкой | Covered-by | Effort |
|---|---|---|---|---|---|---|
| AL-1 | P2 | responsibility | `app/main.py:34-155, 158-279, 465-550, 553-897` | Точка входа держит супервизор фоновых задач, планировщик целостности+сверки, копию health-роутов и 345 строк пост-обработки OpenAPI; 023 добавит третий цикл туда же | 023 (берёт не полностью: добавляет к проблеме) | S |
| AL-2 | P2 | logic-bug / config | `app/core/balance/service.py:54`; 38 сайтов `getattr(settings, …)` | `getattr(settings, KEY, default)` с расходящимися мёртвыми дефолтами и один фантомный ключ `BALANCE_SUMMARY_CACHE_MAX_ENTRIES`, которого в `Settings` нет — его нельзя задать ничем | none | S |
| AL-3 | P2 | config / duplication | `app/api/v1/simulator.py:389`, `app/core/simulator/{artifacts.py:134,storage.py:107,run_lifecycle.py:188,runtime_impl.py:65,runtime_utils.py:56-117}`, `app/main.py:473`, `app/api/v1/health.py:29` | Вторая поверхность конфигурации: 9 чтений `os.environ` мимо `Settings`, включая продуктовый флаг Interact-режима и дважды продублированный дефолт `524288` | none (021 снимает только 14 `SIMULATOR_CLEARING_ADAPTIVE_*`) | S |
| AL-4 | P2 | observability (§12) | `app/config.py:151`, `app/api/v1/admin.py:319`, `app/main.py:378-391`, `app/utils/exceptions.py:43-44`, `app/api/v1/auth.py:177` | `LOG_LEVEL` никем не читается, но объявлен mutable в `/admin/config`; логирование нигде не сконфигурировано; request-id не попадает ни в записи лога, ни в конверт ошибки; аудит логина пишет невалидированный заголовок | none | S |
| AL-5 | P3 | dead-code / docs-drift | `app/db/models/config.py`, `app/api/v1/admin.py:456-474` | Таблица `config` без единого читателя/писателя в `app/`, `scripts/`, `seeds/`; runtime-конфиг и feature-flags живут в мутируемом объекте `settings` процесса — теряются на рестарте, расходятся между репликами | none | S (документировать) |
| AL-6 | P3 | architecture | `app/core/*/service.py` (9 импортов `app.schemas`), `app/utils/validation.py:6`, `app/db/models/equivalent.py:5`, `app/core/simulator/__init__.py:1` | Инверсии `core→schemas`, `utils→schemas`, `db→(utils→)schemas`; 56 ленивых импортов при нулевых циклах — единственная структурная причина: эйджерный `__init__` симулятора | 021 частично (шимы `runtime.py`/`real_runner.py`, но не `__init__`) | S |
| AL-7 | P3 | responsibility | `app/utils/money.py:3-12`, `app/utils/validation.py:139-214, 348-550`, `app/utils/error_codes.py`, `app/utils/exceptions.py:47-125` | `utils/` — домен под другим именем: денежная дверь, коды протокола §9.4 и HTTP-статусы исключений, которые ядро бросает напрямую (payments 20, trustlines 19 сайтов) | none | документировать, не патчить |
| AL-8 | P3 | duplication | `app/main.py:465-550` ≡ `app/api/v1/health.py:41-129` | Три хендлера и три хелпера health дословно в двух файлах; корневой `/health` не отдаёт `environment` | 016 (Non-goal «не объединять root/versioned health routes» — про маршруты, не про один хендлер на два маршрута) | S |
| AL-9 | P3 | duplication | `app/api/deps.py:141-162, 176-196, 317-334`; `app/api/v1/health.py:141-145`; `app/api/v1/websocket.py:28-38` | Блок «decode JWT → участник → active» скопирован трижды с разной семантикой отказа; `/admin/health/db` зовёт `require_admin` руками с обязательным заголовком (нет заголовка → 422, не 403) | 016 частично (Non-goal «auth priority chains») | S |
| AL-10 | P3 | docs-drift | `app/db/types.py:20-35, 81-84, 113-117`, `app/db/journal_tables.py:22-24`, `app/db/models/debt.py:30-33, 50-54`, `app/db/models/equivalent.py:34-35` | Проза о SQLite в настоящем времени после 017 («kept in the DDL of both dialects»); остаток 017 в BACKLOG:1397 перечислял другие файлы и закрыт | none | S |
| AL-11 | P3 | dead-code (живёт ради теста) | `app/schemas/{equivalents.py:25-31, trustline.py:25-29, integrity.py:9-12, admin.py:108-113, admin.py:158-165}` | Пять валидаторов «naive → UTC» — компенсация SQLite: все колонки `DateTime(timezone=True)`, asyncpg отдаёт aware, наивных `datetime.now()`/`utcnow()` в `app/` нет; держит один unit-тест | BACKLOG «Узкие правки», строка «Participant timestamps без UTC-normalization» — берёт перевёрнуто (предлагает расширить) | S |
| AL-12 | P3 | readability / docs-drift | `app/utils/validation.py:254, 182-184, 496-499`; `app/db/types.py`; `app/utils/money.py` | Археология в комментариях: 330 из 632 строк `validation.py`, 118 из 182 `types.py`, 78 из 118 `money.py`; часть фактов устарела («default max_scale of 18» при `= 8`, «le=18 (`equivalents.py:39`, `admin.py:218`)» при `le=8`) | none | S |
| AL-13 | P3 | readability / dead-code | `app/schemas/equivalent.py` vs `equivalents.py`; `app/schemas/common.py:46-52`; `schemas/clearing.py:7-11` ≈ `graph.py:76-80` ≈ `simulator.py:713-715`; `trustline.py:18-20` vs `balance.py:6-10` | Два модуля с именами, различающимися на букву; мёртвые `PaginationParams`, `SignedRequest` (0 ссылок в app и tests; `SignedRequest` есть в каноне `openapi.yaml:4962`); три формы ребра цикла; деньги на wire то `Decimal`, то `str` | none | документировать (Contract: частично) |
| AL-14 | P3 | docs-drift | `docs/ru/03-architecture.md:138-175, 176-460` | §2.2 рисует `api/v1/router.py` (реально `api/router.py`), не знает `utils/`, `core/simulator/` (крупнейший пакет), роутеров balance/equivalents/health/simulator; §2.3 описывает `AuthService.register/create_session`, `RoutingService`, `ClearingEngine.process_triggered`, `IntegrityChecker` с 8 методами, `TrustLineService.get_available_credit` — ничего из этого в коде нет | none | S |
| AL-15 | P3 | duplication | `app/utils/security.py:14-19`, `app/main.py:314-315`, `app/api/deps.py:120-122` | Два канала до одного Redis-клиента: модульный глобал `security._redis_client` и `app.state.redis` через `deps.get_redis_client` | none | S |
| AL-16 | P3 | readability | `app/main.py:158-165`, `app/utils/metrics.py:38-42` | Метрика `geo_recovery_events_total` считает события целостности после того, как 019 убрала recovery | none | S (Contract: имя метрики — наблюдаемая поверхность) |
| AL-17 | P3 | docs-drift | `app/db/models/transaction.py:25`; писатели: `app/core/clearing/service.py:2088` (`NEW`), платежи `COMMITTED/ABORTED` | `chk_transaction_state` допускает `ROUTED/PREPARE_IN_PROGRESS/PREPARED/PROPOSED/WAITING/REJECTED`, которые никто не пишет — «зарегистрировано, но не описано» | 019 (ограда 030 сознательно оставила CHECK) | документировать (Contract: yes — миграция) |
| AL-18 | P3 | duplication | `app/main.py:439, 592`; `app/api/v1/simulator.py:397-405` | Правило «у action-маршрутов симулятора плоский конверт» выражено литералом пути в двух местах `main.py` и третьим знанием в `_action_error` | none | S |

## Детали

### AL-1 — `main.py` держит четыре ответственности помимо сборки приложения

Evidence. Супервизор (общий, не доменный), `app/main.py:112-117`:
```python
def _start_supervised_background_task(
    app: FastAPI,
    *,
    name: str,
    coroutine_factory: Callable[[], Awaitable[None]],
) -> asyncio.Task | None:
```
при том, что состояние задач уже владеет `app/utils/background_jobs.py:6-11` (`background_job_states`). Планировщик домена, `app/main.py:174-176`, `:197-201`:
```python
async def _run_debt_reconciliation_once(session_factory, *, reason: str) -> None:
    """Programme 015 steps 5a and 5b: debt reconciliation ...
    THE ONLY HOST. ...
async def _run_integrity_checkpoints_once(app: FastAPI, *, reason: str) -> bool:
    from app.core.integrity import compute_and_store_integrity_checkpoints
    from app.db.session import AsyncSessionLocal
    from app.utils.distributed_lock import redis_distributed_lock
    from app.utils.exceptions import ConflictException
```
(`ConflictException` импортируется лениво из модуля, который уже импортирован на `:24`). Пост-обработка OpenAPI — `_register_openapi_model_schema`, `_custom_openapi`, `_declare_rate_limit_status`, `_declare_auth_statuses` (`:553-897`, 345 строк; `C901` 23 у последней). Корневые health — см. AL-8.

Почему это важно. Файл читается как «точка входа», а на деле это единственный хост сверки долгов (докстринг `:178` так и говорит) плюс генератор контракта. 023 в owner surface пишет: «`app/main.py` — один новый контролируемый цикл рядом с `_integrity_loop`», а F-023-6 уже фиксирует «копия цикла целостности (`main.py:112`, `:255`)». Второй цикл в этом файле — третья копия шаблона «интервал → лок → работа → метрика → состояние job'а».

Минимальное исправление. Перенос без новых абстракций: `_record_background_job_event`, `_on_background_task_done`, `_start_supervised_background_task` → `app/utils/background_jobs.py` (там уже живёт их состояние); `_run_integrity_checkpoints_once`, `_run_debt_reconciliation_once`, `_integrity_loop`, `_emit_integrity_metric` → `app/core/integrity.py` (или `app/core/ledger/`); четыре функции OpenAPI → `app/api/openapi.py`, `app.openapi = build_openapi(app)`. Итог `main.py` ≈ 250 строк: lifespan, middleware, обработчики, монтирование.

Что может сломаться / call-sites. Тесты патчат имена в `app.main` — `git grep -n "app.main\." tests | grep -E "_run_integrity|_start_supervised|_declare"` перед переносом; `tests/contract/test_p011_reachable_statuses_are_declared.py` пересчитывает набор статусов независимо и не зависит от расположения функций; `test_p011_root_routes_bypass_the_policy_gate.py` импортирует `app.main.app` — не трогается.

Covered-by: 023 (частично, в обратную сторону). Contract: no.

### AL-2 — `getattr(settings, KEY, default)`: мёртвые расходящиеся дефолты и фантомный ключ

Evidence. `app/core/balance/service.py:54`:
```python
max_entries = int(getattr(settings, "BALANCE_SUMMARY_CACHE_MAX_ENTRIES", 1024) or 1024)
```
В `Settings` (`app/config.py:110-255`) поля с таким именем нет (перепись полей `settings_usage.py`: есть только `BALANCE_SUMMARY_CACHE_TTL_SECONDS`, `:201`). `model_config` объявляет `extra="ignore"` (`:115`), поэтому и переменная окружения с этим именем не попадёт в объект: ключ нельзя задать ничем, а код читает его так, будто можно. Расходящиеся дефолты для существующих ключей: `ROUTING_PATH_FINDING_TIMEOUT_MS` — `50` в `app/core/payments/router.py:443`, `500` в `app/config.py:181` и `app/core/payments/service.py:1339`; `SIMULATOR_DB_ENABLED` — `False` в `app/core/simulator/storage.py:27`, `True` в `app/config.py:213`; `COMMIT_RETRY_ATTEMPTS` — `1` в `service.py:2259, :2784`, `3` в `config.py:190`; `INTEGRITY_CHECKPOINT_*` — `app/main.py:203-208, :256-258, :273`. Всего 38 сайтов (`git grep -n 'getattr(settings' -- app`).

Почему это важно. Дефолт в `getattr` недостижим (атрибут всегда есть), то есть это гард, проходящий вхолостую (§9 anti-vacuum), и одновременно ложная документация: читатель `router.py:443` думает, что таймаут поиска пути по умолчанию 50 мс. Фантомный ключ — прямое следствие: опечатка или несуществующее имя не ломает ничего и живёт годами.

Минимальное исправление. `settings.KEY` без `getattr` во всех 38 местах (типы у полей уже есть, `int(... or 0)` не нужен); `BALANCE_SUMMARY_CACHE_MAX_ENTRIES` либо завести в `Settings`, либо заменить константой модуля. Тест-гард формы: AST-скан `app/` на `getattr(settings, <str>, …)` — ноль; контрпроверка — подложенный фрагмент находится.

Что может сломаться. Тесты, которые ставят атрибуты на `settings` через `monkeypatch.setattr(settings, "X", …)`, продолжают работать; тесты, которые `delattr` — маловероятны, проверить `git grep -n "delattr(settings" tests`.

Covered-by: none. Contract: no.

### AL-3 — вторая поверхность конфигурации мимо `Settings`

Evidence. `app/api/v1/simulator.py:388-389`:
```python
def _actions_enabled() -> bool:
    return str(os.environ.get("SIMULATOR_ACTIONS_ENABLE", "") or "").strip() in {"1", "true", "TRUE", "yes"}
```
— продуктовый флаг Interact-режима (документирован в `docs/ru/simulator/backend/backend-driven-demo-mode-spec.md:106`, включается `scripts/run_full_stack.ps1:1364`), читается на каждый запрос из сырого окружения, мимо guardrail'ов `Settings`, мимо `/admin/config` и `/admin/feature-flags`. Тот же дефолт дважды: `app/core/simulator/artifacts.py:134` и `app/core/simulator/storage.py:107` — `os.getenv("SIMULATOR_ARTIFACT_SHA_MAX_BYTES", "524288")`. Версия приложения дважды: `app/main.py:473` и `app/api/v1/health.py:29` (`GEO_APP_VERSION`/`APP_VERSION`). Остальные: `run_lifecycle.py:188` (`SIMULATOR_REAL_MAX_IN_FLIGHT`), `runtime_impl.py:65` (`SIMULATOR_SCENARIO_ALLOWLIST`), `runtime_utils.py:56-117` (обобщённые читатели `os.getenv(name, …)`).

Почему это важно. `Settings` — единственная точка, где есть нормализация `ENV`, отказ на небезопасных секретах и фиксация `DATABASE_URL`; всё, что читается мимо неё, не проходит ни валидации, ни переписи (AL-2 census их не видит), ни админской поверхности. Продуктовый флаг, который нельзя увидеть через `/admin/feature-flags`, — рассогласование двух поверхностей одного факта.

Минимальное исправление. Перечисленные шесть имён — поля `Settings` с типами и дефолтами; `SIMULATOR_ACTIONS_ENABLE` — в `_runtime_config_items()` или feature-flags (это продуктовое решение владельца: делать ли Interact-режим переключаемым из админки; без него — просто поле `Settings`). Гард формы: AST-скан `app/` на `os.environ`/`os.getenv` вне `app/config.py` — ноль.

Что может сломаться / call-sites. `scripts/run_full_stack.ps1:1364` ставит переменную окружения — продолжит работать через `Settings`; тесты, патчащие `os.environ` для этих имён (`git grep -n "SIMULATOR_ACTIONS_ENABLE\|SIMULATOR_ARTIFACT_SHA_MAX_BYTES" tests`), надо перевести на `monkeypatch.setattr(settings, …)`.

Covered-by: none (021 удаляет 14 `SIMULATOR_CLEARING_ADAPTIVE_*`, спека `021:73`; про эти шесть в 021 ничего). Contract: no.

### AL-4 — логирование: канал один, конфигурации нет, request-id не доходит до лога

Evidence. `app/config.py:151` `LOG_LEVEL: str = "INFO"`; читатель один — `app/api/v1/admin.py:319`:
```python
def _runtime_config_items() -> list[tuple[str, bool]]:
    # (key, mutable)
    return [
        ("LOG_LEVEL", True),
```
Ни `basicConfig`, ни `dictConfig`, ни `setLevel`, ни `Formatter`/`Filter` в `app/` и `scripts/` нет (`git grep -n -E 'basicConfig|dictConfig|setLevel|logging\.(Filter|Formatter)' -- app scripts` — только `scripts/measure_p020_dfs_acceptance.py:308`); uvicorn запускается без `--log-level` (`Dockerfile:55`, `scripts/run_local.ps1:1375`). Request-id: middleware ставит contextvar и заголовок (`app/main.py:380-390`), но единственный потребитель — `app/utils/observability.py:21-25` (`log_duration`, 4 вызова); конверт ошибки `app/utils/exceptions.py:43-44`:
```python
def to_dict(self) -> dict:
    return {"error": {"code": self.code, "message": self.message, "details": self.details}}
```
— идентификатора нет. Аудит логина `app/api/v1/auth.py:177` `request_id=http_request.headers.get("X-Request-ID")` — сырой заголовок; `app/api/v1/admin.py:368-370` берёт `validate_request_id(request_id_var.get())`.

Почему это важно. §12: «каждая пользовательская ошибка несёт идентификатор для корреляции — чтобы сообщение из интерфейса можно было найти в логе». Сегодня найти нельзя: у записи лога нет rid, у ошибки — тоже; связь только через заголовок ответа, который клиент не показывает. `PATCH /admin/config {"LOG_LEVEL": "DEBUG"}` пишет аудит и меняет атрибут, который ничего не делает — ложная кнопка на операторской поверхности.

Минимальное исправление. Один модуль конфигурации логов (например, в `app/utils/observability.py`): `logging.Filter`, добавляющий `request_id` из contextvar, формат с `%(request_id)s`, уровень из `settings.LOG_LEVEL`, вызов из `lifespan`; в `to_dict()` — `request_id` в `details` или на верхнем уровне (`Contract: yes` для второго варианта — `ErrorEnvelope` в каноне; первый — совместимый). `auth.py:177` → `request_id_var.get()`. Либо, минимально-минимально: убрать `LOG_LEVEL` из `_runtime_config_items()`.

Что может сломаться. Контрактные тесты формы `ErrorEnvelope` (`tests/contract/test_p011_responses_conform_to_the_canon.py`) при изменении конверта; форматы логов, на которые опираются тесты (`git grep -n 'caplog' tests | wc -l`).

Covered-by: none. Contract: no (в варианте `details`).

### AL-5 — таблица `config` мертва, runtime-конфиг живёт в процессе

Evidence. `app/db/models/config.py:5-13` объявляет таблицу `config` (key/value/version/updated_by); читателей и писателей нет: `git grep -n -E "['\"]config['\"]" -- migrations scripts seeds app` даёт только `migrations/versions/001_initial_schema.py:296`, `004_db_schema_enhancements.py:96-107` и строку аудита `admin.py:485`. `/admin/config` и `/admin/feature-flags` мутируют объект `settings`: `app/api/v1/admin.py:470-474`:
```python
def _publish() -> None:
    try:
        for key, value in validated.items():
            setattr(settings, key, value)
```

Почему это важно. Оператор выключает `CLEARING_ENABLED` через админку, получает аудит-запись — после рестарта или на соседней реплике флаг снова `True`, и никакой поверхности, где это видно. Таблица, которая по имени обещает хранить это, пуста и не описана (§17 «зарегистрировано, но не описано»).

Минимальное исправление. Документировать, не патчить: датированная запись в `docs/ru/09-decisions-and-defaults.md` («runtime-конфиг — процессный, не персистентный; таблица `config` не используется»), либо отдельный cleanup-slice с миграцией drop (Contract: yes). Не строить персистентный конфиг без продуктового запроса (§19).

Covered-by: none. Contract: yes (для удаления таблицы).

### AL-6 — направление зависимостей: инверсии и ленивые импорты

Evidence (матрица `import_graph.py`, число import-операторов): `api→core 19, api→core.simulator 9, api→db 25, api→schemas 19, api→utils 22; core→db 52, core→utils 48, core→schemas 9, core→config 7; core.simulator→core 14, →db 47, →schemas 11; db→utils 2, db→config 1; utils→schemas 1; schemas→schemas 5`. Циклы: нет (и по top-level, и с ленивыми). Инверсии:
```
app/core/trustlines/service.py:22: from app.schemas.trustline import TrustLineCloseRequest, TrustLineCreateRequest, TrustLineUpdateRequest
app/core/balance/service.py:16:    from app.schemas.balance import BalanceSummary, BalanceEquivalent, DebtsDetails, OutgoingDebt, IncomingDebt
app/core/payments/router.py:17:    from app.schemas.payment import CapacityResponse, MaxFlowResponse, MaxFlowPath
app/utils/validation.py:6:         from app.schemas.equivalent import normalize_equivalent_metadata
app/db/models/equivalent.py:5:     from app.utils.validation import validate_equivalent_code, validate_equivalent_precision
```
То есть импорт ORM-модели `Equivalent` поднимает Pydantic-модуль `schemas/equivalent.py`. Ленивых импортов 56; ленивые внутри `app/api/deps.py:287` и `app/api/v1/simulator.py:117` (`app.core.simulator.session`) вынуждены `app/core/simulator/__init__.py:1` `from .runtime import SimulatorRuntime, runtime` — пакет тянет runtime→storage→`db.session` при любом импорте листа; `app/utils/money.py:3-7` описывает ту же причину. Остальные ленивые (`main.py` ×17, `payments/service.py` ×18 для `app.utils.metrics`) — стиль/защита try-except, не циклы.

Почему это важно. Не блокер: сервисы, принимающие DTO, — обычный компромисс маленького FastAPI-приложения, и `03-architecture.md:172` сам называет `schemas/` «API DTO». Вред конкретный и один: эйджерный `__init__` симулятора заставляет держать денежный рендерер в `utils` и импортировать `session.py` лениво в периметре аутентификации.

Минимальное исправление. `app/core/simulator/__init__.py` → пустой (все потребители `runtime` импортируют `app.core.simulator.runtime` явно: `git grep -n 'from app.core.simulator import' -- app tests scripts`). Инверсии `core→schemas` — документировать как принятое, не переносить. `db→utils.validation→schemas` — `validate_equivalent_code/precision` не нуждаются в `schemas.equivalent`; можно оставить и просто записать.

Covered-by: 021 частично (удаляет шимы `runtime.py`/`real_runner.py`, `021:26`, но `__init__` не называет). Contract: no.

### AL-7 — `utils/` как домен под другим именем

Evidence. `app/utils/money.py:3-12` (докстринг): «`app/utils/` imports no application package… The form needed a neutral home first, and this is it». `app/utils/validation.py:168-169`:
```python
MONEY_MAX_SCALE = 8
MONEY_MAX_INTEGER_DIGITS = 12
```
и `:348-421` — «THE ONE STORABILITY RULE», общая для `Book` и `MoneyNumeric`; `:553-632` — политика trustline (доменный словарь ключей `auto_clearing`, `daily_limit`…). `app/utils/error_codes.py:6-19` — коды протокола §9.4. `app/utils/exceptions.py:57-59`:
```python
class NotFoundException(GeoException):
    def __init__(self, message: str | None = None, *, details=None):
        super().__init__(message or "Not Found", code=ErrorCode.E001, details=details, status_code=404)
```
— HTTP-статус выбирается в `utils`, а `E001` по протоколу (`docs/ru/02-protocol-spec.md:1623`) значит «Маршрут не найден»; используется для `Participant … not found` (`admin.py:892`), `Equivalent … not found` (`:1190`). Ядро бросает HTTP-исключения напрямую: `payments/service.py` 20 сайтов, `trustlines/service.py` 19, и даже ставит статус руками — `payments/service.py:463-464` `status_code=500`/`400`, `simulator/metrics_bottlenecks.py:138` `status_code=503`.

Почему это важно. Читатель, ищущий «где денежная дверь», не заглянет в `utils`; и наоборот — «утилиты» на деле защищённый контракт (§8 денежная семантика). Перегрузка кодов (`E001` для любых 404, `E009` для 429/410 — `exceptions.py:85-92`) — wire-контракт, и он уже в каноне.

Минимальное исправление. Документировать, не патчить: абзац в `docs/ru/03-architecture.md` §2.2 («`utils/` — нейтральный по импортам дом денежной двери и кодов протокола; не переносить в `core/` пока жив эйджерный `__init__` симулятора, AL-6»); после AL-6 — перенос `money.py`+денежной части `validation.py` в `core/` отдельным срезом. Коды ошибок не менять (Contract: yes).

Covered-by: none. Contract: yes для кодов, no для переноса.

### AL-8 — health в двух экземплярах

Evidence. `app/main.py:465-474` и `app/api/v1/health.py:41-53` — `_START_TIME`, `_utc_now_iso`, `_best_effort_version` дословно; хендлеры `/health`, `/healthz`, `/health/db` (`main.py:487-550` ↔ `health.py:61-129`) — тот же код, включая один и тот же 14-строчный комментарий про `p011_t1105`. Различие одно: `main.py:497-502` не отдаёт `environment`, `health.py:71-77` — отдаёт. Оба зарегистрированы намеренно (решение `T1103b` 2026-08-23; `tests/contract/test_openapi_contract.py:1391-1405` требует и корневые, и `/api/v1/…`).

Почему это важно. Маршруты — контракт и остаются; дублируется реализация, и она уже разошлась (`environment`).

Минимальное исправление. `main.py` монтирует те же три функции из `health.py` (`app.add_api_route("/health", health.health_check, …)`), собственные копии удаляются. Маршруты, статусы, схема ответа не меняются.

Что может сломаться. `tests/contract/test_p011_root_routes_bypass_the_policy_gate.py` ищет маршруты по `route.path` и проверяет отсутствие `rate_limit` в зависимостях — при `add_api_route` без `dependencies` остаётся зелёным; `test_root_health_and_versioned_api_are_explicitly_classified` сравнивает нормализованные параметры/ответы — корневой `/health` начнёт отдавать `environment` (уточнить, считает ли тест это дрейфом; если да — обёртка без поля).

Covered-by: 016 Non-goal `spec.md:187` — про маршруты; про хендлер не говорит. Contract: no (при сохранении тел ответов).

### AL-9 — три копии разрешения участника и ручной вызов `require_admin`

Evidence. `app/api/deps.py:145-160` (`get_current_participant`), `:179-194` (`require_participant_or_admin`), `:318-334` (`require_simulator_actor`) — decode → `sub` → `select(Participant)` → `status != 'active'`. Семантика расходится: `:145-147` неверный токен → `UnauthorizedException`; `:318-319`:
```python
payload = await decode_token(token)
if payload:
    pid: str | None = payload.get("sub")
```
— неверный Bearer молча проваливается к cookie-актору (`:337-351`), то есть просроченный токен участника с валидной анонимной cookie превращается в `anon` без 401. `app/api/v1/health.py:141-145`:
```python
async def health_db_diagnostic(
    request: Request,
    x_admin_token: str = Header(alias="X-Admin-Token"),
):
    await deps.require_admin(request, x_admin_token)
```
— заголовок обязателен на уровне FastAPI, поэтому без него ответ 422 (RequestValidationError → `main.py:451`), а не 403, как на всех `Depends(deps.require_admin)`; dev-allowlist (`deps.py:210-215`) здесь недостижим. `websocket.py:33` — собственный decode без проверки `active`.

Почему это важно. Периметр — одна модель на бумаге (`deps.py`), четыре механики на практике; расхождение `:318-319` — не дефект по спеке §11 (приоритет описан в докстринге `:278-282`), но и не записано как решение.

Минимальное исправление. Приватный `_participant_from_token(db, token)` внутри `deps.py`, вызываемый из всех трёх; в `require_simulator_actor` — явное решение «плохой Bearer при наличии cookie = 401 или анон», записанное в докстринге. `health.py:143` → `Depends(deps.require_admin)` с `Header(default=None)`.

Что может сломаться. `_declare_auth_statuses` (`main.py:757-768`) перечисляет callables — новый приватный хелпер в закрытие не попадает, набор не меняется; `test_p011_reachable_statuses_are_declared.py` пересчитывает независимо. Для `/admin/health/db` меняется наблюдаемый статус 422→403 без заголовка — `Contract: yes` (канон `openapi.yaml:126`).

Covered-by: 016 Non-goal «auth priority chains» — про цепочку приоритетов, не про копию блока. Contract: частично.

### AL-10 — проза о SQLite после 017

Evidence. `app/db/types.py:113-117`:
```
    ON SQLITE, clause 3 can neither refuse nor accept anything: a bound `NaN` arrives as `NULL`, so
    the comparison is `NULL` and the CHECK passes (measured above). That tier's refusal is
    `MoneyNumeric`, not this clause. The clause is kept in the DDL of both dialects anyway, because
```
`app/db/models/debt.py:30-33`: «It is the only guard that can refuse a `NaN` on SQLite, where the driver turns one into `NULL`…»; `debt.py:50-54`; `app/db/models/equivalent.py:34-35`: «but NOT SQLite's `drop_all` over rows, which still fails with `FOREIGN KEY constraint failed` (measured…)»; `app/db/journal_tables.py:22-24`. Часть уже помечена историей (`types.py:18-19`, `journal_tables.py:288-299`), часть — нет. Остаток 017 в `specs/BACKLOG.md:1397` перечислял `admin.py`, `clearing/service.py`, `reconciliation.py`, `simulator_storage.py`, `schemas/equivalents.py:28`, `schemas/trustline.py:28` и закрыт (зачёркнут); эти четыре файла в нём не значились.

Почему это важно. Следующий редактор `finite_money_clauses` читает «kept in the DDL of both dialects» и ищет второй диалект; комментарий-обоснование становится ложным обоснованием (§15 «неверная посылка опаснее отсутствующей»).

Минимальное исправление. Одним срезом «датировать историю»: каждый абзац о SQLite либо помечается «HISTORY (до 017, 2026-09-24)», либо удаляется с указанием SHA. Код не меняется.

Covered-by: none. Contract: no.

### AL-11 — валидаторы «naive → UTC» — компенсация удалённого диалекта

Evidence. `app/schemas/trustline.py:25-29`:
```python
@field_validator("created_at", "updated_at")
@classmethod
def ensure_utc_for_naive_database_timestamp(cls, value: datetime) -> datetime:
    # A naive server timestamp is interpreted as UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
```
Ещё четыре копии: `equivalents.py:25-31`, `integrity.py:9-12` (+ три `field_validator` на неё), `admin.py:108-113`, `admin.py:158-165`. Все временные колонки в `app/db/models/*` — `DateTime(timezone=True)` (`participant.py:17-18`, `trustline.py:29-30`, `audit_log.py:10`, `integrity_checkpoint.py:13` …). Наивных конструкторов в приложении нет: `git grep -n -P 'datetime\.now\(\)|utcnow\(' -- app` — пусто. INFERENCE (не исполнялось на живой БД в этом ревью): asyncpg возвращает `timestamptz` как aware `datetime`, значит ветка `tzinfo is None` недостижима на единственном движке. Держит её `tests/unit/test_trustline_timestamps.py:11` (`treats_naive_database_value_as_utc`) — «живёт только ради теста». `specs/BACKLOG.md:81` (раздел «Узкие правки — не требуют спеки», «Participant timestamps без UTC-normalization») предлагает добавить такой же валидатор к participant-DTO — посылка «naive SQLite timestamp» после 017 не существует.

Почему это важно. Мелко, но это ровно тот случай, когда BACKLOG ведёт к расширению мёртвого механизма.

Минимальное исправление. Закрыть строку BACKLOG:81 записью «не применимо после 017»; валидаторы либо оставить как безвредные (тогда одна общая функция вместо пяти), либо снять вместе с тестом — второе требует подтверждения на живой Postgres, что все read-пути отдают aware (одним тестом на `created_at.tzinfo is not None` для каждого DTO).

Covered-by: BACKLOG (берёт перевёрнуто). Contract: no.

### AL-12 — археология в комментариях с устаревшими фактами

Evidence. `app/utils/validation.py:253-255` (докстринг `parse_amount_decimal`): «the default `max_scale` of 18 is wider than the `Numeric(20, 8)`» при `DEFAULT_MAX_AMOUNT_SCALE = 8` на `:136`. `:182-184`: «`Equivalent.precision` is declared `ge=0, le=18` (`app/schemas/equivalents.py:39`, `app/schemas/admin.py:218`)» при `le=8` на `equivalents.py:42` и `admin.py:223` (поправлено ниже на `:194-196`, но первая формулировка осталась). Плотность (tokenize): `validation.py` 330 строк комментариев/докстрингов на 197 строк кода; `db/types.py` 118 на 25; `utils/money.py` 78 на 22.

Почему это важно. Комментарий, который спорит сам с собой в пяти редакциях, не читают; а не читают — значит, не видят ту часть, что несущая (`MONEY_MAX_LEXICAL_SCALE = 18` держится ради legacy-строк, `:198-213`).

Минимальное исправление. Оставить в коде только текущее правило и одну ссылку на решение (спека/SHA), историю — в Changelog спек. Не патчить логику.

Covered-by: none. Contract: no.

### AL-13 — schemas: имена, мёртвые модели, три формы ребра, два типа денег

Evidence. `app/schemas/equivalent.py` (валидация metadata) и `app/schemas/equivalents.py` (DTO ответа) — различаются одной буквой. `app/schemas/common.py:46-52` `SignedRequest`, `PaginationParams` — 0 ссылок в `app/` и `tests/` (`git grep -w`); `SignedRequest` объявлен в каноне `api/openapi.yaml:4962`. Ребро цикла в трёх формах: `schemas/clearing.py:7-11` (`debt_id, debtor, creditor, amount: str`), `graph.py:76-80` (`equivalent, debtor, creditor, amount: Decimal`), `simulator.py:713-715` (`cleared_amount: str, edges`). Деньги: `trustline.py:18-20` `limit/used/available: Decimal`, `graph.py:29`, `metrics.py:13-19`, `admin.py:186-200` — `Decimal`; `balance.py:6-10`, `payment.py:8, 65`, `clearing.py:11`, `simulator.py` — `str`. (Pydantic v2 в `mode="json"` сериализует `Decimal` строкой, так что wire совпадает; расходится сгенерированная схема — `anyOf[number,string]` против `string` в каноне — это часть ратчетов `SUCCESS_SCHEMA_DRIFT_COUNT = 62`, `test_openapi_contract.py:314`.)

Минимальное исправление. Документировать; удаление `PaginationParams` — свободно, `SignedRequest` — вместе с каноном (Contract: yes). Переименование модулей — только при следующем касании.

Covered-by: none. Contract: частично.

### AL-14 — `03-architecture.md` §2.2–2.3 описывает несуществующее

Evidence. `docs/ru/03-architecture.md:150` `│   │       ├── router.py       # Main router` под `api/v1/` — реально `app/api/router.py:1-21`; в дереве нет `app/utils/` (11 модулей), `app/core/simulator/` (15 264 строки по `021:14`), `balance.py`, `equivalents.py`, `health.py`, `simulator.py`. §2.3: `AuthService.register/verify_signature/create_session` (`:178-206`) — реально `create_challenge/login/revoke_refresh_token/refresh_tokens` (`app/core/auth/service.py:18-119`); `RoutingService.find_paths/split_payment` (`:250-272`) — реально `PaymentRouter` (`app/core/payments/router.py:25`), `find_paths` удалён (BACKLOG «Мёртвые экспорты»); `ClearingEngine.process_triggered` (`:307-334`) — `ClearingService` без такого метода; `IntegrityChecker` с `handle_violation/save_checkpoint/run_full_check` (`:336-458`) — реально функции `compute_integrity_checkpoint_for_equivalent`, `compute_and_store_integrity_checkpoints` (`app/core/integrity.py:19, 136`), `InvariantChecker` (`invariants.py:16`) и сверка в `ledger/reconciliation.py`; «блокировка операций» реализована как integrity hold (`db/models/equivalent.py:17-24`); `TrustLineService.get_available_credit` (`:240-248`) — нет. Только `PaymentEngine` помечен историческим (`:274-276`, 2026-09-25).

Почему это важно. Документ сам объявляет себя vision (`:11-19`), но §2.2 — не vision, а «дерево проекта», и оно неверно о существующих файлах; §2.3 — единственное место, где новый читатель узнаёт имена сервисов, и все они не те.

Минимальное исправление. §2.2 — заменить на актуальное дерево (одна команда `tree app -L 2`); §2.3 — либо пометить весь раздел «историческая схема, актуальные имена: …» по образцу `PaymentEngine`, либо привести сигнатуры к коду. Ответ на вопрос 9 брифа: фактическая структура соответствует целевой по слоям (`api/core/db/schemas`), но документ не знает двух реальных слоёв (`utils`, `core/simulator`) и ни одного реального имени сервиса, кроме `TrustLineService`.

Covered-by: none. Contract: no.

### AL-15 — два канала к одному Redis

Evidence. `app/main.py:314-315`:
```python
app.state.redis = client
security.set_redis_client(client)
```
`app/utils/security.py:14-19` — модульный глобал `_redis_client`, читается `revoke_jti`/`is_jti_revoked` (`:43, :57`); `app/api/deps.py:120-122` `get_redis_client` читает `app.state.redis` для `payments.py:63`, `clearing.py:46`, `deps.rate_limit:63-65`.

Минимальное исправление. `security.py` берёт клиент параметром (у обоих вызывающих — `auth/service.py:117, :138` — есть доступ к request-scope) или `deps` отдаёт клиент из `security`. Одно место истины, без нового слоя.

Covered-by: none. Contract: no.

### AL-16 — метрика recovery считает целостность

Evidence. `app/main.py:158-165`:
```python
def _emit_integrity_metric(result: str) -> None:
    try:
        from app.utils.metrics import RECOVERY_EVENTS_TOTAL
        RECOVERY_EVENTS_TOTAL.labels(event="integrity_checkpoints", result=result).inc()
```
`app/utils/metrics.py:38-42` — `geo_recovery_events_total`, "Recovery/maintenance events". Recovery-цикл удалён 019 (`main.py:270-272`).

Минимальное исправление. Документировать в `docs/ru/09-decisions-and-defaults.md` («имя метрики историческое, семантика — maintenance») либо новая метрика `geo_integrity_events_total` с датой — второе меняет наблюдаемую поверхность (дашборды). Contract: yes (имя метрики).

Covered-by: none.

### AL-17 — `chk_transaction_state` хранит семь состояний без писателя

Evidence. `app/db/models/transaction.py:25`:
```python
CheckConstraint("state IN ('NEW', 'ROUTED', 'PREPARE_IN_PROGRESS', 'PREPARED', 'COMMITTED', 'ABORTED', 'PROPOSED', 'WAITING', 'REJECTED')", name='chk_transaction_state'),
```
Писатели в `app/`: `clearing/service.py:2088` (`state="NEW"`), платежи — только `COMMITTED`/`ABORTED` (ограда `:31`, миграция 030). `ROUTED/PREPARE_IN_PROGRESS/PREPARED/PROPOSED/WAITING/REJECTED` не пишет никто; `payments/service.py:884` ещё перечисляет `PREPARE_IN_PROGRESS` (зона другого агента).

Минимальное исправление. Документировать (список «зарегистрировано, но не описано» §17) — сужение CHECK требует миграции и решения о сетевом протоколе (`02-protocol-spec.md:962` описывает `NEW → PREPARED → …` для межхабового 2PC).

Covered-by: 019 (сознательно). Contract: yes.

### AL-18 — правило плоского конверта в трёх местах

Evidence. `app/main.py:439`:
```python
if path.startswith("/api/v1/simulator/runs/") and "/actions/" in path:
```
и `app/main.py:592-594` та же пара литералов; `app/api/v1/simulator.py:397-405` `_action_error` строит тот же `SimulatorActionError`. Контракт измерен и записан (`simulator.py:362-369`, 011/T1109), так что это не дефект контракта, а три копии одного предиката.

Минимальное исправление. Один предикат `is_simulator_action_path(path)` (или атрибут на роутере action-эндпоинтов, читаемый через `request.scope["route"]`), вызываемый из обоих мест `main.py`. Без изменения ответов.

Covered-by: none. Contract: no.

## Ответы на вопросы брифа (сжато)

1. **Направление зависимостей.** Циклов нет. `api→core→db` соблюдено; `schemas`/`utils` — листья, кроме `utils.validation→schemas.equivalent` и транзитивного `db→utils→schemas`. `core→schemas` — 20 модулей (сервисы работают на API-DTO). Ленивые импорты — 56; структурная причина одна (`core/simulator/__init__.py`), остальное стиль. AL-6.
2. **`main.py`.** Композиция ≈ 100 строк; всё остальное — супервизор (→ `utils/background_jobs.py`), планировщик целостности/сверки (→ `core/integrity.py`), копия health (→ `api/v1/health.py`), OpenAPI-постобработка (→ `api/openapi.py`). AL-1, AL-8, AL-16, AL-18.
3. **`config.py`.** Точка одна и с хорошими guardrail'ами; мимо неё 9 чтений `os.environ` (AL-3); 38 `getattr` с мёртвыми дефолтами и фантомный ключ (AL-2); не читается `LOG_LEVEL` (AL-4), `RECOVERY_*` (записано инертным, `config.py:168-175`, П4). Runtime-мутация `settings` не персистентна; таблица `config` мертва (AL-5).
4. **Периметр.** Четыре зависимости в одном файле — модель одна; блок разрешения участника скопирован трижды с одним расхождением семантики; один ручной вызов `require_admin`; WebSocket — свой decode. AL-9, AL-15.
5. **`schemas/` vs `openapi.yaml`.** Две копии: рукописный канон и сгенерированная схема, сведённые шестью ратчетами (`PARAMETER 22, TRANSPORT_HEADER 66, REQUEST 13, SUCCESS 62, ERROR_RESPONSE 51, SECURITY 66` — `test_openapi_contract.py:33-441`) и 345 строками постобработки в `main.py`. Дублирования `schemas`↔`core` нет — `core` использует `schemas` напрямую (AL-6); внутри `schemas` — AL-13.
6. **`utils/`.** Из 11 модулей общие — `request_id`, `observability`, `metrics`, `background_jobs`, `distributed_lock`; домен — `money`, `validation` (денежная дверь, политика trustline), `error_codes`, `exceptions` (HTTP+протокол); `event_bus` — доменная доставка событий WS; `security` — JWT+ревокация с глобальным Redis. AL-7, AL-15.
7. **`db/`.** Схема в трёх копиях сознательно и с тестом паритета: metadata (`models/*`, `journal_tables`, `reconciliation_tables`), DDL триггеров (`journal_triggers.py:25-29` «TWO COPIES, ON PURPOSE», `test_p018_b_schema_parity_postgres.py`) и миграции — не находка. Остатки 017: проза (AL-10), валидаторы naive→UTC (AL-11), состояния транзакций (AL-17). `SimulatorRunMetric.value` — `Numeric(20, 8)`, не `MoneyNumeric` (`simulator_storage.py:80`) — уже в BACKLOG («Переполнение NUMERIC(20,8) на total_debt»).
8. **Логирование и ошибки.** Канал один (stdlib, 15 логгеров), формата и уровня — нет; исключение→HTTP — один обработчик `GeoException` + один `RequestValidationError` с литералом пути + `_action_error` в симуляторе (записанный контракт 011/T1109). AL-4, AL-18.
9. **Соответствие `03-architecture.md`.** По слоям — да; по составу и именам — нет. AL-14.

## Что не проверено

- `app/api/v1/admin.py` и `app/api/v1/simulator.py` целиком (5 382 строки) — читал только якоря конфигурации, health, конвертов ошибок и `action_*`-описаний; внутренние дубли роутеров не искал (F-016-4 их частично покрывает).
- Внутренности `app/core/*` — только сигнатуры классов, импорты и точки чтения `settings`; выводы о ядре (число HTTP-исключений, ленивые импорты в `payments/service.py`) — статистика grep, не чтение.
- Утверждение AL-11 «asyncpg отдаёт aware datetime, ветка недостижима» — INFERENCE по семантике драйвера и отсутствию наивных конструкторов; на живой Postgres не исполнялось (ограничение «только чтение»).
- Тесты не запускались; `ruff` — только статистика по зоне.
- `migrations/` — только grep по `chk_transaction_state` и `config`; полного сравнения metadata↔миграций не делал (его делает `test_p018_b_schema_parity_postgres.py`).
- `docs/ru/config-reference.md` не читал — возможно, часть переменных из AL-3 там описана как «сырой env по дизайну».
- Динамические входы для «мёртвых» символов проверены `git grep -w` по `app/`, `tests/`, `scripts/`, `seeds/`, `migrations/`; `getattr`-по-строке для `PaginationParams`/`SignedRequest`/таблицы `config` не найдено, но `admin-ui`/`simulator-ui` не сканировал (для `SignedRequest` в каноне это могло бы быть значимо).

## Оценка направления текущего плана

**023 — берёт `main.py` не за тот конец.** Owner surface 023 (`spec.md:6`) добавляет «один новый контролируемый цикл рядом с `_integrity_loop`» и `app/config.py` — интервал раннера. Это третья копия шаблона «интервал → аренда → работа → метрика → состояние job'а» в точке входа, и F-023-6 сам называет копию цикла целостности проблемой. Предложение: срез (b)/(c) 023 начинать с переноса супервизора в `utils/background_jobs.py` и `_integrity_loop` в `core/integrity.py` (AL-1; ~120 строк перемещения, без новых сущностей), а раннер клиринга делать вторым клиентом одного супервизора. Плюс: новую настройку интервала читать `settings.X`, а не `getattr(settings, "X", …)` (AL-2), чтобы не расширять класс мёртвых дефолтов.

**021 — не полностью.** Симулятор как «клиент домена» останется вторым приложением, пока у него своя конфигурационная поверхность (6 сырых `os.environ`, AL-3) и эйджерный `__init__` (AL-6), который заставляет периметр (`deps.py:287`) и денежный рендерер (`utils/money.py`) обходить пакет. Оба — в owner surface 021 (`app/core/simulator/`), но спека их не называет; добавить `app/core/simulator/__init__.py` и перенос шести переменных в `Settings` в срез удаления шимов (`021:26`).

**016 — устарела до авторизации.** Owner surface (`spec.md:27-33`) и находки F-016-2/F-016-3/F-016-5 якорятся на `app/core/payments/engine.py` и `app/core/recovery.py`, которых нет после 019 (`ls app/core` — `payments/{router,service}.py`, `recovery.py` отсутствует). F-016-1 (тройная оценка инвариантов в `/verify`) и F-016-4 (двойной resolve фильтра) по-прежнему актуальны по именам файлов; F-016-5 (stale-политика) частично снята вместе с recovery. Перед любой авторизацией — перепроверка якорей на HEAD, иначе спека предлагает чинить удалённое. Non-goals 016 про health и auth chains верны для маршрутов, но не должны читаться как запрет на один хендлер/один хелпер (AL-8, AL-9).

**Чего нет в очереди, а должно быть — как узкие правки, не программы (§13):** гигиена конфигурации (AL-2, AL-3, AL-4 — три среза по полдня, каждый со своим AST-гардом формы); декомпозиция `main.py` (AL-1) — до 023; сметание остатков 017 в прозе и валидаторах (AL-10, AL-11) — вместе с закрытием BACKLOG:81. Ничего из этого не требует спеки: ожидаемое поведение очевидно, проверка — существующие тесты плюс гард формы.

**Что в очереди лишнее с точки зрения слоёв:** ничего. Программы 021/023 направлены на реальные границы (симулятор→домен, клиринг→поток). Единственное, что я бы не делал по итогам этого ревью, — новые слои: ни «service layer» между `api` и `core`, ни отдельный «domain model» вместо `schemas`-DTO (AL-6 документируется, не чинится).
