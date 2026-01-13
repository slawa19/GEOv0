# GEO Hub: API Reference

**Версия:** 0.1  
**Дата:** Январь 2026

---

## Содержание

1. [Общая информация](#1-общая-информация)
2. [Аутентификация](#2-аутентификация)
3. [Участники](#3-участники)
4. [Линии доверия](#4-линии-доверия)
5. [Платежи](#5-платежи)
6. [Баланс](#6-баланс)
7. [Клиринг](#7-клиринг)
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

Канонический контракт ответов описан в [api/openapi.yaml](../../api/openapi.yaml).

В v0.1 большинство успешных ответов — **plain JSON** (без envelope вида `{success,data}`).

**Ошибка (единый формат):**
```json
{
  "error": {
    "code": "E001",
    "message": "Human readable message",
    "details": {"any": "payload"}
  }
}
```

### 1.4. Пагинация

В v0.1 используется простая пагинация через query параметры `page` и `per_page`.

Форма успешного ответа зависит от конкретного эндпоинта (см. OpenAPI). Например, list-эндпоинты
возвращают объект вида `{ "items": [...] }`.

**Query параметры:**
- `page` (default: 1)
- `per_page` (default: 20, max: 200)

### 1.5. Health endpoints

Эндпоинты здоровья доступны на корне приложения и также доступны под `/api/v1/*` в виде алиасов (для клиентов, которые используют API base URL).

- `GET /healthz` → `{ "status": "ok" }`
- `GET /health` → `{ "status": "ok" }`
- `GET /health/db` → проверка доступности БД
- `GET /api/v1/healthz` → `{ "status": "ok" }`
- `GET /api/v1/health` → `{ "status": "ok" }`
- `GET /api/v1/health/db` → проверка доступности БД

### 1.6. Корреляция запросов (`X-Request-ID`)

Сервер поддерживает корреляцию запросов через заголовок `X-Request-ID`:

- если клиент отправляет `X-Request-ID`, сервер **возвращает его обратно** (echo).
- если заголовка нет, сервер **генерирует** новый идентификатор и возвращает его.

Заголовок ответа `X-Request-ID` присутствует всегда и позволяет связать ошибки клиента с логами/аудитом на сервере.

### 1.7. Эндпоинт метрик (`/metrics`)

Метрики Prometheus доступны на корне приложения:

- `GET /metrics` (text format)

Важно: `/metrics` **не** находится под `/api/v1`.

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

`device_info` опционален и фиксируется в `audit_log` при успешном login (best-effort).

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
POST /participants
Content-Type: application/json

{
  "public_key": "base64_encoded_32_bytes",
  "display_name": "Alice",
  "type": "person",
  "profile": {
    "description": "Developer"
  },
  "signature": "base64_encoded_ed25519_signature"
}
```

Подпись регистрации считается по **canonical JSON** payload **без** поля `signature`.
Точный состав подписываемых полей определён в `api/openapi.yaml`.

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

**`profile` (рекомендуемые ключи):**
- `type` — тип участника (например, `person`, `business`, `hub`)
- `description` — свободный текст-описание
- `contacts` — объект с контактами (например, `{ "email": "...", "telegram": "..." }`)

Дополнительные ключи могут присутствовать.

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
- `type` — фильтр по типу (`person`, `business`, `hub`)
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

Примечание (MVP): `policy.daily_limit` принимается и сохраняется как часть policy, но **не enforced** в платёжном критическом пути (informational only).
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
- Реализация по умолчанию должна соответствовать режиму маршрутизации, заданному на Hub.
- Если включён экспериментальный режим **full multipath**, ответ может содержать больше путей и дополнительные поля диагностики, но контракт (наличие `max_amount`) сохраняется.

### 5.3. Создать платёж

```http
POST /payments
Authorization: Bearer {token}
Idempotency-Key: {key}   # optional
Content-Type: application/json

{
  "to": "recipient_pid",
  "equivalent": "UAH",
  "amount": "100.00",
  "description": "За услуги",
  "constraints": {
    "max_hops": 4,
    "max_paths": 3,
    "avoid": ["pid_to_avoid"],
    "timeout_ms": 5000
  },
  "signature": "base64_signature"
}
```

**`constraints` (опционально):**
- `max_hops` — ограничение на число hops в маршруте
- `max_paths` — ограничение на количество альтернативных путей
- `timeout_ms` — бюджет времени на маршрутизацию/выполнение
- `avoid` — список PID, которых следует избегать при построении маршрутов

**Idempotency-Key (опционально):** если повторить запрос с тем же ключом, сервер вернёт тот же результат. Если ключ повторно используется с другим payload — вернётся `409 Conflict`.

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

### 5.4. История платежей

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

### 5.5. Детали платежа

```http
GET /payments/{tx_id}
Authorization: Bearer {token}
```

---

## 6. Баланс

### 6.0. Эквиваленты (справочник)

Назначение: получить read-only список эквивалентов, известных данному Hub. Используется для автодополнения и валидации в клиентских приложениях.

```http
GET /equivalents
Authorization: Bearer {token}
```

**Response:**
```json
{
  "items": [
    {
      "code": "UAH",
      "symbol": "₴",
      "description": "Ukrainian hryvnia",
      "precision": 2,
      "metadata": {"country": "UA"},
      "is_active": true,
      "created_at": "2025-11-29T12:00:00Z",
      "updated_at": "2025-11-29T12:00:00Z"
    }
  ]
}
```

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

В MVP v0.1 отдельного endpoint `GET /balance/history` **нет**. Для истории/активности используйте `GET /payments` с фильтрами `equivalent`, `from_date`, `to_date`, `direction`.

---

## 7. Клиринг

Канонический контракт — в [api/openapi.yaml](../../api/openapi.yaml).

### 7.1. Список циклов долгов (по эквиваленту)

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

### 7.2. Автоклиринг циклов (по эквиваленту)

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

## 8. Коды ошибок

### 8.1. Таблица кодов

HTTP-статус может зависеть от контекста; таблица ниже отражает типичное сопоставление, используемое бэкендом.

| Код | HTTP | Описание |
|-----|------|----------|
| `E001` | 400/404 | Маршрут не найден / Not found |
| `E002` | 400 | Недостаточная ёмкость для платежа |
| `E003` | 400 | Превышен лимит линии доверия |
| `E004` | 400 | Линия доверия не активна |
| `E005` | 400 | Неверная подпись |
| `E006` | 401/403 | Не авторизован / Недостаточно прав |
| `E007` | 504 | Таймаут операции |
| `E008` | 409 | Конфликт состояний |
| `E009` | 400/429 | Ошибка валидации / Слишком много запросов |
| `E010` | 500 | Внутренняя ошибка |

### 8.2. Пример ошибки

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

Полная OpenAPI 3.0 спецификация доступна:
- JSON: `/openapi.json`
- YAML (в репозитории): `api/openapi.yaml`
- Swagger UI: `/docs`
- ReDoc: `/redoc`

---

## Связанные документы

- [00-overview.md](00-overview.md) — Обзор проекта
- [02-protocol-spec.md](02-protocol-spec.md) — Спецификация протокола
- [03-architecture.md](03-architecture.md) — Архитектура системы
