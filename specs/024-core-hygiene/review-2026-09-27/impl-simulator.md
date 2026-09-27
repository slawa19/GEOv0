# impl-simulator — backend симулятора (что остаётся после 021 и чего 021 не видит)

HEAD: 4119ace (main, чистое дерево). Только чтение.

**Прочитано целиком:** `runtime_impl.py` 1015, `run_lifecycle.py` 479, `storage.py` 603, `sse_broadcast.py` 727, `money_replay.py` 707, `commit_resolution.py` 191, `artifacts.py` 370, `snapshot_builder.py` 424, `metrics_bottlenecks.py` 502, `viz_rules.py` 123, `viz_patch_helper.py` 308, `edge_patch_builder.py` 286, `scenario_registry.py` 270, `fixtures_runner.py` 142, `session.py` 88, `cache_invalidator.py` 111, `post_tick_audit.py` 267, `real_payment_planner.py` 728, `real_scenario_seeder.py` 234, `real_debt_snapshot_loader.py` 83, `runtime_utils.py` 192, `models.py` 258, `helpers.py` 37, `rejection_codes.py` 69, `run_perimeter.py` 30, `scenario_equivalent.py` 23, `real_payment_action.py` 12, `__init__.py` 3, `runtime.py` 15, `real_runner.py` 36; `app/api/v1/websocket.py` 96; `app/db/models/simulator_storage.py` 137; `app/api/v1/simulator.py` строки 1–976 и 2322–3053 (всё вне блока `action_*`); `app/schemas/simulator.py` 1–260 целиком, 260–818 по структуре (классы, конфиги, типы); `specs/021-simulator-as-domain-client/spec.md` (153) целиком.
**Прочитано частично (зона удаления 021, только ради «корректировок 021»):** `real_tick_orchestrator.py` (140–240, 391–400, 505–600, 640–726), `real_payments_executor.py` (170–200, 455–560), `net_balance_utils.py` (70–99, docstring по grep), блок `simulator.py:976-2322` (grep по вызовам сервисов), `app/core/payments/service.py:971-1200, 2211-2241` (сверка контрактов).
**Не прочитано:** `real_tick_clearing_coordinator.py` 583, `real_clearing_engine.py` 773, `adaptive_clearing_policy.py` 429, `trust_drift_engine.py` 588, `real_tick_trust_drift_coordinator.py` 125, `real_tick_persistence.py` 280, `real_tick_metrics.py` 137, `real_tick_payments_coordinator.py` 199, `inject_executor.py` 1161, `real_runner_impl.py` 1048. 021 удаляет или переписывает их целиком, поэтому по брифу находки там не нужны.

## Summary

После 021 в зоне остаются две части. Первая — рантайм рана: реестр в памяти, жизненный цикл, SSE, артефакты и запись в БД. Вторая — чтение для UI: снапшоты, патчи, метрики. Главный дефект здесь не денежный, а в **периметре рана**. Периметр строится по сценарию, а сценарий может загрузить анонимный посетитель. Он сам выбирает `scenario_id` (это путь на диске) и pid участников, в том числе настоящих. По этому периметру real-mode и Interact-платёж исполняют неподписанные операции (SIM-01, SIM-02). Это опровергает несущую посылку 021: «настоящий участник в симулированный ран не попадает (периметр рана)».

Второе — **жизненный цикл**. `restart`, а также `resume` из `error`, переводят ран в `running`, не создавая heartbeat-задачу: получается зомби (SIM-03, SIM-04). У «активного рана» два источника истины (SIM-05).

Третье — **запись в БД на три четверти никто не читает** (SIM-06). `simulator_runs` и `simulator_run_artifacts` не читает никто. Метрики читаются, только пока ран в памяти. Авточистки нет. Именно туда 021 собирается класть приватные ключи — это нужно развернуть.

Четвёртое — одно и то же число считается по-разному: нетто узла в снапшоте не совпадает с нетто в SSE-патче (SIM-10). Пост-тиковый аудит сравнивает выборки разного охвата и пишет ложные `IntegrityAuditLog` (SIM-09).

Что делать первым: SIM-01 (узкая правка, S) и решение по SIM-02 до авторизации 021. Затем SIM-03, SIM-04 и SIM-05 одним срезом жизненного цикла.

## Findings

