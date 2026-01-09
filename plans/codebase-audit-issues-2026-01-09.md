# Итог аудита: проблемы/несоответствия/риски/оптимизации + верификация последних доработок

**Дата:** 2026-01-09  
**Цель:** зафиксировать фактические проблемы/риски и готовность к реализации UI (PWA), без overengineering.  
**Ограничение:** этот файл — только аудит/верификация/процессные шаги; без предложений крупных переработок и без добавления новых фич.

---

## 1) Краткая сводка готовности к UI (PWA)

### Что по API уже выглядит готовым для UI (по коду/OpenAPI)
Ниже — пункты, которые закрывают базовые UI-потребности (логин/профиль/списки/пагинация/фильтры) и подтверждены наличием в коде и/или спецификации:

- Профиль текущего участника:
  - `GET /participants/me` + `PATCH /participants/me` и порядок роутов до `/{pid:path}` — [`app/api/v1/participants.py`](app/api/v1/participants.py:51)
- Поиск участников:
  - alias `GET /participants/search` — [`app/api/v1/participants.py`](app/api/v1/participants.py:30)
- Публичная статистика в `GET /participants/{pid}`:
  - `public_stats` добавлена/возвращается — [`app/core/participants/service.py`](app/core/participants/service.py:93)
- Аутентификация/челлендж:
  - challenge: 32 bytes base64url без padding — [`app/core/auth/service.py`](app/core/auth/service.py:31)
- Логин/refresh payloads:
  - `LoginRequest.device_info`, `TokenPair.expires_in` + `participant` — [`app/schemas/auth.py`](app/schemas/auth.py:20), [`app/core/auth/service.py`](app/core/auth/service.py:90)
- Trustlines (для UI списков/фильтров):
  - `status` filter + pagination + `from_display_name/to_display_name` — [`app/api/v1/trustlines.py`](app/api/v1/trustlines.py:29), [`app/core/trustlines/service.py`](app/core/trustlines/service.py:337), [`app/schemas/trustline.py`](app/schemas/trustline.py:15)
- OpenAPI обновлён под перечисленные изменения:
  - `/participants/me`, `/participants/search`, `TokenPair`, `DebtsDetails.incoming` — [`api/openapi.yaml`](api/openapi.yaml:190), [`api/openapi.yaml`](api/openapi.yaml:156), [`api/openapi.yaml`](api/openapi.yaml:888), [`api/openapi.yaml`](api/openapi.yaml:1387)
- Точечные backend-оптимизации/стандартизация:
  - миграции: `amount>0` и expression indexes — [`migrations/versions/010_debts_amount_gt_zero.py`](migrations/versions/010_debts_amount_gt_zero.py:17), [`migrations/versions/011_transactions_payment_payload_btree_indexes.py`](migrations/versions/011_transactions_payment_payload_btree_indexes.py:25)
  - LRU cache для balance summary — [`app/core/balance/service.py`](app/core/balance/service.py:47)
  - router prefixes balance/clearing унифицированы — [`app/api/router.py`](app/api/router.py:12)

### Что НЕ блокирует реализацию UI, но снижает «релизную уверенность»
- `device_info` уже принимается, и он используется как минимум для best-effort аудита: записывается в `audit_log` при успешном login (плюс логирование на уровне API) — [`login()`](app/api/v1/auth.py:25).

### Что является блокером релизной уверенности (верификация/тесты/окружение)
- На текущий момент не подтверждено состояние «pytest/contract tests green» и, как следствие, нет твёрдой верификации регрессий и синхронизации OpenAPI↔Code (см. CRIT-001 ниже).  
  На уровне репозитория видно, что тестовая обвязка требует dev-зависимостей, например `pytest_asyncio` — [`pytest_asyncio`](tests/conftest.py:4), и runtime-зависимостей типа `pydantic_settings` — [`pydantic_settings`](app/config.py:1).

---

## 2) Проверка корректности последних доработок из [`plans/codebase-audit-report-2026-01-09.md`](plans/codebase-audit-report-2026-01-09.md:1)

