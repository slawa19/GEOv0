# Real Mode Runbook (Monorepo): как поднять окружение симулятора

Дата: 2026-01-28

Цель этого runbook — чтобы новый разработчик мог **быстро поднять backend + UI + зависимости** и начать проверять контракты Real Mode (scenarios/runs/events/snapshots/metrics).

Важно: в репозитории уже есть удобные скрипты запуска для Windows. С программы 017 backend работает только на PostgreSQL (Docker Compose или локальный Postgres); SQLite снят.

---

## 0) Что именно мы поднимаем

Минимальный набор для разработки:

- Backend (FastAPI, GEO Hub)
- Admin UI (Vue/Vite) — для админских экранов
- Simulator UI (Vite, `simulator-ui/v2`) — прототип интерфейса симулятора

Опционально:

- Postgres + Redis (через Docker Compose) — чтобы тестировать real‑semantics (таймауты, локи, конкурентность)

---

## 1) Быстрый старт (Windows, PowerShell): Backend + Admin UI

Рекомендованный путь: репо‑раннер скрипт.

Команда из корня репозитория:

- `./scripts/run_local.ps1 start`

Что делает `scripts/run_local.ps1`:

- Стартует backend на `http://127.0.0.1:18000` (по умолчанию)
- Стартует Admin UI на `http://localhost:5173/` (по умолчанию)
- Пишет `admin-ui/.env.local` с адресом backend-а (Admin UI всегда ходит в реальный backend; режима mock нет с 2026-10-07, 032 S4):
  - `VITE_API_BASE_URL=http://127.0.0.1:<backendPort>`
- Держит состояние в `.local-run/` (PID, логи)

Полезные команды:

- `./scripts/run_local.ps1 status`
- `./scripts/run_local.ps1 stop`
- `./scripts/run_local.ps1 restart-backend` (exact single-process ownership, без `--reload`)

Проверка, что backend жив:

- `GET http://127.0.0.1:18000/api/v1/health`
- Swagger: `http://127.0.0.1:18000/docs`

См. также (ops-навигация):

- `observability.md` — `/metrics`, request_id, метрики runner vs Prometheus
- `run-storage.md` — где хранится run state/metrics/bottlenecks и как устроены artifacts (export)

---

## 2) Данные для локальной разработки

`scripts/run_local.ps1` работает на собственной PostgreSQL-базе `geov0_dev_<DbSlug>`
(`geov0_dev_local` по умолчанию) на `127.0.0.1:5432`; нужен запущенный PostgreSQL
(`docker compose up -d db`). Свежая база лаунчера мигрируется и наполняется рецептом
сообщества через доменные сервисы. Прежние SQLite-файлы (`geov0.db`,
`.local-run/geov0.db`) — данные пользователя: лаунчер их не читает и не удаляет.

Чтобы **пересоздать** базу лаунчера и заново исполнить рецепт:

- `./scripts/run_local.ps1 reset-db` (сообщество `-SeedCommunity`, по умолчанию `riverside-town-50`;
  `greenfield-village-100` засевается с 2026-10-04, 028 `F-028-29`)

Проверка готовности (схема на head, популяция рецепта, baseline):

- `./scripts/run_local.ps1 check-db`

---

## 3) Запуск Simulator UI (Windows)

Simulator UI v2 — отдельный Vite‑прототип.

Из корня репозитория, против запущенного backend (Real Mode):

- `./scripts/run_simulator_ui.ps1 -Mode real -BackendOrigin http://127.0.0.1:<порт backend>` (порт `run_local.ps1` по умолчанию — 18000)

Без флагов скрипт стартует в `-Mode fixtures` — офлайн-снимок без backend (печатает `?mode=fixtures&debug=1`).

По умолчанию:

- порт: 5176 (`-Port`)
- host: 127.0.0.1 (`-HostName`)

Скрипт аккуратно обходит «зарезервированные» Windows порты (excludedportrange) и подбирает альтернативный, если надо.

Для Interact (действия участников) backend поднимайте через `./scripts/run_full_stack.ps1`, а не через `run_local.ps1`: только он выставляет `SIMULATOR_ACTIONS_ENABLE=1`, а также `SIMULATOR_MAX_ACTIVE_RUNS=10` и `SIMULATOR_REAL_AMOUNT_CAP=500`, если они не заданы в окружении процесса (блок `SIMULATOR_*` в `scripts/run_full_stack.ps1` перед запуском backend; значение из `.env` он при этом перекрывает). `run_local.ps1` их не выставляет: если они не заданы в shell или в `.env` (`app/config.py:128`, `env_file=".env"`), backend работает на умолчаниях `app/config.py` — действия выключены (`403 ACTIONS_DISABLED`), один активный прогон на процесс, без потолка суммы. `run_full_stack.ps1` сам поднимает и Simulator UI в режиме real на порту `-SimulatorUiPort` (5176 по умолчанию; адрес `http://localhost:5176/?mode=real` скрипт печатает в конце), поэтому `run_simulator_ui.ps1` после него не нужен: на том же порту он ответит «already running».