| ID | Sev | Category | path:line | Одной строкой | Survives-021 | Covered-by | Effort |
|---|---|---|---|---|---|---|---|
| SIM-01 | P1 | logic-bug | `app/core/simulator/scenario_registry.py:206-227` | Анонимный `POST /simulator/scenarios`: у `scenario_id` нет шаблона, поэтому файл можно записать вне `.local-run` (`../`, абсолютный путь) и подменить пресет для всех пользователей | yes | none | S |
| SIM-02 | P1 | architecture | `real_scenario_seeder.py:97-133,172-234`; `simulator.py:1689`; `payments/service.py:984-989` | Периметр рана — это pid из сценария; симулированный участник от настоящего не отличается. Сидер и неподписанные платежи действуют от имени настоящих участников. Посылка 021 о хранении ключей ложна (корректировка 021) | partly | BACKLOG:T1547 (частично), 021 (неверно) | M |
| SIM-03 | P2 | logic-bug | `run_lifecycle.py:438-479`, `:292` | `restart` ставит `running`, но не создаёт heartbeat и writer событий, и ран не тикает. Состояние меняется до проверки конфликта | yes | none | S |
| SIM-04 | P2 | logic-bug | `run_lifecycle.py:324-339`; `runtime_impl.py:911-912` | `resume` из `error` даёт `running` без heartbeat. Получается зомби, и per-owner лимит блокирует владельца | yes | none | S |
| SIM-05 | P2 | responsibility | `runtime_impl.py:94,291-315`; `run_lifecycle.py:66-98,227-265`; `simulator.py:3034-3048` | «Активный ран» имеет два источника истины: карту owner→один run_id и скан state. При лимите >1 карта теряет раны, `fail_run` её не чистит, stop-all перетирает `error` на `stopped` | yes | none | S |
| SIM-06 | P2 | architecture | `storage.py:45-95,98-207,555-603`; `metrics_bottlenecks.py:157,342` | Записи в БД никто не читает: у `simulator_runs` и `simulator_run_artifacts` нет читателя, метрики доступны только при ране в памяти, ретенции нет. Корректировка 021: ключи рана не класть в `storage.py` и в БД | yes | 021 (неверно, по ключам) | M |
| SIM-07 | P2 | architecture | `money_replay.py:508-707`; `payments/service.py:1127-1130,2211-2230` | Пока тик — одна SERIALIZABLE-транзакция с N платежами, повтор всей фазы обязателен. 021 называет `money_replay` лишь «остатком неизвестного COMMIT»; развилку «фаза или `pay()` на платёж» надо решить явно (корректировка 021) | partly | 021 (неполно) | M |
| SIM-08 | P2 | duplication | `commit_resolution.py:127-182`; `money_replay.py:691`; `payments/service.py` `_settle_failed_commit` | Две политики для вопроса «что значит упавший COMMIT». Домен читает сохранённую строку, симулятор считает COMMIT откаченным, если прошёл `rollback()`, и публикует `tx.failed` для платежей, которые могли закоммититься. Ветки `terminal=False` мёртвые | partly | none | S |
| SIM-09 | P2 | logic-bug | `post_tick_audit.py:201-220` vs `real_debt_snapshot_loader.py:64-66`; `real_tick_orchestrator.py:573-600` | Аудит тика берёт «до» из снапшота рана (AND), а «после» — из выборки по OR. Отсюда ложный drift в `IntegrityAuditLog`. Модуль дублирует доменный `check_payment_delta`, а 021 молчит о его судьбе | partly | none | S |
| SIM-10 | P2 | duplication | `snapshot_builder.py:116-131,264-284` vs `viz_patch_helper.py:250-301` | Нетто узла считается по-разному: снапшот учитывает только долги внутри рана, SSE `node_patch` — все долги, как домен. UI получает два разных `net_balance` для одного узла | yes | 022 (частично: только viz-ключи, не нетто) | S |
| SIM-11 | P2 | docs-drift | `artifacts.py:182`; `schemas/simulator.py:814` | `GET /runs/{id}/artifacts` отдаёт абсолютный путь сервера (`artifact_path`) владельцу рана, в том числе анонимному. UI это поле не читают (§12) | yes | none | S |
| SIM-12 | P2 | responsibility | `runtime_impl.py:122,161-165`; `artifacts.py:122-177,297-311`; `storage.py:98-207` | Артефакты: TTL по умолчанию 0 и применяется только на старте, лимита числа нет, события отбрасываются молча. GET индекса пишет в БД delete+insert, дублируя diff-синхронизацию `storage.sync_artifacts` | yes | none | S |
| SIM-13 | P2 | logic-bug | `simulator.py:2555,2798,2465-2466`; `sse_broadcast.py:62-67,331-349` | Подписка создаётся в обработчике, а снимается только в `finally` генератора. Если клиент оборвал соединение до первого чанка, подписка утекает и занимает лимиты 10/50 (INFERENCE) | yes | none | S |
| SIM-14 | P2 | duplication | `runtime_impl.py:99-172`; `config.py:223` | 44 из 51 параметра `SIMULATOR_*` читаются через `os.getenv` мимо `Settings`, поэтому `.env` для них не работает. `settings.SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER` не читается, рантайм берёт сырой env | partly | none | S |
| SIM-15 | P2 | architecture | `simulator.py:146-970`, `:532-617`, `:620-639` | Owner surface 021 (`:972-2322`) не покрывает ~820 строк опор блока `action_*`: `_ensure_run_seeded` (HTTP-сидер), `_trustline_used_amount`, мутацию топологии, viz-патчи. `_require_run_accepts_actions_or_error` мёртв (корректировка 021) | partly | 021 (неполно) | S |
| SIM-16 | P3 | responsibility | `real_tick_orchestrator.py:690-694`; `simulator.py:109` | `last_error.message = str(e)` уходит владельцу рана без корреляционного id; логгер `uvicorn.error` вместо модульного (§12). Корректировка 021 для `tick.py` | partly | none | S |
| SIM-17 | P3 | dead-code | `runtime_impl.py:312-315,758-787,1003-1012`; `sse_broadcast.py:69-77,278-300,441-445`; `viz_rules.py:44-49,115-123`; `money_replay.py:568-573,646,680,696` | Мёртвый код, код только для тестов и диагностика, которую никто не читает: `subscribe`, `next_event_id`, `broadcast`, `count_active_runs`, `_db_enabled`, `_real_money_*`, `_real_total_debt_*` | partly | none | S |
| SIM-18 | P3 | readability | `models.py:121-258`; `real_payment_planner.py:251-728`; `snapshot_builder.py:76-324`; `rejection_codes.py:42-48`; `money_replay.py:87` | RunRecord на 70 полей; `plan_payments` на 478 строк (C901=93); `_check_run_access(runtime.get_run())` повторён 14 раз; код выводится из текста сообщения; ссылка на удалённый `PaymentEngine` | yes | none | M |
| SIM-19 | P3 | logic-bug | `snapshot_builder.py:173`; `edge_patch_builder.py:71`; `viz_patch_helper.py:54` | `int(precision or 2)` превращает точность `0` в `2`: атомы и строки эквивалента с `precision=0` получают неверный масштаб | yes | none | S |
| SIM-20 | P3 | logic-bug | `storage.py:464-480`; `storage.py:353-361` + `run_lifecycle.py:441` | Мелкое: узкое место с таймаутами <20 % маркируется `HIGH_USED/High utilization`; `restart` обнуляет `sim_time_ms`, и upsert метрик по `t_ms` перетирает точки до рестарта | yes | none | S |

