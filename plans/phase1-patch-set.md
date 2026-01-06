# GEO v0.1 Phase 1 Patch-Set (P0)

_Дата: 2026-01-06_  
_Спецификация (источник истины): [`plans/remediation-spec-v0.1-2026-01-06.md`](remediation-spec-v0.1-2026-01-06.md)_

## Заметки об изменениях (почему план обновлён)

- Ранее этот patch-set ссылался на `remediation-spec-v0.1.md` и включал задачи, которые уже реализованы в коде (например, global rate limiting, background recovery/integrity loop) или относятся к более поздним приоритетам.
- Этот документ приведён в соответствие с актуальной standalone-спецификацией и фокусируется на закрытии P0 (критических) FIX.

---

## Phase 1 Patch-Set (P0) — 6 PR

Цель фазы: закрыть FIX-001/002/003/004/015/016/017.

### PR-1: /auth/refresh endpoint (FIX-004)

**Файлы (минимум):**
- `app/api/v1/auth.py`
- `app/schemas/auth.py`
- `app/core/auth/service.py`
- `api/openapi.yaml`

**Критерии приёмки:**
- refresh принимает только refresh-токен (access отклоняется)
- старый refresh-токен становится недействительным после успешного refresh

---

### PR-2: Canonical JSON для подписей (FIX-015)

**Файлы (минимум):**
- Новый `app/core/auth/canonical.py`
- Обновление подписываемых payload в registration/payment
- `api/openapi.yaml` (явно описать подписываемые поля)

**Критерии приёмки:**
- `canonical_json(payload)` детерминированен
- подпись/верификация для одного и того же payload воспроизводима

---

### PR-3: Advisory locks против oversubscription в prepare (FIX-016)

**Файлы:**
- `app/core/payments/engine.py`

**Критерии приёмки:**
- два параллельных prepare на общем сегменте не приводят к превышению лимитов

---

### PR-4: Enforce auto_clearing (FIX-017)

**Файлы:**
- `app/core/clearing/service.py`

**Критерии приёмки:**
- цикл с любым ребром `auto_clearing=false` не исполняется

---

### PR-5: Invariants: zero-sum + trust limits (FIX-002, FIX-003)

**Файлы:**
- Новый `app/core/invariants.py`
- Интеграция в `app/core/payments/engine.py` (проверки после применения flows)

**Критерии приёмки:**
- есть тесты на trust limit
- zero-sum реализован как формальная smoke-check (как в спецификации)

---

### PR-6: PID generation + миграция (FIX-001)

**Файлы:**
- `app/core/auth/crypto.py`
- миграции Alembic + скрипт пересчёта PID

**Критерии приёмки:**
- новый PID = `base58(sha256(public_key))`
- миграция обновляет связанные таблицы и проходит на staging

---

## Gate для завершения Phase 1

- Все P0 FIX закрыты и покрыты тестами
- OpenAPI синхронизирован с реализацией
- План миграции PID прогнан на staging (с rollback)