---

## 4) Real Mode семантика: Postgres + Redis через Docker Compose

Для более «реального» окружения поднимайте сервисы из `docker-compose.yml`:

- Postgres 16
- Redis 7
- app (FastAPI)

Локальный запуск с dev‑секретами и reload:

- `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`

Production-like запуск использует только base Compose, но лишь после копирования
`.env.example` в `.env` и заполнения `JWT_SECRET`, `ADMIN_TOKEN`,
`SIMULATOR_SESSION_SECRET` и `SIMULATOR_CSRF_ORIGIN_ALLOWLIST`:

- `docker compose up -d --build`

Порты по умолчанию:

- API: `http://localhost:8000`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

Можно переопределять host‑порты переменными окружения:

- `GEO_API_PORT` (по умолчанию 8000)
- `GEO_DB_PORT` (по умолчанию 5432)
- `GEO_REDIS_PORT` (по умолчанию 6379)

Проверки:

- `http://localhost:8000/health` (или `http://localhost:8000/api/v1/health`) и `http://localhost:8000/docs`
- `http://localhost:8000/metrics` (если `METRICS_ENABLED=true`)

Если нужна подробная инструкция для WSL2 без Docker Desktop — см. `docs/ru/runbook-dev-wsl2-docker-no-desktop.md`.

---

## 5) Как понять, что Real Mode симулятора работает

### 5.1 Базовые признаки «ок»

- Backend отвечает на `GET /api/v1/health`
- Swagger UI открывается (`/docs`)
- Admin UI смотрит на этот backend (в `admin-ui/.env.local` выставлен `VITE_API_BASE_URL=...`)

### 5.2 Симуляторный control plane (когда endpoints реализованы)

Ориентир по контракту — `api/openapi.yaml` и документы протокола.

Ключевые группы:

- Scenarios: `GET/POST /api/v1/simulator/scenarios` — после загрузки сценария перезагрузить страницу Simulator UI: каталог сценариев читается при загрузке страницы, кнопки его обновления в интерфейсе нет (слово владельца 2026-10-19, 048 S1).
- Runs: `POST /api/v1/simulator/runs` и `GET /api/v1/simulator/runs/{run_id}`
- Stream: `GET /api/v1/simulator/runs/{run_id}/events` (SSE)
- Snapshots/Metrics/Bottlenecks/Artifacts: соответствующие endpoints `.../snapshot`, `.../metrics`, `.../bottlenecks`, `.../artifacts`

Смысловая проверка для UI:

- В stream регулярно приходит `run_status` (heartbeat во время `running`)
- UI не вычисляет смысл `viz_*` — backend присылает готовые `viz_*` поля

---

## 6) Troubleshooting

### 6.1 Порты заняты

- Для Backend/Admin UI используйте `-AutoPorts`:
  - `./scripts/run_local.ps1 start -AutoPorts`
- Simulator UI сам подберёт порт, если 5176 занят или «excluded».

### 6.2 Нет `.venv` или Python зависимостей

`scripts/run_local.ps1` ожидает Python в `.venv`.

- Создать venv: `py -m venv .venv`
- Активировать: `./.venv/Scripts/Activate.ps1`
- Установить deps: `pip install -r requirements.txt -r requirements-dev.txt`

### 6.3 Admin UI не видит backend или получает 401/403

Режима mock у Admin UI больше нет (удалён 2026-10-07, 032 S4): он всегда обращается к реальному backend-у. Если экраны пусты или с ошибкой, проверьте `admin-ui/.env.local`:

- Должно быть `VITE_API_BASE_URL=http://127.0.0.1:<порт backend>`
- Токен — `VITE_ADMIN_TOKEN` (или localStorage `admin-ui.adminToken`); production-сборка без токена отказывает в admin-запросах с 401 `ADMIN_TOKEN_MISSING`
- База должна быть засеяна: `python scripts/seed_db.py --source recipe --community riverside-town-50`

Проще всего: запускать через `./scripts/run_local.ps1 start`.

### 6.4 Docker окружение не стартует