## Детали

### SIM-01 — анонимная загрузка сценария пишет вне каталога и подменяет пресеты
Evidence:
```python
# app/core/simulator/scenario_registry.py:206-227
scenario_id = str(scenario.get("scenario_id") or "").strip()
...
base = self._local_state_dir / "scenarios" / scenario_id
path = base / "scenario.json"
if path.exists(): raise BadRequestException(...)
...
base.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(scenario, ...))
with self._lock:
    self._scenarios[scenario_id] = rec
```
В схеме (`fixtures/simulator/scenario.schema.json`, `properties.scenario_id`) — `{"type":"string","minLength":1}`, без `pattern`. Эндпоинт `POST /simulator/scenarios` (`simulator.py:2632-2638`) требует только `require_simulator_actor`. Cookie анонима выдаёт `POST /session/ensure` без аутентификации (`simulator.py:112-143`).

Почему это важно:
1. `scenario_id="../../x"` или абсолютный путь (на Windows `Path / "C:\\..."` заменяет базу) создаёт каталог и `scenario.json` вне `.local-run/simulator`. Это запрещено §7 и §12.
2. `scenario_id="clearing-demo-10"`: пресеты лежат в `fixtures/`, файла в `.local-run/.../scenarios/` нет, и проверка `path.exists()` проходит. Запись пресета в реестре **заменяется для всех пользователей** и переживает рестарт, потому что `load_uploaded_scenarios` идёт после фикстур (`:199-201`). В real-mode подменённый пресет засевает в общую БД чужих участников и линии (см. SIM-02).
3. Размер сценария не ограничен (`maxItems` нет).

Репродьюсер (5 строк): `c=TestClient(app); c.post("/api/v1/simulator/session/ensure"); r=c.post("/api/v1/simulator/scenarios", json={"scenario":{"schema_version":…,"scenario_id":"clearing-demo-10","participants":[…],"trustlines":[]}}); assert r.status_code==200; assert runtime.get_scenario("clearing-demo-10").source_path.parts[-3]=="scenarios"`.

Минимальное исправление:
- регэксп `^[a-z0-9][a-z0-9-]{0,63}$` на `scenario_id` в `save_uploaded_scenario` и в схеме;
- отказ, если id уже есть в `self._scenarios`;
- `path.resolve()` обязан лежать под `base_root.resolve()`.

Что может сломаться / call-sites: `save_uploaded_scenario` вызывается только из `runtime_impl.py:369-370`. `tests/integration/test_simulator_scenario_upload_validation.py` остаётся зелёным (там id вида `invalid-equivalent-…`).

Covered-by: none (008 `evidence-index.md:222` — только про отсутствие валидации при загрузке с диска). Contract: no (сужение входа, 400 уже объявлен).

### SIM-02 — периметр рана не отличает симулированного участника от настоящего
Evidence:
```python
# real_scenario_seeder.py:107-111 — существующий pid пропускается: строка НЕ создаётся, но дальше используется
have_p = {p.pid for p in existing_p}
for p in participants:
    pid = ...; if not pid or pid in have_p: continue
# :170-233 — линии строятся между ЛЮБЫМИ найденными pid, включая существующих
p_by_pid = {p.pid: p for p in p_rows} ... session.add(TrustLine(from_participant_id=p_from.id, ...))
```
```python
# app/core/payments/service.py:984-989
"""Internal-only payment path for the simulator runner.
IMPORTANT:
- This must never be exposed via HTTP endpoints.
- It bypasses signature verification ..."""
# app/api/v1/simulator.py:1689 (action_payment_real, HTTP)
res = await service.create_payment_internal(
```
Периметр — это узлы снапшота из `run._scenario_raw` (`simulator.py:642-679`, `run_perimeter.py:27-30`). Маркера «симулированный» у `Participant` нет. Режим `real` доступен любому актору: в `RunCreateRequest` (`schemas/simulator.py:379-384`) и в `start_run` (`simulator.py:2661-2674`) гейта нет. Роутер симулятора подключён безусловно (`app/api/router.py:20`).

Почему это важно: цепочка такая. SIM-01 позволяет загрузить сценарий с pid настоящих участников (pid публичны). Дальше запускается real-run:
- сидер создаёт между этими участниками `TrustLine` без подписи;
- тик и `action_payment_real` исполняют платежи от их имени без подписи (`require_signature=False`);
- сидер же создаёт в каталоге `Equivalent` с любым кодом (`:88-91`).

Это уже не «пишет в общую `debts` без opt-in» (`T1547`), а **действие от имени настоящего участника**. Для 021 это существенно. Раздел «Хранение ключей, сказанное вслух» (`021/spec.md:26`) обосновывает хранение ключей на хабе тем, что «настоящий участник в симулированный ран не попадает (периметр рана)». Сегодня это неверно. Кроме того, неподписанный платёжный путь симулятора останется и после 021: на подпись 021 переводит только линии.