### Подтверждено по коду/spec (фактически внедрено)
- `GET /participants/me` + `PATCH /participants/me` + корректный порядок роутов до `/{pid:path}` — [`app/api/v1/participants.py`](app/api/v1/participants.py:51)
- alias `GET /participants/search` — [`app/api/v1/participants.py`](app/api/v1/participants.py:30)
- `public_stats` при получении участника — [`app/core/participants/service.py`](app/core/participants/service.py:93)
- Генерация challenge: 32 bytes base64url без padding — [`app/core/auth/service.py`](app/core/auth/service.py:31)
- `LoginRequest.device_info`, а также `TokenPair.expires_in` и `participant` — [`app/schemas/auth.py`](app/schemas/auth.py:20), [`app/core/auth/service.py`](app/core/auth/service.py:90)
- Trustlines: `status` filter + pagination + `from_display_name/to_display_name` — [`app/api/v1/trustlines.py`](app/api/v1/trustlines.py:29), [`app/core/trustlines/service.py`](app/core/trustlines/service.py:337), [`app/schemas/trustline.py`](app/schemas/trustline.py:15)
- OpenAPI обновлён под `/participants/me`, `/participants/search`, `TokenPair`, `DebtsDetails.incoming` — [`api/openapi.yaml`](api/openapi.yaml:190), [`api/openapi.yaml`](api/openapi.yaml:156), [`api/openapi.yaml`](api/openapi.yaml:888), [`api/openapi.yaml`](api/openapi.yaml:1387)
- Миграции: `amount>0` и expression indexes — [`migrations/versions/010_debts_amount_gt_zero.py`](migrations/versions/010_debts_amount_gt_zero.py:17), [`migrations/versions/011_transactions_payment_payload_btree_indexes.py`](migrations/versions/011_transactions_payment_payload_btree_indexes.py:25)
- LRU cache balance summary — [`app/core/balance/service.py`](app/core/balance/service.py:47)
- Унификация router prefixes — [`app/api/router.py`](app/api/router.py:12)

### Не подтверждено / частично подтверждено
- Утверждение «pytest проходит / contract test green / OpenAPI↔Code sync ✅» не подтверждено фактическим прогоном тестов (см. CRIT-001).  
  В репозитории присутствуют импорты dev/runtime зависимостей, из-за отсутствия которых тесты не стартуют:
  - `pytest_asyncio` — [`pytest_asyncio`](tests/conftest.py:4)
  - `pydantic_settings` — [`pydantic_settings`](app/config.py:1)
- `device_info` **не игнорируется на уровне API** (он прокидывается в сервис и логируется), а также фиксируется в `audit_log` при успешном login (best-effort):
  - прокидывание/логирование и best-effort audit запись — [`login()`](app/api/v1/auth.py:25)
  - сигнатура сервиса с параметром — [`AuthService.login()`](app/core/auth/service.py:48)
- Формулировка «убрать dead checkpoint_before logic» из предыдущего аудита некорректна: логика присутствует и выглядит используемой для аудита/интегрити — [`app/core/trustlines/service.py`](app/core/trustlines/service.py:74), [`app/core/clearing/service.py`](app/core/clearing/service.py:587), [`app/core/payments/engine.py`](app/core/payments/engine.py:679)
- Ужесточение `amount>0` и индексы зависят от применённости миграций (см. MED-003) — [`migrations/versions/010_debts_amount_gt_zero.py`](migrations/versions/010_debts_amount_gt_zero.py:17), [`migrations/versions/011_transactions_payment_payload_btree_indexes.py`](migrations/versions/011_transactions_payment_payload_btree_indexes.py:25)

---

## 3) Проблемы и риски (без overengineering)

### CRIT-001 — Верификация контракт/регрессий зависит от корректного окружения (закрыто в репозитории)
**Суть:** ранее утверждение «pytest/contract tests green» не подтверждалось из-за проблем окружения (missing deps, переполненный `%TEMP%`).
Сейчас в репозитории верификация подтверждена при корректной установке runtime+dev зависимостей и (при необходимости) переназначении `TEMP/TMP`.

**Факты в репозитории (зависимости, без которых тесты не стартуют):**
- `pytest_asyncio` импортируется в тестовой конфигурации — [`pytest_asyncio`](tests/conftest.py:4)
- `pydantic_settings` импортируется в конфигурации приложения — [`pydantic_settings`](app/config.py:1)

**Статус:** ✅ закрыто (локально прогоняется `python -m pytest -q`, включая `tests/contract/test_openapi_contract.py`).

---

### MED-002 — `device_info` принимаетcя; минимальная практическая семантика: best-effort аудит при login
**Суть:** контракт приёма `device_info` есть (схема и endpoint). На текущий момент ему задана минимальная практическая семантика: при успешном login `device_info` записывается в `audit_log` (best-effort), дополнительно к логированию на уровне API.
**Риск:** ограниченная полезность без дальнейшей аналитики/корреляции (нет гарантий доставки и нет отдельного query API под device fingerprint) — но это уже не «пустое поле».

