# GEO Hub: API Reference

**Version:** 0.1  
**Date:** November 2025

---

## Table of Contents

1. [General Information](#1-general-information)
2. [Authentication](#2-authentication)
3. [Participants](#3-participants)
4. [Trust Lines](#4-trust-lines)
5. [Payments](#5-payments)
6. [Balance](#6-balance)
7. [Clearing](#7-clearing)
8. [Error Codes](#8-error-codes)

---

## 1. General Information

### 1.1. Base URL

```
Production:  https://hub.example.com/api/v1
Development: http://localhost:8000/api/v1
```

### 1.2. Request/Response Format

- **Content-Type:** `application/json`
- **Encoding:** UTF-8
- **Dates:** ISO 8601 (`2025-11-29T12:00:00Z`)
- **Monetary amounts:** strings with fixed precision (`"100.00"`)

### 1.3. Standard Response

The canonical API contract is defined by `api/openapi.yaml`.

In v0.1, successful responses are typically **plain JSON** (no `{success,data}` envelope).

**Error (common format):**
```json
{
  "error": {
    "code": "E001",
    "message": "Human readable message",
    "details": { ... }
  }
}
```

### 1.4. Pagination

In v0.1 pagination is done via query parameters `page` and `per_page`.

The shape of successful list responses depends on the endpoint (see OpenAPI). For example,
list endpoints return an object like `{ "items": [...] }`.

**Query parameters:**
- `page` (default: 1)
- `per_page` (default: 20, max: 200)
```

**Query parameters:**
- `page` (default: 1)
- `per_page` (default: 20, max: 100)

---

## 2. Authentication

### 2.1. Challenge-Response Flow

#### Step 1: Get challenge

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

#### Step 2: Sign and send

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

### 2.2. Registration

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

### 2.3. Token refresh

```http
POST /auth/refresh
Content-Type: application/json

{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

### 2.4. Using token

```http
GET /participants/me
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

---

## 3. Participants

### 3.1. Get current participant

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

### 3.2. Update profile

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

### 3.3. Get participant by PID

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

### 3.4. Search participants

```http
GET /participants/search?q=alice&type=person&limit=10
Authorization: Bearer {token}
```

**Query parameters:**
- `q` — search query (by name)
- `type` — filter by type (`person`, `organization`, `hub`)
- `limit` — maximum results (default: 20)

---

## 4. Trust Lines

### 4.1. Create trust line

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

### 4.2. List trust lines

```http
GET /trustlines?direction=outgoing&equivalent=UAH&status=active
Authorization: Bearer {token}
```

**Query parameters:**
- `direction` — `outgoing` (I give) | `incoming` (given to me) | `all`
- `equivalent` — filter by equivalent
- `status` — `active` | `frozen` | `closed`
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
  "pagination": {...}
}
```

### 4.3. Get trust line by ID

```http
GET /trustlines/{id}
Authorization: Bearer {token}
```

### 4.4. Update trust line

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

### 4.5. Close trust line

```http
DELETE /trustlines/{id}
Authorization: Bearer {token}

{
  "signature": "base64_signature"
}
```

**Requirements:** `used` must be 0 (debt cleared).

---

## 5. Payments

### 5.1. Check capacity

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

### 5.2. Create payment

```http
POST /payments
Authorization: Bearer {token}
Idempotency-Key: {key}   # optional
Content-Type: application/json

{
  "to": "recipient_pid",
  "equivalent": "UAH",
  "amount": "100.00",
  "description": "For services",
  "constraints": {
    "max_hops": 4,
    "timeout_ms": 5000
  },
  "signature": "base64_signature"
}
```

**Idempotency-Key (optional):** retrying the same request with the same key returns the same result. Re-using the key with a different payload returns `409 Conflict`.

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

### 5.3. Payment history

```http
GET /payments?direction=all&equivalent=UAH&status=COMMITTED
Authorization: Bearer {token}
```

**Query parameters:**
- `direction` — `sent` | `received` | `all`
- `equivalent` — filter by equivalent
- `status` — `COMMITTED` | `ABORTED` | `all`
- `from_date`, `to_date` — date range
- `page`, `per_page`

### 5.4. Payment details

```http
GET /payments/{tx_id}
Authorization: Bearer {token}
```

---

## 6. Balance

### 6.1. Overall balance

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

### 6.2. Debt details

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

### 6.3. Balance change history

```http
GET /balance/history?equivalent=UAH&from_date=2025-11-01
Authorization: Bearer {token}
```

---

## 7. Clearing

The canonical contract is defined in `api/openapi.yaml`.

### 7.1. List debt cycles (by equivalent)

```http
GET /clearing/cycles?equivalent=UAH&max_depth=6
Authorization: Bearer {access_token}
```

**Response:**
```json
{
  "cycles": [
    [
      {"debt_id": "uuid", "debtor": "A", "creditor": "B", "amount": "10.00"},
      {"debt_id": "uuid", "debtor": "B", "creditor": "C", "amount": "10.00"},
      {"debt_id": "uuid", "debtor": "C", "creditor": "A", "amount": "10.00"}
    ]
  ]
}
```

### 7.2. Auto-clear cycles (by equivalent)

```http
POST /clearing/auto?equivalent=UAH
Authorization: Bearer {access_token}
```

**Response:**
```json
{
  "equivalent": "UAH",
  "cleared_cycles": 2
}
```

---

## 8. Error Codes

### 8.1. Error code table

| Code | HTTP | Description |
|------|------|-------------|
| `E001` | 404 | Route not found |
| `E002` | 400 | Insufficient capacity for payment |
| `E003` | 400 | Trust line limit exceeded |
| `E004` | 400 | Trust line not active |
| `E005` | 401 | Invalid signature |
| `E006` | 403 | Insufficient permissions |
| `E007` | 408 | Operation timeout |
| `E008` | 409 | State conflict |
| `E009` | 422 | Invalid data |
| `E010` | 500 | Internal error |
| `E011` | 404 | Participant not found |
| `E012` | 404 | Trust line not found |
| `E013` | 400 | Debt not cleared (when closing line) |
| `E014` | 429 | Too many requests |

### 8.2. Error example

```json
{
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

Full OpenAPI 3.0 specification available:
- JSON: `/api/v1/openapi.json`
- YAML (in repository): `api/openapi.yaml`
- Swagger UI: `/api/v1/docs`
- ReDoc: `/api/v1/redoc`

---

## Related Documents

- [00-overview.md](00-overview.md) — Project overview
- [02-protocol-spec.md](02-protocol-spec.md) — Protocol specification
- [03-architecture.md](03-architecture.md) — System architecture