Минимальное исправление (до или внутри 021):
- сидер **отказывает** в ране, если любой pid сценария уже существует и создан не симулятором. Маркер — `Participant.profile["simulated_run"]` или отдельный префикс pid, который выдаёт сидер;
- периметр рана — только участники, созданные сидером этого сценария;
- отдельно — решение владельца, доступен ли `mode="real"` анонимному актору.

Что может сломаться: повторный ран того же сценария. Сегодня участники штатно переиспользуются между ранами (`:97-111`). Это и есть продуктовая развилка `T1547`.

Covered-by: BACKLOG:`T1547` (частично: там «общая `debts`», а не имперсонация), 021 (берёт неверно: посылка про периметр). Contract: no (поведение сидера), но нужно решение владельца.

### SIM-03 — `restart` не перезапускает heartbeat
Evidence:
```python
# run_lifecycle.py:292 — единственное место создания задачи
run._heartbeat_task = asyncio.create_task(self._heartbeat_loop(run_id), ...)
# run_lifecycle.py:409-410 — stop() обнуляет её
run._heartbeat_task = None
# run_lifecycle.py:438-451 — restart() меняет только поля
run.state = "running"; run.started_at = self._utc_now()
```
`git grep _heartbeat_task -- app` находит создание только в `:292`. Тест `tests/unit/test_simulator_owner_isolation.py:528-554` закрепляет «stop → restart восстанавливает маппинг», но heartbeat не проверяет. Кроме того, `restart`:
- не вызывает `start_events_writer` (его останавливает `stop`, `:414`);
- не проверяет глобальный лимит (`_enforce_active_run_limit_locked`);
- выставляет `state="running"` **до** проверки конфликта владельца (`:450` → `:466-473`), так что при `ConflictException` ран остаётся «running».

Почему это важно: `POST /runs/{id}/restart` (OpenAPI `api/openapi.yaml:1749`) возвращает `running`, SSE показывает `running`, но тиков нет никогда. Per-owner лимит блокирует владельца до явного `stop`.

Минимальное исправление: в `restart` делать проверки до изменений. Если `_heartbeat_task` отсутствует или завершена — создать её и вызвать `start_events_writer` под тем же локом, что и в `create_run`.

Covered-by: none. Contract: no.

### SIM-04 — `resume` из `error` создаёт зомби
Evidence:
```python
# run_lifecycle.py:327-335
if run.state == "running": pass
elif run.state == "paused": run.state = "running"
elif run.state in ("stopping", "stopped"): pass
else: run.state = "running"          # "error" попадает сюда
# runtime_impl.py:911-912 — heartbeat уже вышел на error
if run.state in ("stopped", "stopping", "error"): return
```
Почему это важно: после `fail_run` задача рана отменена (`real_tick_orchestrator.py:166-201`). `POST /resume` делает такой ран `running` без задачи. Тогда `create_run` того же владельца отказывает с `owner_active_exists` (`run_lifecycle.py:266-278`): state `running` не считается stale.

Минимальное исправление: добавить `"error"` в no-op ветку `:331` (как сделано у `pause`, `:314`).

Covered-by: none. Contract: no.

### SIM-05 — два источника истины «активного рана»
Evidence:
- `runtime_impl.py:94`: `self._active_run_id_by_owner: dict[str, str] = {}` — один run на владельца;
- `run_lifecycle.py:66-68` считает активные раны по `state`;
- комментарий `:227-230`: «The active mapping is the primary source of truth… But if it points to a run that is no longer active… stale», а ниже — ветки, чинящие рассинхрон;
- `set_active_run_id` (`runtime_impl.py:291-294`) перезаписывает значение, если `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER>1`;
- `fail_run` (`real_tick_orchestrator.py:160-216`) маппинг не чистит;
- `admin_stop_all_runs` (`simulator.py:3034-3048`) идёт только по маппингу и зовёт `stop()`. Тот переводит `error`→`stopping`→`stopped` (`run_lifecycle.py:367-377`): терминальное `error` теряется, ран засчитывается в `stopped`.

Почему это важно: при лимите на владельца >1 первый ран невидим для `/runs/active`, `/graph/snapshot`, `/events` (все идут через `get_active_run_id`, `simulator.py:2480,2501,2527,2686`) и для stop-all. Код починки рассинхрона (`:231-264`) существует только из-за второго источника.

Минимальное исправление: выводить «активный ран владельца» из `self._runs` по `state ∈ {running,paused,stopping}` — функция уже есть (`_count_active_runs_for_owner_locked`), карту удалить. `stop()` не должен трогать `error`.

Covered-by: none. Contract: no.

### SIM-06 — запись рана в БД фактически никто не читает
Evidence (читатели таблиц, `git grep 'SimulatorRun\b\|SimulatorRunArtifact' -- app scripts`):
- `SimulatorRun` — только upsert (`storage.py:53`) и `reconcile_stale_runs` (`:578`);
- `SimulatorRunArtifact` — только синхронизация (`storage.py:141-197`, `artifacts.py:159-177`);
- оба читает лишь ручной `scripts/cleanup_simulator_runs.py`;
- метрики и узкие места читаются через `self._get_run(run_id)` (`metrics_bottlenecks.py:157,342`). Когда ран вытеснен из памяти (лимит 200 записей, `run_lifecycle.py:115-145`) или процесс перезапущен, данные в БД недостижимы (404);
- heartbeat делает `upsert_run` (`session.merge`) каждые 1–5 с на каждый ран (`runtime_impl.py:944-998`);
- ретенции нет; в модели TODO про FK «when runs management becomes DB-first» (`simulator_storage.py:67-68`);
- `reconcile_stale_runs` при старте помечает `error` все строки в `running` (`storage.py:570-586`). При двух процессах (второй воркер, rolling deploy) это портит состояние чужих живых ранов, а следующий upsert соседа вернёт `running` — состояние флаппит. Однопроцессность (`runtime_impl.py:87`: «in-memory and best-effort for MVP») нигде не записана как требование.

