# GEO v0.1 — Roadmap реализации (код + документация)

**Версия:** 1.0  
**Дата:** 2026-01-06  
**Статус:** Draft  

Этот документ — исполнимый план работ по реализации FIX из:
- [`plans/remediation-spec-v0.1-2026-01-06.md`](remediation-spec-v0.1-2026-01-06.md) (источник истины по требованиям и критериям)
- [`plans/audit-final-report-v0.1.md`](audit-final-report-v0.1.md) (итоговая сводка/риски/обоснование приоритетов)

---

## 0) Общие правила выполнения работ

- Каждый FIX закрывается отдельным PR (или набором PR), включающим:
  - изменения кода,
  - обновление [`api/openapi.yaml`](../api/openapi.yaml) (если меняются схемы/эндпоинты/подписываемые payload),
  - тесты (unit и/или integration) и зелёный прогон `pytest`.
- Для Postgres-специфичных доработок (например, advisory locks) тесты должны выполняться на Postgres.
- Любые изменения форматов идентификаторов/подписей (FIX-001/FIX-015) требуют заранее согласованной стратегии миграции и окна cutover.

---

## 1) Подготовка (pre-work)

### 1.1 Окружение

- Dev/staging окружение с Postgres.
- Alembic миграции прогоняются на пустой базе и на staging-копии.

### 1.2 Решения, которые нужно принять до реализации

- **FIX-001 (PID):** стратегия миграции (dual field vs cutover), таблицы-референсы, rollback.
- **FIX-015 (подписи):** точный состав подписываемых полей для registration/payment/trustlines, canonicalization правила.
- **FIX-022 (нулевые долги):** выбрать политику:
  - A: удалять `Debt` при `amount==0`, или
  - B: хранить нули и игнорировать их во всех инвариантах.
- **FIX-023 (daily_limit):** реализовать enforcement или явно исключить из MVP (и синхронизировать API/docs).

---

## 2) Фаза P0 — критические доработки (production-blocker)

Цель: закрыть протокольно-критические несоответствия и гонки целостности.

### 2.1 Scope (FIX)

- FIX-004 — `/auth/refresh`
- FIX-015 — canonical JSON signatures
- FIX-016 — oversubscription race в prepare (advisory locks)
- FIX-017 — enforce `auto_clearing`
- FIX-003 — trust limit invariant
- FIX-002 — zero-sum invariant (формальный)
- FIX-001 — PID generation + миграция

### 2.2 Рекомендуемая последовательность PR

1) **PR-P0-1: FIX-004 /auth/refresh**
- Код: `app/api/v1/auth.py`, `app/core/auth/service.py`, `app/schemas/auth.py`
- Контракт: `api/openapi.yaml`
- Тесты:
  - refresh принимает только refresh-токен
  - reuse старого refresh → 401

2) **PR-P0-2: FIX-015 canonical JSON (registration/payment)**
- Код: новый модуль `app/core/auth/canonical.py` + приведение подписываемых payload
- Контракт: явное описание signed payload в `api/openapi.yaml`
- Тесты:
  - `canonical_json(payload)` детерминированен
  - подпись/верификация воспроизводима

3) **PR-P0-3: FIX-016 advisory locks в prepare**
- Код: `app/core/payments/engine.py` (`prepare_routes()`)
- Тесты (integration, Postgres):
  - два параллельных prepare через общий сегмент не приводят к суммарному превышению capacity

4) **PR-P0-4: FIX-017 enforce auto_clearing**
- Код: `app/core/clearing/service.py`
- Тесты:
  - цикл с любым ребром `auto_clearing=false` не исполняется

5) **PR-P0-5: FIX-002/003 invariants module + commit enforcement**
- Код: новый `app/core/invariants.py` + интеграция в `PaymentEngine.commit()`
- Тесты:
  - trust limit violation детектируется и приводит к отказу
  - zero-sum реализован как smoke-check согласно спецификации

6) **PR-P0-6: FIX-001 PID generation + миграции**
- Код: `app/core/auth/crypto.py` + зависимости + Alembic миграции
- Тесты:
  - корректность PID = `base58(sha256(pk))`
  - прогон миграции на staging (обязательный шаг перед merge в release)

### 2.3 DoD (Definition of Done) для P0

- [ ] Все PR-P0 закрыты и смёржены
- [ ] `pytest` зелёный (включая integration на Postgres для FIX-016)
- [ ] `api/openapi.yaml` соответствует коду (эндпоинты/схемы/подписи)
- [ ] План миграции PID прогнан на staging и документирован rollback

---

## 3) Фаза P1 — высокоприоритетные доработки (надёжность/контракт/политики)

Цель: усилить корректность модели, политики, контракт API и эксплуатационную предсказуемость.

### 3.1 Scope (FIX)

- FIX-005 — debt symmetry + взаимозачёт
- FIX-006 — cleanup stale locks (расширение existing recovery-loop)
- FIX-007 — таймауты согласно спецификации
- FIX-008 — equivalent code validation + DB CHECK
- FIX-018 — trustline signatures
- FIX-019 — blocked_participants routing
- FIX-020 — OpenAPI vs code (`ParticipantCreateRequest.type`)
- FIX-021 — transaction state machine doc/align

