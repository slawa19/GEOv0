# API examples для ручной проверки Real Mode симулятора

Цель: быстро проверить **control plane** симулятора без UI (через REST + SSE), строго по OpenAPI.

Документ ориентирован на Windows PowerShell.

## 0) Переменные окружения

```powershell
$BaseUrl = "http://127.0.0.1:18000/api/v1"
$ScenarioPath = "fixtures/simulator/minimal/scenario.json"
$Equivalent = "UAH"
```

Если backend запущен в другом месте — поменяйте `$BaseUrl`.

Предпосылки:
- команды ниже предполагают, что вы находитесь в корне репозитория
- предпочтительно активировать `.venv` (или убедиться, что зависимости из `requirements.txt` установлены)

## 1) Авторизация (получить Bearer token)

Simulator endpoints в OpenAPI требуют `Authorization: Bearer ...`.

Кто вызывает — решает `require_simulator_actor` (`app/api/deps.py:258-340`), по порядку: заголовок `X-Admin-Token` (администратор, владелец `admin`, либо `cli:<имя>` с `X-Simulator-Owner`), Bearer-токен участника (владелец `pid:<pid>`), cookie `geo_sim_sid` (анонимный посетитель демо, владелец `anon:…`). Владелец — единица лимитов и доступа: у владельца не больше одного активного прогона, чужой прогон закрыт (403), администратор видит все (`real-mode-runbook.md`, раздел 6.6). Примеры ниже идут от участника (Bearer); шаги, требующие администратора, помечены.

Ниже — минимальный «dev-flow», который:
1) генерирует Ed25519 ключи,
2) регистрирует участника через `POST /participants`,
3) берёт challenge через `POST /auth/challenge`,
4) логинится через `POST /auth/login`,
5) печатает `PID=...` и `ACCESS_TOKEN=...`.

```powershell
$PyExe = if (Test-Path .\.venv\Scripts\python.exe) { ".\\.venv\\Scripts\\python.exe" } else { "python" }

$env:GEO_API_BASE_URL = $BaseUrl

$py = @'
import base64
import os

import httpx
from nacl.signing import SigningKey

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair

BASE_URL = os.environ.get("GEO_API_BASE_URL", "http://127.0.0.1:18000/api/v1")

public_key, private_key = generate_keypair()

# 1) Register participant (signature over canonical JSON)
message = canonical_json(
    {
        "display_name": "Dev User",
        "type": "person",
        "public_key": public_key,
        "profile": {},
    }
)
sk = SigningKey(base64.b64decode(private_key))
register_sig_b64 = base64.b64encode(sk.sign(message).signature).decode("utf-8")

r = httpx.post(
    f"{BASE_URL}/participants",
    json={
        "display_name": "Dev User",
        "type": "person",
        "public_key": public_key,
        "signature": register_sig_b64,
        "profile": {},
    },
)
r.raise_for_status()
pid = r.json()["pid"]

# 2) Challenge
r = httpx.post(f"{BASE_URL}/auth/challenge", json={"pid": pid})
r.raise_for_status()
challenge = r.json()["challenge"]

# 3) Login (signature over raw challenge string)
login_sig_b64 = base64.b64encode(sk.sign(challenge.encode("utf-8")).signature).decode("utf-8")

r = httpx.post(
    f"{BASE_URL}/auth/login",
    json={"pid": pid, "challenge": challenge, "signature": login_sig_b64},
)
r.raise_for_status()
tokens = r.json()

print("PID=" + pid)
print("ACCESS_TOKEN=" + tokens["access_token"])
'@

$authLines = & $PyExe -c $py
$pid = ($authLines | Select-String '^PID=').Line.Split('=',2)[1]
$accessToken = ($authLines | Select-String '^ACCESS_TOKEN=').Line.Split('=',2)[1]

$Headers = @{ Authorization = "Bearer $accessToken" }

"PID=$pid"
"ACCESS_TOKEN=$accessToken"
```