Почему это важно (§19.2 п.1): эти записи не предотвращают никакой наблюдаемой потери — ни API, ни UI их не читают. Цена — запись на каждый heartbeat, четыре таблицы без ретенции и восстановительный код под их же состояние.

**Корректировка 021.** 021 кладёт приватные ключи рана в `storage.py` «колонкой/JSON» (`021/spec.md:5,26,79`) и обещает «ключи удаляются вместе с раном». Но пути удаления рана из БД нет. Ран после рестарта не возобновляется: реестр в памяти пуст, `reconcile` переводит его в `error`. Значит, ключи в БД — чистая ответственность без пользы. Ключи должны жить в `RunRecord` в памяти и умирать вместе с ним.

Минимальное исправление:
- (а) для 021 — ключи только в памяти;
- (б) отдельно — решить судьбу `simulator_runs` и `simulator_run_artifacts` (удалить запись или дать им читателя). Метрики — либо читать без `_get_run`, либо чистить при вытеснении.

Covered-by: 021 (неверно — по ключам). Contract: no для (а); для (б) нужна миграция.

### SIM-07 — повтор фазы — не «остаток», а следствие дизайна «тик = одна транзакция»
Evidence:
```python
# app/core/payments/service.py:1127-1130 (execute)
# A transaction-level conflict (40001, 40P01, the book's `DebtVersionConflict`) is PROPAGATED as
# `RetryablePaymentConflictException`: a savepoint rollback does not refresh a SERIALIZABLE snapshot,
# so only the owner of the whole transaction can retry it - `pay()` for the API, the money-phase
# replay for the simulator.
```
`money_replay.run_money_phase_with_bounded_replay` вызывается только из `real_tick_orchestrator.py:326`. 021 (`spec.md:40`) описывает новый тик как «`PaymentService.execute` для каждого платежа (savepoint на операцию)». Это ровно сегодняшняя форма (`real_payments_executor.py:464-472`: `session.begin_nested()` + `create_payment_internal_staged`). При такой форме повтор всей фазы на 40001/40P01/`DebtVersionConflict`/23505 **обязателен**. Спека же упоминает `money_replay.py` только как «Разрешение неизвестного исхода COMMIT — остаток», и в Verification plan нет `tests/unit/test_p015_p1_money_conflict_predicate.py` и соседних тестов.

Почему это важно: без явного решения исполнитель 021 может «оставить только остаток» и вернуть дефект 015/P1 — конфликт снова означает потерянный тик и расход бюджета ошибок (`money_replay.py:3-11`).

Есть альтернатива, которую 021 не рассматривает: `PaymentService.pay()` на каждый платёж. У него своя транзакция, свой ретрай и своё разрешение неизвестного COMMIT (`service.py:2211-2241`). Тогда 707 строк `money_replay` и `commit_resolution` в тике не нужны вовсе. Теряется только атомарность «платёжная фаза + метрики», а это свойство аналитики, не денег (§19.2 п.1).

Минимальное исправление: записать выбор в 021. Вариант 1: «фаза + повтор» — перенести `money_replay` целиком, с перечнем тестов. Вариант 2: «`pay()` на платёж» — убрать `money_replay` и `commit_resolution` из тика, метрики писать после.

Covered-by: 021 (неполно). Contract: no.

### SIM-08 — две политики исхода COMMIT
Evidence:
```python
# commit_resolution.py:162-165
if not rollback_terminal: on_unknown()
elif rollback_error is None and not commit_cancelled:
    on_rollback()          # «commit упал, rollback прошёл» => откат
```
Домен в той же ситуации сначала читает сохранённую строку и только потом терминализирует (`PaymentService._settle_failed_commit`; `tests/unit/test_p015_t1523_the_commit_landed_then_the_caller_failed.py:18-24`: «the outcome of the commit is unknown to the caller, so the stored row is read»). В симуляторе `rolled_back` ведёт к `_resolve_non_replayed(phase, "rolled_back")` → `apply_rollback_observations()` (`money_replay.py:691,502-503`), то есть к публикации `tx.failed`.

Почему это важно (INFERENCE, нужен стенд): если соединение обрывается во время `COMMIT`, SQLAlchemy инвалидирует его, и последующий `session.rollback()` не бросает исключения. Исход классифицируется как «откат», хотя COMMIT мог примениться. Денег это не удваивает — не-конфликт не реплеится. Но UI и NDJSON получают `tx.failed` и счётчик `rejected` для закоммиченных платежей.

Кроме того, есть мёртвые ветки: `_drain_task` всегда возвращает `terminal=True` (`:39`), поэтому `:52-53`, `:78-79`, `:127-128`, `:162-163` недостижимы.

Минимальное исправление: ошибку COMMIT классифицировать как `unknown` — кроме SQLSTATE 40001/40P01, которые PostgreSQL откатывает сам. `money_replay` уже умеет проверить такой исход по `tx_id` (`_attempt_landed`, `:276-321`). Если 021 выберет `pay()` (SIM-07), модуль уходит из тика.

Covered-by: none. Contract: no.