### 3.2 Рекомендуемая последовательность PR

1) **PR-P1-1: FIX-005 debt symmetry**
- Код: `app/core/invariants.py` (проверка) + `app/core/payments/engine.py::_apply_flow()` (взаимозачёт)
- Решение по FIX-022 (нулевые долги) желательно принять до/во время этого PR

2) **PR-P1-2: FIX-006 recovery hardening**
- Код: `app/core/recovery.py`
- Добавить: тесты, метрики/алерты, конфиг (без второго scheduler)

3) **PR-P1-3: FIX-007 timeouts**
- Код: `app/config.py`, `app/core/payments/service.py`
- Тесты: базовые на корректность timeouts/ошибок

4) **PR-P1-4: FIX-008 equivalent code CHECK**
- Код: `app/utils/validation.py` + миграция constraint
- Тесты: схема/валидация

5) **PR-P1-5: FIX-018 trustline signatures**
- Требует FIX-015 (canonical JSON)
- Код: `app/core/trustlines/service.py`, `app/api/v1/trustlines.py`, `api/openapi.yaml`
- Тесты: неверная подпись → 400, валидная → success

6) **PR-P1-6: FIX-019 blocked_participants routing**
- Код: `app/core/payments/router.py`

7) **PR-P1-7: FIX-020 Participant.type sync**
- Код+контракт: `app/schemas/participant.py` и `api/openapi.yaml`

8) **PR-P1-8: FIX-021 transaction state machine**
- Документация: state diagram + переходы по типам
- (Минимально) валидация переходов или явная фиксация как extension

### 3.3 DoD для P1

- [ ] Все FIX P1 закрыты PR’ами с тестами
- [ ] Контракт OpenAPI синхронизирован
- [ ] Документация отражает подписи/policies/state machine

---

## 4) Фаза P2 — полнота реализации и эксплуатируемость

Цель: протокольная полнота по integrity/clearing, трассируемость, консистентность данных.

### 4.1 Scope (FIX)

- FIX-009 — integrity endpoints
- FIX-010 — periodic integrity checks (расширение existing loop)
- FIX-011 — clearing neutrality
- FIX-012 — SQL cycle detection
- FIX-022 — policy по `Debt.amount==0`
- FIX-023 — daily_limit (или MVP out-of-scope)
- FIX-024 — Equivalent.metadata validation
- FIX-025 — clearing payload enrichment

### 4.2 Рекомендуемая последовательность PR

1) **PR-P2-1: FIX-009 integrity endpoints**
- Эндпоинты: `/integrity/status`, `/integrity/checksum/{equivalent}`, `/integrity/verify`, `/integrity/audit-log`
- Контракт: `api/openapi.yaml`

2) **PR-P2-2: FIX-010 periodic checks**
- Расширить existing background integrity-loop, сохранять результаты

3) **PR-P2-3: FIX-011 clearing neutrality**
- Инвариант до/после исполнения цикла

4) **PR-P2-4: FIX-012 SQL cycle detection**

5) **PR-P2-5: FIX-022 zero debt policy**
- Выбранная стратегия применяется в engine/clearing/invariants

6) **PR-P2-6: FIX-025 clearing payload enrichment**
- Рекомендовано до/вместе с audit-log (FIX-014) для пригодности данных

7) **PR-P2-7: FIX-024 Equivalent.metadata validation**

8) **PR-P2-8: FIX-023 daily_limit decision**
- Реализация или документированное исключение из MVP

### 4.3 DoD для P2

- [ ] Реализован минимальный протокольный набор integrity endpoints
- [ ] Периодические проверки сохраняют результаты и пригодны для мониторинга
- [ ] Clearing трассируем (payload) и проверяется нейтралити

---

## 5) Фаза P3 — release polish

### 5.1 Scope (FIX)

- FIX-013 — error codes
- FIX-014 — integrity audit log

### 5.2 DoD для P3

- [ ] Единые коды ошибок используются во всех ключевых местах
- [ ] Audit log позволяет воспроизводимо анализировать результаты verify/проверок

---

## 6) План доработки документации (параллельно фазам)

### 6.1 Принципы

- `api/openapi.yaml` — источник истины для API reference.
- Документация обновляется в каждом PR, который меняет контракт/подписи/поля.

### 6.2 Конкретные шаги

- **После P0:**
  - README: PID и refresh flow
  - Документировать canonical signatures (примеры payload + подпись)
- **После P1:**
  - TrustLines: подписи и политики
  - Transaction state machine: диаграмма и переходы
- **После P2/P3:**
  - Integrity endpoints: статус/verify/audit-log
  - Синхронизация EN/PL с OpenAPI

---

## 7) Контрольные точки (milestones)

- **M1 (после P0):** протокольно-критические несоответствия закрыты
- **M2 (после P1):** надёжность/политики/контракт стабилизированы
- **M3 (после P2):** полнота integrity/clearing + эксплуатируемость
- **M4 (после P3):** production-ready release
