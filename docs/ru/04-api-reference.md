# GEO Hub: API Reference

**Версия:** 0.1  
**Дата:** Ноябрь 2025

---

## Содержание

1. [Общая информация](#1-общая-информация)
2. [Аутентификация](#2-аутентификация)
3. [Участники](#3-участники)
4. [Линии доверия](#4-линии-доверия)
5. [Платежи](#5-платежи)
6. [Баланс](#6-баланс)
7. [WebSocket API](#7-websocket-api)
8. [Коды ошибок](#8-коды-ошибок)

---

## 1. Общая информация

### 1.1. Base URL

```
Production:  https://hub.example.com/api/v1
Development: http://localhost:8000/api/v1
```

### 1.2. Формат запросов/ответов

- **Content-Type:** `application/json`
- **Кодировка:** UTF-8
- **Даты:** ISO 8601 (`2025-11-29T12:00:00Z`)
- **Денежные суммы:** строки с фиксированной точностью (`"100.00"`)

### 1.3. Стандартный ответ

**Успех:**
```json
{
  "success": true,
  "data": { ... }
}
```

**Ошибка:**
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

### 1.4. Пагинация

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

**Query параметры:**
- `page` (default: 1)
- `per_page` (default: 20, max: 100)

---

## 2. Аутентификация

### 2.1. Challenge-Response Flow

#### Шаг 1: Получить challenge

```http
POST /auth/challenge
Content-Type: application/json

{
  "pid": "5HueCGU8rMjxEXxiPuD5BDku..."
}
```

**Response:**
```json
{
  "challenge": "base64url_string",
  "expires_at": "2025-11-29T12:05:00Z"
}
```

**Нормативные требования к `challenge`:**
- Длина: 32 байта (256 бит) *до* кодирования
- Генерация: криптографически безопасный CSPRNG
- TTL: 300 секунд (5 минут)
- Кодирование: base64url без padding (`=`), ASCII
- Хранение: сервер хранит challenge до `expires_at` и инвалидирует после успешного `POST /auth/login`
- Идемпотентность: повторный `POST /auth/challenge` для того же `pid` *может* возвращать тот же challenge до истечения TTL (поведение должно быть стабильным и документированным)

#### Шаг 2: Подписать и отправить

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

### 2.2. Регистрация

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

### 2.3. Обновление токена

```http
POST /auth/refresh
Content-Type: application/json

{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

### 2.4. Использование токена

```http
GET /participants/me
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

---

## 3. Участники

### 3.1. Получить текущего участника

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

### 3.2. Обновить профиль

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

### 3.3. Получить участника по PID

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

### 3.4. Поиск участников

```http
GET /participants/search?q=alice&type=person&limit=10
Authorization: Bearer {token}
```

**Query параметры:**
- `q` — поисковый запрос (по имени)
- `type` — фильтр по типу (`person`, `organization`, `hub`)
- `limit` — максимум результатов (default: 20)

---

## 4. Линии доверия

### 4.1. Создать линию доверия

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

### 4.2. Список линий доверия

```http
GET /trustlines?direction=outgoing&equivalent=UAH&status=active
Authorization: Bearer {token}
```

**Query параметры:**
- `direction` — `outgoing` (я даю) | `incoming` (мне дают) | `all`
- `equivalent` — фильтр по эквиваленту
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

### 4.3. Получить линию по ID

```http
GET /trustlines/{id}
Authorization: Bearer {token}
```

### 4.4. Обновить линию

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

### 4.5. Закрыть линию

```http
DELETE /trustlines/{id}
Authorization: Bearer {token}

{
  "signature": "base64_signature"
}
```

**Требования:** `used` должен быть 0 (долг погашен).

---

## 5. Платежи

### 5.1. Проверить ёмкость

Назначение: проверить, может ли отправитель оплатить **конкретную сумму** `amount` адресату `to` в заданном `equivalent` (с учётом текущих лимитов trustlines, занятых резервов и параметров маршрутизации).

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

### 5.2. Рассчитать максимум (max-flow)

Назначение: получить оценку **максимально возможной суммы** платежа от текущего пользователя к `to` в заданном `equivalent` и диагностическую информацию (состав путей и узкие места). Используется для UI подсказок и для тестирования производительности алгоритмов маршрутизации.

```http
GET /payments/max-flow?to={pid}&equivalent={code}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "max_amount": "1500.00",
  "paths": [
    {
      "path": ["my_pid", "p2", "p3", "recipient_pid"],
      "capacity": "700.00"
    },
    {
      "path": ["my_pid", "p4", "recipient_pid"],
      "capacity": "800.00"
    }
  ],
  "bottlenecks": [
    {
      "from": "my_pid",
      "to": "p2",
      "limit": "1000.00",
      "used": "400.00",
      "available": "600.00"
    }
  ],
  "algorithm": "limited_multipath",
  "computed_at": "2025-11-29T12:00:00Z"
}
```

**Примечания:**
- Реализация по умолчанию должна соответствовать режиму маршрутизации, заданному конфигом (см. [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)).
- Если включён экспериментальный режим **full multipath**, ответ может содержать больше путей и дополнительные поля диагностики, но контракт (наличие `max_amount`) сохраняется.

### 5.3. Создать платёж

```http
POST /payments
Authorization: Bearer {token}
Content-Type: application/json

{
  "to": "recipient_pid",
  "equivalent": "UAH",
  "amount": "100.00",
  "description": "За услуги",
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

### 5.3. История платежей

```http
GET /payments?direction=all&equivalent=UAH&status=COMMITTED
Authorization: Bearer {token}
```

**Query параметры:**
- `direction` — `sent` | `received` | `all`
- `equivalent` — фильтр по эквиваленту
- `status` — `COMMITTED` | `ABORTED` | `all`
- `from_date`, `to_date` — диапазон дат
- `page`, `per_page`

### 5.4. Детали платежа

```http
GET /payments/{tx_id}
Authorization: Bearer {token}
```

---

## 6. Баланс

### 6.1. Общий баланс

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

### 6.2. Детализация долгов

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

### 6.3. История изменений баланса

```http
GET /balance/history?equivalent=UAH&from_date=2025-11-01
Authorization: Bearer {token}
```

---

## 7. WebSocket API

### 7.1. Подключение

```
wss://hub.example.com/api/v1/ws?token={access_token}
```

### 7.2. Формат сообщений

```json
{
  "type": "event_type",
  "payload": { ... },
  "timestamp": "2025-11-29T12:00:00Z"
}
```

### 7.3. События от сервера

#### Новый входящий платёж

```json
{
  "type": "payment.received",
  "payload": {
    "tx_id": "uuid",
    "from": "sender_pid",
    "from_name": "Alice",
    "amount": "100.00",
    "equivalent": "UAH",
    "description": "За услуги"
  }
}
```

#### Изменение линии доверия

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

#### Выполнен клиринг

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

### 7.4. Команды от клиента

#### Ping

```json
{
  "type": "ping"
}
```

**Ответ:**
```json
{
  "type": "pong",
  "timestamp": "2025-11-29T12:00:00Z"
}
```

#### Подписка на события

```json
{
  "type": "subscribe",
  "payload": {
    "events": ["payment.received", "trustline.updated"]
  }
}
```

#### Отписка от событий

```json
{
  "type": "unsubscribe",
  "payload": {
    "events": ["payment.received"]
  }
}
```

### 7.5. Heartbeat (normative)

- Клиент отправляет `ping` каждые 30 секунд при отсутствии исходящего трафика.
- Сервер отвечает `pong` и обновляет `timestamp`.
- Если в течение 90 секунд нет ни одного входящего сообщения (включая `pong`) — клиент должен считать соединение разорванным и переподключиться.

### 7.6. Переподключение (reconnect) и устойчивость (normative)

- Клиент должен переподключаться с backoff (например, 1s, 2s, 5s, 10s, 30s).
- Клиент должен быть готов к дубликатам событий и не полагаться на доставку ровно один раз.
- После переподключения клиент должен:
  1) заново выполнить `subscribe`;
  2) при необходимости сверить состояние через REST (например, история/список доверительных линий/платежей), так как WS не гарантирует доставку пропущенных событий.

### 7.7. Гарантии доставки и порядок (normative)

- Доставка: best-effort (at-most-once). Возможны пропуски и дубликаты при сетевых сбоях.
- Порядок: порядок сообщений гарантируется только *в рамках одного соединения*; между переподключениями порядок не гарантируется.

### 7.8. Ошибки WebSocket (normative)

Сервер может отправлять сообщение типа `error`:

```json
{
  "type": "error",
  "payload": {
    "code": "E014",
    "message": "Too many requests",
    "details": { }
  },
  "timestamp": "2025-11-29T12:00:00Z"
}
```

После ошибок уровня auth (например, невалидный token) сервер может закрыть соединение.

---

## 8. Коды ошибок

### 8.1. Таблица кодов

| Код | HTTP | Описание |
|-----|------|----------|
| `E001` | 404 | Маршрут не найден |
| `E002` | 400 | Недостаточная ёмкость для платежа |
| `E003` | 400 | Превышен лимит линии доверия |
| `E004` | 400 | Линия доверия не активна |
| `E005` | 401 | Неверная подпись |
| `E006` | 403 | Недостаточно прав |
| `E007` | 408 | Таймаут операции |
| `E008` | 409 | Конфликт состояний |
| `E009` | 422 | Некорректные данные |
| `E010` | 500 | Внутренняя ошибка |
| `E011` | 404 | Участник не найден |
| `E012` | 404 | Линия доверия не найдена |
| `E013` | 400 | Долг не погашен (при закрытии линии) |
| `E014` | 429 | Слишком много запросов |

### 8.2. Пример ошибки

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

Полная OpenAPI 3.0 спецификация доступна:
- JSON: `/api/v1/openapi.json`
- YAML: `/api/v1/openapi.yaml`
- Swagger UI: `/api/v1/docs`
- ReDoc: `/api/v1/redoc`

---

## Связанные документы

- [00-overview.md](00-overview.md) — Обзор проекта
- [02-protocol-spec.md](02-protocol-spec.md) — Спецификация протокола
- [03-architecture.md](03-architecture.md) — Архитектура системы
