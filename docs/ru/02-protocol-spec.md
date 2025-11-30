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
11. [Верификация целостности системы](#11-верификация-целостности-системы)

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

#### 6.3.5. Max Flow алгоритм (расширенный)

Для больших платежей или оптимальной маршрутизации используется алгоритм максимального потока:

```python
def max_flow_routing(graph, source, target, required_amount, equivalent):
    """
    Алгоритм Эдмондса-Карпа для максимального потока.
    
    Находит оптимальное распределение потока по сети,
    минимизируя количество задействованных рёбер.
    """
    # Построить residual graph
    residual = build_residual_graph(graph, equivalent)
    
    total_flow = Decimal("0")
    flow_assignment = defaultdict(Decimal)
    
    while total_flow < required_amount:
        # BFS для поиска augmenting path
        path = bfs_find_path(residual, source, target)
        
        if not path:
            break  # Нет пути — достигнут max flow
        
        # Найти bottleneck
        bottleneck = min(
            residual[u][v]["capacity"] 
            for u, v in zip(path[:-1], path[1:])
        )
        
        # Ограничить bottleneck оставшейся суммой
        augment = min(bottleneck, required_amount - total_flow)
        
        # Обновить residual graph и flow assignment
        for u, v in zip(path[:-1], path[1:]):
            residual[u][v]["capacity"] -= augment
            residual[v][u]["capacity"] += augment  # Reverse edge
            flow_assignment[(u, v)] += augment
        
        total_flow += augment
    
    if total_flow < required_amount:
        raise InsufficientCapacity(
            f"Max flow {total_flow} < required {required_amount}"
        )
    
    # Преобразовать flow_assignment в пути
    return decompose_flow_to_paths(flow_assignment, source, target)


def decompose_flow_to_paths(flow_assignment, source, target):
    """
    Разложить поток на набор путей (flow decomposition).
    
    Результат: список {path: [...], amount: X}
    """
    paths = []
    remaining_flow = flow_assignment.copy()
    
    while True:
        # DFS для поиска пути с положительным потоком
        path = dfs_find_flow_path(remaining_flow, source, target)
        if not path:
            break
        
        # Найти минимальный поток на пути
        min_flow = min(
            remaining_flow[(u, v)] 
            for u, v in zip(path[:-1], path[1:])
        )
        
        # Вычесть из remaining
        for u, v in zip(path[:-1], path[1:]):
            remaining_flow[(u, v)] -= min_flow
            if remaining_flow[(u, v)] == 0:
                del remaining_flow[(u, v)]
        
        paths.append({"path": path, "amount": min_flow})
    
    return paths
```

**Когда использовать Max Flow:**

| Сценарий | Алгоритм | Причина |
|----------|----------|---------|
| Малые платежи (< 1000) | k-shortest paths | Быстрее, достаточно |
| Большие платежи | Max Flow | Оптимальное распределение |
| Фрагментированная сеть | Max Flow | Использует все возможности |
| Проверка capacity | Max Flow | Точный ответ "можно/нельзя" |

#### 6.3.6. Атомарность Multi-path платежей

**Проблема:** При multi-path платеже часть путей может успешно пройти PREPARE, а часть — fail. Необходимо гарантировать атомарность: либо все пути успешны, либо ни один.

**Решение: Групповой PREPARE**

```python
class MultiPathPaymentCoordinator:
    """
    Координатор атомарного multi-path платежа.
    
    Гарантирует, что либо все пути коммитятся, либо все абортятся.
    """
    
    async def execute_multipath_payment(
        self,
        tx_id: str,
        routes: list[PaymentRoute],
        timeout: timedelta
    ) -> PaymentResult:
        """
        Атомарное исполнение multi-path платежа.
        """
        # Фаза 1: PREPARE всех путей параллельно
        prepare_tasks = [
            self.prepare_route(tx_id, route, timeout)
            for route in routes
        ]
        prepare_results = await asyncio.gather(
            *prepare_tasks, 
            return_exceptions=True
        )
        
        # Проверить результаты
        all_prepared = all(
            isinstance(r, PrepareSuccess) 
            for r in prepare_results
        )
        
        if all_prepared:
            # Фаза 2a: COMMIT всех путей
            await self.commit_all_routes(tx_id, routes)
            return PaymentResult(status="COMMITTED", routes=routes)
        else:
            # Фаза 2b: ABORT всех путей (включая успешно подготовленные)
            await self.abort_all_routes(tx_id, routes)
            failed_route = next(
                r for r in prepare_results 
                if isinstance(r, Exception)
            )
            return PaymentResult(
                status="ABORTED", 
                reason=str(failed_route)
            )
    
    async def prepare_route(
        self,
        tx_id: str,
        route: PaymentRoute,
        timeout: timedelta
    ) -> PrepareSuccess | Exception:
        """
        PREPARE одного пути с таймаутом.
        """
        try:
            async with asyncio.timeout(timeout.total_seconds()):
                for participant in route.intermediate_participants:
                    result = await self.send_prepare(
                        tx_id, participant, route.effects_for(participant)
                    )
                    if not result.ok:
                        raise PrepareFailure(participant, result.reason)
                return PrepareSuccess(route)
        except asyncio.TimeoutError:
            raise PrepareFailure(route.path, "timeout")
    
    async def abort_all_routes(
        self,
        tx_id: str,
        routes: list[PaymentRoute]
    ) -> None:
        """
        ABORT всех путей (идемпотентно).
        """
        abort_tasks = []
        for route in routes:
            for participant in route.all_participants:
                abort_tasks.append(
                    self.send_abort(tx_id, participant)
                )
        
        # Игнорируем ошибки — ABORT идемпотентен
        await asyncio.gather(*abort_tasks, return_exceptions=True)
```

**Диаграмма состояний multi-path:**

```
                    ┌─────────────────┐
                    │      NEW        │
                    └────────┬────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │   PREPARE_ALL_IN_PROGRESS    │
              │  (параллельно все маршруты)  │
              └──────────────┬───────────────┘
                             │
              ┌──────────────┴───────────────┐
              │                              │
              ▼                              ▼
       ┌────────────┐                 ┌────────────┐
       │ All OK     │                 │ Any FAIL   │
       └─────┬──────┘                 └─────┬──────┘
             │                              │
             ▼                              ▼
      ┌────────────┐                 ┌────────────┐
      │ COMMIT_ALL │                 │ ABORT_ALL  │
      │ (все пути) │                 │ (все пути) │
      └─────┬──────┘                 └─────┬──────┘
            │                              │
            ▼                              ▼
      ┌────────────┐                 ┌────────────┐
      │ COMMITTED  │                 │  ABORTED   │
      └────────────┘                 └────────────┘
```

**Инвариант атомарности:**

```
∀ multi-path payment MP:
  (∀ route R ∈ MP: R.state = COMMITTED) 
  XOR 
  (∀ route R ∈ MP: R.state = ABORTED)
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

### 9.4. Протокол восстановления состояния участника

При сбое участника (клиентского приложения) во время операции необходим протокол восстановления для гарантии eventual consistency.

#### 9.4.1. Состояния участника после сбоя

```
┌─────────────────────────────────────────────────────────────┐
│              Стейт-машина восстановления                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────┐     online      ┌──────────────┐            │
│   │ OFFLINE  │────────────────►│  RECOVERING  │            │
│   │ (сбой)   │                 │              │            │
│   └──────────┘                 └──────┬───────┘            │
│                                       │                     │
│                         ┌─────────────┼─────────────┐      │
│                         │             │             │      │
│                         ▼             ▼             ▼      │
│                   ┌─────────┐   ┌─────────┐   ┌─────────┐  │
│                   │COMMITTED│   │ ABORTED │   │UNCERTAIN│  │
│                   │ (sync)  │   │ (sync)  │   │(escalate)│  │
│                   └─────────┘   └─────────┘   └─────────┘  │
│                         │             │             │      │
│                         └─────────────┼─────────────┘      │
│                                       ▼                     │
│                              ┌───────────────┐             │
│                              │ SYNCHRONIZED  │             │
│                              │  (нормальная  │             │
│                              │   работа)     │             │
│                              └───────────────┘             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 9.4.2. Сообщения протокола восстановления

**RECOVERY_QUERY** — запрос состояния операции:

```json
{
  "msg_type": "RECOVERY_QUERY",
  "tx_id": "uuid",
  "from": "PID_recovering",
  "to": "PID_neighbor",
  "payload": {
    "my_last_known_state": "PREPARE_ACK_SENT",
    "timestamp": "ISO8601"
  },
  "signature": "..."
}
```

**RECOVERY_RESPONSE** — ответ о состоянии:

```json
{
  "msg_type": "RECOVERY_RESPONSE",
  "tx_id": "uuid",
  "from": "PID_neighbor",
  "to": "PID_recovering",
  "payload": {
    "tx_state": "COMMITTED | ABORTED | UNKNOWN",
    "my_effects_applied": true,
    "evidence": {
      "commit_signature": "...",
      "commit_timestamp": "ISO8601"
    }
  },
  "signature": "..."
}
```

#### 9.4.3. Алгоритм восстановления

```python
class ParticipantRecoveryService:
    """
    Сервис восстановления состояния участника после сбоя.
    """
    
    async def recover_pending_operations(
        self,
        participant_id: str
    ) -> list[RecoveryResult]:
        """
        Восстановить состояние всех pending операций.
        """
        results = []
        
        # 1. Получить все операции в неопределённом состоянии
        pending_locks = await self.get_local_prepare_locks(participant_id)
        
        for lock in pending_locks:
            result = await self.recover_single_operation(
                participant_id, lock.tx_id, lock
            )
            results.append(result)
        
        return results
    
    async def recover_single_operation(
        self,
        participant_id: str,
        tx_id: str,
        local_lock: PrepareLock
    ) -> RecoveryResult:
        """
        Восстановить состояние одной операции.
        """
        # 2. Определить соседей по данной операции
        neighbors = self.get_neighbors_from_lock(local_lock)
        
        # 3. Опросить соседей о состоянии операции
        responses = await self.query_neighbors(tx_id, neighbors)
        
        # 4. Принять решение на основе ответов
        decision = self.decide_from_responses(responses)
        
        if decision == RecoveryDecision.COMMITTED:
            # Применить эффекты локально
            await self.apply_effects(local_lock.effects)
            await self.delete_lock(local_lock)
            return RecoveryResult(tx_id, "COMMITTED")
            
        elif decision == RecoveryDecision.ABORTED:
            # Освободить резерв
            await self.release_reservation(local_lock)
            await self.delete_lock(local_lock)
            return RecoveryResult(tx_id, "ABORTED")
            
        else:  # UNCERTAIN
            # Эскалация на Hub или ручное разрешение
            await self.escalate_to_hub(tx_id, responses)
            return RecoveryResult(tx_id, "ESCALATED")
    
    async def query_neighbors(
        self,
        tx_id: str,
        neighbors: list[str]
    ) -> list[RecoveryResponse]:
        """
        Опросить соседей по цепочке операции.
        """
        tasks = [
            self.send_recovery_query(tx_id, neighbor)
            for neighbor in neighbors
        ]
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [r for r in responses if isinstance(r, RecoveryResponse)]
    
    def decide_from_responses(
        self,
        responses: list[RecoveryResponse]
    ) -> RecoveryDecision:
        """
        Принять решение на основе ответов соседей.
        
        Правила:
        - Если хотя бы один сосед подтвердил COMMITTED → COMMITTED
        - Если все соседи ответили ABORTED → ABORTED
        - Если нет консенсуса → UNCERTAIN
        """
        committed_count = sum(1 for r in responses if r.tx_state == "COMMITTED")
        aborted_count = sum(1 for r in responses if r.tx_state == "ABORTED")
        
        if committed_count > 0:
            # Хотя бы один подтвердил коммит — операция завершена
            return RecoveryDecision.COMMITTED
        elif aborted_count == len(responses) and len(responses) > 0:
            # Все соседи отменили
            return RecoveryDecision.ABORTED
        else:
            # Неопределённость
            return RecoveryDecision.UNCERTAIN
```

#### 9.4.4. Гарантии протокола восстановления

| Свойство | Гарантия |
|----------|----------|
| **Safety** | Участник не применит COMMITTED эффекты к ABORTED операции |
| **Liveness** | При доступности хотя бы одного соседа — восстановление завершится |
| **Consistency** | Все участники придут к одному состоянию операции |
| **Idempotency** | Повторное восстановление безопасно |

#### 9.4.5. Таймауты восстановления

| Этап | Таймаут | Действие |
|------|---------|----------|
| RECOVERY_QUERY | 5 сек | Повторить или пропустить соседа |
| Общее восстановление | 30 сек | Эскалация на Hub |
| Hub resolution | 5 мин | Ручное разрешение админом |

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

## 11. Верификация целостности системы

### 11.1. Назначение

Система верификации целостности обеспечивает:
- **Обнаружение багов** в алгоритмах клиринга и платежей
- **Защиту от коррупции данных** при сбоях
- **Аудит корректности** всех операций
- **Раннее предупреждение** о проблемах

### 11.2. Фундаментальные инварианты

#### 11.2.1. Zero-Sum Invariant (Инвариант нулевой суммы)

**Определение:** Сумма всех балансов по каждому эквиваленту должна равняться нулю.

```
∀ equivalent E: ∑ net_balance(participant, E) = 0
```

**SQL-проверка:**
```sql
-- Должно возвращать 0 для каждого эквивалента
SELECT 
    e.code as equivalent,
    COALESCE(SUM(
        CASE 
            WHEN d.debtor_id = p.id THEN -d.amount
            WHEN d.creditor_id = p.id THEN d.amount
            ELSE 0
        END
    ), 0) as net_balance_sum
FROM equivalents e
CROSS JOIN participants p
LEFT JOIN debts d ON d.equivalent_id = e.id 
    AND (d.debtor_id = p.id OR d.creditor_id = p.id)
GROUP BY e.code;
```

**Частота проверки:** После каждой транзакции + периодически (каждые 5 минут)

#### 11.2.2. Trust Limit Invariant (Инвариант лимита доверия)

**Определение:** Долг не может превышать лимит линии доверия.

```
∀ (debtor, creditor, equivalent):
  debt[debtor → creditor, E] ≤ trust_line[creditor → debtor, E].limit
```

**SQL-проверка:**
```sql
SELECT 
    d.debtor_id,
    d.creditor_id,
    d.amount as debt,
    tl.limit as trust_limit,
    d.amount - tl.limit as violation_amount
FROM debts d
LEFT JOIN trust_lines tl ON 
    tl.from_participant_id = d.creditor_id 
    AND tl.to_participant_id = d.debtor_id
    AND tl.equivalent_id = d.equivalent_id
    AND tl.status = 'active'
WHERE d.amount > COALESCE(tl.limit, 0);
```

**Частота проверки:** После каждой транзакции

#### 11.2.3. Clearing Neutrality Invariant (Инвариант нейтральности клиринга)

**Определение:** Клиринг уменьшает взаимные долги, но НЕ меняет чистые позиции участников.

```
∀ participant P in cycle:
  net_position(P, E)_before = net_position(P, E)_after
```

**Алгоритм проверки:**
```python
def verify_clearing_neutrality(cycle: list[str], amount: Decimal, equivalent: str):
    """
    Проверка, что клиринг не изменил чистые позиции участников
    """
    # Рассчитать чистые позиции ДО
    positions_before = {}
    for pid in cycle:
        positions_before[pid] = calculate_net_position(pid, equivalent)
    
    # Выполнить клиринг
    apply_clearing(cycle, amount, equivalent)
    
    # Рассчитать чистые позиции ПОСЛЕ
    positions_after = {}
    for pid in cycle:
        positions_after[pid] = calculate_net_position(pid, equivalent)
    
    # Проверить инвариант
    for pid in cycle:
        if positions_before[pid] != positions_after[pid]:
            raise IntegrityViolation(
                f"Clearing changed net position of {pid}: "
                f"{positions_before[pid]} → {positions_after[pid]}"
            )
    
    return True
```

**Частота проверки:** При каждом клиринге

#### 11.2.4. Debt Symmetry Invariant (Инвариант симметрии долга)

**Определение:** Для каждой пары участников долг может существовать только в одном направлении.

```
∀ (A, B, E): NOT (debt[A→B, E] > 0 AND debt[B→A, E] > 0)
```

**SQL-проверка:**
```sql
SELECT 
    d1.debtor_id as a,
    d1.creditor_id as b,
    d1.equivalent_id,
    d1.amount as debt_a_to_b,
    d2.amount as debt_b_to_a
FROM debts d1
JOIN debts d2 ON 
    d1.debtor_id = d2.creditor_id 
    AND d1.creditor_id = d2.debtor_id
    AND d1.equivalent_id = d2.equivalent_id
WHERE d1.amount > 0 AND d2.amount > 0;
```

**Частота проверки:** После каждой транзакции

### 11.3. Контрольные суммы состояния

#### 11.3.1. State Checksum (Контрольная сумма состояния)

**Формат:** SHA-256 хеш канонического представления всех долгов

```python
def compute_state_checksum(equivalent: str) -> str:
    """
    Вычисление контрольной суммы состояния для эквивалента
    """
    # Получить все долги, отсортированные детерминистически
    debts = db.query("""
        SELECT debtor_id, creditor_id, amount
        FROM debts
        WHERE equivalent_id = (SELECT id FROM equivalents WHERE code = :eq)
        ORDER BY debtor_id, creditor_id
    """, {"eq": equivalent})
    
    # Сформировать каноническую строку
    canonical = "|".join([
        f"{d.debtor_id}:{d.creditor_id}:{d.amount}"
        for d in debts
    ])
    
    # Вычислить SHA-256
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
```

#### 11.3.2. Incremental Checksum (Инкрементальная контрольная сумма)

Для оптимизации при частых операциях:

```python
def update_checksum_incremental(
    current_checksum: str,
    operation: str,  # "add" | "remove" | "update"
    debt_record: DebtRecord,
    previous_amount: Decimal | None = None
) -> str:
    """
    Инкрементальное обновление контрольной суммы
    """
    delta = f"{operation}:{debt_record.debtor_id}:{debt_record.creditor_id}:"
    
    if operation == "add":
        delta += f"{debt_record.amount}"
    elif operation == "remove":
        delta += f"-{debt_record.amount}"
    elif operation == "update":
        delta += f"{previous_amount}→{debt_record.amount}"
    
    return hashlib.sha256(
        f"{current_checksum}|{delta}".encode('utf-8')
    ).hexdigest()
```

### 11.4. Audit Trail (Журнал аудита)

#### 11.4.1. Структура записи аудита

```json
{
  "audit_id": "uuid",
  "timestamp": "ISO8601",
  "operation_type": "PAYMENT | CLEARING | TRUST_LINE_*",
  "tx_id": "string",
  "equivalent": "string",
  "state_checksum_before": "sha256",
  "state_checksum_after": "sha256",
  "affected_participants": ["PID1", "PID2", ...],
  "invariants_checked": {
    "zero_sum": true,
    "trust_limits": true,
    "debt_symmetry": true
  },
  "verification_passed": true
}
```

#### 11.4.2. SQL-схема журнала аудита

```sql
CREATE TABLE integrity_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    operation_type VARCHAR(50) NOT NULL,
    tx_id VARCHAR(64),
    equivalent_code VARCHAR(16) NOT NULL,
    state_checksum_before VARCHAR(64) NOT NULL,
    state_checksum_after VARCHAR(64) NOT NULL,
    affected_participants JSONB NOT NULL,
    invariants_checked JSONB NOT NULL,
    verification_passed BOOLEAN NOT NULL,
    error_details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_timestamp ON integrity_audit_log(timestamp);
CREATE INDEX idx_audit_tx_id ON integrity_audit_log(tx_id);
CREATE INDEX idx_audit_failures ON integrity_audit_log(verification_passed) 
WHERE verification_passed = false;
```

### 11.5. Алгоритмы восстановления

#### 11.5.1. При обнаружении нарушения Zero-Sum

```python
async def handle_zero_sum_violation(equivalent: str, imbalance: Decimal):
    """
    Обработка нарушения инварианта нулевой суммы
    """
    # 1. Заблокировать операции с этим эквивалентом
    await lock_equivalent(equivalent)
    
    # 2. Найти последнюю корректную контрольную сумму
    last_valid = await find_last_valid_checkpoint(equivalent)
    
    # 3. Уведомить администраторов
    await alert_admins(
        severity="CRITICAL",
        message=f"Zero-sum violation in {equivalent}: imbalance = {imbalance}",
        last_valid_checkpoint=last_valid
    )
    
    # 4. Создать отчёт для ручного анализа
    report = await generate_integrity_report(equivalent, since=last_valid.timestamp)
    
    return report
```

#### 11.5.2. При обнаружении нарушения Trust Limit

```python
async def handle_trust_limit_violation(
    debtor: str, 
    creditor: str, 
    equivalent: str,
    debt: Decimal,
    limit: Decimal
):
    """
    Обработка превышения лимита доверия
    """
    violation_amount = debt - limit
    
    # 1. Заморозить линию доверия
    await freeze_trust_line(creditor, debtor, equivalent)
    
    # 2. Логировать инцидент
    await log_security_incident(
        type="TRUST_LIMIT_VIOLATION",
        details={
            "debtor": debtor,
            "creditor": creditor,
            "debt": str(debt),
            "limit": str(limit),
            "violation": str(violation_amount)
        }
    )
    
    # 3. Уведомить участников
    await notify_participants([debtor, creditor], 
        message="Trust line frozen due to integrity violation")
```

### 11.6. Периодические проверки

#### 11.6.1. Расписание проверок

| Проверка | Частота | Приоритет |
|----------|---------|-----------|
| Zero-Sum | Каждые 5 мин | Critical |
| Trust Limits | Каждые 5 мин | Critical |
| Debt Symmetry | Каждые 15 мин | High |
| State Checksum | Каждый час | Medium |
| Full Audit | Раз в сутки | Low |

#### 11.6.2. Background Task для проверок

```python
async def integrity_check_task():
    """
    Периодическая задача проверки целостности
    """
    while True:
        try:
            for equivalent in await get_active_equivalents():
                report = await run_integrity_checks(equivalent)
                
                if not report.all_passed:
                    await handle_integrity_failure(report)
                else:
                    await save_checkpoint(equivalent, report.checksum)
            
            await asyncio.sleep(300)  # 5 минут
            
        except Exception as e:
            await alert_admins(severity="ERROR", message=str(e))
            await asyncio.sleep(60)  # Повторить через минуту
```

### 11.7. API для верификации

#### 11.7.1. Endpoints

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/v1/integrity/status` | Текущий статус целостности |
| GET | `/api/v1/integrity/checksum/{equivalent}` | Контрольная сумма состояния |
| POST | `/api/v1/integrity/verify` | Запустить полную проверку |
| GET | `/api/v1/integrity/audit-log` | Журнал аудита |

#### 11.7.2. Формат ответа /integrity/status

```json
{
  "status": "healthy | warning | critical",
  "last_check": "ISO8601",
  "equivalents": {
    "UAH": {
      "status": "healthy",
      "checksum": "sha256...",
      "last_verified": "ISO8601",
      "invariants": {
        "zero_sum": {"passed": true, "value": "0.00"},
        "trust_limits": {"passed": true, "violations": 0},
        "debt_symmetry": {"passed": true, "violations": 0}
      }
    }
  },
  "alerts": []
}
```

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

### D. Платформа электронных денег (Future Extension)

> **Статус:** Опция для будущего расширения. **Не входит в scope v0.1.**

GEO может использоваться как низкоуровневая платформа для выпуска электронных денег, обеспеченных фиатом.

#### D.1. Концепция Gateway (Шлюз)

**Gateway** — особый участник сети с фиатным обеспечением:

```
┌────────────────────────────────────────────────────────────┐
│                    GATEWAY (Шлюз)                          │
├────────────────────────────────────────────────────────────┤
│  • Имеет банковский счёт с фиатными резервами             │
│  • Создаёт TrustLines для пользователей при депозите      │
│  • Погашает долги при выводе фиата (redemption)           │
│  • Должен поддерживать 100% резервирование                │
└────────────────────────────────────────────────────────────┘
```

#### D.2. Алгоритм работы

**Deposit (ввод фиата):**
```
1. User переводит X фиата → Gateway (банковский перевод)
2. Gateway создаёт: TrustLine(Gateway → User, limit=X, equivalent=UAH)
3. User может "тратить" до X внутри GEO-сети
```

**Redemption (вывод фиата):**
```
1. User запрашивает вывод amount
2. Gateway проверяет: debt[Gateway → User] ≥ amount
3. Gateway уменьшает долг в GEO (DEBT_REDUCTION транзакция)
4. Gateway переводит фиат на счёт User
```

**Платёж внутри сети:**
```
User_A → Gateway → User_B  (через стандартный PAYMENT)
```

#### D.3. Ключевые операции

| Операция | GEO-действие | Банковское действие |
|----------|--------------|---------------------|
| **Deposit** | TrustLine создание | Приём фиата |
| **Payment** | Стандартный PAYMENT | — |
| **Redemption** | Уменьшение долга | Вывод фиата |

#### D.4. Регуляторные требования

По директиве **2000/46/EC** (Electronic Money Directive):
- Gateway = эмитент электронных денег
- Требуется лицензия финрегулятора
- Обязательно 100% резервирование
- Аудит резервов: `∑ debts[Gateway→*] ≤ fiat_reserves`

#### D.5. Риски

| Риск | Описание | Митигация |
|------|----------|-----------|
| Банкротство Gateway | Потеря средств пользователей | Страхование, регулирование |
| Мошенничество | Недостаточное резервирование | Аудит, прозрачность |
| Регуляторный | Запрет деятельности | Лицензирование |

#### D.6. Полный протокол Gateway

```python
class GatewayService:
    """
    Участник-шлюз для ввода/вывода фиата.
    
    Любой участник может стать Gateway, если:
    1. Прошёл верификацию сообщества (verification_level ≥ 2)
    2. Имеет резервы для обеспечения обязательств
    3. Согласился с правилами Gateway
    """
    
    async def register_as_gateway(
        self,
        participant_id: str,
        supported_currencies: list[str],
        bank_details: BankDetails,
        initial_reserve: Decimal
    ) -> GatewayRegistration:
        """
        Регистрация участника как Gateway.
        """
        # Проверить верификацию
        participant = await self.get_participant(participant_id)
        if participant.verification_level < 2:
            raise InsufficientVerification()
        
        # Создать профиль Gateway
        gateway = Gateway(
            pid=participant_id,
            currencies=supported_currencies,
            bank_details=bank_details,
            reserve_balance=initial_reserve,
            status="pending_approval"
        )
        
        return await self.save_gateway(gateway)
    
    async def deposit(
        self,
        gateway_pid: str,
        user_pid: str,
        fiat_amount: Decimal,
        fiat_currency: str,
        proof_of_payment: str  # Подтверждение банковского перевода
    ) -> DepositResult:
        """
        Пользователь вносит фиат → Gateway создаёт TrustLine.
        
        1. Проверить proof_of_payment (вручную или через банковский API)
        2. Создать/увеличить TrustLine от Gateway к User
        3. Обновить учёт резервов
        """
        # Проверить Gateway
        gateway = await self.get_gateway(gateway_pid)
        if gateway.status != "active":
            raise GatewayNotActive()
        
        # Определить эквивалент
        equivalent = self.currency_to_equivalent(fiat_currency)
        
        # Создать или увеличить TrustLine
        trust_line = await self.get_or_create_trust_line(
            from_pid=gateway_pid,
            to_pid=user_pid,
            equivalent=equivalent
        )
        
        new_limit = trust_line.limit + fiat_amount
        await self.update_trust_line_limit(trust_line.id, new_limit)
        
        # Обновить учёт резервов Gateway
        await self.update_gateway_reserve(
            gateway_pid, 
            fiat_currency, 
            delta=fiat_amount
        )
        
        return DepositResult(
            trust_line_id=trust_line.id,
            new_limit=new_limit,
            equivalent=equivalent
        )
    
    async def withdraw(
        self,
        gateway_pid: str,
        user_pid: str,
        amount: Decimal,
        equivalent: str,
        bank_details: UserBankDetails
    ) -> WithdrawResult:
        """
        Gateway погашает долг пользователя → выводит фиат.
        
        1. Проверить, что Gateway должен User достаточно
        2. Уменьшить долг в GEO
        3. Инициировать банковский перевод
        """
        # Проверить долг Gateway перед User
        debt = await self.get_debt(
            debtor=gateway_pid,
            creditor=user_pid,
            equivalent=equivalent
        )
        
        if debt.amount < amount:
            raise InsufficientBalance(
                f"Gateway owes {debt.amount}, requested {amount}"
            )
        
        # Уменьшить долг (создать DEBT_REDUCTION транзакцию)
        tx = await self.create_debt_reduction(
            debtor=gateway_pid,
            creditor=user_pid,
            equivalent=equivalent,
            amount=amount,
            reason="fiat_withdrawal"
        )
        
        # Инициировать банковский перевод
        fiat_currency = self.equivalent_to_currency(equivalent)
        transfer = await self.initiate_bank_transfer(
            from_account=gateway.bank_details,
            to_account=bank_details,
            amount=amount,
            currency=fiat_currency,
            reference=tx.tx_id
        )
        
        return WithdrawResult(
            tx_id=tx.tx_id,
            bank_transfer_id=transfer.id,
            status="pending_transfer"
        )
    
    async def get_gateway_stats(
        self,
        gateway_pid: str
    ) -> GatewayStats:
        """
        Статистика Gateway для аудита.
        """
        # Сумма всех обязательств Gateway (TrustLines)
        total_liabilities = await self.sum_outgoing_trust_lines(gateway_pid)
        
        # Фактические резервы
        reserves = await self.get_gateway_reserves(gateway_pid)
        
        # Reserve ratio
        reserve_ratio = reserves / total_liabilities if total_liabilities > 0 else Decimal("inf")
        
        return GatewayStats(
            total_liabilities=total_liabilities,
            reserves=reserves,
            reserve_ratio=reserve_ratio,
            is_fully_reserved=reserve_ratio >= 1.0
        )
```

### E. Товарные талоны (Commodity Tokens)

> **Статус:** Future Extension. **Не входит в scope v0.1.**

Товарные талоны — обязательства, номинированные в количестве товара, а не в деньгах.

#### E.1. Концепция

```
┌────────────────────────────────────────────────────────────┐
│               ТОВАРНЫЙ ТАЛОН (Commodity Token)              │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Пример: "Колионовские яйца"                              │
│                                                            │
│  • 1 талон = 10 яиц                                       │
│  • Эмитент: Фермер Шляпников                              │
│  • Погашение: Реальные яйца с фермы                       │
│                                                            │
│  Отличия от денежных обязательств:                        │
│  • Номинация в товаре, не в валюте                        │
│  • Погашается товаром, не деньгами                        │
│  • Цена в фиате может меняться                            │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

#### E.2. Расширенная модель эквивалента

```python
class EquivalentType(Enum):
    FIAT_REFERENCE = "fiat"      # Привязка к фиатной валюте (UAH, USD)
    TIME_UNIT = "time"           # Час работы
    COMMODITY_TOKEN = "commodity" # Товарный талон
    ABSTRACT = "abstract"        # Абстрактная единица сообщества

class CommoditySpec:
    """Спецификация товарного талона"""
    commodity_name: str          # "Яйца куриные"
    unit: str                    # "шт" | "кг" | "л"
    quantity_per_token: Decimal  # 10 (яиц за 1 талон)
    quality_grade: str           # "C1" | "organic"
    producer_pid: str            # PID производителя/эмитента

class RedemptionRules:
    """Правила погашения товарного талона"""
    redemption_locations: list[str]  # Адреса точек выдачи
    min_redemption_amount: Decimal   # Минимум для погашения
    advance_notice_hours: int        # За сколько часов уведомить
    expiration_date: date | None     # Срок годности талона
    seasonal_availability: str | None # "март-октябрь"

class CommodityEquivalent(Equivalent):
    """Эквивалент для товарного талона"""
    code: str                    # "EGG_KOLIONOVO"
    type: EquivalentType = EquivalentType.COMMODITY_TOKEN
    precision: int = 0           # Обычно целые числа
    
    commodity_spec: CommoditySpec
    redemption_rules: RedemptionRules
```

#### E.3. Жизненный цикл товарного талона

```
┌─────────────────────────────────────────────────────────────┐
│              Жизненный цикл товарного талона                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. ЭМИССИЯ                                                │
│     Фермер создаёт эквивалент EGG_KOLIONOVO                │
│     Фермер открывает TrustLines покупателям                │
│     Лимит TrustLine = макс. количество талонов             │
│                                                             │
│  2. ОБРАЩЕНИЕ                                              │
│     Покупатели получают талоны (платят фермеру услугами)   │
│     Талоны передаются между участниками как обычные долги  │
│     Клиринг работает стандартно                            │
│                                                             │
│  3. ПОГАШЕНИЕ                                              │
│     Держатель талона приезжает на ферму                    │
│     Фермер выдаёт товар и создаёт REDEMPTION транзакцию    │
│     Долг фермера уменьшается                               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### E.4. Транзакция REDEMPTION

```json
{
  "tx_id": "uuid",
  "type": "COMMODITY_REDEMPTION",
  "initiator": "FARMER_PID",
  "payload": {
    "equivalent": "EGG_KOLIONOVO",
    "redeemer": "CUSTOMER_PID",
    "amount": 50,
    "commodity_delivered": {
      "quantity": 500,
      "unit": "шт",
      "delivery_date": "2025-03-15",
      "delivery_location": "Ферма Колионово"
    }
  },
  "signatures": [
    {"signer": "FARMER_PID", "signature": "..."},
    {"signer": "CUSTOMER_PID", "signature": "..."}
  ],
  "state": "COMMITTED"
}
```

### F. Конвертация между эквивалентами (Exchange)

> **Статус:** Future Extension. **Не входит в scope v0.1.**

#### F.1. Концепция

GEO не конвертирует эквиваленты автоматически, но предоставляет инфраструктуру для рыночного обмена.

```
┌────────────────────────────────────────────────────────────┐
│                    Exchange в GEO                          │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Участник A хочет обменять UAH на HOUR                    │
│                                                            │
│  1. A публикует offer: "Продаю 100 UAH за 2 HOUR"         │
│  2. B видит offer и соглашается                           │
│  3. Система исполняет атомарный обмен:                    │
│     - A получает +2 HOUR от B                             │
│     - B получает +100 UAH от A                            │
│                                                            │
│  Цена определяется рынком (order book)                    │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

#### F.2. Модель данных Exchange

```python
class ExchangeOffer:
    """Предложение обмена"""
    id: UUID
    creator_pid: str
    
    # Что продаю
    sell_equivalent: str
    sell_amount: Decimal
    
    # Что хочу получить
    buy_equivalent: str
    buy_amount: Decimal
    
    # Расчётная цена (для order book)
    price: Decimal  # buy_amount / sell_amount
    
    status: str  # "open" | "partial" | "filled" | "cancelled"
    filled_amount: Decimal = Decimal("0")
    
    expires_at: datetime
    created_at: datetime

class ExchangeMatch:
    """Совпадение офферов"""
    offer_a: ExchangeOffer  # Продавец
    offer_b: ExchangeOffer  # Покупатель
    
    matched_amount_a: Decimal  # Сколько A продаёт
    matched_amount_b: Decimal  # Сколько B продаёт
    
    execution_price: Decimal
```

#### F.3. Протокол Exchange

```python
class ExchangeService:
    async def create_offer(
        self,
        creator_pid: str,
        sell_equivalent: str,
        sell_amount: Decimal,
        buy_equivalent: str,
        buy_amount: Decimal,
        expires_at: datetime
    ) -> ExchangeOffer:
        """Создать предложение обмена"""
        
        # Проверить, что у создателя есть capacity продать
        capacity = await self.check_sell_capacity(
            creator_pid, sell_equivalent, sell_amount
        )
        if not capacity.sufficient:
            raise InsufficientCapacity()
        
        offer = ExchangeOffer(
            creator_pid=creator_pid,
            sell_equivalent=sell_equivalent,
            sell_amount=sell_amount,
            buy_equivalent=buy_equivalent,
            buy_amount=buy_amount,
            price=buy_amount / sell_amount,
            expires_at=expires_at
        )
        
        # Попробовать сразу найти match
        matches = await self.find_matches(offer)
        if matches:
            await self.execute_matches(offer, matches)
        
        return await self.save_offer(offer)
    
    async def execute_match(
        self,
        offer_a: ExchangeOffer,
        offer_b: ExchangeOffer,
        amount: Decimal
    ) -> ExchangeTransaction:
        """
        Атомарно исполнить обмен между двумя офферами.
        
        Создаёт две связанные PAYMENT транзакции:
        1. A → B в sell_equivalent A
        2. B → A в sell_equivalent B (= buy_equivalent A)
        """
        
        # Рассчитать суммы
        amount_a_sells = amount
        amount_b_sells = amount * (offer_a.buy_amount / offer_a.sell_amount)
        
        # Атомарная транзакция обмена
        async with self.db.transaction():
            # Платёж 1: A продаёт
            tx1 = await self.payment_engine.create_payment(
                from_pid=offer_a.creator_pid,
                to_pid=offer_b.creator_pid,
                equivalent=offer_a.sell_equivalent,
                amount=amount_a_sells,
                description=f"Exchange: {offer_a.id}"
            )
            
            # Платёж 2: B продаёт (встречный)
            tx2 = await self.payment_engine.create_payment(
                from_pid=offer_b.creator_pid,
                to_pid=offer_a.creator_pid,
                equivalent=offer_b.sell_equivalent,
                amount=amount_b_sells,
                description=f"Exchange: {offer_b.id}"
            )
            
            # Обновить filled_amount
            offer_a.filled_amount += amount_a_sells
            offer_b.filled_amount += amount_b_sells
            
        return ExchangeTransaction(tx1=tx1, tx2=tx2)
```

### G. Защита от спама через TrustLines

> **Статус:** Future Extension. **Не входит в scope v0.1.**

TrustLines могут использоваться для фильтрации нежелательных коммуникаций.

#### G.1. Концепция

```
┌────────────────────────────────────────────────────────────┐
│            Фильтрация коммуникаций через доверие           │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Проблема: спам в сообщениях, запросах на TrustLine       │
│                                                            │
│  Решение: разрешать коммуникацию только от участников     │
│  в пределах N звеньев в сети доверия                      │
│                                                            │
│  Пример (N=2):                                            │
│  • Alice ← доверяет ← Bob ← доверяет ← Charlie            │
│  • Charlie может писать Alice (2 звена)                   │
│  • Dave (не связан) — не может                           │
│                                                            │
│  Дополнительно:                                           │
│  • "Стоимость" сообщения в единицах доверия              │
│  • Reputation-based filtering                             │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

#### G.2. Политика коммуникаций

```python
class CommunicationPolicy:
    """Политика приёма сообщений участника"""
    
    # Максимальное расстояние в сети доверия
    max_trust_distance: int = 3  # Принимать от участников в 3 звеньях
    
    # Минимальный уровень репутации отправителя
    min_sender_reputation: int = 0
    
    # Whitelist (всегда принимать)
    whitelist: list[str] = []
    
    # Blacklist (никогда не принимать)
    blacklist: list[str] = []
    
    # Требовать "оплату" за сообщение
    require_message_fee: bool = False
    message_fee_equivalent: str | None = None
    message_fee_amount: Decimal | None = None

class SpamFilter:
    async def can_send_message(
        self,
        sender_pid: str,
        recipient_pid: str,
        message_type: str
    ) -> FilterResult:
        """Проверить, может ли отправитель связаться с получателем"""
        
        policy = await self.get_communication_policy(recipient_pid)
        
        # Проверить whitelist/blacklist
        if sender_pid in policy.blacklist:
            return FilterResult(allowed=False, reason="blacklisted")
        if sender_pid in policy.whitelist:
            return FilterResult(allowed=True)
        
        # Проверить расстояние в сети доверия
        distance = await self.calculate_trust_distance(sender_pid, recipient_pid)
        if distance > policy.max_trust_distance:
            return FilterResult(
                allowed=False, 
                reason=f"trust_distance {distance} > {policy.max_trust_distance}"
            )
        
        # Проверить репутацию
        reputation = await self.get_reputation_score(sender_pid)
        if reputation < policy.min_sender_reputation:
            return FilterResult(
                allowed=False,
                reason=f"reputation {reputation} < {policy.min_sender_reputation}"
            )
        
        return FilterResult(allowed=True)
    
    async def calculate_trust_distance(
        self,
        from_pid: str,
        to_pid: str
    ) -> int:
        """
        Расстояние в графе доверия (BFS).
        
        Возвращает минимальное количество звеньев TrustLine.
        """
        if from_pid == to_pid:
            return 0
        
        visited = {from_pid}
        queue = [(from_pid, 0)]
        
        while queue:
            current, distance = queue.pop(0)
            
            # Получить всех, кто доверяет current или кому доверяет current
            neighbors = await self.get_trust_neighbors(current)
            
            for neighbor in neighbors:
                if neighbor == to_pid:
                    return distance + 1
                
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, distance + 1))
        
        return float('inf')  # Не связаны