- `docker compose -f docker-compose.yml -f docker-compose.dev.yml ps`
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f app`
- Проверьте конфликты портов и healthcheck’и db/redis.

### 6.5 Симулятор: полезные env-параметры

SSE replay buffer (best-effort):
- `SIMULATOR_EVENT_BUFFER_SIZE` (по умолчанию 2000)
- `SIMULATOR_EVENT_BUFFER_TTL_SEC` (по умолчанию 600)
- `SIMULATOR_SSE_SUB_QUEUE_MAX` (по умолчанию 500)

`HTTP 410` для invalid/future/pruned/oversized `Last-Event-ID` включён всегда и
не имеет отдельного env-переключателя. После `410` или закрытия stream из-за
queue overflow клиент обязан переподключиться; snapshot refresh используется
для восстановления materialized UI state.

Real Mode guardrails:
- `SIMULATOR_REAL_MAX_IN_FLIGHT` (по умолчанию 1)
- `SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK` (по умолчанию 5)
- `SIMULATOR_REAL_MAX_ERRORS_TOTAL` (по умолчанию 200)
- ~~`SIMULATOR_CLEARING_MAX_DEPTH` (по умолчанию 6)~~ — не читается с 2026-09-28 (программа 023, срез (d)):
  клиринг тика идёт через общий раннер плана потока, глубины у исполнения нет.
- `SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS` по умолчанию `0`: неположительное значение отключает мягкий дедлайн,
  обычный проход доходит до результата общего раннера. Положительные миллисекунды явно включают прежний бюджет,
  проверяемый между циклами; это opt-in ограничение времени, а не финансовое правило.
- `SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC` — независимый аварийный таймаут, по умолчанию 60 с,
  с защитной нижней границей 0.1 с. `HARD` — целочисленный env-параметр: 0 и отрицательное значение теперь дают 0.1 с (раньше снимали потолок с производного лимита), тогда как неположительный `SOFT` отключает дедлайн; дробный env-ввод возвращает default, субсекундные значения в проверках — только внутренняя тестовая подмена. Он не вычисляется из мягкого бюджета. Порог запрашивает отмену,
  затем ожидается установленный исход начатого commit и завершение отменяемой задачи; это не точная wall-time гарантия.
- При отмене после commit сохраняются объём и известные изменения долга. Публикация DB-backed node/edge patches
  и закрытых линий получает отдельное ограниченное окно восстановления; новая операция клиринга или trust growth
  при этом не начинается. Если чтение для patches не удалось или отменено, остаётся summary с точным подтверждённым
  объёмом и диагностикой без выдуманного изменения долга. Полный результат нельзя заключать из такого summary.

Clearing policy:
- фиксированный cadence `SIMULATOR_CLEARING_EVERY_N_TICKS`; других расписаний нет. Кроме расписания есть один внеочередной проход — по стопору (054-A-7a): на третьем подряд тике, все платежи которого отвергнуты, тик сразу запускает обычный проход клиринга; его отключает только `CLEARING_ENABLED=false`, а `SIMULATOR_CLEARING_EVERY_N_TICKS=0` — нет. Подробно — `docs/ru/simulator/scenarios-and-engine.md`, «Стопор: все автоплатежи отвергнуты». `SIMULATOR_CLEARING_POLICY` и `SIMULATOR_CLEARING_ADAPTIVE_*` не читаются с 2026-09-28 (адаптивный режим удалён программой 021, стадия 3).

Real Mode: тюнинг дисковой нагрузки (артефакты) для dev/UI:
- `SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS` — как часто обновлять `last_tick.json` (по умолчанию `500`).
  - `<=0` отключает запись `last_tick.json` на каждом тике.
- `SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS` — как часто синхронизировать индекс артефактов в БД (по умолчанию `5000`).
  - `<=0` отключает `sync_artifacts` (уменьшает IO, но список артефактов может обновляться реже/только при finalize).

Где искать артефакты прогона локально:
- `.local-run/simulator/runs/<run_id>/artifacts/` (включая `events.ndjson`) — путь по умолчанию; с заданным
  `SIMULATOR_STATE_DIR` — `<SIMULATOR_STATE_DIR>/runs/<run_id>/artifacts/` (см. `run-storage.md`, раздел 4.2)

### 6.6 Публичное демо: лимиты, простой и потолок памяти (044 стадия 2, 2026-10-18; модель прогонов — стадия 3, 2026-10-19)

Якоря кода ниже — на `efc12f62` (`main`); числа замеров — из Changelog `specs/044-public-demo-per-visitor-runs/spec.md`: это замеры, а не свойства кода.

**Модель: у каждого посетителя свой прогон.**

- **Кто посетитель.** Анонимная cookie `geo_sim_sid` (`app/core/simulator/session.py:17`; выдаётся в `app/api/v1/simulator.py:170-181`, `Secure` — по `X-Forwarded-Proto`) даёт актёра `anon` со своим `owner_id` (`app/api/deps.py:327-331`). Прогон принадлежит этому владельцу; чужой прогон отвечает 403 на любое чтение и действие (`_check_run_access`, `simulator.py:563-574`; администратору доступны все), `GET /simulator/runs/active` отдаёт только прогон самого владельца (`simulator.py:2876-2900`). Присоединиться к чужому прогону нельзя — решение владельца 044.
- **Один активный прогон на владельца.** `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER` (по умолчанию `1`, `app/config.py:234`): второй `POST /simulator/runs` того же посетителя — 409 `details.conflict_kind=owner_active_exists` (`app/core/simulator/run_lifecycle.py:370-382`). Активные состояния — `running`, `paused`, `stopping` (`run_lifecycle.py:81`); остановка освобождает место сразу — `stop` снимает привязку владельца (`run_lifecycle.py:497-505`).
- **Общий предел `N`.** `SIMULATOR_MAX_ACTIVE_RUNS` (`app/config.py:246`) — число активных прогонов одного процесса; рантайм копирует значение при создании (`app/core/simulator/runtime_impl.py:94`), настройки читаются один раз при старте процесса. В коде по умолчанию `1` (консервативно); `scripts/run_full_stack.ps1:1380-1382` и оба Compose-файла (`docker-compose.yml:52`, `docker-compose.dev.yml:19`) ставят `10`, если значение не задано. `0` и меньше — предела нет (`run_lifecycle.py:115-117`). Замер (Changelog 044, 2026-10-18): 10 прогонов на темпе 15 на одном процессе — 67 % ядра, растяжение тика 1.28, ошибок 0 за 30 минут. Темп по умолчанию у посетителя (экран без админ-токена) — 5 (049 S2, было 15), у оператора — 30 (`simulator-ui/v2/src/composables/useInteractAutoBootstrapRun.ts:9-24`).
- **Три ступени темпа посетителя (049, решение 10).** В «Подробнее» посетитель видит только слова «медленно / обычно / быстро»; они ставят темп прогона 2 / 5 / 15 % (`simulator-ui/v2/src/components/TopBar.vue`, `TEMPO_STEPS`) тем же запросом, что поле процентов оператора с «Применить» (`POST /api/v1/simulator/runs/{run_id}/intensity`). Бюджет действий такта — `SIMULATOR_ACTIONS_PER_TICK_MAX` × темп (`app/config.py:244`, по умолчанию 20) при такте `SIMULATOR_TICK_MS_BASE` (по умолчанию 1000 мс): 0.4 / 1 / 3 **попытки** платежа за такт, то есть около одной попытки в 2.5 с, одной в секунду и трёх в секунду. Ступени задают плановый темп попыток, а не успешных платежей: часть попыток маршрутизация отклоняет за отсутствием маршрута (`tx.failed`; замер 2026-10-21, 049-11: ≈24 % на деревне из ста участников — 52 из 217 попыток за 6 мин, ≈2 % на «Демо клиринга», сети посетителя по умолчанию с 034 S9-A — 5 из 207 за 4 мин), поэтому на сети посетителя темп успешных платежей близок к обещанному, на деревне ниже на четверть. Живой тест (`simulator-ui/v2/e2e/live/autostart.live.ts`) считает `tx.updated` + `tx.failed`, то есть попытки, и сравнивает лишь «медленно» с «быстро»; дробный остаток копится между тактами (049 S2a, `app/core/simulator/real_payment_planner.py`). Разгон сценария (`settings.warmup`) масштабирует только часть темпа выше 15 % (`VISITOR_FAST_STEP_PERCENT`), поэтому три ступени держат свой темп с первого такта (сверх него — только окно открытия, такты 1..5, §4.2); темп оператора выше 15 % разгоняется на превышении (`runner-algorithm.md` §4.2). Темп, заданный оператором вне трёх ступеней, на экране посетителя ни одну ступень не отмечает.
- **Что видит посетитель, когда места кончились.** Бэкенд отвечает на `POST /simulator/runs` кодом 409: сообщение `Global active runs limit reached: all N places are taken` и `details = {conflict_kind: "global_active_limit", max_active_runs: N, active_runs: <занято>}` (`run_lifecycle.py:114-128`; сообщение и `details` держит `tests/unit/test_p044_s2_global_limit.py`). Экран выбирает текст по коду `conflict_kind`, а не по сообщению, и берёт число из `details.max_active_runs` (`simulator-ui/v2/src/utils/runErrorClassification.ts`, `startRunErrorText`, `isGlobalLimitRefusal`): «Сейчас заняты все 10 мест: демо смотрят 10 человек. Попробуйте через минуту.» (ключ `runStart.limitAll` в `simulator-ui/v2/src/i18n/ru.ts`; EN — в `en.ts`; без числа — «Демо уже кто-то смотрит на этом сервере; попробуйте позже.», ключ `runStart.limitBusy`). Рядом со строкой — кнопка «Повторить» (ключ `topBar.retry`; `startRetryOffered` в том же файле); повтор идёт по нажатию, автоматического повтора нет. Место освобождает остановка любого прогона, в том числе по простою (ниже).
- **Брошенный прогон останавливается сам** (единственная новая сущность 044). Прогон без SSE-подписчика дольше `SIMULATOR_RUN_IDLE_STOP_SEC` (по умолчанию `600` с, `0` — выключено; `app/config.py:268`, читается `runtime_impl.py:98`) останавливает собственный цикл прогона (`_heartbeat_loop`, `runtime_impl.py:733-752`; `_idle_expired_locked`, `:707-714`; `_stop_idle_run`, `:716-731`) вызовом `stop(source="server", reason="idle_no_subscribers")` (константа `IDLE_STOP_REASON`, `run_lifecycle.py:24`). Место освобождается; данные и артефакты остаются. Подробности:
  - **Отсчёт** — от ухода последнего подписчика или от входа прогона в `running` (создание, `resume`, `restart`), что позже (`note_subscribers_locked`, `app/core/simulator/models.py:293-297`; вызовы — `run_lifecycle.py:396, 439, 446, 605`, `sse_broadcast.py:192, 392, 419`). Достаточно одного подписчика, вкладок может быть несколько (`run._subs`, `runtime_impl.py:713`); переподключение SSE в пределах срока прогон не теряет. Часы монотонные (`runtime_impl.py:99`).
  - **Точность.** Проверка идёт раз за период цикла прогона — `settings.playback.tick_seconds` сценария (0.25–5 с), иначе 1 с (`runtime_impl.py:694-704`), плюс работа тика; остановка случается на первом проходе цикла после истечения срока, а не ровно в срок.
  - **Пауза прогон не держит**: проверка стоит в цикле раньше пропуска паузы (`runtime_impl.py:742-745`), прогон на паузе без подписчика тоже останавливается.
  - **Что видит посетитель, вернувшийся позже.** `GET /simulator/runs/{run_id}` отдаёт `state=stopped`, `stop_source=server`, `stop_reason=idle_no_subscribers` (`app/schemas/simulator.py:591-592`, `app/core/simulator/runtime_utils.py:116-117`). Экран пишет «Прогон остановлен сервером: его долго никто не смотрел. Чтобы запустить снова, перезагрузите страницу.», а в верхней полосе причина — «остановлен сервером: его никто не смотрел» (ключи `runLine.idleStopped` и `runStop.idle_no_subscribers` в `ru.ts`). Перезагрузка страницы — новый автостарт: найденный остановленный прогон заменяется новым (`autostartDecision` в `useInteractAutoBootstrapRun.ts`); привязка владельца при остановке снята, поэтому 409 `owner_active_exists` не мешает.
  - **Известная неполнота.** Подписка SSE, созданная до итерации ответа, остаётся, если клиент ушёл раньше старта генератора, — такой прогон не остановится по простою (BACKLOG 044-5; достижимость не проверена).
  - Тесты: `tests/unit/test_p044_s2_idle_stop.py` (управляемые часы, без `sleep`): остановка после срока, живой подписчик не останавливается, пауза останавливается, переподключение сбрасывает окно, закрытие по переполнению очереди запускает окно, `0` выключает.
- **Записи прогонов и диск.** `SIMULATOR_MAX_RUN_RECORDS` (по умолчанию 200, `app/config.py:247`) — порог чистки завершённых записей, а не жёсткая граница реестра: сверх порога удаляются старейшие завершённые, `stopped` и `error`; активные записи не удаляются, поэтому живых прогонов может быть больше порога (`run_lifecycle.py:148-182`; проходы — при создании, `:231`, и после остановки прогона). Это **только запись в памяти**: строки базы, деньги и каталог `runs/<run_id>/` на диске не трогаются (`run-storage.md`, раздел 4.4); для API удалённая запись — 404.
- **Изоляция данных прогонов.** Каждый прогон получает свежих участников под идентичностями `<run_id>:p<n>`, связанными один раз при создании (`app/core/simulator/run_perimeter.py:59-62, 76`; вызов — `run_lifecycle.py:289`); `restart` того же прогона ничего не перевязывает — те же участники и долги (`run_lifecycle.py:555-588`). Два посетителя одного сценария не делят строки участников, линий и долгов (`tests/integration/test_p044_s0_two_visitors_runs_are_isolated.py`). Для не-администратора сценарий вне допуска отвечает как неизвестный, 404 (`runtime_impl.py:341-351`); допуск — `SIMULATOR_SCENARIO_ALLOWLIST`: пусто — три демо-сценария (`greenfield-village-100-realistic-v2`, `riverside-town-50-realistic-v2`, `clearing-demo-10`), `*`/`all` — все (`runtime_impl.py:49-70`); загрузка сценария — только администратор (`simulator.py:2804-2816`).
- **Публичный сервер: что учесть оператору.**
  - Состояние прогонов живёт в памяти одного процесса (реестр `_runs`, привязки владельцев, подписчики — `runtime_impl.py`, `_SimulatorRuntimeBase`): предел `N`, проверка простоя и замер нагрузки относятся к одному процессу; образ запускает один `uvicorn` без `--workers` (`docker/Dockerfile:38`). Прогон одного процесса другому неизвестен (404), поэтому несколько процессов за одним адресом посетителей не делят.
  - Перезапуск процесса обрывает живые прогоны: при старте активные прогоны прежнего процесса помечаются `error` (`app/main.py:52-61`, `app/core/simulator/storage.py:569`); вернувшийся посетитель (cookie живёт `SIMULATOR_SESSION_TTL_SEC`, 7 дней, `app/config.py:232`) получает новый прогон.
  - Вне dev обязательны `SIMULATOR_SESSION_SECRET` и `SIMULATOR_CSRF_ORIGIN_ALLOWLIST` (`app/config.py:501-531`, `.env.example:33-36`); за прокси нужен заголовок `X-Forwarded-Proto`, иначе cookie выдаётся без `Secure`.
  - **Адрес клиента за прокси (054-C-4, 2026-10-30).** Корзина rate limit — `request.client.host` (`app/api/deps.py:66, 86`); тот же адрес пишут аудит (`app/api/audit.py:54`) и журнал входа (`app/api/v1/auth.py:33, 96`). Образ запускает `uvicorn` 0.27 без флагов прокси (`docker/Dockerfile:38`): заголовок `X-Forwarded-For` принимается только от адресов из `FORWARDED_ALLOW_IPS`, по умолчанию `127.0.0.1`. Поэтому за внешним прокси (другой контейнер или хост) `client.host` — адрес прокси, и **все посетители делят одну корзину** `RATE_LIMIT_REQUESTS_PER_WINDOW` — лимит срабатывает на всех сразу. Чтобы ключом был настоящий посетитель, задайте `FORWARDED_ALLOW_IPS` равным адресу (адресам) своего прокси — не `*`: при `*` uvicorn берёт крайний левый адрес заголовка — его пишет клиент, если прокси пропускает присланный клиентом `X-Forwarded-For` или uvicorn доступен напрямую, — и клиент обходит лимит подделкой. При явном списке uvicorn берёт крайний правый адрес заголовка, не входящий в список (`uvicorn/middleware/proxy_headers.py`, 0.27.0), поэтому прокси, дописывающий адрес подключившегося клиента к заголовку (nginx `$proxy_add_x_forwarded_for`), безопасен; в цепочке из нескольких прокси (CDN → nginx → приложение) перечислите в `FORWARDED_ALLOW_IPS` все, иначе ключом станет адрес внешнего прокси. Проверка — по журналу входа или аудиту: в поле адреса должны быть разные адреса посетителей, а не один адрес прокси. Код от этого не меняется: это настройка развёртывания (`/session/ensure` вне лимита по-прежнему, `deps.py:43-49`).
  - Подписки SSE ограничены отдельно: `SIMULATOR_SSE_MAX_CONNECTIONS` (50 на процесс) и `SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN` (10 на прогон), сверх — 429 (`app/config.py:263-264`, `sse_broadcast.py:342-355`). Подписка на прогон — расход этих пределов; при `N = 10` и одной подписке на прогон до 50 далеко.
  - База демо одноразовая (посылка (i) спеки 044): удаление записей прогонов участников, линии, долги, транзакции и журнал не освобождает; замена базы — решение оператора (следующий пункт).
- **Память процесса и замена базы.** Память процесса ≈ плато прогонов (≈145 МБ при одном, ≈234 МБ при десяти). До
  2026-10-19 к ней прибавлялось ~6.5 КБ × число операций в истории эквивалента: проверка целостности
  (`INTEGRITY_CHECKPOINT_*`) материализовала всю историю, и процесс держал высшую отметку. С `T4703` спеки
  [047](../../../../specs/047-integrity-checkpoint-memory/spec.md) она читает историю потоком и не материализует её
  целиком: на чистой истории пик её прогона от длины не зависит (≈2 МБ и при 1 000, и при 10 000 операциях; остаток
  зависит от числа рёбер, находок, порции и самой большой операции); прогон идёт при старте и раз в сутки (`T4701`),
  с историей растут его время и сортировка на сервере. Замер нагрузки 2026-10-19 (10 прогонов `clearing-demo-10`, темп
  15, окно 29.2 мин): ≈1 464 операции (≈2 909 записей журнала) в минуту в одном эквиваленте; ≈2.1 млн операций в сутки —
  экстраполяция этого окна, а не измеренные сутки. До `T4703` пик одной проверки дошёл бы до 1 ГБ примерно через 2 часа
  такой нагрузки. База демо по-прежнему одноразовая (посылка (i)
  спеки 044): растут диск (≈270 МБ базы за те же 30 минут) и время проверки; правило оператора — **следить за RSS процесса и размером базы и заменять
  базу**, когда проверка или диск становятся дорогими.

### 6.7 Где и что смотреть: прогоны, файлы прогона, аналитика (048 S2, 2026-10-19)

В интерфейсе демо этого нет — меню «Admin ▾» упразднено (слово владельца 2026-10-19: крайние случаи интерфейс не обслуживает, файлы и разбор прогонов админ поручает ИИ), и всё ниже читается через API; админский токен — в заголовке `X-Admin-Token` (значение `ADMIN_TOKEN` бэкенда; dev-сборка Simulator UI на localhost подставляет свой). **Все прогоны процесса** — `GET /api/v1/simulator/admin/runs` (фильтры `state`, `owner_id`, `limit`, `offset`; на прогон — `run_id`, `scenario_id`, `state`, владелец, темп, время старта и остановки, счётчики `attempts_total` / `committed_total` / `rejected_total` / `errors_total`, а с 2026-10-30 (054-A-7d) и `last_error` с `pause_reason` — почему прогон остановился или встал на паузу сам, см. 6.8); **остановить все активные** — `POST /api/v1/simulator/admin/runs/stop-all` (причина по умолчанию `admin_stop_all`), один прогон — `POST /api/v1/simulator/runs/{run_id}/stop`. **Файлы прогона** лежат в `<SIMULATOR_STATE_DIR>/runs/<run_id>/artifacts/` (по умолчанию `.local-run/simulator/runs/<run_id>/artifacts/`): `status.json` — паспорт прогона при создании (`run_id`, `scenario_id`, `mode`, `created_at`, `seed`), после остановки — его итоговый статус; `last_tick.json` — состояние последнего такта (пишется раз в `SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS`); `events.ndjson` — события потока прогона, по одному JSON в строке; `summary.json` (итог: сценарий, состояние, статус) и `bundle.zip` (все файлы одним архивом) — появляются после остановки. Инструментам их отдаёт API, пока прогон известен процессу: индекс с типом, размером и sha256 (sha256 — только для файлов не больше `SIMULATOR_ARTIFACT_SHA_MAX_BYTES`) — `GET /api/v1/simulator/runs/{run_id}/artifacts`, файл — `GET …/runs/{run_id}/artifacts/{name}`; после перезапуска процесса API прогон не знает (404), а файлы остаются на диске. **Метрики и узкие места** прогона (их показывает док аналитики, который в интерфейсе есть только с `?debug=1`) — `GET …/runs/{run_id}/metrics?equivalent=…&from_ms=…&to_ms=…&step_ms=…` и `GET …/runs/{run_id}/bottlenecks?equivalent=…`. Эндпоинты одного прогона доступны и его владельцу без админского токена (своя сессия); примеры запросов к эндпоинтам прогона — `api-examples.md`, раздел 5.

### 6.8 Где смотреть при сбое прогона (054-A-7d, 2026-10-30)

Отказ платежа — штатный исход сюжета (`tx.failed`, счётчик `rejected_total`), а не сбой. Сбой прогона — это `state = "error"`; отдельно от него прогон может сам встать на паузу (`state = "paused"`, `pause_reason = "scenario_exhausted"`).

**Статус.** `state`, `last_error` (`code`, `message`, `at`) и `pause_reason` одинаковы в трёх местах: событие SSE `run_status` (`GET /api/v1/simulator/runs/{run_id}/events`), `GET /api/v1/simulator/runs/{run_id}` и строка прогона в `GET /api/v1/simulator/admin/runs` (все прогоны процесса, админский токен). `last_error.message` публичный и без сырого текста исключения: у внутренних сбоев в нём идентификатор ошибки (`error id …`), по которому строка лога находится поиском.

**Строки лога** (ищите по `run_id=`; у `simulator.real.payment_internal_error` прогона в строке нет — её находят по `error_id` из `last_error.message` или `tx.failed`):

| Событие | Уровень | Что значит |
|---|---|---|
| `simulator.run.failed run_id=… code=… errors_total=…` | ERROR | прогон остановлен как аварийный; `code` = `last_error.code`. Одна строка на остановку |
| `simulator.real.tick_failed run_id=… tick=… error_id=… error_type=…` | WARNING | тик упал исключением (трейсбек здесь); `error_id` — тот же, что в `last_error.message` |
| `simulator.real.payment_internal_error error_id=… error_type=…` | WARNING | внутренний сбой платежа тика (трейсбек здесь); `error_id` — тот же, что в `tx.failed` и `last_error` |
| `simulator.real.payment_integrity_violation run_id=… tick=… tx_id=… invariant=…` | ERROR | ядро отказало в платеже из-за нарушенного инварианта |
| `simulator.real.money_phase_no_progress run_id=… conflict=… consec=… error_type=…` | WARNING | денежная фаза тика проиграла конкуренции и не закоммитилась |
| `simulator.real.run_refused code=… run_id=…` | WARNING | засев сценария отказал (`SIMULATOR_PID_TAKEN`, `SCENARIO_TRUSTLINE_REFUSED`) |
| `simulator.real.clearing_failed run_id=… eq=…` | WARNING | проход клиринга упал (трейсбек здесь); прогон продолжает работу |
| `simulator.heartbeat.failed run_id=… error_type=…` | ERROR | упала сама итерация heartbeat (трейсбек здесь) |
| `simulator.real.stall_clearing run_id=… consec_stall_ticks=…` | INFO | три тика подряд ни одного проведённого платежа и в каждом хотя бы один отказ по маршруту, ёмкости или политике линии (тики, где отказал только остановленный или удержанный эквивалент, не считаются) — запущен клиринг вне расписания |
| `simulator.run.scenario_exhausted run_id=… ticks=…` | WARNING | после этого клиринга снова всё отвергнуто — прогон встал на паузу |

**Файлы прогона.** `<SIMULATOR_STATE_DIR>/runs/<run_id>/artifacts/` (по умолчанию `.local-run/simulator/runs/<run_id>/artifacts/`, раздел 6.7): в `events.ndjson` — все события потока, в том числе каждый `tx.failed` с `error.code` и `error.details` и каждый `run_status`; после остановки — `status.json` с итоговым статусом и `last_error`.

**Коды `last_error`.** «Терминальный» — `resume` отвечает `409` (`details.reason = run_failed_terminal`), вернуть в работу только `restart`.

| Код | Состояние | Откуда | Терминальный для `resume` |
|---|---|---|---|
| `REAL_MODE_TOO_MANY_ERRORS` | `error` | бюджет ошибок прогона (`SIMULATOR_REAL_MAX_ERRORS_TOTAL`) | да |
| `REAL_MODE_TOO_MANY_TIMEOUTS` | `error` | таймауты платежей одного тика (`SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK`) | да |
| `REAL_MODE_TICK_FAILED_REPEATED` | `error` | тики падали подряд (`SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES`) | да |
| `REAL_MODE_MONEY_NO_PROGRESS` | `error` | денежная фаза не продвигалась подряд (`SIMULATOR_REAL_MAX_CONSEC_MONEY_NO_PROGRESS`) | да |
| `INTEGRITY_VIOLATION` | `error` | ядро отказало в платеже тика из-за нарушенного инварианта | да |
| `HEARTBEAT_FAILED` | `error` | упала итерация heartbeat | нет |
| `SIMULATOR_PID_TAKEN`, `SCENARIO_TRUSTLINE_REFUSED` | `error` | засев сценария отказал по названной причине | нет (`resume` засевает заново) |
| `REAL_MODE_TICK_FAILED` | `running` | один тик упал; при повторе подряд — `REAL_MODE_TICK_FAILED_REPEATED` | — |
| `REAL_MODE_MONEY_CONFLICT_UNRESOLVED` | `running` | одна денежная фаза проиграла конкуренции | — |
| `CLEARING_ERROR`, `CLEARING_REFUSED` | `running` | сбой или отказ прохода клиринга | — |
| `INTERNAL_ERROR`, `PAYMENT_TIMEOUT`, `SENDER_NOT_FOUND` | `running` | ошибка одного платежа тика (тот же код — в его `tx.failed`) | — |

Подробнее о кодах `tx.failed` и о стопоре — `docs/ru/simulator/scenarios-and-engine.md`, раздел 2.12.3 и «Стопор» в 2.4. Список терминальных кодов в коде — `RESUME_REFUSED_ERROR_CODES` (`app/core/simulator/run_lifecycle.py`).


---

## 7) Примечание про UX артефактов (важно для Real Mode)

Файлы прогона в интерфейсе демо не показываются (с 048 S2, 2026-10-19): их читают инструменты и ИИ через API (раздел 6.7), а не человек в браузере. Если показ когда-нибудь вернётся, браузер не открывает папку на диске — только ссылки на скачивание (`url` из индекса, `bundle_url`) или `artifact_path` как копируемая строка.
