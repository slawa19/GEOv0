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

```powershell
$scenarioObj = Get-Content $ScenarioPath -Raw | ConvertFrom-Json
$body = @{ scenario = $scenarioObj } | ConvertTo-Json -Depth 100

Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/scenarios" -Headers $Headers -ContentType 'application/json' -Body $body
```

### 2.3 Get scenario summary

```powershell
Invoke-RestMethod -Method Get -Uri "$BaseUrl/simulator/scenarios/minimal" -Headers $Headers
```

## 3) Run: start / status / pause / resume / stop / restart / intensity

### 3.1 Start run

```powershell
$run = Invoke-RestMethod -Method Post -Uri "$BaseUrl/simulator/runs" -Headers $Headers -ContentType 'application/json' -Body (
  @{ scenario_id = 'minimal'; mode = 'fixtures'; intensity_percent = 30 } | ConvertTo-Json
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

## 4) SSE: подключение к stream событий

Endpoint: `GET /simulator/runs/{run_id}/events?equivalent=...`.

Примечания:
- `equivalent` обязателен по OpenAPI.
- Для стриминга в Windows PowerShell используйте `curl.exe` (не алиас `curl`).

```powershell
$eventsUrl = "$BaseUrl/simulator/runs/$runId/events?equivalent=$Equivalent"
curl.exe -N -H "Accept: text/event-stream" -H "Authorization: Bearer $accessToken" "$eventsUrl"
```

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

Если в индексе есть имя артефакта (например `graph.json`), можно скачать:

```powershell
$artifactName = "graph.json"
Invoke-WebRequest -Method Get -Uri "$BaseUrl/simulator/runs/$runId/artifacts/$artifactName" -Headers $Headers -OutFile ".\\.artifacts\\$artifactName"
```

## 6) Типовые ошибки и быстрые проверки

- `401 Unauthorized`: токен отсутствует/протух/не тот тип; повторите шаг (1).
- `404 Not Found` на run endpoints: неправильный `run_id` или run ещё не создан.
- `400 Bad Request`: чаще всего отсутствует обязательный query `equivalent`.
- Проверка базовой доступности сервиса: `GET $BaseUrl/health`.
