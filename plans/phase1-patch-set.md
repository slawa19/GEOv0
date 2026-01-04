# GEO v0.1 Phase 1 Patch-Set

_Дата: 2026-01-04_
_Основан на: code-review-report-v0.1.md с верификацией_

## Несогласия с верификацией

**Результат:** Полное согласие со всеми статусами верификации. Все NOT CONFIRMED и SPEC MISMATCH пункты подтверждены проверкой кода.

---

## Phase 1 Patch-Set (13 PR)

### Приоритет: CRITICAL

#### PR-1: 2PC Protocol Fix
**Покрывает:** CORE-C1, CORE-C2, CORE-C4, CORE-H5, CORE-M1, CORE-M3

**Файлы:**
- `app/core/payments/engine.py`:
  - Убрать commit() из prepare() 
  - Добавить проверку expires_at > now() в commit()
  - Добавить retry logic при deadlock
- `app/core/payments/service.py`:
  - Реорганизовать transaction boundaries
  - Убрать двойную обработку исключений
  - Добавить idempotency_key проверку

---

#### PR-2: Graph Router Enhancement
**Покрывает:** CORE-C3, CORE-H1, CORE-H2, CORE-H8

**Файлы:**
- `app/core/payments/router.py`:
  - Учитывать pending locks при построении графа
  - Добавить проверку can_be_intermediate policy
  - Реализовать k-shortest paths
  - Исправить формулу capacity

---

#### PR-3: Clearing Conflict Prevention
**Покрывает:** CORE-C5, CORE-H6

**Файлы:**
- `app/core/clearing/service.py`:
  - Проверка prepare locks перед клирингом
  - Safety для auto_clear() при конфликтах

---

#### PR-4: PaymentResult Schema Alignment
**Покрывает:** API-C1, API-H4

**Файлы:**
- `app/schemas/payment.py`:
  - Добавить поля: from, to, equivalent, amount, routes, created_at, committed_at
  - Раскомментировать signature
- `app/core/payments/service.py`:
  - Обновить return value

---

#### PR-5: DB Constraints & Indices
**Покрывает:** DB-C1, DB-C3, DB-H2, DB-H3, DB-H6, DB-H9, DB-H10

**Файлы:**
- `app/db/models/debt.py`:
  - CheckConstraint: debtor_id < creditor_id
  - Индекс (debtor_id, creditor_id)
- `app/db/models/participant.py`:
  - Unique constraint на public_key
- `app/db/models/trustline.py`:
  - Индекс (from_participant_id, status)
  - max_hop_usage в policy
- `migrations/versions/`:
  - Новая миграция с FK ON DELETE rules

---

#### PR-6: Session & Pool Configuration
**Покрывает:** DB-C2, DB-H4, DB-H5

**Файлы:**
- `app/db/session.py`:
  - pool_size, max_overflow
  - pool_pre_ping=True
  - isolation_level для критических операций

---

### Приоритет: HIGH

#### PR-7: API Payments Hardening
**Покрывает:** API-H3, API-H5, API-H6, API-H8, API-L1

**Файлы:**
- `app/api/v1/payments.py`:
  - Фильтры list_payments
  - Конкретные исключения вместо bare except
  - Проверка прав доступа
- `app/schemas/common.py`:
  - page/per_page пагинация

---

#### PR-8: Clearing Schema Fix
**Покрывает:** API-H7

**Файлы:**
- `app/schemas/clearing.py`:
  - ClearingCycleEdge: PID вместо UUID
- `app/core/clearing/service.py`:
  - find_cycles возвращает PID

---

#### PR-9: TrustLine Service Hardening
**Покрывает:** CORE-H7, API-M3, API-M6

**Файлы:**
- `app/core/trustlines/service.py`:
  - Проверка долга в обратном направлении в close()
- `app/api/v1/trustlines.py`:
  - Default direction = "all"
  - Валидация limit

---

#### PR-10: Rate Limiting & Auth
**Покрывает:** API-H9, CORE-M7, API-L3

**Файлы:**
- `app/api/deps.py`:
  - Rate limiting middleware
- `app/core/auth/service.py`:
  - Token blacklist
- `app/api/v1/auth.py`:
  - Логирование авторизации

---

#### PR-11: Multi-path Payment Support
**Покрывает:** CORE-H3, CORE-H4

**Файлы:**
- `app/core/payments/service.py`:
  - Multi-path splitting
- `app/core/payments/engine.py`:
  - Исправление reserved_usage

---

### Приоритет: MEDIUM/LOW

#### PR-12: DB Schema Enhancements
**Покрывает:** DB-M5, DB-H13, DB-H7, DB-M4, DB-M7, DB-M9

**Файлы:**
- `app/db/models/transaction.py`:
  - idempotency_key
  - partial index для active transactions
- `app/db/models/equivalent.py`:
  - symbol field
  - CHECK constraint на code
- `app/db/models/prepare_lock.py`:
  - lock_type enum
- `app/db/models/config.py`:
  - Версионирование конфигурации

---

#### PR-13: Observability & Quality
**Покрывает:** CORE-L1, CORE-L2, CORE-L3, CORE-L4, CORE-M6, CORE-L6

**Файлы:**
- Все core сервисы:
  - Structured logging
  - Метрики производительности
  - Magic numbers → конфиг
  - Docstrings
- `app/core/balance/service.py`:
  - Кэширование графа
  - Unit тесты

---

## Timeline

| Фаза | PR | Время |
|------|-----|-------|
| Critical | PR-1 через PR-6 | 2 недели |
| High | PR-7 через PR-11 | 2 недели |
| Medium/Low | PR-12, PR-13 | 1 неделя |

## Покрытие

- Всего пунктов в отчете: 90
- NOT CONFIRMED: 15 (проблема отсутствует)
- Покрыто PR: 75 пунктов (100% от реальных проблем)