**Факты:**
- поле присутствует в схеме — [`LoginRequest.device_info`](app/schemas/auth.py:24)
- поле прокидывается в сервис и логируется — [`login()`](app/api/v1/auth.py:25)
- поле фиксируется в `audit_log` при успешном login (best-effort) — [`login()`](app/api/v1/auth.py:25)

---

### MED-003 — Риск неприменённых миграций (валидация `amount>0`, индексы)
**Суть:** изменения на уровне миграций работают только при БД на head.  
**Риск:** если среда/инстанс БД не обновлён, то часть ожидаемых инвариантов/перформанс-ожиданий не выполняется (в частности, `amount>0` и индексы для payload).

**Факты:**
- миграция `amount>0` — [`010_debts_amount_gt_zero.py`](migrations/versions/010_debts_amount_gt_zero.py:17)
- миграция индексов — [`011_transactions_payment_payload_btree_indexes.py`](migrations/versions/011_transactions_payment_payload_btree_indexes.py:25)

---

### LOW-004 — Некорректная формулировка про «dead checkpoint_before logic» (замечание к прошлому отчёту)
**Суть:** «dead» как утверждение не подтверждается — логика присутствует в нескольких местах и выглядит частью механизма интегрити/аудита.  
**Риск:** минимальный (скорее риск неверного решения при дальнейших правках, если ориентироваться на неправильную формулировку).

**Факты:**
- [`app/core/trustlines/service.py`](app/core/trustlines/service.py:74)
- [`app/core/clearing/service.py`](app/core/clearing/service.py:587)
- [`app/core/payments/engine.py`](app/core/payments/engine.py:679)

---

## 4) Минимальные рекомендации (remediation)

### 4.1 Верификация (обязательный минимум перед UI-релизом)
1) Освободить место в окружении, устранить системную причину `[Errno 28] No space left on device` (иначе установка зависимостей и запуск тестов блокируются).  
2) Настроить окружение Python (venv) и поставить зависимости из:
   - [`requirements.txt`](requirements.txt)
   - [`requirements-dev.txt`](requirements-dev.txt)
3) Запустить тесты:
   - `pytest -q` (быстрый smoke всей матрицы)
4) Запустить отдельно контрактный тест OpenAPI↔Code:
   - файл контракта присутствует — [`tests/contract/test_openapi_contract.py`](tests/contract/test_openapi_contract.py:1)

---

## 5) TODO (статусы)

CRIT:
- [x] CRIT-ADMIN — Минимальный `/admin/*` слой добавлен в код и контракт (защита через `X-Admin-Token`)
- [x] CRIT-PWA-ENVELOPE — убрать рассинхрон про `{success,data}` envelope в PWA/Admin UI спеках (приведено к plain JSON)
- [x] CRIT-PWA-PARTICIPANTS — поддержать `page/per_page` для `/participants/search` и отразить в OpenAPI
- [x] CRIT-HEALTH-API-BASE — добавить health алиасы под `/api/v1/*` и отразить в OpenAPI

MED:
- [x] MED-DEVICE-INFO — `device_info` фиксируется в `audit_log` при login (best-effort)
- [x] MED-MIGRATIONS — добавлен `/admin/migrations` для проверки current vs head

LOW:
- [x] LOW-DOCS — UI спеки синхронизированы с `api/openapi.yaml` и форматом ответов

### 4.2 Точечные уточнения по `device_info` (без добавления новой фичи)
- Зафиксировать ожидаемую минимальную семантику: `device_info` участвует в best-effort аудите при login (пишется в `audit_log`), без гарантий и без отдельной аналитики.  
  Реализация: запись в `audit_log` делается в API-слое при успешном login — [`login()`](app/api/v1/auth.py:25).

### 4.3 Миграции
- Для целевых окружений убедиться, что БД применена до head (иначе ожидания `amount>0`/индексов не гарантируются) — [`010_debts_amount_gt_zero.py`](migrations/versions/010_debts_amount_gt_zero.py:17), [`011_transactions_payment_payload_btree_indexes.py`](migrations/versions/011_transactions_payment_payload_btree_indexes.py:25).

---

## Примечание о предыдущем отчёте
Предыдущий отчёт [`plans/codebase-audit-report-2026-01-09.md`](plans/codebase-audit-report-2026-01-09.md:1) содержит исторические CRIT/MED формулировки. Часть из них на текущий момент закрыта (см. раздел 2), но утверждения уровня «всё зелёное» требуют подтверждения реальным прогоном тестов (см. CRIT-001).