### SIM-09 — пост-тиковый аудит с несогласованным охватом пишет ложные нарушения
Evidence:
```python
# real_debt_snapshot_loader.py:64-66 — «до»: оба конца в ране
Debt.debtor_id.in_(participant_uuids), Debt.creditor_id.in_(participant_uuids),
# post_tick_audit.py:213-216 — «после»: любой конец в ране
or_(debtor.pid.in_(pids), creditor.pid.in_(pids)),
```
При drift пишется `IntegrityAuditLog(operation_type="SIMULATOR_AUDIT_DRIFT", verification_passed=False, ...)` (`real_tick_orchestrator.py:573-600`) и уходит SSE `audit.drift`.

Почему это важно: возьмём участника рана, у которого есть долг перед не-участником (общая БД, `T1547`, или платёж по публичному API между снапшотом и аудитом). Для него `actual - expected` равно внешнему долгу, и в доменный журнал попадает ложное нарушение целостности. Этот журнал читают админские экраны целостности.

Сам инвариант уже проверяется точно внутри каждого платежа (`check_payment_delta`, docstring `service.py:1120-1121`) и сверкой 015. 021 модуль не упоминает: при удалении оркестратора он либо тихо умрёт, либо будет перенесён вместе с дефектом.

Минимальное исправление: в 021 удалить `post_tick_audit.py` и ветку `SIMULATOR_AUDIT_DRIFT` с записью в Changelog (§19.4: модуль дублирует доменный гейт). Если модуль оставлять — выборку «после» строить тем же AND.

Covered-by: none. Contract: yes (SSE `audit.drift` с `source="post_tick_audit"` перестанет приходить; форма события не меняется).

### SIM-10 — два разных `net_balance` одного узла
Evidence:
- снапшот: `snapshot_builder.py:124-127` (`Debt.creditor_id.in_(participant_ids), Debt.debtor_id.in_(participant_ids)`) → `net = credit - debit` в `:270-272`;
- SSE `node_patch`: `viz_patch_helper.py:252-262` (`Debt.creditor_id.in_(part_ids)` без условия на должника) → `:287`;
- домен: `balance/service.py:73`, «net_balance: total_credit - total_debt» по всем контрагентам;
- квантили в той же helper-функции (`:73-87`) — снова по AND.

Почему это важно: у участника с долгом вне рана UI показывает одно нетто после загрузки снапшота и другое после первого патча; `viz_color_key` и `viz_size` прыгают. 022 удаляет viz-модули, но `net_balance` в `node_patch` — часть SSE-контракта, и считать его кто-то должен будет и дальше.

Минимальное исправление: одна функция нетто, которую вызывают оба места. Охват («как домен» или «внутри рана») — решение.

Covered-by: 022 (частично — только viz-ключи). Contract: no (меняется значение, не форма).

### SIM-11 — абсолютный путь сервера в ответе API
Evidence: `artifacts.py:182` `artifact_path=str(base)`, где base = `repo_root()/.local-run/simulator/runs/<id>/artifacts` (`runtime_utils.py:22-29`). Схема: `schemas/simulator.py:814` `artifact_path: Optional[str]`. `git grep artifact_path -- simulator-ui/v2/src admin-ui/src` ничего не находит.

Почему это важно: §12 запрещает даже логировать абсолютные локальные пути, а здесь путь уходит наружу любому владельцу рана, включая анонимного.

Минимальное исправление: `artifact_path=None` (в OpenAPI поле nullable, `api/openapi.yaml:7507-7509`).

Covered-by: none. Contract: yes (меняется значение поля на проволоке).

### SIM-12 — артефакты без ретенции по §12 и два синхронизатора
Evidence:
- `runtime_impl.py:122`: `SIMULATOR_ARTIFACTS_TTL_HOURS` по умолчанию `0`, то есть чистка выключена;
- чистка вызывается только в конструкторе (`:161-165`), а не сразу после записи;
- лимита на число ранов на диске нет;
- `enqueue_event_artifact` при `QueueFull` делает `return` без счётчика и лога (`artifacts.py:307-311`), и `events.ndjson` молча оказывается неполным;
- `list_artifacts` (GET) делает `delete(...)` + `add_all` (`artifacts.py:159-177`), а `storage.sync_artifacts` (`storage.py:138-199`) делает diff-upsert той же таблицы. У одного индекса два писателя.

Почему это важно: на долгоживущем стенде `.local-run/simulator/runs/*` растёт без предела. Неполный NDJSON нельзя отличить от полного (§1: «отсутствующее измерение ≠ нулевое»).

Минимальное исправление:
- TTL и лимит числа — константами по умолчанию (например, 72 ч и 50 ранов) в одном месте, с вызовом после `finalize_run_artifacts`;
- счётчик отброшенных событий в `summary.json`;
- убрать запись в БД из GET.

Covered-by: none (в BACKLOG «Индекс артефактов…» — только про content type). Contract: no.

### SIM-13 — утечка SSE-подписки при обрыве до старта генератора
Evidence:
- подписка создаётся в обработчике до возврата ответа — `simulator.py:2555-2557`, `:2798-2800`;
- снимается она только в `finally` генератора `_run_events_stream` (`:2465-2466`);
- лимиты считают подписки **всех** ранов, включая остановленные (`sse_broadcast.py:62-67`, `:331-349`);
- сама подписка закрывается только при переполнении очереди (`:225-235`). На остановленном ране или ране на паузе переполнения не будет: heartbeat не публикует статус рана не в `running` (`runtime_impl.py:913-914`).

Почему это важно (INFERENCE о Starlette: если задача стрима отменена до первого `__anext__`, тело async-генератора не стартует и `finally` не выполняется): 10 обрывов на ран дают 429 владельцу, 50 в сумме — 429 всем.

