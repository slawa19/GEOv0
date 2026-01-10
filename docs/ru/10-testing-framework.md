# GEO Hub v0.1 — Testing Framework Spec (pytest e2e + artifacts + domain events)

**Версия:** 0.1  
**Статус:** draft (фиксирует принятые решения команды)  
**Цель:** определить минимально достаточную спецификацию, чтобы:
- вручную и автоматически прогонять сценарии MVP (TS-01…TS-23);
- получать трассируемые, воспроизводимые артефакты прогонов;
- иметь человекочитаемые логи событий и возможность расследования через SSR админку.

Связанные документы:
- Канонический API контракт: [`docs/ru/04-api-reference.md`](docs/ru/04-api-reference.md:1)
- Сценарии для e2e: [`docs/ru/08-test-scenarios.md`](docs/ru/08-test-scenarios.md:1)
- Минимальная админка: [`docs/ru/admin/specs/admin-console-minimal-spec.md`](docs/ru/admin/specs/admin-console-minimal-spec.md:1)
- Реестр конфигурации (runtime vs restart): [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)
- OpenAPI (должен быть согласован с каноном): [`api/openapi.yaml`](api/openapi.yaml:1)

---

## 0. Как запустить тесты локально (venv + команды)

Проект использует виртуальное окружение Python (`.venv`) и зависимости из [`requirements.txt`](../../requirements.txt) и [`requirements-dev.txt`](../../requirements-dev.txt).
На Windows **не полагайтесь на `pytest` в PATH**: используйте `python -m pytest`, чтобы гарантировать запуск под правильным интерпретатором.

### 0.1. Windows PowerShell

```powershell
py -m venv .venv
& .\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt

# Все тесты (включая OpenAPI contract test)
python -m pytest -q

# Только OpenAPI contract test
python -m pytest -q tests/contract/test_openapi_contract.py
```

### 0.2. Windows CMD

