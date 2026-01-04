# GEO Hub v0.1 — Specyfikacja frameworku testowego (pytest e2e + artefakty + zdarzenia domenowe)

**Wersja:** 0.1  
**Status:** wersja robocza (zawiera decyzje zespołu)  
**Cel:** zdefiniować minimalną specyfikację, aby:
- ręcznie i automatycznie uruchamiać scenariusze MVP (TS-01…TS-23);
- uzyskiwać śledzone, odtwarzalne artefakty uruchomień;
- mieć czytelne dla człowieka logi zdarzeń i możliwość analizy przez konsolę administracyjną SSR.

Powiązane dokumenty:
- Kanoniczny kontrakt API: [`docs/pl/04-api-reference.md`](docs/pl/04-api-reference.md)
- Scenariusze E2E: [`docs/pl/08-test-scenarios.md`](docs/pl/08-test-scenarios.md)
- Minimalna konsola administracyjna: [`docs/pl/admin-console-minimal-spec.md`](docs/pl/admin-console-minimal-spec.md)
- Rejestr konfiguracji (runtime vs restart): [`docs/pl/config-reference.md`](docs/pl/config-reference.md)
- OpenAPI (musi być zgodny ze specyfikacją kanoniczną): [`api/openapi.yaml`](api/openapi.yaml)

---

## 1. Kanoniczny kontrakt API (źródło prawdy)

### 1.1. Kanon
Kanoniczny kontrakt API = [`docs/pl/04-api-reference.md`](docs/pl/04-api-reference.md).

### 1.2. Bazowy URL
Bazowy URL = `/api/v1` (patrz [`docs/pl/04-api-reference.md`](docs/pl/04-api-reference.md)).

### 1.3. Format odpowiedzi (koperta)
Wszystkie endpointy REST (publiczne i testowe) zwracają kopertę:

**Sukces:**
```json
{
  "success": true,
  "data": { }
}
```

**Sukces (paginowany):**
```json
{
  "success": true,
  "data": [],
  "pagination": { "total": 0, "page": 1, "per_page": 20, "pages": 0 }
}
```

**Błąd:**
```json
{
  "success": false,
  "error": {
    "code": "E002",
    "message": "Insufficient capacity",
    "details": { }
  }
}
```

### 1.4. Swagger UI / ReDoc
- `/api/v1/docs`, `/api/v1/redoc`
- OpenAPI YAML: [`api/openapi.yaml`](api/openapi.yaml)

---

## 2. Decyzja testowa (MVP)

**Przyjęto:**
- Konsola administracyjna SSR (wewnątrz backendu) do zadań operatora.
- pytest e2e — główny "uruchamiacz scenariuszy".
- Endpointy DEV/TEST-only `/api/v1/_test/*` do szybszego setup/teardown.
- Zdarzenia domenowe przechowywane:
  - w DB (`event_log`) — dla zapytań admina, analizy i niezawodności;
  - eksportowane do JSONL jako artefakty uruchomień.

**Kluczowa zasada:** endpointy testowe służą do **setup/teardown i diagnostycznych snapshotów**, ale główne akcje (np. `POST /payments`) powinny być testowane przez publiczne API kiedy tylko to możliwe.

---

## 3. Korelacja (wymagane identyfikatory)

### 3.1. Co korelujemy
Aby powiązać:
- żądania/odpowiedzi HTTP,
- transakcje domenowe (`tx_id`),
- zdarzenia w DB,
- JSONL w artefaktach,
- ekrany konsoli administracyjnej SSR,

używamy 4 identyfikatorów:

- `run_id` — UUID uruchomienia (sesja pytest)
- `scenario_id` — identyfikator scenariusza (np. `TS-12`)
- `request_id` — UUID na żądanie HTTP
- `tx_id` — UUID transakcji domenowej (jeśli dotyczy)

### 3.2. Nagłówki
Wprowadzamy nagłówki (normatywne):

- `X-Run-ID: <uuid>` (opcjonalny, ale wymagany dla e2e)
- `X-Scenario-ID: <string>` (opcjonalny, ale wymagany dla e2e)
- `X-Request-ID: <uuid>` (jeśli brak — serwer generuje i zwraca)

Zalecenie: serwer zawsze zwraca `X-Request-ID` w nagłówkach odpowiedzi (echo / wygenerowany).

### 3.3. Propagacja do zdarzeń
Przy zapisie zdarzenia domenowego do `event_log`, dołącz:
- `run_id`, `scenario_id`, `request_id`, `tx_id`, `actor_pid` (jeśli obecne).