Минимальное исправление: снимать подписку и при закрытии ответа (`BackgroundTask(runtime.unsubscribe, …)` на `StreamingResponse`). Это идемпотентно: `unsubscribe` уже терпит отсутствие подписки (`:406-409`).

Covered-by: none. Contract: no.

### SIM-14 — две системы конфигурации
Evidence: `git grep -ohE '"SIMULATOR_[A-Z_]+"' -- app/core/simulator app/api/v1/simulator.py | sort -u | wc -l` даёт 51; в `app/config.py` из них 7. `Settings` читает `.env` (`config.py:112`), а `os.getenv` — нет (`load_dotenv` в `app/` не вызывается). Поле `config.py:223` `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER: int = 1` никто не читает — рантайм берёт сырой env (`runtime_impl.py:108-110`).

Почему это важно: значение из `.env` для этого параметра (и для `SIMULATOR_ACTIONS_ENABLE`, `simulator.py:388-389`) молча игнорируется. `settings` показывает одно, а рантайм делает другое.

Минимальное исправление: для параметров, живых после 021, — либо убрать поле из `Settings`, либо читать из `settings`. 14 адаптивных параметров уходят вместе с 021.

Covered-by: none. Contract: no.

### SIM-15 — owner surface 021 не покрывает опоры блока `action_*`
Evidence: 021 владеет `simulator.py:972-2322` и `:1117,1359`. Вне этого диапазона лежат:
- `_ensure_run_seeded` (`:532-617`) — HTTP-путь к сидеру (`_real_scenario_seeder.seed_scenario_into_db` + `commit`), то есть ещё один писатель `TrustLine`/`Participant`/`Equivalent`;
- `_trustline_used_amount` и `_trustline_reverse_used_amount` (`:745-782`) — свои чтения `Debt`;
- `_mutate_runtime_trustline_topology_best_effort` (`:816-933`) — 118 строк мутации сценария в памяти, C901=20;
- `_compute_viz_patches_best_effort` и два эмиттера `clearing.done` (`:181-360`).

`_require_run_accepts_actions_or_error` (`:620-639`) не вызывается нигде: `git grep` в `app` и `tests` находит только определение и упоминания в комментариях (`:496,2020,2080`). Гард 021 (`spec.md:65`) сканирует только `Debt(` и `TrustLine(`, а сидер создаёт ещё `Participant(` и `Equivalent(` (`real_scenario_seeder.py:91,122`).

Минимальное исправление: расширить owner surface 021 до `:146-2322`; добавить `Participant(` и `Equivalent(` в гард или явно исключить с причиной.

Covered-by: 021 (неполно). Contract: no.

### SIM-16 — ошибка рана без корреляции
Evidence: `real_tick_orchestrator.py:690-694` пишет `run.last_error = {"code": "REAL_MODE_TICK_FAILED", "message": str(e), ...}`. Текст исключения (в нём могут быть SQL и параметры) уходит в `RunStatus` и `run_status` владельцу. Идентификатора, по которому строку можно найти в логе `simulator.real.tick_failed` (`:667-673`), нет. Кроме того, `simulator.py:109` использует `logger = logging.getLogger("uvicorn.error")`.

Минимальное исправление (для `tick.py` в 021): в `message` — класс ошибки и короткий id; тот же id — в строке лога.

Covered-by: none. Contract: no (`message` — просто строка).

### SIM-17 — мёртвое, тестовое и то, что никто не читает
Evidence (поиск `git grep -n <sym> -- app` и `-- tests`):
- `runtime_impl.count_active_runs` (`:312-315`) — 0 вызовов в app, 9 в тестах;
- `runtime_impl.subscribe` (`:758-787`) — 0 в app, 10 тестов; дублирует 30 строк `subscribe_with_status` (`:809-831`);
- `SseBroadcast.next_event_id` и `broadcast` (`sse_broadcast.py:69-77,278-300`) и ветка совместимости `_publish` (`:441-445`) — по docstring «for tests»;
- `SimulatorRuntime._db_enabled` (`runtime_impl.py:1010-1012`) — 0 вызовов; класс-наследник существует только ради него;
- `viz_rules.net_sign_from_atoms` (`:44-49`, дубль `net_balance_utils.atoms_to_net_sign`) и `collect_magnitudes` (`:115-123`) — 0 вызовов в app и tests;
- `_real_total_debt_by_eq` и `_real_total_debt_tick` пишутся (`real_tick_metrics.py:80-81`), но не читаются;
- `_real_money_conflicts_total`, `_replays_total`, `_replay_exhausted_total`, `_committed_payments_total` пишутся (`money_replay.py:568-573,646,680,696`), но не логируются и не отдаются наружу (`models.py:213-219`: «INTERNAL ON PURPOSE»). Их читают только тесты — по §12 «результат нельзя найти».

Минимальное исправление: удалить мёртвое; тестовые пути перевести на `subscribe_with_status` и `publish_event`; `_real_money_*` — одной строкой лога при остановке рана либо удалить.

Covered-by: none. Contract: no.

### SIM-18 — читаемость
Evidence:
- `RunRecord` (`models.py:121-258`) — около 70 полей четырёх ответственностей (статус, SSE, артефакты, real-mode), доступ через `getattr(run, "...", None)` по всему пакету;
- `plan_payments` (`real_payment_planner.py:251-728`, C901=93, 275 statements) переживает 021;
- `_enrich_snapshot_from_db` — C901=37;
- `_check_run_access(runtime.get_run(run_id), actor, run_id)` повторён в 14 обработчиках (`simulator.py:2709-2951`) — кандидат в FastAPI-зависимость;
- `rejection_codes.py:42-48` выводит код из подстрок сообщения (`"equivalent" in m`) — нарушение §9 «смысл из формы»;
- `money_replay.py:87` ссылается на удалённый в 019 `PaymentEngine._is_retryable_db_error`, и комментарий про «three retry sites» устарел: константа живёт только здесь.