Проверка:

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/health" -Headers $Headers
```

## 2) Сценарии: list / upload / get

### 2.1 List scenarios

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/scenarios" -Headers $Headers
```

### 2.2 Upload scenario из JSON файла

OpenAPI ожидает: `{ "scenario": <scenario-json> }`.

**Загрузка — только администратору** (`app/api/v1/simulator.py:2804-2816`: иначе 403): добавьте заголовок `X-Admin-Token` со значением `ADMIN_TOKEN` бэкенда — в примере ниже `-Headers $AdminHeaders`.

```powershell
$AdminHeaders = @{ 'X-Admin-Token' = '<значение ADMIN_TOKEN бэкенда>' }
$scenarioObj = Get-Content $ScenarioPath -Raw | ConvertFrom-Json
$body = @{ scenario = $scenarioObj } | ConvertTo-Json -Depth 100

Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/scenarios" -Headers $AdminHeaders -ContentType 'application/json' -Body $body
```

После загрузки сценария — перезагрузить страницу Simulator UI: каталог читается при загрузке страницы (кнопки обновления каталога в интерфейсе нет, 048 S1).

### 2.3 Get scenario summary

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/scenarios/minimal" -Headers $Headers
```

## 3) Run: start / status / pause / resume / stop / restart / intensity

### 3.1 Start run

Не-администратор стартует и читает только допущенные сценарии: по умолчанию это три демо-сценария, остальные отвечают 404 как неизвестные; допуск — `SIMULATOR_SCENARIO_ALLOWLIST` (`*` или `all` — все; `app/core/simulator/runtime_impl.py:49-70, 341-351`). Для `minimal` из примера под Bearer-участником задайте `SIMULATOR_SCENARIO_ALLOWLIST=minimal` (или `*`) либо стартуйте с `-Headers $AdminHeaders`. Прогон получает свежих участников под идентичностями `<run_id>:p<n>`, поэтому участники в ответах прогона называются так, а не pid сценария (`app/core/simulator/run_perimeter.py:59-62`).

```powershell
$run = Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/runs" -Headers $Headers -ContentType 'application/json' -Body (
  @{ scenario_id = 'minimal'; mode = 'real'; intensity_percent = 30 } | ConvertTo-Json
)
$runId = $run.run_id
"run_id=$runId"
```

### 3.2 Status

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/runs/$runId" -Headers $Headers
```

### 3.3 Pause / resume / stop / restart

```powershell
Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/runs/$runId/pause" -Headers $Headers
Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/runs/$runId/resume" -Headers $Headers
Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/runs/$runId/stop" -Headers $Headers
Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/runs/$runId/restart" -Headers $Headers
```

### 3.4 Set intensity

```powershell
Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/runs/$runId/intensity" -Headers $Headers -ContentType 'application/json' -Body (
  @{ intensity_percent = 70 } | ConvertTo-Json
)
```

### 3.5 Лимиты, простой и причина остановки

Что вернёт сервер (подробности и якоря — `real-mode-runbook.md`, раздел 6.6):

- второй активный прогон того же владельца — 409, `error.details.conflict_kind = owner_active_exists`;
- все места процесса заняты (`SIMULATOR_MAX_ACTIVE_RUNS`) — 409, `error.details.conflict_kind = global_active_limit`, `max_active_runs`, `active_runs`; повторить можно, когда любой прогон остановится;
- чужой прогон — 403; неизвестный, удалённый из памяти или оставшийся от прежнего процесса — 404;
- свой активный прогон владельца — `GET $BaseUrl/simulator/runs/active` (`{ "run_id": "…" }`; `{ "run_id": null }`, если активного нет);
- прогон без SSE-подписчика дольше `SIMULATOR_RUN_IDLE_STOP_SEC` (600 с по умолчанию) останавливается сам: `GET …/runs/$runId` отдаёт `state = stopped`, `stop_source = server`, `stop_reason = idle_no_subscribers`; `POST …/runs` того же владельца после этого создаёт новый прогон.

## 4) SSE: подключение к stream событий

Endpoint: `GET /simulator/runs/{run_id}/events?equivalent=...`.

