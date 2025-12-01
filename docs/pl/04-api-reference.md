# GEO Hub: Dokumentacja API

**Wersja:** 0.1  
**Data:** Listopad 2025

---

## Spis treści

1. [Informacje ogólne](#1-informacje-ogólne)  
2. [Autentykacja](#2-autentykacja)  
3. [Uczestnicy](#3-uczestnicy)  
4. [Linie zaufania](#4-linie-zaufania)  
5. [Płatności](#5-płatności)  
6. [Bilans](#6-bilans)  
7. [WebSocket API](#7-websocket-api)  
8. [Kody błędów](#8-kody-błędów)

---

## 1. Informacje ogólne

### 1.1. Base URL

```text
Production:  https://hub.example.com/api/v1
Development: http://localhost:8000/api/v1
```

### 1.2. Format żądań/odpowiedzi

- **Content-Type:** `application/json`  
- **Kodowanie:** UTF-8  
- **Daty:** ISO 8601 (`2025-11-29T12:00:00Z`)  
- **Kwoty pieniężne:** stringi z ustaloną precyzją (`"100.00"`)

### 1.3. Standardowa odpowiedź

**Sukces:**
```json
{
  "success": true,
  "data": { ... }
}
```

**Błąd:**
```json
{
  "success": false,
  "error": {
    "code": "E001",
    "message": "Human readable message",
    "details": { ... }
  }
}
```

### 1.4. Paginacja

```json
{
  "data": [...],
  "pagination": {
    "total": 150,
    "page": 1,
    "per_page": 20,
    "pages": 8
  }
}
```

**Parametry query:**

- `page` (domyślnie: 1)  
- `per_page` (domyślnie: 20, max: 100)

---

## 2. Autentykacja

### 2.1. Challenge-Response Flow

#### Krok 1: Pobranie challenge

```http
GET /auth/challenge?pid={pid}
```

**Response:**
```json
{
  "challenge": "random_string_32_chars",
  "expires_at": "2025-11-29T12:05:00Z"
}
```

#### Krok 2: Podpisanie i wysłanie

```http
POST /auth/login
Content-Type: application/json

{
  "pid": "5HueCGU8rMjxEXxiPuD5BDku...",
  "challenge": "random_string_32_chars",
  "signature": "base64_encoded_ed25519_signature",
  "device_info": {
    "platform": "android",
    "app_version": "1.0.0"
  }
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_in": 3600,
  "participant": {
    "pid": "5HueCGU8rMjxEXxiPuD5BDku...",
    "display_name": "Alice",
    "status": "active"
  }
}
```

### 2.2. Rejestracja

```http
POST /auth/register
Content-Type: application/json

{
  "public_key": "base64_encoded_32_bytes",
  "display_name": "Alice",
  "profile": {
    "type": "person",
    "description": "Developer"
  },
  "signature": "base64_encoded_registration_proof"
}
```

**Response:**
```json
{
  "pid": "5HueCGU8rMjxEXxiPuD5BDku...",
  "display_name": "Alice",
  "status": "active",
  "created_at": "2025-11-29T12:00:00Z"
}
```

### 2.3. Odświeżenie tokenu

```http
POST /auth/refresh
Content-Type: application/json

{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

### 2.4. Użycie tokenu

```http
GET /participants/me
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

---

## 3. Uczestnicy

### 3.1. Pobranie bieżącego uczestnika

```http
GET /participants/me
Authorization: Bearer {token}
```

**Response:**
```json
{
  "pid": "5HueCGU8rMjxEXxiPuD5BDku...",
  "display_name": "Alice",
  "profile": {
    "type": "person",
    "description": "Developer"
  },
  "status": "active",
  "verification_level": 1,
  "created_at": "2025-11-29T12:00:00Z",
  "stats": {
    "total_incoming_trust": "5000.00",
    "total_outgoing_trust": "3000.00",
    "total_debt": "500.00",
    "total_credit": "800.00",
    "net_balance": "300.00"
  }
}
```

### 3.2. Aktualizacja profilu

```http
PATCH /participants/me
Authorization: Bearer {token}
Content-Type: application/json

{
  "display_name": "Alice Smith",
  "profile": {
    "description": "Senior Developer"
  },
  "signature": "base64_signature_of_changes"
}
```

### 3.3. Pobranie uczestnika po PID

```http
GET /participants/{pid}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "pid": "5HueCGU8rMjxEXxiPuD5BDku...",
  "display_name": "Bob",
  "profile": {
    "type": "person"
  },
  "status": "active",
  "verification_level": 2,
  "public_stats": {
    "total_incoming_trust": "10000.00",
    "member_since": "2025-01-15T00:00:00Z"
  }
}
```

### 3.4. Wyszukiwanie uczestników

```http
GET /participants/search?q=alice&type=person&limit=10
Authorization: Bearer {token}
```

**Parametry query:**

- `q` — fraza wyszukiwania (po nazwie)  
- `type` — filtr po typie (`person`, `organization`, `hub`)  
- `limit` — maksymalna liczba wyników (domyślnie: 20)

---

## 4. Linie zaufania

### 4.1. Utworzenie linii zaufania

```http
POST /trustlines
Authorization: Bearer {token}
Content-Type: application/json

{
  "to": "target_pid",
  "equivalent": "UAH",
  "limit": "1000.00",
  "policy": {
    "auto_clearing": true,
    "can_be_intermediate": true,
    "daily_limit": null
  },
  "signature": "base64_signature"
}
```

**Response:**
```json
{
  "id": "uuid",
  "from": "my_pid",
  "to": "target_pid",
  "equivalent": "UAH",
  "limit": "1000.00",
  "used": "0.00",
  "available": "1000.00",
  "policy": {
    "auto_clearing": true,
    "can_be_intermediate": true
  },
  "status": "active",
  "created_at": "2025-11-29T12:00:00Z"
}
```

### 4.2. Lista linii zaufania

```http
GET /trustlines?direction=outgoing&equivalent=UAH&status=active
Authorization: Bearer {token}
```

**Parametry query:**

- `direction` — `outgoing` (ja daję zaufanie) \| `incoming` (mnie ufają) \| `all`  
- `equivalent` — filtr po ekwiwalencie  
- `status` — `active` \| `frozen` \| `closed`  
- `page`, `per_page`

**Response:**
```json
{
  "data": [
    {
      "id": "uuid",
      "from": "my_pid",
      "to": "bob_pid",
      "to_display_name": "Bob",
      "equivalent": "UAH",
      "limit": "1000.00",
      "used": "300.00",
      "available": "700.00",
      "status": "active"
    }
  ],
  "pagination": { ... }
}
```

### 4.3. Szczegóły linii

```http
GET /trustlines/{id}
Authorization: Bearer {token}
```

### 4.4. Aktualizacja linii

```http
PATCH /trustlines/{id}
Authorization: Bearer {token}
Content-Type: application/json

{
  "limit": "1500.00",
  "policy": {
    "daily_limit": "500.00"
  },
  "signature": "base64_signature"
}
```

### 4.5. Zamknięcie linii

```http
DELETE /trustlines/{id}
Authorization: Bearer {token}

{
  "signature": "base64_signature"
}
```

**Wymagania:** `used` musi być 0 (dług spłacony).

---

## 5. Płatności

### 5.1. Sprawdzenie pojemności

```http
GET /payments/capacity?to={pid}&equivalent={code}&amount={amount}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "can_pay": true,
  "max_amount": "500.00",
  "routes_count": 2,
  "estimated_hops": 3
}
```

### 5.2. Utworzenie płatności

```http
POST /payments
Authorization: Bearer {token}
Content-Type: application/json

{
  "to": "recipient_pid",
  "equivalent": "UAH",
  "amount": "100.00",
  "description": "Za usługi",
  "constraints": {
    "max_hops": 4,
    "timeout_ms": 5000
  },
  "signature": "base64_signature"
}
```

**Response (success):**
```json
{
  "tx_id": "uuid",
  "status": "COMMITTED",
  "from": "my_pid",
  "to": "recipient_pid",
  "equivalent": "UAH",
  "amount": "100.00",
  "routes": [
    {
      "path": ["my_pid", "intermediate_pid", "recipient_pid"],
      "amount": "100.00"
    }
  ],
  "created_at": "2025-11-29T12:00:00Z",
  "committed_at": "2025-11-29T12:00:01Z"
}
```

**Response (failed):**
```json
{
  "tx_id": "uuid",
  "status": "ABORTED",
  "error": {
    "code": "E002",
    "message": "Insufficient capacity",
    "details": {
      "requested": "100.00",
      "available": "50.00"
    }
  }
}
```

### 5.3. Historia płatności

```http
GET /payments?direction=all&equivalent=UAH&status=COMMITTED
Authorization: Bearer {token}
```

**Parametry query:**

- `direction` — `sent` \| `received` \| `all`  
- `equivalent` — filtr po ekwiwalencie  
- `status` — `COMMITTED` \| `ABORTED` \| `all`  
- `from_date`, `to_date` — zakres dat  
- `page`, `per_page`

### 5.4. Szczegóły płatności

```http
GET /payments/{tx_id}
Authorization: Bearer {token}
```

---

## 6. Bilans

### 6.1. Bilans ogólny

```http
GET /balance
Authorization: Bearer {token}
```

**Response:**
```json
{
  "equivalents": [
    {
      "code": "UAH",
      "total_debt": "500.00",
      "total_credit": "800.00",
      "net_balance": "300.00",
      "available_to_spend": "1200.00",
      "available_to_receive": "2000.00"
    },
    {
      "code": "HOUR",
      "total_debt": "10.00",
      "total_credit": "5.00",
      "net_balance": "-5.00",
      "available_to_spend": "15.00",
      "available_to_receive": "20.00"
    }
  ]
}
```

### 6.2. Szczegóły długów

```http
GET /balance/debts?equivalent=UAH&direction=all
Authorization: Bearer {token}
```

**Response:**
```json
{
  "outgoing": [
    {
      "creditor": "bob_pid",
      "creditor_name": "Bob",
      "equivalent": "UAH",
      "amount": "300.00"
    }
  ],
  "incoming": [
    {
      "debtor": "charlie_pid",
      "debtor_name": "Charlie",
      "equivalent": "UAH",
      "amount": "800.00"
    }
  ]
}
```

### 6.3. Historia zmian bilansu

```http
GET /balance/history?equivalent=UAH&from_date=2025-11-01
Authorization: Bearer {token}
```

---

## 7. WebSocket API

### 7.1. Połączenie

```text
wss://hub.example.com/api/v1/ws?token={access_token}
```

### 7.2. Format wiadomości

```json
{
  "type": "event_type",
  "payload": { ... },
  "timestamp": "2025-11-29T12:00:00Z"
}
```

### 7.3. Zdarzenia z serwera

#### Nowa płatność przychodząca

```json
{
  "type": "payment.received",
  "payload": {
    "tx_id": "uuid",
    "from": "sender_pid",
    "from_name": "Alice",
    "amount": "100.00",
    "equivalent": "UAH",
    "description": "Za usługi"
  }
}
```

#### Zmiana linii zaufania

```json
{
  "type": "trustline.updated",
  "payload": {
    "id": "uuid",
    "from": "bob_pid",
    "limit": "1500.00",
    "previous_limit": "1000.00"
  }
}
```

#### Wykonany kliring

```json
{
  "type": "clearing.completed",
  "payload": {
    "tx_id": "uuid",
    "cycle": ["my_pid", "bob_pid", "charlie_pid", "my_pid"],
    "amount": "50.00",
    "equivalent": "UAH",
    "my_debt_reduced": "50.00"
  }
}
```

### 7.4. Komendy klienta

#### Ping

```json
{
  "type": "ping"
}
```

**Odpowiedź:**
```json
{
  "type": "pong",
  "timestamp": "2025-11-29T12:00:00Z"
}
```

#### Subskrypcja zdarzeń

```json
{
  "type": "subscribe",
  "payload": {
    "events": ["payment.received", "trustline.updated"]
  }
}
```

---

## 8. Kody błędów

### 8.1. Tabela kodów

| Kod   | HTTP | Opis                                  |
|-------|------|----------------------------------------|
| `E001`| 404  | Trasa nie znaleziona                  |
| `E002`| 400  | Niewystarczająca pojemność płatności  |
| `E003`| 400  | Przekroczony limit linii zaufania     |
| `E004`| 400  | Linia zaufania nieaktywna             |
| `E005`| 401  | Nieprawidłowy podpis                  |
| `E006`| 403  | Brak uprawnień                        |
| `E007`| 408  | Timeout operacji                      |
| `E008`| 409  | Konflikt stanów                       |
| `E009`| 422  | Nieprawidłowe dane                    |
| `E010`| 500  | Błąd wewnętrzny                       |
| `E011`| 404  | Uczestnik nie znaleziony              |
| `E012`| 404  | Linia zaufania nie znaleziona         |
| `E013`| 400  | Dług niespłacony (przy zamknięciu linii) |
| `E014`| 429  | Zbyt wiele żądań                      |

### 8.2. Przykład błędu

```json
{
  "success": false,
  "error": {
    "code": "E003",
    "message": "Trust line limit exceeded",
    "details": {
      "trust_line_id": "uuid",
      "limit": "1000.00",
      "current_used": "900.00",
      "requested": "200.00"
    }
  }
}
```

---

## OpenAPI Specification

Pełna specyfikacja OpenAPI 3.0 jest dostępna pod adresami:

- JSON: `/api/v1/openapi.json`  
- YAML: `/api/v1/openapi.yaml`  
- Swagger UI: `/api/v1/docs`  
- ReDoc: `/api/v1/redoc`

---

## Powiązane dokumenty

- [00-overview.md](00-overview.md) — Przegląd projektu  
- [02-protocol-spec.md](02-protocol-spec.md) — Specyfikacja protokołu  
- [03-architecture.md](03-architecture.md) — Architektura systemu