Covered-by: none. Contract: no.

### SIM-19 — точность `0` превращается в `2`
Evidence: `snapshot_builder.py:173` `precision = int(getattr(eq, "precision", 2) or 2)`; то же в `edge_patch_builder.py:71`, `viz_patch_helper.py:54` (и в `inject_executor.py:420`, зона 021). Колонка `NOT NULL` (`app/db/models/equivalent.py:14`), а домен допускает `0` (`seeds/communities/community_schema.py:137`).

Почему это важно: для эквивалента с `precision=0` `net_balance_atoms` в 100 раз больше того, что UI ожидает при обратном переводе по своей точности (`NodeCardOverlay.vue:94-108`, ветка атомов). `net_balance` получает лишние «.00».

Минимальное исправление: `int(eq.precision)` без `or`.

Covered-by: none. Contract: no.

### SIM-20 — мелкие ошибки узких мест и рестарта
Evidence:
- `storage.py:464-480`: ребро с `timeouts>0`, `timeouts/attempts<0.2` и без `rejected`/`errors` получает `reason="HIGH_USED"`, `label="High utilization"`, хотя причина — таймауты;
- `run_lifecycle.py:441` обнуляет `sim_time_ms`, а ключ upsert метрик — `(run_id, eq, key, t_ms)` (`storage.py:353-361`). Точки после рестарта молча перетирают точки до него.

Covered-by: none. Contract: no.

## Что не проверено

- Файлы зоны удаления 021 (список в шапке) целиком не читались; находки в них не заявляются.
- Блок `action_*` (`simulator.py:976-2322`) — только grep по вызовам сервисов. Предусловия и их дубли с `trustlines/service.py` — поверхность 021.
- SIM-08 и SIM-13 опираются на выводы о поведении SQLAlchemy (rollback на инвалидированном соединении) и Starlette (отмена до первого `__anext__`), стендом не проверенные. Нужен репродьюсер на PostgreSQL и на `httpx` с ранним обрывом.
- SIM-02: не проверено, выставлен ли роутер симулятора наружу в продовой конфигурации (docker-compose, прокси). Вывод «достижимо из интернета» — INFERENCE.
- pytest, раннер и Playwright не запускались (ограничение брифа). Ruff запускался только на чтение (`--select ARG,PLR0915,C901`).
- `app/api/v1/websocket.py` не относится к симулятору (это event_bus участников); дефектов, относящихся к зоне, не найдено.
- Производительность SSE-буфера оценена по коду, но не измерена: `run_status` раз в секунду занимает буфер и при `SIMULATOR_SSE_SUB_QUEUE_MAX=500` сокращает окно replay примерно до 8 минут. В находки не вынесено.

## Оценка направления текущего плана

1. **Посылка 021 о периметре ложна** (SIM-02). Хранение приватных ключей на хабе обосновано тем, что настоящий участник в ран не попадает. Сегодня он попадает через сценарий, который может загрузить аноним (SIM-01), а платёжный путь симулятора и после 021 останется неподписанным. До авторизации 021 нужно решение владельца по двум вопросам: периметр рана — только участники, созданные сидером этого рана (с маркером); и кто может запускать `mode="real"`. Это же закрывает большую часть `T1547` без продуктового «opt-in».
2. **Ключи — в память, не в `storage.py`** (SIM-06). Раны не переживают рестарт, пути удаления в БД нет, поэтому обещание «ключи удаляются вместе с раном» для БД невыполнимо.
3. **Решить явно: «фаза + повтор» или `pay()` на платёж** (SIM-07, SIM-08). При выборе `pay()` из тика целиком уходят `money_replay.py` (707 строк) и `commit_resolution.py` (191). Теряется только атомарность «платежи + метрики», а это свойство аналитики (§19.2 п.1). Сейчас 021 одновременно обещает «тик ~300 строк» и оставляет механизм, который один больше двух таких тиков.
4. **Что добавить в owner surface 021:**
   - `simulator.py:146-970` (SIM-15);
   - `post_tick_audit.py` — на удаление (SIM-09);
   - `real_debt_snapshot_loader.py`, `cache_invalidator.py`, `run_perimeter.py` — все три обслуживают только оркестратор и инжект;
   - `rejection_codes.py` и шим `runtime._map_rejection_code`.
5. **После 021 — отдельная небольшая программа «рантайм рана»** (не часть 021: по §2 у неё независимая причина). В неё входят жизненный цикл (SIM-03, SIM-04, SIM-05), запись в БД и ретенция (SIM-06, SIM-12), утечка SSE-подписок (SIM-13) и конфигурация (SIM-14). Денег это не касается (P2), но пользователь видит эти дефекты сразу: «ран в running и стоит». SIM-01 и SIM-11 — узкие правки, их не надо ждать: в BACKLOG или сразу.
6. **022.** Удаляя `viz_patch_helper` и `edge_patch_builder`, не потерять `net_balance` в `node_patch` (это SSE-контракт) и при переносе выбрать один охват нетто (SIM-10). В `app/api/v1/admin.py:1693-1735,2061-2103` есть ещё две копии `debt_bin`/`scale_from_pct` — четвёртая и пятая.