```bat
py -m venv .venv
call .\.venv\Scripts\activate.bat

python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

### 0.3. Типичные проблемы

- `ModuleNotFoundError: No module named 'pytest_asyncio'` → dev-зависимости не установлены в текущий интерпретатор:
  - `python -m pip install -r requirements-dev.txt`
  - проверка: `python -m pip show pytest-asyncio`
- `ensurepip ... returned non-zero exit status 1` при `py -m venv .venv`:
  - удалите venv и повторите: `rmdir /s /q .venv` (CMD) или `Remove-Item -Recurse -Force .venv` (PowerShell)
  - восстановите pip в базовом Python: `py -m ensurepip --upgrade`
  - обходной путь: `py -m pip install virtualenv`, затем `py -m virtualenv .venv`
- `OSError: [Errno 28] No space left on device` при установке зависимостей:
  - проверьте, куда указывает `%TEMP%`; временно перенаправьте temp на диск с местом:
    - CMD: `set TEMP=D:\Temp` и `set TMP=D:\Temp`
    - PowerShell: `$env:TEMP='D:\Temp'; $env:TMP='D:\Temp'`
- Проверить используемый интерпретатор:
  - `python -c "import sys; print(sys.executable)"`

---

## 1. Канонический контракт API (source of truth)

### 1.1. Канон
Канонический контракт API = [`docs/ru/04-api-reference.md`](docs/ru/04-api-reference.md:1).

### 1.2. Base URL
Base URL = `/api/v1` (см. [`docs/ru/04-api-reference.md`](docs/ru/04-api-reference.md:23)).

### 1.3. Формат ответа (v0.1)

В v0.1 успешные ответы обычно возвращаются как **plain JSON** (без envelope `{success,data}`).
List-эндпоинты, как правило, возвращают объект вида `{ "items": [...] }`.

**Ошибка (единый формат):**
```json
{
  "error": {
    "code": "E002",
    "message": "Insufficient capacity",
    "details": {}
  }
}
```

См. [`docs/ru/04-api-reference.md`](docs/ru/04-api-reference.md:37).

### 1.4. Swagger UI / ReDoc
- `/docs`, `/redoc` — см. [`docs/ru/04-api-reference.md`](docs/ru/04-api-reference.md:782)
- OpenAPI YAML: [`api/openapi.yaml`](api/openapi.yaml:1)

---

## 2. Решение по тестированию (MVP)

**Принято:**
- SSR админка (внутри backend) для операторских задач.
- pytest e2e — основной “scenario runner”.
- DEV/TEST-only endpoints `/api/v1/_test/*` для ускорения setup/teardown.
- Доменные события храним:
  - в БД (`event_log`) — для запросов админки, расследования и надёжности;
  - и экспортируем в JSONL как артефакты прогона.

**Ключевое правило:** test-only endpoints используются для **setup/teardown и диагностических snapshot**, но основное действие (например `POST /payments`) по возможности тестируется через публичное API.

---

## 3. Корреляция (обязательные идентификаторы)

### 3.1. Что коррелируем
Чтобы связать:
- HTTP запросы/ответы,
- доменные транзакции (`tx_id`),
- события в БД,
- JSONL в артефактах,
- экраны SSR админки,

используем 4 идентификатора:

- `run_id` — UUID прогона (pytest session)
- `scenario_id` — идентификатор сценария (например `TS-12`)
- `request_id` — UUID на каждый HTTP запрос
- `tx_id` — UUID доменной транзакции (если применимо)

### 3.2. Заголовки
Ввести заголовки (normative):

- `X-Run-ID: <uuid>` (опционально, но обязателен для e2e)
- `X-Scenario-ID: <string>` (опционально, но обязателен для e2e)
- `X-Request-ID: <uuid>` (если отсутствует — сервер генерирует и возвращает)

Рекомендация: сервер возвращает `X-Request-ID` в response headers всегда (echo / generated).

### 3.3. Протаскивание в события
При записи доменного события в `event_log` должны попадать:
- `run_id`, `scenario_id`, `request_id`, `tx_id`, `actor_pid` (если есть).

---

## 4. Доменные события (минимальный словарь)

### 4.1. Типы событий (MVP)
Минимальный словарь событий, покрывающий TS-01…TS-23 из [`docs/ru/08-test-scenarios.md`](docs/ru/08-test-scenarios.md:1):

- `participant.created`
- `participant.frozen`
- `participant.unfrozen`
- `trustline.created`
- `trustline.updated`
- `trustline.closed`
- `payment.committed`
- `payment.aborted`
- `clearing.executed`
- `clearing.skipped`
- `config.changed`
- `feature_flag.toggled`

### 4.2. Канонический формат события (для JSONL и для БД)
```json
{
  "event_id": "uuid",
  "event_type": "payment.committed",
  "timestamp": "2025-12-22T14:30:00Z",

  "run_id": "uuid",
  "scenario_id": "TS-12",
  "request_id": "uuid",
  "tx_id": "uuid",
  "actor_pid": "alice_pid",

  "payload": {
    "equivalent": "UAH",
    "amount": "100.00",
    "routes": []
  }
}
```

---

## 5. Хранение событий (БД)

### 5.1. Таблица `event_log` (минимум)
Рекомендуемая схема (PostgreSQL):

```sql
CREATE TABLE event_log (
    id          BIGSERIAL PRIMARY KEY,
    event_id    UUID NOT NULL,
    event_type  VARCHAR(64) NOT NULL,
    event_data  JSONB NOT NULL,

    run_id      UUID,
    scenario_id VARCHAR(32),
    request_id  UUID,
    tx_id       UUID,
    actor_pid   VARCHAR(128),

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_event_log_event_type ON event_log(event_type);
CREATE INDEX idx_event_log_actor_pid  ON event_log(actor_pid);
CREATE INDEX idx_event_log_tx_id      ON event_log(tx_id);
CREATE INDEX idx_event_log_request_id ON event_log(request_id);
CREATE INDEX idx_event_log_run_scn    ON event_log(run_id, scenario_id);
```

### 5.2. Retention
Для PROD: политика retention определяется отдельно (например, 90–365 дней, в зависимости от требований аудита).  
Для DEV/TEST: можно чистить полностью на `/_test/reset`.

---

## 6. Test-only API (DEV/TEST ONLY)

### 6.1. Общие требования безопасности
- Test-only routes должны существовать **только** в `dev/test` окружениях.
- В `prod`:
  - либо маршруты вообще не регистрируются,
  - либо включены guard-проверки и конфигурационный запрет, который невозможно обойти.

### 6.2. Эндпоинты (MVP)

#### POST `/api/v1/_test/reset`
Назначение: очистить БД и Redis (baseline).

Ответ:
```json
{"success": true, "data": {"reset": true}}
```

#### POST `/api/v1/_test/seed`
Назначение: быстрый setup типовых топологий.

Request:
```json
{
  "scenario": "triangle",
  "params": {},
  "seed": "optional-seed"
}
```

Response:
```json
{"success": true, "data": {"summary": {}}}
```

Список допустимых `scenario` фиксируется в реализации (и документируется).

#### GET `/api/v1/_test/snapshot?include_events=true&run_id=...&scenario_id=...`
Назначение: получить snapshot состояния для assert’ов и артефактов.

Если `include_events=true`, вернуть также события из `event_log`, отфильтрованные по:
- `run_id` + `scenario_id` (если заданы),
- иначе — по `request_id` текущего контекста (best-effort).

Response (структура `data` может быть гибкой на MVP, но ключи желательно стабилизировать):
```json
{
  "success": true,
  "data": {
    "participants": [],
    "trustlines": [],
    "debts": {},
    "payments": [],
    "events": [
      {"event_type": "payment.committed", "...": "..."}
    ]
  }
}
```

---

## 7. Артефакты pytest e2e прогонов

### 7.1. Рекомендуемая структура
```
tests/artifacts/
  <run_id>/
    meta.json
    TS-05/
      scenario_params.json
      requests/
      responses/
      snapshot.json
      events.jsonl
    TS-12/
      ...
```

### 7.2. Что сохранять обязательно
- `scenario_params.json` — входные параметры теста (seed, суммы, эквиваленты)
- `requests/` и `responses/` — все HTTP обмены (как минимум публичные endpoints)
- `snapshot.json` — результат `/_test/snapshot?include_events=true`
- `events.jsonl` — события из snapshot (каждая строка = JSON объект)

---

## 8. pytest соглашения (рекомендации)

### 8.1. run_id / scenario_id
- `run_id` — fixture уровня session (UUID)
- `scenario_id` — marker `@pytest.mark.scenario("TS-12")` или derivation из имени теста

### 8.2. http client
Клиент (httpx) обязан:
- добавлять `X-Run-ID`, `X-Scenario-ID`
- добавлять/генерировать `X-Request-ID` (или полагаться на middleware сервера)
- логировать request/response в артефакты

### 8.3. Селективный запуск
Пример:
```bash
pytest -k TS_12
```

---

## 9. SSR Admin Console: Domain Events Timeline

На базе [`docs/ru/admin/specs/admin-console-minimal-spec.md`](docs/ru/admin/specs/admin-console-minimal-spec.md:1) добавить экран:

**Domain Events / Timeline**
- Фильтры:
  - `event_type`
  - `actor_pid`
  - `tx_id`
  - `run_id` (для тестов)
  - `scenario_id` (для тестов)
  - интервал дат
- Таблица:
  - `timestamp | event_type | actor | tx_id | short_summary`
- Детали:
  - raw JSON payload

---

## 10. MVP+ (опционально)

### 10.1. `/_test/time-travel`
Назначение: ускорить сценарии, зависящие от времени (TS-03, TS-17, TS-18) из [`docs/ru/08-test-scenarios.md`](docs/ru/08-test-scenarios.md:1) без ожидания real-time.

### 10.2. `/_test/inject-fault`
Назначение: воспроизводимость конкурентных/ошибочных сценариев (например, TS-23) через управляемые задержки/ошибки.