```

### H. Контрциклическая функция

> **Статус:** Концептуальное описание. Требует реального использования для валидации.

#### H.1. Теоретическая основа

Кредитные сети взаимного доверия (как WIR в Швейцарии) демонстрируют **контрциклическое поведение**:

```
┌────────────────────────────────────────────────────────────┐
│               Контрциклическая функция GEO                 │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ЭКОНОМИЧЕСКИЙ СПАД:                                      │
│  • Фиатные деньги в дефиците                             │
│  • Банки сокращают кредитование                          │
│  • Бизнесы не могут платить друг другу                   │
│                                                            │
│  GEO В ЭТОЙ СИТУАЦИИ:                                    │
│  • Взаимный кредит НЕ зависит от банков                  │
│  • Участники продолжают обмениваться через TrustLines    │
│  • Клиринг освобождает "застрявшие" долги               │
│  • Ликвидность сохраняется внутри сообщества            │
│                                                            │
│  РЕЗУЛЬТАТ:                                               │
│  • Когда обычная экономика сжимается — GEO расширяется  │
│  • Смягчение последствий кризиса для участников          │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

#### H.2. Метрики контрциклического эффекта

```python
class ContracyclicalMetrics:
    """Метрики для отслеживания контрциклического эффекта"""
    
    async def calculate_metrics(
        self,
        period: DateRange
    ) -> ContracyclicalReport:
        """
        Рассчитать метрики контрциклического поведения.
        """
        return ContracyclicalReport(
            # Объём транзакций в GEO
            geo_transaction_volume=await self.sum_transactions(period),
            
            # Количество активных участников
            active_participants=await self.count_active(period),
            
            # Средняя загрузка TrustLines
            trust_line_utilization=await self.avg_utilization(period),
            
            # Объём клиринга (освобождённые долги)
            clearing_volume=await self.sum_clearings(period),
            
            # Velocity — скорость оборота
            velocity=await self.calculate_velocity(period),
            
            # Сравнение с внешними индикаторами
            correlation_with_gdp=await self.correlate_with_external(
                "gdp_growth", period
            ),
            correlation_with_unemployment=await self.correlate_with_external(
                "unemployment_rate", period
            )
        )
    
    async def detect_countercyclical_behavior(
        self,
        period: DateRange
    ) -> bool:
        """
        Определить, демонстрирует ли сеть контрциклическое поведение.
        
        True если:
        - Внешняя экономика сжимается (GDP↓, unemployment↑)
        - GEO активность растёт (volume↑, participants↑)
        """
        external = await self.get_external_indicators(period)
        internal = await self.calculate_metrics(period)
        
        economy_contracting = (
            external.gdp_growth < 0 or 
            external.unemployment_delta > 0
        )
        
        geo_expanding = (
            internal.transaction_volume_growth > 0 and
            internal.active_participants_growth > 0
        )
        
        return economy_contracting and geo_expanding
```

#### H.3. Стимулирование контрциклического эффекта

Для усиления контрциклической функции можно:

1. **Информировать участников** о роли GEO как альтернативы при кризисах
2. **Облегчить onboarding** в периоды экономического стресса
3. **Увеличить лимиты клиринга** при обнаружении спада
4. **Публиковать метрики** для демонстрации эффекта

---

## Связанные документы

- [00-overview.md](00-overview.md) — Обзор проекта
- [01-concepts.md](01-concepts.md) — Ключевые концепции
- [03-architecture.md](03-architecture.md) — Архитектура системы
- [04-api-reference.md](04-api-reference.md) — Справочник API
