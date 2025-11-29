# GEO Protocol: Полная спецификация v0.1

**Версия:** 0.1  
**Дата:** Ноябрь 2025  
**Статус:** Финальная спецификация

---

## Содержание

1. [Назначение и область применения](#1-назначение-и-область-применения)
2. [Криптографические примитивы](#2-криптографические-примитивы)
3. [Модель данных](#3-модель-данных)
4. [Протокол сообщений](#4-протокол-сообщений)
5. [Операции с линиями доверия](#5-операции-с-линиями-доверия)
6. [Платежи](#6-платежи)
7. [Клиринг](#7-клиринг)
8. [Межхабовое взаимодействие](#8-межхабовое-взаимодействие)
9. [Обработка ошибок и восстановление](#9-обработка-ошибок-и-восстановление)
10. [Разрешение споров](#10-разрешение-споров)

---

## 1. Назначение и область применения

### 1.1. Цели протокола

GEO v0.1 — это протокол для:

- **P2P-экономики взаимного кредита** между участниками и сообществами
- **Без единой валюты** — только обязательства в произвольных эквивалентах
- **Без глобального леджера** — только локальные состояния и подписи
- **С автоматическим клирингом** замкнутых циклов долгов

### 1.2. Принципы проектирования

| Принцип | Реализация |
|---------|------------|
| **Простота** | Проверенные алгоритмы (BFS, 2PC), минимум сущностей |
| **Локальность** | Консенсус только между затронутыми участниками |
| **Расширяемость** | Протокол отделён от транспорта |
| **Безопасность** | Криптографические подписи на всех операциях |

### 1.3. Ограничения версии 0.1

- Hub выступает координатором транзакций
- Максимальная длина пути платежа: 6 звеньев
- Клиринг циклов: 3–6 узлов
- Multi-path: до 3 маршрутов на платёж

---

## 2. Криптографические примитивы

### 2.1. Схема подписи

**Алгоритм:** Ed25519 (Edwards-curve Digital Signature Algorithm)

| Параметр | Значение |
|----------|----------|
| Кривая | Curve25519 |
| Размер приватного ключа | 32 байта |
| Размер публичного ключа | 32 байта |
| Размер подписи | 64 байта |

### 2.2. Хеширование

**Алгоритм:** SHA-256

Используется для:
- Генерации PID из публичного ключа
- Вычисления `tx_id` как хеша содержимого транзакции
- Верификации целостности данных

### 2.3. Идентификатор участника (PID)

```
PID = base58(sha256(public_key))
```

- Входные данные: 32 байта публичного ключа Ed25519
- Хеширование: SHA-256 → 32 байта
- Кодирование: Base58 → строка ~44 символа

**Пример:**
```
public_key: 0x3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29
PID: "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"
```

### 2.4. Формат подписи

```json
{
  "signer": "PID",
  "signature": "base64(ed25519_sign(message))",
  "timestamp": "ISO8601"
}
```

**Подписываемое сообщение:** канонический JSON payload без поля `signatures`.

---

## 3. Модель данных

### 3.1. Participant (Участник)

```json
{
  "pid": "string (base58)",
  "public_key": "bytes (32)",
  "display_name": "string",
  "profile": {
    "type": "person | organization | hub",
    "description": "string",
    "contacts": {}
  },
  "status": "active | suspended | left | deleted",
  "verification_level": "integer (0-3)",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Статусы:**

| Статус | Описание |
|--------|----------|
| `active` | Активный участник |
| `suspended` | Временно заморожен |
| `left` | Вышел из сообщества |
| `deleted` | Удалён |

### 3.2. Equivalent (Эквивалент)

```json
{
  "code": "string (unique)",
  "precision": "integer (0-8)",
  "description": "string",
  "metadata": {
    "type": "fiat | time | commodity | custom",
    "iso_code": "string (optional)"
  },
  "created_at": "ISO8601"
}
```

**Правила:**
- `code` — уникальный, 1–16 символов, A-Z0-9_
- `precision` — количество знаков после запятой (0–8)

### 3.3. TrustLine (Линия доверия)

```json
{
  "id": "uuid",
  "from": "PID",
  "to": "PID",
  "equivalent": "string (code)",
  "limit": "decimal",
  "policy": {
    "auto_clearing": true,
    "can_be_intermediate": true,
    "daily_limit": "decimal | null",
    "blocked_participants": ["PID"]
  },
  "status": "active | frozen | closed",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Инвариант:**
```
∀ (from, to, equivalent): debt[to→from] ≤ limit
```

**Политики:**

| Поле | Описание | По умолчанию |
|------|----------|--------------|
| `auto_clearing` | Автоматическое согласие на клиринг | `true` |
| `can_be_intermediate` | Разрешить использование как посредника | `true` |
| `daily_limit` | Лимит оборота в сутки | `null` (без лимита) |
| `blocked_participants` | Запрет маршрутов через указанных | `[]` |

### 3.4. Debt (Долг)

```json
{
  "id": "uuid",
  "debtor": "PID",
  "creditor": "PID",
  "equivalent": "string (code)",
  "amount": "decimal (> 0)",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Правила:**
- Для каждой тройки `(debtor, creditor, equivalent)` — одна запись
- `amount` всегда > 0 (нулевые записи удаляются)
- Обновляется атомарно в рамках транзакций

### 3.5. Transaction (Транзакция)

```json
{
  "tx_id": "string (uuid | hash)",
  "type": "TRUST_LINE_CREATE | TRUST_LINE_UPDATE | TRUST_LINE_CLOSE | PAYMENT | CLEARING",
  "initiator": "PID",
  "payload": { /* type-specific */ },
  "signatures": [
    {
      "signer": "PID",
      "signature": "base64",
      "timestamp": "ISO8601"
    }
  ],
  "state": "string",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

---

## 4. Протокол сообщений

### 4.1. Базовый формат сообщения

```json
{
  "msg_id": "uuid",
  "msg_type": "string",
  "tx_id": "string | null",
  "from": "PID",
  "to": "PID | null",
  "payload": { /* type-specific */ },
  "signature": "base64(ed25519_sign(canonical_json))"
}
```

### 4.2. Типы сообщений

#### 4.2.1. Управление TrustLines

| Тип | Описание |
|-----|----------|
| `TRUST_LINE_CREATE` | Создание линии доверия |
| `TRUST_LINE_UPDATE` | Изменение лимита/политики |
| `TRUST_LINE_CLOSE` | Закрытие линии |

#### 4.2.2. Платежи

| Тип | Описание |
|-----|----------|
| `PAYMENT_REQUEST` | Запрос платежа (клиент → hub) |
| `PAYMENT_PREPARE` | Подготовка (hub → участники) |
| `PAYMENT_PREPARE_ACK` | Ответ на подготовку |
| `PAYMENT_COMMIT` | Фиксация |
| `PAYMENT_ABORT` | Отмена |

#### 4.2.3. Клиринг

| Тип | Описание |
|-----|----------|
| `CLEARING_PROPOSE` | Предложение клиринга |
| `CLEARING_ACCEPT` | Согласие участника |
| `CLEARING_REJECT` | Отказ участника |
| `CLEARING_COMMIT` | Фиксация клиринга |
| `CLEARING_ABORT` | Отмена клиринга |

#### 4.2.4. Служебные

| Тип | Описание |
|-----|----------|
| `PING` | Проверка связи |
| `PONG` | Ответ на PING |
| `ERROR` | Сообщение об ошибке |

### 4.3. Транспорт

Протокол независим от транспорта. Рекомендуемые варианты:

| Транспорт | Использование |
|-----------|---------------|
| **HTTPS + JSON** | REST API для клиентов |
| **WebSocket + JSON** | Real-time уведомления |
| **gRPC + Protobuf** | Межхабовое взаимодействие |

---

## 5. Операции с линиями доверия

### 5.1. TRUST_LINE_CREATE

**Payload:**
```json
{
  "from": "PID_A",
  "to": "PID_B",
  "equivalent": "UAH",
  "limit": 1000.00,
  "policy": {
    "auto_clearing": true,
    "can_be_intermediate": true
  }
}
```

**Требования:**
- Подпись `from` (A) обязательна
- Не существует активной линии `(from, to, equivalent)`
- `limit` > 0

**Алгоритм:**
1. Проверить подпись A
2. Проверить уникальность линии
3. Валидировать policy
4. Создать запись TrustLine
5. Создать транзакцию `TRUST_LINE_CREATE (COMMITTED)`

### 5.2. TRUST_LINE_UPDATE

**Payload:**
```json
{
  "trust_line_id": "uuid",
  "limit": 1500.00,
  "policy": { /* обновлённые поля */ }
}
```

**Требования:**
- Подпись владельца (`from`) обязательна
- Линия существует и `status = active`
- Новый `limit` ≥ текущий `debt[to→from]`

**Алгоритм:**
1. Проверить подпись владельца
2. Проверить, что limit не ниже текущего долга
3. Обновить запись TrustLine
4. Создать транзакцию `TRUST_LINE_UPDATE (COMMITTED)`

### 5.3. TRUST_LINE_CLOSE

**Payload:**
```json
{
  "trust_line_id": "uuid"
}
```

**Требования:**
- Подпись владельца обязательна
- `debt[to→from] = 0` (долг погашен)

**Алгоритм:**
1. Проверить подпись владельца
2. Проверить отсутствие долга
3. Установить `status = closed`
4. Создать транзакцию `TRUST_LINE_CLOSE (COMMITTED)`

---

## 6. Платежи

### 6.1. Обзор процесса

```
                    ┌──────────────────┐
                    │ PAYMENT_REQUEST  │
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │    Routing       │
                    │  (find paths)    │
                    └────────┬─────────┘
                             ▼
              ┌──────────────────────────────┐
              │        PREPARE Phase         │
              │  (reserve on all edges)      │
              └──────────────┬───────────────┘
                             │
              ┌──────────────┴───────────────┐
              ▼                              ▼
       ┌────────────┐                 ┌────────────┐
       │ All OK     │                 │ Any FAIL   │
       └─────┬──────┘                 └─────┬──────┘
             ▼                              ▼
      ┌────────────┐                 ┌────────────┐
      │   COMMIT   │                 │   ABORT    │
      │ (apply)    │                 │ (release)  │
      └────────────┘                 └────────────┘
```

### 6.2. PAYMENT_REQUEST

**Сообщение клиента к Hub'у:**

```json
{
  "msg_type": "PAYMENT_REQUEST",
  "from": "PID_A",
  "payload": {
    "to": "PID_B",
    "equivalent": "UAH",
    "amount": 100.00,
    "description": "Оплата за услуги",
    "constraints": {
      "max_hops": 4,
      "max_paths": 3,
      "timeout_ms": 5000,
      "avoid": ["PID_X"]
    }
  },
  "signature": "..."
}
```

**Constraints:**

| Поле | Описание | По умолчанию |
|------|----------|--------------|
| `max_hops` | Максимальная длина пути | 6 |
| `max_paths` | Максимум маршрутов | 3 |
| `timeout_ms` | Таймаут транзакции | 5000 |
| `avoid` | Исключить участников | [] |

### 6.3. Маршрутизация

#### 6.3.1. Расчёт доступного кредита

Для ориентированного ребра `(A→B, E)`:

```
available_credit(A→B, E) = limit(A→B, E) - debt[B→A, E]
```

Где:
- `limit(A→B, E)` — лимит TrustLine от A к B в эквиваленте E
- `debt[B→A, E]` — текущий долг B перед A в эквиваленте E

#### 6.3.2. Алгоритм поиска путей (k-shortest paths)

```python
def find_k_paths(graph, source, target, k=3, max_hops=6):
    """
    Модифицированный алгоритм Йена для k кратчайших путей
    с учётом available_credit как "веса"
    """
    paths = []
    
    # Первый путь - BFS по максимальному available_credit
    path1 = bfs_max_capacity(graph, source, target, max_hops)
    if path1:
        paths.append(path1)
    
    # Остальные пути - итеративное удаление рёбер
    for i in range(1, k):
        candidates = []
        for j in range(len(paths[i-1]) - 1):
            # Временно удалить ребро
            spur_node = paths[i-1][j]
            # Найти альтернативный путь
            alt_path = bfs_max_capacity(
                graph_without_edge(graph, paths[i-1][j], paths[i-1][j+1]),
                spur_node, target, max_hops - j
            )
            if alt_path:
                candidates.append(paths[i-1][:j] + alt_path)
        
        if candidates:
            # Выбрать путь с максимальной ёмкостью
            paths.append(max(candidates, key=path_capacity))
    
    return paths
```

#### 6.3.3. Расчёт ёмкости пути

```
capacity(path) = min(available_credit(edge) for edge in path)
```

#### 6.3.4. Multi-path разбиение

```python
def split_payment(amount, paths):
    """
    Разбить платёж по нескольким маршрутам
    """
    result = []
    remaining = amount
    
    for path in sorted(paths, key=path_capacity, reverse=True):
        cap = path_capacity(path)
        use = min(cap, remaining)
        if use > 0:
            result.append({
                "path": path,
                "amount": use
            })
            remaining -= use
        
        if remaining <= 0:
            break
    
    if remaining > 0:
        raise InsufficientCapacity(f"Cannot route {amount}, missing {remaining}")
    
    return result
```

### 6.4. Транзакция PAYMENT

```json
{
  "tx_id": "uuid",
  "type": "PAYMENT",
  "initiator": "PID_A",
  "payload": {
    "from": "PID_A",
    "to": "PID_B",
    "equivalent": "UAH",
    "total_amount": 100.00,
    "description": "Оплата за услуги",
    "routes": [
      {
        "path": ["PID_A", "PID_X", "PID_B"],
        "amount": 60.00
      },
      {
        "path": ["PID_A", "PID_Y", "PID_Z", "PID_B"],
        "amount": 40.00
      }
    ]
  },
  "signatures": [...],
  "state": "NEW"
}
```

### 6.5. Фаза PREPARE

#### 6.5.1. Сообщение PAYMENT_PREPARE

Hub отправляет каждому участнику маршрута:

```json
{
  "msg_type": "PAYMENT_PREPARE",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_X",
  "payload": {
    "equivalent": "UAH",
    "local_effects": [
      {
        "edge": ["PID_A", "PID_X"],
        "direction": "incoming",
        "delta": 60.00
      },
      {
        "edge": ["PID_X", "PID_B"],
        "direction": "outgoing",
        "delta": 60.00
      }
    ],
    "timeout_at": "ISO8601"
  },
  "signature": "..."
}
```

#### 6.5.2. Проверки участника

```python
def validate_prepare(participant, effects):
    for effect in effects:
        edge = effect["edge"]
        delta = effect["delta"]
        
        if effect["direction"] == "outgoing":
            # Участник становится должен следующему
            next_pid = edge[1]
            trust_line = get_trust_line(next_pid, participant.pid)
            
            if not trust_line or trust_line.status != "active":
                return FAIL("No active trust line")
            
            current_debt = get_debt(participant.pid, next_pid)
            new_debt = current_debt + delta
            
            if new_debt > trust_line.limit:
                return FAIL("Exceeds trust limit")
            
            # Проверка политик
            if not trust_line.policy.can_be_intermediate:
                return FAIL("Not allowed as intermediate")
        
        elif effect["direction"] == "incoming":
            # Участник получает долг от предыдущего
            prev_pid = edge[0]
            trust_line = get_trust_line(participant.pid, prev_pid)
            
            if not trust_line or trust_line.status != "active":
                return FAIL("No active trust line")
    
    return OK()
```

#### 6.5.3. Резервирование

При успешной валидации участник:
1. Создаёт запись `prepare_lock` с `tx_id` и эффектами
2. Уменьшает `available_credit` на величину резерва
3. Отвечает `PAYMENT_PREPARE_ACK (OK)`

```json
{
  "msg_type": "PAYMENT_PREPARE_ACK",
  "tx_id": "uuid",
  "from": "PID_X",
  "payload": {
    "status": "OK"
  },
  "signature": "..."
}
```

### 6.6. Фаза COMMIT

#### 6.6.1. Сообщение PAYMENT_COMMIT

```json
{
  "msg_type": "PAYMENT_COMMIT",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_X",
  "signature": "..."
}
```

#### 6.6.2. Применение изменений

```python
def apply_commit(participant, tx_id):
    lock = get_prepare_lock(tx_id)
    if not lock:
        return  # Идемпотентность
    
    for effect in lock.effects:
        if effect["direction"] == "outgoing":
            # Увеличить долг участника
            update_debt(
                debtor=participant.pid,
                creditor=effect["edge"][1],
                delta=effect["delta"]
            )
        elif effect["direction"] == "incoming":
            # Увеличить долг перед участником
            update_debt(
                debtor=effect["edge"][0],
                creditor=participant.pid,
                delta=effect["delta"]
            )
    
    delete_prepare_lock(tx_id)
```

### 6.7. Фаза ABORT

```json
{
  "msg_type": "PAYMENT_ABORT",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_X",
  "payload": {
    "reason": "Timeout on PID_Y"
  },
  "signature": "..."
}
```

Участник освобождает резерв без изменения долгов.

### 6.8. Стейт-машина PAYMENT

```
        ┌─────┐
        │ NEW │
        └──┬──┘
           │ routing complete
           ▼
      ┌────────┐
      │ ROUTED │
      └───┬────┘
          │ send PREPARE
          ▼
┌─────────────────────┐
│ PREPARE_IN_PROGRESS │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │             │
    ▼             ▼
┌─────────┐  ┌─────────┐
│COMMITTED│  │ ABORTED │
└─────────┘  └─────────┘
```

### 6.9. Таймауты

| Этап | Таймаут | Действие при истечении |
|------|---------|------------------------|
| Routing | 500 мс | ABORT (no routes) |
| PREPARE | 3 сек | ABORT (timeout) |
| COMMIT | 5 сек | Retry, затем ABORT |
| Общий | 10 сек | ABORT |

---

## 7. Клиринг

### 7.1. Обзор

Клиринг — автоматический взаимозачёт долгов в замкнутых циклах.

### 7.2. Поиск циклов

#### 7.2.1. Триггерный поиск (после каждой транзакции)

```python
def find_cycles_triggered(changed_edges, max_length=4):
    """
    Поиск коротких циклов вокруг изменённых рёбер
    """
    cycles = []
    
    for edge in changed_edges:
        # Поиск циклов длины 3
        cycles += find_triangles(edge)
        
        # Поиск циклов длины 4
        cycles += find_quadrangles(edge)
    
    return deduplicate(cycles)
```

#### 7.2.2. Периодический поиск

| Длина цикла | Частота | Алгоритм |
|-------------|---------|----------|
| 3 узла | После каждой TX | SQL JOIN |
| 4 узла | После каждой TX | SQL JOIN |
| 5 узлов | Каждый час | DFS с ограничением |
| 6 узлов | Раз в сутки | DFS с ограничением |

#### 7.2.3. SQL для поиска треугольников

```sql
SELECT DISTINCT 
    d1.debtor as a,
    d1.creditor as b,
    d2.creditor as c,
    LEAST(d1.amount, d2.amount, d3.amount) as clear_amount
FROM debts d1
JOIN debts d2 ON d1.creditor = d2.debtor 
             AND d1.equivalent = d2.equivalent
JOIN debts d3 ON d2.creditor = d3.debtor 
             AND d3.creditor = d1.debtor
             AND d2.equivalent = d3.equivalent
WHERE d1.equivalent = :equivalent
  AND LEAST(d1.amount, d2.amount, d3.amount) > :min_amount
ORDER BY clear_amount DESC
LIMIT 100;
```

### 7.3. Транзакция CLEARING

```json
{
  "tx_id": "uuid",
  "type": "CLEARING",
  "initiator": "HUB_PID",
  "payload": {
    "equivalent": "UAH",
    "cycle": ["PID_A", "PID_B", "PID_C", "PID_A"],
    "amount": 50.00
  },
  "signatures": [...],
  "state": "NEW"
}
```

### 7.4. Режимы согласия

#### 7.4.1. Авто-согласие (по умолчанию)

Если у всех участников цикла `policy.auto_clearing = true`:
- Клиринг применяется без явного подтверждения
- Отправляется `CLEARING_NOTICE` для информирования

#### 7.4.2. Явное согласие

Если хотя бы у одного `policy.auto_clearing = false`:
- Отправляется `CLEARING_PROPOSE` всем участникам
- Ожидается `CLEARING_ACCEPT` от всех
- При `CLEARING_REJECT` от кого-либо → `REJECTED`

### 7.5. Сообщения клиринга

#### CLEARING_PROPOSE

```json
{
  "msg_type": "CLEARING_PROPOSE",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_A",
  "payload": {
    "equivalent": "UAH",
    "cycle": ["PID_A", "PID_B", "PID_C", "PID_A"],
    "amount": 50.00,
    "your_effect": {
      "debt_to_reduce": ["PID_A", "PID_B", 50.00],
      "debt_from_reduce": ["PID_C", "PID_A", 50.00]
    },
    "expires_at": "ISO8601"
  },
  "signature": "..."
}
```

#### CLEARING_ACCEPT

```json
{
  "msg_type": "CLEARING_ACCEPT",
  "tx_id": "uuid",
  "from": "PID_A",
  "payload": {},
  "signature": "..."
}
```

### 7.6. Применение клиринга

```python
def apply_clearing(cycle, amount, equivalent):
    """
    Уменьшить долги по всем рёбрам цикла на amount
    """
    for i in range(len(cycle) - 1):
        debtor = cycle[i]
        creditor = cycle[i + 1]
        
        current_debt = get_debt(debtor, creditor, equivalent)
        new_debt = current_debt - amount
        
        if new_debt <= 0:
            delete_debt(debtor, creditor, equivalent)
        else:
            update_debt(debtor, creditor, equivalent, new_debt)
```

### 7.7. Стейт-машина CLEARING

```
     ┌─────┐
     │ NEW │
     └──┬──┘
        │
   ┌────┴────┐
   │         │
   ▼         ▼
(auto)    (explicit)
   │         │
   │    ┌─────────┐
   │    │PROPOSED │
   │    └────┬────┘
   │         │
   │    ┌────┴────┐
   │    │         │
   │    ▼         ▼
   │ ┌───────┐ ┌────────┐
   │ │WAITING│ │REJECTED│
   │ └───┬───┘ └────────┘
   │     │
   ▼     ▼
┌───────────┐
│ COMMITTED │
└───────────┘
```

---

## 8. Межхабовое взаимодействие

### 8.1. Принцип «Hub как участник»

Каждый Hub в федерации — обычный участник протокола:
- Имеет `PID` и пару ключей Ed25519
- Может открывать TrustLines с другими Hub'ами
- Участвует в платежах и клиринге как обычный узел

### 8.2. Регистрация Hub'а

```json
{
  "pid": "HUB_A_PID",
  "public_key": "...",
  "profile": {
    "type": "hub",
    "name": "Community A Hub",
    "description": "Локальное сообщество A",
    "endpoint": "https://hub-a.example.com",
    "supported_equivalents": ["UAH", "HOUR"]
  },
  "status": "active"
}
```

### 8.3. Межхабовые TrustLines

Hub A открывает линию доверия Hub B:

```json
{
  "from": "HUB_A_PID",
  "to": "HUB_B_PID",
  "equivalent": "UAH",
  "limit": 100000.00,
  "policy": {
    "auto_clearing": true,
    "settlement_schedule": "weekly",
    "max_single_payment": 10000.00
  }
}
```

### 8.4. Маршрутизация между сообществами

Платёж от участника A@HubA к участнику B@HubB:

```
A@HubA → ... → HubA → HubB → ... → B@HubB
```

**Алгоритм:**
1. HubA получает запрос от A
2. HubA определяет, что B принадлежит HubB
3. HubA ищет маршрут: A → ... → HubA
4. HubA→HubB добавляется к маршруту
5. HubB ищет маршрут: HubB → ... → B
6. Составной маршрут исполняется как единая транзакция

### 8.5. Протокол межхабового платежа

#### 8.5.1. INTER_HUB_PAYMENT_REQUEST

```json
{
  "msg_type": "INTER_HUB_PAYMENT_REQUEST",
  "from": "HUB_A_PID",
  "to": "HUB_B_PID",
  "payload": {
    "tx_id": "uuid",
    "original_sender": "PID_A",
    "final_recipient": "PID_B",
    "equivalent": "UAH",
    "amount": 500.00,
    "incoming_routes": [
      {"path": ["PID_A", "PID_X", "HUB_A_PID"], "amount": 500.00}
    ]
  },
  "signature": "..."
}
```

#### 8.5.2. INTER_HUB_PAYMENT_ACCEPT

```json
{
  "msg_type": "INTER_HUB_PAYMENT_ACCEPT",
  "from": "HUB_B_PID",
  "to": "HUB_A_PID",
  "payload": {
    "tx_id": "uuid",
    "outgoing_routes": [
      {"path": ["HUB_B_PID", "PID_Y", "PID_B"], "amount": 500.00}
    ]
  },
  "signature": "..."
}
```

### 8.6. Координация 2PC между Hub'ами

```
HubA                              HubB
  │                                 │
  │ INTER_HUB_PAYMENT_REQUEST       │
  │────────────────────────────────►│
  │                                 │
  │◄────────────────────────────────│
  │ INTER_HUB_PAYMENT_ACCEPT        │
  │                                 │
  │      Local PREPARE (both)       │
  │─────────────┬───────────────────│
  │             │                   │
  │ INTER_HUB_PREPARE_OK            │
  │◄────────────┼───────────────────│
  │             │                   │
  │             ▼                   │
  │ INTER_HUB_COMMIT                │
  │────────────────────────────────►│
  │                                 │
  │      Local COMMIT (both)        │
  │                                 │
```

### 8.7. Межхабовый клиринг

Hub'ы могут участвовать в циклах клиринга:

```
HubA → HubB → HubC → HubA
```

Клиринг происходит по стандартному протоколу CLEARING.

### 8.8. Транспорт между Hub'ами

| Вариант | Протокол | Использование |
|---------|----------|---------------|
| **REST** | HTTPS + JSON | Простой вариант |
| **gRPC** | HTTP/2 + Protobuf | Высокая производительность |
| **WebSocket** | WSS + JSON | Двусторонние уведомления |

---

## 9. Обработка ошибок и восстановление

### 9.1. Идемпотентность операций

**Правило:** любая операция с одинаковым `tx_id` должна давать одинаковый результат.

```python
def handle_commit(tx_id):
    tx = get_transaction(tx_id)
    
    if tx.state == "COMMITTED":
        return SUCCESS  # Уже применено
    
    if tx.state == "ABORTED":
        return FAIL  # Уже отменено
    
    # Применить изменения
    apply_changes(tx)
    tx.state = "COMMITTED"
    save_transaction(tx)
    return SUCCESS
```

### 9.2. Таймауты и повторы

| Операция | Таймаут | Повторов | Действие |
|----------|---------|----------|----------|
| PREPARE | 3 сек | 2 | ABORT |
| COMMIT | 5 сек | 3 | Retry |
| Межхабовый запрос | 10 сек | 2 | ABORT |

### 9.3. Восстановление после сбоя Hub'а

#### 9.3.1. При запуске Hub'а

```python
def recover_on_startup():
    # Найти незавершённые транзакции
    pending = get_transactions_in_state(["PREPARE_IN_PROGRESS", "ROUTED"])
    
    for tx in pending:
        if tx.created_at < now() - TIMEOUT:
            # Таймаут истёк — отменить
            abort_transaction(tx)
        else:
            # Продолжить с места остановки
            resume_transaction(tx)
```

#### 9.3.2. Очистка prepare_locks

```python
def cleanup_stale_locks():
    stale = get_prepare_locks_older_than(MAX_LOCK_AGE)
    for lock in stale:
        tx = get_transaction(lock.tx_id)
        if tx.state in ["COMMITTED", "ABORTED"]:
            delete_lock(lock)
        else:
            # Транзакция в неопределённом состоянии
            mark_for_manual_review(lock)
```

### 9.4. Коды ошибок

| Код | Категория | Описание |
|-----|-----------|----------|
| `E001` | Routing | Маршрут не найден |
| `E002` | Routing | Недостаточная ёмкость |
| `E003` | TrustLine | Лимит превышен |
| `E004` | TrustLine | Линия не активна |
| `E005` | Auth | Неверная подпись |
| `E006` | Auth | Недостаточно прав |
| `E007` | Timeout | Таймаут операции |
| `E008` | Conflict | Конфликт состояний |
| `E009` | Validation | Некорректные данные |
| `E010` | Internal | Внутренняя ошибка |

### 9.5. Формат сообщения об ошибке

```json
{
  "msg_type": "ERROR",
  "tx_id": "uuid | null",
  "payload": {
    "code": "E003",
    "message": "Trust line limit exceeded",
    "details": {
      "trust_line_id": "uuid",
      "limit": 1000.00,
      "requested": 1500.00
    }
  }
}
```

---

## 10. Разрешение споров

### 10.1. Типы споров

| Тип | Описание | Частота |
|-----|----------|---------|
| **Несогласие с платежом** | Участник оспаривает факт или сумму | Редко |
| **Спор о качестве** | Товар/услуга не соответствует | Средне |
| **Технический сбой** | Расхождение состояний | Редко |
| **Мошенничество** | Намеренный обман | Очень редко |

### 10.2. Статус «Спорная транзакция»

```json
{
  "tx_id": "uuid",
  "dispute_status": "disputed",
  "dispute_info": {
    "opened_by": "PID_A",
    "opened_at": "ISO8601",
    "reason": "Товар не получен",
    "evidence": ["url1", "url2"]
  }
}
```

### 10.3. Процесс разрешения

```
┌──────────────────┐
│ Транзакция OK    │
└────────┬─────────┘
         │ dispute()
         ▼
┌──────────────────┐
│    DISPUTED      │
└────────┬─────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌────────┐ ┌────────┐
│RESOLVED│ │ESCALATE│
└───┬────┘ └───┬────┘
    │          │
    ▼          ▼
(закрыть)  (арбитраж)
```

### 10.4. API для споров

#### Открытие спора

```json
{
  "action": "DISPUTE_OPEN",
  "tx_id": "uuid",
  "reason": "string",
  "evidence": ["url"],
  "requested_outcome": "refund | adjustment | investigation"
}
```

#### Ответ на спор

```json
{
  "action": "DISPUTE_RESPOND",
  "dispute_id": "uuid",
  "response": "string",
  "evidence": ["url"],
  "proposed_resolution": {...}
}
```

### 10.5. Роли в разрешении споров

| Роль | Права |
|------|-------|
| **Участник** | Открыть спор, предоставить доказательства |
| **Оператор** | Просмотр логов, запрос информации |
| **Арбитр** | Принятие решения, создание компенсаций |
| **Админ** | Заморозка участников, эскалация |

### 10.6. Компенсирующие транзакции

При подтверждении спора создаётся компенсирующая транзакция:

```json
{
  "tx_id": "uuid-compensation",
  "type": "COMPENSATION",
  "initiator": "ARBITER_PID",
  "payload": {
    "original_tx_id": "uuid-original",
    "reason": "Dispute resolution #123",
    "effects": [
      {
        "debtor": "PID_A",
        "creditor": "PID_B",
        "delta": -100.00,
        "equivalent": "UAH"
      }
    ]
  },
  "signatures": [
    {"signer": "ARBITER_PID", "signature": "..."}
  ]
}
```

### 10.7. Неизменяемость истории

**Важно:** транзакции не удаляются и не модифицируются.

- Для исправления ошибок создаются **новые транзакции**
- История всегда восстановима
- Аудит возможен по цепочке транзакций

---

## Приложения

### A. Канонический JSON

Для вычисления подписей используется каноническая форма JSON:
- Ключи отсортированы по алфавиту
- Без лишних пробелов
- UTF-8 кодировка
- Числа без лишних нулей

### B. Base58 алфавит

```
123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
```

(исключены: 0, O, I, l)

### C. Версионирование протокола

Формат версии: `MAJOR.MINOR`

| Версия | Совместимость |
|--------|---------------|
| 0.x | Экспериментальная, может ломать |
| 1.x | Стабильная, обратная совместимость |

---

## Связанные документы

- [00-overview.md](00-overview.md) — Обзор проекта
- [01-concepts.md](01-concepts.md) — Ключевые концепции
- [03-architecture.md](03-architecture.md) — Архитектура системы
- [04-api-reference.md](04-api-reference.md) — Справочник API