---

## 4. Zdarzenia domenowe (minimalny słownik)

### 4.1. Typy zdarzeń (MVP)
Minimalny słownik zdarzeń pokrywający TS-01…TS-23 z [`docs/pl/08-test-scenarios.md`](docs/pl/08-test-scenarios.md):

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

### 4.2. Kanoniczny format zdarzenia (dla JSONL i DB)
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

## 5. Przechowywanie zdarzeń (DB)

### 5.1. Tabela `event_log` (minimum)
Zalecana schema (PostgreSQL):

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

### 5.2. Retencja
Dla PROD: polityka retencji definiowana osobno (np. 90–365 dni, w zależności od wymagań audytowych).  
Dla DEV/TEST: może być całkowicie wyczyszczona przy `/_test/reset`.

---

## 6. API tylko testowe (DEV/TEST ONLY)

### 6.1. Ogólne wymagania bezpieczeństwa
- Trasy testowe muszą istnieć **tylko** w środowiskach `dev/test`.
- W `prod`:
  - albo trasy nie są w ogóle rejestrowane,
  - albo włączone są sprawdzenia guard i zakaz konfiguracji, których nie da się obejść.

### 6.2. Endpointy (MVP)

#### POST `/api/v1/_test/reset`
Cel: wyczyszczenie DB i Redis (baseline).

Odpowiedź:
```json
{"success": true, "data": {"reset": true}}
```

#### POST `/api/v1/_test/seed`
Cel: szybkie utworzenie typowych topologii.

Żądanie:
```json
{
  "scenario": "triangle",
  "params": {},
  "seed": "optional-seed"
}
```

Odpowiedź:
```json
{"success": true, "data": {"summary": {}}}
```

Lista dozwolonych wartości `scenario` jest ustalona w implementacji (i udokumentowana).

#### GET `/api/v1/_test/snapshot?include_events=true&run_id=...&scenario_id=...`
Cel: pobranie snapshotu stanu do asercji i artefaktów.

Jeśli `include_events=true`, zwróć również zdarzenia z `event_log` filtrowane przez:
- `run_id` + `scenario_id` (jeśli podane),
- w przeciwnym razie — przez `request_id` bieżącego kontekstu (best-effort).

Odpowiedź (struktura `data` może być elastyczna dla MVP, ale klucze powinny być ustabilizowane):
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

## 7. Artefakty uruchomień pytest e2e

### 7.1. Zalecana struktura
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

### 7.2. Co zapisywać (wymagane)
- `scenario_params.json` — parametry wejściowe testu (seed, kwoty, ekwiwalenty)
- `requests/` i `responses/` — wszystkie wymiany HTTP (co najmniej endpointy publiczne)
- `snapshot.json` — wynik `/_test/snapshot?include_events=true`
- `events.jsonl` — zdarzenia ze snapshotu (każda linia = obiekt JSON)

---

## 8. Konwencje pytest (zalecenia)

### 8.1. run_id / scenario_id
- `run_id` — fixture na poziomie sesji (UUID)
- `scenario_id` — marker `@pytest.mark.scenario("TS-12")` lub derywacja z nazwy testu

### 8.2. Klient HTTP
Klient (httpx) musi:
- dodawać `X-Run-ID`, `X-Scenario-ID`
- dodawać/generować `X-Request-ID` (lub polegać na middleware serwera)
- logować żądanie/odpowiedź do artefaktów

### 8.3. Selektywne uruchamianie
Przykład:
```bash
pytest -k TS_12
```

---

## 9. Konsola administracyjna SSR: Timeline zdarzeń domenowych

Na podstawie [`docs/pl/admin-console-minimal-spec.md`](docs/pl/admin-console-minimal-spec.md), dodaj ekran:

**Zdarzenia domenowe / Timeline**
- Filtry:
  - `event_type`
  - `actor_pid`
  - `tx_id`
  - `run_id` (dla testów)
  - `scenario_id` (dla testów)
  - zakres dat
- Tabela:
  - `timestamp | event_type | actor | tx_id | short_summary`
- Szczegóły:
  - surowy payload JSON

---

## 10. MVP+ (opcjonalnie)

### 10.1. `/_test/time-travel`
Cel: przyspieszenie scenariuszy zależnych od czasu (TS-03, TS-17, TS-18) bez rzeczywistego oczekiwania.

### 10.2. `/_test/inject-fault`
Cel: odtwarzalność scenariuszy współbieżnych/błędnych (np. TS-23) przez kontrolowane opóźnienia/błędy.