Примечания:
- `equivalent` обязателен по OpenAPI.
- Для стриминга в Windows PowerShell используйте `curl.exe` (не алиас `curl`).

```powershell
$eventsUrl = "$BaseUrl/simulator/runs/$runId/events?equivalent=$Equivalent"
curl.exe -N -H "Accept: text/event-stream" -H "Authorization: Bearer $accessToken" "$eventsUrl"
```

### 4.1 Replay при реконнекте (Last-Event-ID)

Если SSE соединение оборвалось, клиент может переподключиться с заголовком `Last-Event-ID: <event_id>`.

```powershell
$lastEventId = "<event_id_from_previous_stream>"
curl.exe -N -H "Accept: text/event-stream" -H "Authorization: Bearer $accessToken" -H "Last-Event-ID: $lastEventId" "$eventsUrl"
```

Backend всегда возвращает `HTTP 410`, если `Last-Event-ID` невалиден, относится
к другому run, опережает текущую последовательность, вышел из окна ring-buffer
или replay не помещается целиком в bootstrap queue. В этом случае очистите
cursor, обновите `GET /simulator/runs/{run_id}` + snapshot и переподключитесь.
Transient ошибка status/snapshot не означает stale run: сохраните run context и
повторите reconnect; только подтверждённый `404` завершает этот контекст.

Ожидаемое поведение: сервер периодически присылает `run_status` (heartbeat) во время `running`.

## 5) Snapshot / Metrics / Bottlenecks / Artifacts

### 5.1 Graph snapshot

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/runs/$runId/graph/snapshot?equivalent=$Equivalent" -Headers $Headers
```

### 5.2 Metrics time-series

Параметры обязательны: `from_ms`, `to_ms`, `step_ms`.

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/runs/$runId/metrics?equivalent=$Equivalent&from_ms=0&to_ms=60000&step_ms=1000" -Headers $Headers
```

### 5.3 Bottlenecks

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/runs/$runId/bottlenecks?equivalent=$Equivalent&limit=20&min_score=0.1" -Headers $Headers
```

### 5.4 Artifacts index + download

```powershell
$idx = Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/runs/$runId/artifacts" -Headers $Headers
$idx
```

Индекс перечисляет файлы каталога прогона: `status.json`, `last_tick.json`, `events.ndjson`, после остановки — ещё `summary.json` и `bundle.zip` (что в каждом — `real-mode-runbook.md`, раздел 6.7). Скачать один из них:

```powershell
$artifactName = "events.ndjson"
$artifactOutputDir = ".\\.local-run\\api-artifacts"
New-Item -ItemType Directory -Path $artifactOutputDir -Force | Out-Null
Invoke-WebRequest -Method Get -Uri "$BaseUrl/simulator/runs/$runId/artifacts/$artifactName" -Headers $Headers -OutFile (Join-Path $artifactOutputDir $artifactName)
```

## 6) Типовые ошибки и быстрые проверки

- `401 Unauthorized`: токен отсутствует/протух/не тот тип; повторите шаг (1).
- `403 Forbidden` на run endpoints: прогон принадлежит другому владельцу (`pid:…` / `anon:…` / `admin`); загрузка сценария без `X-Admin-Token`.
- `404 Not Found` на run endpoints: неправильный `run_id`, run ещё не создан, запись удалена по `SIMULATOR_MAX_RUN_RECORDS` или процесс перезапускался; сценарий вне `SIMULATOR_SCENARIO_ALLOWLIST` для не-администратора.
- `409 Conflict` на `POST /simulator/runs`: `details.conflict_kind` — `owner_active_exists` или `global_active_limit` (раздел 3.5).
- `429 Too Many Requests` на `GET …/events`: предел подписок SSE (`SIMULATOR_SSE_MAX_CONNECTIONS`, `SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN`; `ws-protocol.md`, раздел 2.1.1).
- `400 Bad Request`: чаще всего отсутствует обязательный query `equivalent`.
- Проверка базовой доступности сервиса: `GET $BaseUrl/health`.
