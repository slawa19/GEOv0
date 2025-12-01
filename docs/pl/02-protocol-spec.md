# Protokół GEO: Pełna specyfikacja v0.1

**Wersja:** 0.1  
**Data:** Listopad 2025  
**Status:** Specyfikacja finalna

---

## Spis treści

1. [Przeznaczenie i zakres](#1-przeznaczenie-i-zakres)
2. [Prymitywy kryptograficzne](#2-prymitywy-kryptograficzne)
3. [Model danych](#3-model-danych)
4. [Protokół wiadomości](#4-protokół-wiadomości)
5. [Operacje na liniach zaufania](#5-operacje-na-liniach-zaufania)
6. [Płatności](#6-płatności)
7. [Kliring](#7-kliring)
8. [Interakcja między hubami](#8-interakcja-między-hubami)
9. [Obsługa błędów i odzyskiwanie](#9-obsługa-błędów-i-odzyskiwanie)
10. [Rozwiązywanie sporów](#10-rozwiązywanie-sporów)
11. [Weryfikacja integralności systemu](#11-weryfikacja-integralności-systemu)

---

## 1. Przeznaczenie i zakres

### 1.1. Cele protokołu

GEO v0.1 to protokół do:

- **P2P ekonomii wzajemnego kredytu** między uczestnikami i społecznościami
- **Bez jednej wspólnej waluty** — tylko zobowiązania w dowolnych ekwiwalentach
- **Bez globalnego rejestru (ledger'a)** — tylko lokalne stany i podpisy
- **Z automatycznym kliringiem** zamkniętych cykli długów

### 1.2. Zasady projektowe

| Zasada | Realizacja |
|--------|------------|
| **Prostota** | Użycie sprawdzonych algorytmów (BFS, 2PC), minimalny zestaw bytów |
| **Lokalność** | Konsensus tylko między bezpośrednio zaangażowanymi uczestnikami |
| **Rozszerzalność** | Protokół oddzielony od warstwy transportu |
| **Bezpieczeństwo** | Podpisy kryptograficzne na wszystkich operacjach |

### 1.3. Ograniczenia wersji 0.1

- Hub pełni rolę koordynatora transakcji
- Maksymalna długość ścieżki płatności: 6 ogniw
- Kliring cykli: 3–6 węzłów
- Multi-path: do 3 tras na jedną płatność

---

## 2. Prymitywy kryptograficzne

### 2.1. Schemat podpisu

**Algorytm:** Ed25519 (Edwards-curve Digital Signature Algorithm)

| Parametr | Wartość |
|----------|---------|
| Krzywa | Curve25519 |
| Rozmiar klucza prywatnego | 32 bajty |
| Rozmiar klucza publicznego | 32 bajty |
| Rozmiar podpisu | 64 bajty |

### 2.2. Haszowanie

**Algorytm:** SHA-256

Używany do:

- Generowania PID z klucza publicznego
- Wyliczania `tx_id` jako hash zawartości transakcji
- Weryfikacji integralności danych

### 2.3. Identyfikator uczestnika (PID)

```
PID = base58(sha256(public_key))
```

- Dane wejściowe: 32 bajty klucza publicznego Ed25519
- Haszowanie: SHA-256 → 32 bajty
- Kodowanie: Base58 → ~44 znaków

**Przykład:**
```
public_key: 0x3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29
PID: "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"
```

### 2.4. Format podpisu

```json
{
  "signer": "PID",
  "signature": "base64(ed25519_sign(message))",
  "timestamp": "ISO8601"
}
```

**Podpisywana wiadomość:** kanoniczny JSON payload bez pola `signatures`.

---

## 3. Model danych

### 3.1. Participant (Uczestnik)

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

**Statusy:**

| Status | Opis |
|--------|------|
| `active` | Aktywny uczestnik |
| `suspended` | Tymczasowo zawieszony |
| `left` | Opuścił społeczność |
| `deleted` | Usunięty |

### 3.2. Equivalent (Ekwiwalent)

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

**Zasady:**

- `code` — unikalny, 1–16 znaków, A-Z0-9\_
- `precision` — liczba miejsc po przecinku (0–8)

### 3.3. TrustLine (Linia zaufania)

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

**Niezmiennik:**

```
∀ (from, to, equivalent): debt[to→from] ≤ limit
```

**Polityki:**

| Pole | Opis | Domyślnie |
|------|------|-----------|
| `auto_clearing` | Automatyczna zgoda na kliring | `true` |
| `can_be_intermediate` | Można używać jako pośrednika | `true` |
| `daily_limit` | Limit obrotu na dobę | `null` (brak limitu) |
| `blocked_participants` | Zakaz tras przez wskazanych | `[]` |

### 3.4. Debt (Dług)

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

**Zasady:**

- Dla każdej trójki `(debtor, creditor, equivalent)` — jeden wpis
- `amount` zawsze > 0 (rekordy z 0 są usuwane)
- Aktualizowany atomowo w ramach transakcji

### 3.5. Transaction (Transakcja)

```json
{
  "tx_id": "string (uuid | hash)",
  "type": "TRUST_LINE_CREATE | TRUST_LINE_UPDATE | TRUST_LINE_CLOSE | PAYMENT | CLEARING",
  "initiator": "PID",
  "payload": { /* zależne od typu */ },
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

## 4. Protokół wiadomości

### 4.1. Podstawowy format wiadomości

```json
{
  "msg_id": "uuid",
  "msg_type": "string",
  "tx_id": "string | null",
  "from": "PID",
  "to": "PID | null",
  "payload": { /* zależnie od typu */ },
  "signature": "base64(ed25519_sign(canonical_json))"
}
```

### 4.2. Typy wiadomości

#### 4.2.1. Zarządzanie TrustLines

| Typ | Opis |
|-----|------|
| `TRUST_LINE_CREATE` | Utworzenie linii zaufania |
| `TRUST_LINE_UPDATE` | Zmiana limitu/polityki |
| `TRUST_LINE_CLOSE` | Zamknięcie linii |

#### 4.2.2. Płatności

| Typ | Opis |
|-----|------|
| `PAYMENT_REQUEST` | Żądanie płatności (klient → hub) |
| `PAYMENT_PREPARE` | Faza przygotowania (hub → uczestnicy) |
| `PAYMENT_PREPARE_ACK` | Odpowiedź na przygotowanie |
| `PAYMENT_COMMIT` | Zatwierdzenie |
| `PAYMENT_ABORT` | Anulowanie |

#### 4.2.3. Kliring

| Typ | Opis |
|-----|------|
| `CLEARING_PROPOSE` | Propozycja kliringu |
| `CLEARING_ACCEPT` | Akceptacja przez uczestnika |
| `CLEARING_REJECT` | Odrzucenie |
| `CLEARING_COMMIT` | Zatwierdzenie kliringu |
| `CLEARING_ABORT` | Anulowanie kliringu |

#### 4.2.4. Służbowe

| Typ | Opis |
|-----|------|
| `PING` | Sprawdzenie połączenia |
| `PONG` | Odpowiedź na PING |
| `ERROR` | Komunikat o błędzie |

### 4.3. Transport

Protokół jest niezależny od warstwy transportu. Zalecane warianty:

| Transport | Zastosowanie |
|-----------|-------------|
| **HTTPS + JSON** | REST API dla klientów |
| **WebSocket + JSON** | Powiadomienia w czasie rzeczywistym |
| **gRPC + Protobuf** | Komunikacja między hubami |

---

## 5. Operacje na liniach zaufania

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

**Wymagania:**

- Podpis `from` (A) jest obowiązkowy
- Nie istnieje aktywna linia `(from, to, equivalent)`
- `limit` > 0

**Algorytm:**

1. Sprawdź podpis A
2. Sprawdź unikalność linii
3. Zweryfikuj `policy`
4. Utwórz wpis TrustLine
5. Utwórz transakcję `TRUST_LINE_CREATE (COMMITTED)`

### 5.2. TRUST_LINE_UPDATE

**Payload:**
```json
{
  "trust_line_id": "uuid",
  "limit": 1500.00,
  "policy": { /* zaktualizowane pola */ }
}
```

**Wymagania:**

- Podpis właściciela (`from`) jest obowiązkowy
- Linia istnieje i `status = active`
- Nowy `limit` ≥ bieżący `debt[to→from]`

**Algorytm:**

1. Sprawdź podpis właściciela
2. Sprawdź, że `limit` nie jest niższy niż bieżący dług
3. Zaktualizuj wpis TrustLine
4. Utwórz transakcję `TRUST_LINE_UPDATE (COMMITTED)`

### 5.3. TRUST_LINE_CLOSE

**Payload:**
```json
{
  "trust_line_id": "uuid"
}
```

**Wymagania:**

- Podpis właściciela jest obowiązkowy
- `debt[to→from] = 0` (dług spłacony)

**Algorytm:**

1. Sprawdź podpis właściciela
2. Upewnij się, że nie ma długu
3. Ustaw `status = closed`
4. Utwórz transakcję `TRUST_LINE_CLOSE (COMMITTED)`

---

## 6. Płatności

### 6.1. Przegląd procesu

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
              │                              │
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

**Wiadomość klient → hub:**

```json
{
  "msg_type": "PAYMENT_REQUEST",
  "from": "PID_A",
  "payload": {
    "to": "PID_B",
    "equivalent": "UAH",
    "amount": 100.00,
    "description": "Opłata za usługi",
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

| Pole | Opis | Domyślnie |
|------|------|-----------|
| `max_hops` | Maksymalna długość ścieżki | 6 |
| `max_paths` | Maksimum tras | 3 |
| `timeout_ms` | Timeout transakcji | 5000 |
| `avoid` | Wykluczeni uczestnicy | [] |

### 6.3. Routing

#### 6.3.1. Obliczanie dostępnego kredytu

Dla skierowanej krawędzi `(A→B, E)`:

```
available_credit(A→B, E) = limit(A→B, E) - debt[B→A, E]
```

Gdzie:

- `limit(A→B, E)` — limit TrustLine od A do B w ekwiwalencie E
- `debt[B→A, E]` — bieżący dług B wobec A w ekwiwalencie E

#### 6.3.2. Algorytm szukania ścieżek (k-shortest paths)

```python
def find_k_paths(graph, source, target, k=3, max_hops=6):
    """
    Zmodyfikowany algorytm Yena dla k najkrótszych ścieżek
    z uwzględnieniem available_credit jako „wagi".
    """
    paths = []
    
    # Pierwsza ścieżka - BFS maksymalizujący available_credit
    path1 = bfs_max_capacity(graph, source, target, max_hops)
    if path1:
        paths.append(path1)
    
    # Kolejne ścieżki - iteracyjne usuwanie krawędzi
    for i in range(1, k):
        candidates = []
        for j in range(len(paths[i-1]) - 1):
            # Tymczasowe usunięcie krawędzi
            spur_node = paths[i-1][j]
            # Znalezienie alternatywnej ścieżki
            alt_path = bfs_max_capacity(
                graph_without_edge(graph, paths[i-1][j], paths[i-1][j+1]),
                spur_node, target, max_hops - j
            )
            if alt_path:
                candidates.append(paths[i-1][:j] + alt_path)
        
        if candidates:
            # Wybrać ścieżkę o największej pojemności
            paths.append(max(candidates, key=path_capacity))
    
    return paths
```

#### 6.3.3. Pojemność ścieżki

```
capacity(path) = min(available_credit(edge) for edge in path)
```

#### 6.3.4. Multi-path: podział płatności

```python
def split_payment(amount, paths):
    """
    Podzielić płatność na kilka tras.
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

#### 6.3.5. Algorytm Max Flow (rozszerzony)

Dla dużych płatności lub optymalnej dystrybucji używamy algorytmu maksymalnego przepływu:

```python
def max_flow_routing(graph, source, target, required_amount, equivalent):
    """
    Algorytm Edmondsa-Karpa dla maksymalnego przepływu.
    
    Znajduje optymalny rozkład przepływu w sieci,
    minimalizując liczbę użytych krawędzi.
    """
    # Zbuduj residual graph
    residual = build_residual_graph(graph, equivalent)
    
    total_flow = Decimal("0")
    flow_assignment = defaultdict(Decimal)
    
    while total_flow < required_amount:
        # BFS do szukania ścieżki powiększającej
        path = bfs_find_path(residual, source, target)
        
        if not path:
            break  # Brak ścieżki — osiągnięto max flow
        
        # Znajdź bottleneck
        bottleneck = min(
            residual[u][v]["capacity"] 
            for u, v in zip(path[:-1], path[1:])
        )
        
        # Ogranicz bottleneck pozostałą kwotą
        augment = min(bottleneck, required_amount - total_flow)
        
        # Zaktualizuj residual graph i flow assignment
        for u, v in zip(path[:-1], path[1:]):
            residual[u][v]["capacity"] -= augment
            residual[v][u]["capacity"] += augment  # Krawędź odwrotna
            flow_assignment[(u, v)] += augment
        
        total_flow += augment
    
    if total_flow < required_amount:
        raise InsufficientCapacity(
            f"Max flow {total_flow} < required {required_amount}"
        )
    
    # Rozłóż flow_assignment na ścieżki
    return decompose_flow_to_paths(flow_assignment, source, target)


def decompose_flow_to_paths(flow_assignment, source, target):
    """
    Rozłożenie przepływu na zestaw ścieżek (flow decomposition).
    
    Wynik: lista {path: [...], amount: X}
    """
    paths = []
    remaining_flow = flow_assignment.copy()
    
    while True:
        # DFS do szukania ścieżki z dodatnim przepływem
        path = dfs_find_flow_path(remaining_flow, source, target)
        if not path:
            break
        
        # Minimalny przepływ na ścieżce
        min_flow = min(
            remaining_flow[(u, v)] 
            for u, v in zip(path[:-1], path[1:])
        )
        
        # Odjęcie z remaining
        for u, v in zip(path[:-1], path[1:]):
            remaining_flow[(u, v)] -= min_flow
            if remaining_flow[(u, v)] == 0:
                del remaining_flow[(u, v)]
        
        paths.append({"path": path, "amount": min_flow})
    
    return paths
```

**Kiedy używać Max Flow:**

| Scenariusz | Algorytm | Powód |
|-----------|----------|-------|
| Małe płatności (< 1000) | k-shortest paths | Wystarczająco szybki |
| Duże płatności | Max Flow | Optymalna dystrybucja |
| Fragmentaryczna sieć | Max Flow | Wykorzystuje wszystkie możliwości |
| Sprawdzenie pojemności | Max Flow | Precyzyjna odpowiedź „da się / nie da się" |

#### 6.3.6. Atomowość płatności multi-path

**Problem:** Przy płatnościach multi-path część tras może zakończyć PREPARE sukcesem, a część błędem. Potrzebna jest gwarancja atomowości: albo wszystkie trasy się powiodą, albo żadna.

**Rozwiązanie: grupowy PREPARE**

```python
class MultiPathPaymentCoordinator:
    """
    Koordynator atomowych płatności multi-path.
    
    Gwarantuje, że wszystkie trasy są albo zatwierdzone, albo anulowane.
    """
    
    async def execute_multipath_payment(
        self,
        tx_id: str,
        routes: list[PaymentRoute],
        timeout: timedelta
    ) -> PaymentResult:
        """
        Atomowe wykonanie płatności multi-path.
        """
        # Faza 1: PREPARE wszystkich tras równolegle
        prepare_tasks = [
            self.prepare_route(tx_id, route, timeout)
            for route in routes
        ]
        prepare_results = await asyncio.gather(
            *prepare_tasks, 
            return_exceptions=True
        )
        
        # Sprawdź wyniki
        all_prepared = all(
            isinstance(r, PrepareSuccess) 
            for r in prepare_results
        )
        
        if all_prepared:
            # Faza 2a: COMMIT wszystkich tras
            await self.commit_all_routes(tx_id, routes)
            return PaymentResult(status="COMMITTED", routes=routes)
        else:
            # Faza 2b: ABORT wszystkich tras (łącznie z tymi, które się przygotowały)
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
        PREPARE pojedynczej trasy z timeoutem.
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
        ABORT wszystkich tras (idemopotentnie).
        """
        abort_tasks = []
        for route in routes:
            for participant in route.all_participants:
                abort_tasks.append(
                    self.send_abort(tx_id, participant)
                )
        
        # Ignorujemy błędy — ABORT jest idempotentny
        await asyncio.gather(*abort_tasks, return_exceptions=True)
```

**Diagram stanów płatności multi-path:**

```
                    ┌─────────────────┐
                    │      NEW        │
                    └────────┬────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │   PREPARE_ALL_IN_PROGRESS    │
              │  (wszystkie trasy równolegle)│
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
      │ (wszystkie │                 │ (wszystkie │
      │   trasy)   │                 │   trasy)   │
      └─────┬──────┘                 └─────┬──────┘
            │                              │
            ▼                              ▼
      ┌────────────┐                 ┌────────────┐
      │ COMMITTED  │                 │  ABORTED   │
      └────────────┘                 └────────────┘
```

**Niezmiennik atomowości:**

```
∀ multi-path payment MP:
  (∀ route R ∈ MP: R.state = COMMITTED) 
  XOR 
  (∀ route R ∈ MP: R.state = ABORTED)
```

### 6.4. Transakcja PAYMENT

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
    "description": "Opłata za usługi",
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

### 6.5. Faza PREPARE

#### 6.5.1. Wiadomość PAYMENT_PREPARE

Hub wysyła do każdego uczestnika na trasie:

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

#### 6.5.2. Weryfikacja po stronie uczestnika

```python
def validate_prepare(participant, effects):
    for effect in effects:
        edge = effect["edge"]
        delta = effect["delta"]
        
        if effect["direction"] == "outgoing":
            # Uczestnik staje się dłużny następnemu
            next_pid = edge[1]
            trust_line = get_trust_line(next_pid, participant.pid)
            
            if not trust_line or trust_line.status != "active":
                return FAIL("No active trust line")
            
            current_debt = get_debt(participant.pid, next_pid)
            new_debt = current_debt + delta
            
            if new_debt > trust_line.limit:
                return FAIL("Exceeds trust limit")
            
            # Sprawdzenie polityk
            if not trust_line.policy.can_be_intermediate:
                return FAIL("Not allowed as intermediate")
        
        elif effect["direction"] == "incoming":
            # Uczestnik otrzymuje dług od poprzednika
            prev_pid = edge[0]
            trust_line = get_trust_line(participant.pid, prev_pid)
            
            if not trust_line or trust_line.status != "active":
                return FAIL("No active trust line")
    
    return OK()
```

#### 6.5.3. Rezerwacja

Przy pomyślnej walidacji uczestnik:

1. Tworzy wpis `prepare_lock` z `tx_id` i efektami
2. Zmniejsza `available_credit` o zarezerwowaną kwotę
3. Odpowiada `PAYMENT_PREPARE_ACK (OK)`

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

### 6.6. Faza COMMIT

#### 6.6.1. Wiadomość PAYMENT_COMMIT

```json
{
  "msg_type": "PAYMENT_COMMIT",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_X",
  "signature": "..."
}
```

#### 6.6.2. Zastosowanie zmian

```python
def apply_commit(participant, tx_id):
    lock = get_prepare_lock(tx_id)
    if not lock:
        return  # Idempotentność
    
    for effect in lock.effects:
        if effect["direction"] == "outgoing":
            # Zwiększ dług uczestnika
            update_debt(
                debtor=participant.pid,
                creditor=effect["edge"][1],
                delta=effect["delta"]
            )
        elif effect["direction"] == "incoming":
            # Zwiększ dług wobec uczestnika
            update_debt(
                debtor=effect["edge"][0],
                creditor=participant.pid,
                delta=effect["delta"]
            )
    
    delete_prepare_lock(tx_id)
```

### 6.7. Faza ABORT

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

Uczestnik zwalnia rezerwę bez zmiany długów.

### 6.8. Maszyna stanów PAYMENT

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

### 6.9. Timeouty

| Etap | Timeout | Działanie po upływie |
|------|---------|----------------------|
| Routing | 500 ms | ABORT (brak tras) |
| PREPARE | 3 s | ABORT (timeout) |
| COMMIT | 5 s | Retry, potem ABORT |
| Całość | 10 s | ABORT |

---

## 7. Kliring

### 7.1. Przegląd

Kliring to automatyczne wzajemne rozliczenie długów w zamkniętych cyklach.

### 7.2. Szukanie cykli

#### 7.2.1. Szukanie wyzwalane (po każdej transakcji)

```python
def find_cycles_triggered(changed_edges, max_length=4):
    """
    Szukanie krótkich cykli wokół zmienionych krawędzi.
    """
    cycles = []
    
    for edge in changed_edges:
        # Szukanie cykli długości 3
        cycles += find_triangles(edge)
        
        # Szukanie cykli długości 4
        cycles += find_quadrangles(edge)
    
    return deduplicate(cycles)
```

#### 7.2.2. Szukanie okresowe

| Długość cyklu | Częstotliwość | Algorytm |
|--------------|---------------|----------|
| 3 węzły | Po każdej TX | SQL JOIN |
| 4 węzły | Po każdej TX | SQL JOIN |
| 5 węzłów | Co godzinę | DFS z ograniczeniem |
| 6 węzłów | Raz na dobę | DFS z ograniczeniem |

#### 7.2.3. SQL do szukania trójkątów

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

### 7.3. Transakcja CLEARING

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

### 7.4. Tryby zgody

#### 7.4.1. Auto-zgoda (domyślnie)

Jeśli wszyscy uczestnicy cyklu mają `policy.auto_clearing = true`:

- Kliring wykonywany jest bez jawnej zgody
- Wysyłane jest `CLEARING_NOTICE` dla informacji

#### 7.4.2. Zgoda jawna

Jeśli choć jeden ma `policy.auto_clearing = false`:

- Do wszystkich uczestników wysyłane jest `CLEARING_PROPOSE`
- Oczekiwane jest `CLEARING_ACCEPT` od wszystkich
- Jeśli którykolwiek wyśle `CLEARING_REJECT` → `REJECTED`

### 7.5. Wiadomości kliringu

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

### 7.6. Wykonanie kliringu

```python
def apply_clearing(cycle, amount, equivalent):
    """
    Zmniejszyć długi na wszystkich krawędziach cyklu o amount.
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

### 7.7. Maszyna stanów CLEARING

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

## 8. Interakcja między hubami

### 8.1. Zasada „Hub jako uczestnik"

Każdy hub w federacji jest zwykłym uczestnikiem protokołu:

- Ma `PID` i parę kluczy Ed25519
- Może otwierać TrustLines z innymi hubami
- Uczestniczy w płatnościach i kliringu jak zwykły węzeł

### 8.2. Rejestracja hubu

```json
{
  "pid": "HUB_A_PID",
  "public_key": "...",
  "profile": {
    "type": "hub",
    "name": "Community A Hub",
    "description": "Społeczność lokalna A",
    "endpoint": "https://hub-a.example.com",
    "supported_equivalents": ["UAH", "HOUR"]
  },
  "status": "active"
}
```

### 8.3. Międzyhubowe TrustLines

Hub A otwiera linię zaufania do Hub B:

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

### 8.4. Routing między społecznościami

Płatność od uczestnika A@HubA do uczestnika B@HubB:

```
A@HubA → ... → HubA → HubB → ... → B@HubB
```

**Algorytm:**

1. HubA otrzymuje żądanie od A
2. HubA określa, że B należy do HubB
3. HubA szuka ścieżki: A → ... → HubA
4. HubA→HubB dodawane do trasy
5. HubB szuka ścieżki: HubB → ... → B
6. Złożona trasa wykonywana jest jako jedna transakcja

### 8.5. Protokół płatności między hubami

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

### 8.6. Koordynacja 2PC między hubami

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

### 8.7. Kliring między hubami

Huby mogą brać udział w cyklach kliringu:

```
HubA → HubB → HubC → HubA
```

Kliring odbywa się według standardowego protokołu CLEARING.

### 8.8. Transport między hubami

| Wariant | Protokół | Zastosowanie |
|---------|----------|--------------|
| **REST** | HTTPS + JSON | Prosty wariant |
| **gRPC** | HTTP/2 + Protobuf | Wysoka wydajność |
| **WebSocket** | WSS + JSON | Dwukierunkowe powiadomienia |

---

## 9. Obsługa błędów i odzyskiwanie

### 9.1. Idempotentność operacji

**Zasada:** każda operacja z tym samym `tx_id` musi dawać ten sam wynik.

```python
def handle_commit(tx_id):
    tx = get_transaction(tx_id)
    
    if tx.state == "COMMITTED":
        return SUCCESS  # Już zastosowano
    
    if tx.state == "ABORTED":
        return FAIL  # Już anulowano
    
    # Zastosuj zmiany
    apply_changes(tx)
    tx.state = "COMMITTED"
    save_transaction(tx)
    return SUCCESS
```

### 9.2. Timeouty i powtórzenia

| Operacja | Timeout | Powtórzenia | Działanie |
|----------|---------|------------|-----------|
| PREPARE | 3 s | 2 | ABORT |
| COMMIT | 5 s | 3 | Retry |
| Żądanie między hubami | 10 s | 2 | ABORT |

### 9.3. Odzyskiwanie po awarii hubu

#### 9.3.1. Przy starcie hubu

```python
def recover_on_startup():
    # Znaleźć niezakończone transakcje
    pending = get_transactions_in_state(["PREPARE_IN_PROGRESS", "ROUTED"])
    
    for tx in pending:
        if tx.created_at < now() - TIMEOUT:
            # Timeout minął — anuluj
            abort_transaction(tx)
        else:
            # Kontynuuj od miejsca zatrzymania
            resume_transaction(tx)
```

#### 9.3.2. Czyszczenie prepare_locks

```python
def cleanup_stale_locks():
    stale = get_prepare_locks_older_than(MAX_LOCK_AGE)
    for lock in stale:
        tx = get_transaction(lock.tx_id)
        if tx.state in ["COMMITTED", "ABORTED"]:
            delete_lock(lock)
        else:
            # Transakcja w stanie nieokreślonym
            mark_for_manual_review(lock)
```

### 9.4. Protokół odzyskiwania stanu uczestnika

W razie awarii uczestnika (aplikacji klienckiej) podczas operacji konieczny jest protokół odzyskiwania, zapewniający finalną spójność.

#### 9.4.1. Stany uczestnika po awarii

```
┌─────────────────────────────────────────────────────────────┐
│              Maszyna stanów odzyskiwania                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────┐     online      ┌──────────────┐            │
│   │ OFFLINE  │────────────────►│  RECOVERING  │            │
│   │ (awaria) │                 │              │            │
│   └──────────┘                 └──────┬───────┘            │
│                                       │                     │
│                         ┌─────────────┼─────────────┐      │
│                         │             │             │      │
│                         ▼             ▼             ▼      │
│                   ┌─────────┐   ┌─────────┐   ┌─────────┐  │
│                   │COMMITTED│   │ ABORTED │   │UNCERTAIN│  │
│                   │ (sync)  │   │ (sync)  │   │(eskalacja)│ │
│                   └─────────┘   └─────────┘   └─────────┘  │
│                         │             │             │      │
│                         └─────────────┼─────────────┘      │
│                                       ▼                     │
│                              ┌───────────────┐             │
│                              │ SYNCHRONIZED  │             │
│                              │  (normalna    │             │
│                              │   praca)      │             │
│                              └───────────────┘             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 9.4.2. Wiadomości protokołu odzyskiwania

**RECOVERY_QUERY** — żądanie stanu operacji:

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

**RECOVERY_RESPONSE** — odpowiedź o stanie:

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

#### 9.4.3. Algorytm odzyskiwania

```python
class ParticipantRecoveryService:
    """
    Serwis odzyskiwania stanu uczestnika po awarii.
    """
    
    async def recover_pending_operations(
        self,
        pid: str
    ) -> list[RecoveryResult]:
        """
        Odzyskać stan wszystkich operacji w stanie pending.
        """
        results = []
        
        # 1. Pobierz wszystkie operacje w stanie nieokreślonym
        pending_locks = await self.get_local_prepare_locks(pid)
        
        for lock in pending_locks:
            result = await self.recover_single_operation(
                pid, lock.tx_id, lock
            )
            results.append(result)
        
        return results
    
    async def recover_single_operation(
        self,
        pid: str,
        tx_id: str,
        local_lock: PrepareLock
    ) -> RecoveryResult:
        """
        Odzyskać stan pojedynczej operacji.
        """
        # 2. Wyznacz sąsiadów w danej operacji
        neighbors = self.get_neighbors_from_lock(local_lock)
        
        # 3. Odpytaj sąsiadów o stan operacji
        responses = await self.query_neighbors(tx_id, neighbors)
        
        # 4. Podejmij decyzję na podstawie odpowiedzi
        decision = self.decide_from_responses(responses)
        
        if decision == RecoveryDecision.COMMITTED:
            # Zastosuj efekty lokalnie
            await self.apply_effects(local_lock.effects)
            await self.delete_lock(local_lock)
            return RecoveryResult(tx_id, "COMMITTED")
            
        elif decision == RecoveryDecision.ABORTED:
            # Zwolnij rezerwę
            await self.release_reservation(local_lock)
            await self.delete_lock(local_lock)
            return RecoveryResult(tx_id, "ABORTED")
            
        else:  # UNCERTAIN
            # Eskalacja do hubu lub ręczne rozstrzygnięcie
            await self.escalate_to_hub(tx_id, responses)
            return RecoveryResult(tx_id, "ESCALATED")
    
    async def query_neighbors(
        self,
        tx_id: str,
        neighbors: list[str]
    ) -> list[RecoveryResponse]:
        """
        Odpytanie sąsiadów w łańcuchu operacji.
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
        Decyzja na podstawie odpowiedzi sąsiadów.
        
        Zasady:
        - Jeśli przynajmniej jeden sąsiad potwierdzi COMMITTED → COMMITTED
        - Jeśli wszyscy odpowiedzieli ABORTED → ABORTED
        - Jeśli brak konsensusu → UNCERTAIN
        """
        committed_count = sum(1 for r in responses if r.tx_state == "COMMITTED")
        aborted_count = sum(1 for r in responses if r.tx_state == "ABORTED")
        
        if committed_count > 0:
            # Przynajmniej jeden potwierdził commit — operacja zakończona
            return RecoveryDecision.COMMITTED
        elif aborted_count == len(responses) and len(responses) > 0:
            # Wszyscy anulowali
            return RecoveryDecision.ABORTED
        else:
            # Niepewność
            return RecoveryDecision.UNCERTAIN
```

#### 9.4.4. Gwarancje protokołu odzyskiwania

| Właściwość | Gwarancja |
|-----------|-----------|
| **Safety** | Uczestnik nie zastosuje COMMIT do operacji, która faktycznie została ABORTED |
| **Liveness** | Przy dostępności przynajmniej jednego sąsiada — odzyskiwanie kończy się decyzją |
| **Consistency** | Wszyscy uczestnicy dochodzą do tego samego stanu operacji |
| **Idempotency** | Powtórne odzyskiwanie jest bezpieczne |

#### 9.4.5. Timeouty odzyskiwania

| Etap | Timeout | Działanie |
|------|---------|-----------|
| RECOVERY_QUERY | 5 s | Powtórzyć lub pominąć sąsiada |
| Ogólne odzyskiwanie | 30 s | Eskalacja do hubu |
| Rozstrzygnięcie przez hub | 5 min | Ręczne rozstrzygnięcie admina |

### 9.5. Kody błędów

| Kod | Kategoria | Opis |
|-----|-----------|------|
| `E001` | Routing | Ścieżka nie znaleziona |
| `E002` | Routing | Niewystarczająca pojemność |
| `E003` | TrustLine | Limit przekroczony |
| `E004` | TrustLine | Linia nieaktywna |
| `E005` | Auth | Nieprawidłowy podpis |
| `E006` | Auth | Brak uprawnień |
| `E007` | Timeout | Timeout operacji |
| `E008` | Conflict | Konflikt stanów |
| `E009` | Validation | Nieprawidłowe dane |
| `E010` | Internal | Błąd wewnętrzny |

### 9.6. Format komunikatu błędu

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

## 10. Rozwiązywanie sporów

### 10.1. Typy sporów

| Typ | Opis | Częstość |
|-----|------|----------|
| **Brak zgody co do płatności** | Uczestnik kwestionuje fakt lub kwotę | Rzadko |
| **Spór o jakość** | Towar/usługa niezgodna z oczekiwaniami | Średnio |
| **Awaria techniczna** | Rozbieżności stanów | Rzadko |
| **Oszustwo** | Celowe działanie w złej wierze | Bardzo rzadko |

### 10.2. Status „Transakcja sporna"

```json
{
  "tx_id": "uuid",
  "dispute_status": "disputed",
  "dispute_info": {
    "opened_by": "PID_A",
    "opened_at": "ISO8601",
    "reason": "Towar nie dostarczony",
    "evidence": ["url1", "url2"]
  }
}
```

### 10.3. Proces rozwiązywania

```
┌──────────────────┐
│ Transakcja OK    │
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
(closure)  (arbitraż)
```

### 10.4. API do obsługi sporów

#### Otwarcie sporu

```json
{
  "action": "DISPUTE_OPEN",
  "tx_id": "uuid",
  "reason": "string",
  "evidence": ["url"],
  "requested_outcome": "refund | adjustment | investigation"
}
```

#### Odpowiedź na spór

```json
{
  "action": "DISPUTE_RESPOND",
  "dispute_id": "uuid",
  "response": "string",
  "evidence": ["url"],
  "proposed_resolution": {...}
}
```

### 10.5. Role w rozstrzyganiu sporów

| Rola | Uprawnienia |
|------|-------------|
| **Uczestnik** | Otwarcie sporu, dostarczanie dowodów |
| **Operator** | Podgląd logów, żądanie dodatkowych informacji |
| **Arbiter** | Podejmowanie decyzji, tworzenie kompensacji |
| **Admin** | Zamrażanie uczestników, eskalacja |

### 10.6. Transakcje kompensacyjne

Przy uwzględnieniu sporu tworzona jest transakcja kompensacyjna:

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

### 10.7. Nieusuwalność historii

**Ważne:** transakcje nie są usuwane ani modyfikowane.

- Do korekt używane są **nowe transakcje**
- Historia jest zawsze odtwarzalna
- Audyt jest możliwy na podstawie łańcucha transakcji

---

## 11. Weryfikacja integralności systemu

### 11.1. Przeznaczenie

System weryfikacji integralności zapewnia:

- **Wykrywanie błędów** w algorytmach kliringu i płatności
- **Ochronę przed korupcją danych** przy awariach
- **Audyt poprawności** wszystkich operacji
- **Wczesne ostrzeganie** o problemach

### 11.2. Fundamentalne niezmienniki

#### 11.2.1. Inwariant sumy zerowej (Zero-Sum Invariant)

**Definicja:** Suma wszystkich sald w każdym ekwiwalencie musi równać się zero.

```
∀ equivalent E: ∑ net_balance(participant, E) = 0
```

**Sprawdzenie SQL:**

```sql
-- Dla każdego ekwiwalentu wynik powinien być 0
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

**Częstotliwość:** Po każdej transakcji + okresowo (co 5 minut)

#### 11.2.2. Inwariant limitu zaufania (Trust Limit Invariant)

**Definicja:** Dług nie może przekroczyć limitu linii zaufania.

```
∀ (debtor, creditor, equivalent):
  debt[debtor → creditor, E] ≤ trust_line[creditor → debtor, E].limit
```

**Sprawdzenie SQL:**

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

**Częstotliwość:** Po każdej transakcji

#### 11.2.3. Inwariant neutralności kliringu (Clearing Neutrality Invariant)

**Definicja:** Kliring zmniejsza wzajemne długi, ale NIE zmienia pozycji netto uczestników.

```
∀ participant P in cycle:
  net_position(P, E)_before = net_position(P, E)_after
```

**Sprawdzenie:**

```python
def verify_clearing_neutrality(cycle: list[str], amount: Decimal, equivalent: str):
    """
    Sprawdzić, że kliring nie zmienił pozycji netto uczestników.
    """
    # Obliczyć pozycje netto PRZED
    positions_before = {}
    for pid in cycle:
        positions_before[pid] = calculate_net_position(pid, equivalent)
    
    # Wykonać kliring
    apply_clearing(cycle, amount, equivalent)
    
    # Obliczyć pozycje netto PO
    positions_after = {}
    for pid in cycle:
        positions_after[pid] = calculate_net_position(pid, equivalent)
    
    # Sprawdzić inwariant
    for pid in cycle:
        if positions_before[pid] != positions_after[pid]:
            raise IntegrityViolation(
                f"Clearing changed net position of {pid}: "
                f"{positions_before[pid]} → {positions_after[pid]}"
            )
    
    return True
```

**Częstotliwość:** Przy każdym kliringu

#### 11.2.4. Inwariant symetrii długu (Debt Symmetry Invariant)

**Definicja:** Dla każdej pary uczestników dług może istnieć tylko w jednym kierunku.

```
∀ (A, B, E): NOT (debt[A→B, E] > 0 AND debt[B→A, E] > 0)
```

**Sprawdzenie SQL:**

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

**Częstotliwość:** Po każdej transakcji

### 11.3. Sumy kontrolne stanu

#### 11.3.1. State Checksum (suma kontrolna stanu)

**Format:** SHA-256 z kanonicznej reprezentacji wszystkich długów

```python
def compute_state_checksum(equivalent: str) -> str:
    """
    Wyliczanie sumy kontrolnej stanu dla ekwiwalentu.
    """
    # Pobierz wszystkie długi, deterministycznie posortowane
    debts = db.query("""
        SELECT debtor_id, creditor_id, amount
        FROM debts
        WHERE equivalent_id = (SELECT id FROM equivalents WHERE code = :eq)
        ORDER BY debtor_id, creditor_id
    """, {"eq": equivalent})
    
    # Zbuduj kanoniczny string
    canonical = "|".join([
        f"{d.debtor_id}:{d.creditor_id}:{d.amount}"
        for d in debts
    ])
    
    # Wylicz SHA-256
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
```

#### 11.3.2. Incremental Checksum (przyrostowa suma kontrolna)

Dla optymalizacji przy częstych operacjach:

```python
def update_checksum_incremental(
    current_checksum: str,
    operation: str,  # "add" | "remove" | "update"
    debt_record: DebtRecord,
    previous_amount: Decimal | None = None
) -> str:
    """
    Przyrostowe aktualizowanie sumy kontrolnej.
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

### 11.4. Audit Trail (dziennik audytu)

#### 11.4.1. Struktura wpisu audytu

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

#### 11.4.2. Schemat SQL dziennika audytu

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

### 11.5. Algorytmy odzyskiwania

#### 11.5.1. Przy wykryciu naruszenia Zero-Sum

```python
async def handle_zero_sum_violation(equivalent: str, imbalance: Decimal):
    """
    Obsługa naruszenia inwariantu sumy zerowej.
    """
    # 1. Zablokuj operacje w tym ekwiwalencie
    await lock_equivalent(equivalent)
    
    # 2. Znajdź ostatnią poprawną sumę kontrolną
    last_valid = await find_last_valid_checkpoint(equivalent)
    
    # 3. Powiadom administratorów
    await alert_admins(
        severity="CRITICAL",
        message=f"Zero-sum violation in {equivalent}: imbalance = {imbalance}",
        last_valid_checkpoint=last_valid
    )
    
    # 4. Utwórz raport do analizy ręcznej
    report = await generate_integrity_report(equivalent, since=last_valid.timestamp)
    
    return report
```

#### 11.5.2. Przy wykryciu naruszenia limitu zaufania

```python
async def handle_trust_limit_violation(
    debtor: str, 
    creditor: str, 
    equivalent: str,
    debt: Decimal,
    limit: Decimal
):
    """
    Obsługa przekroczenia limitu zaufania.
    """
    violation_amount = debt - limit
    
    # 1. Zamroź linię zaufania
    await freeze_trust_line(creditor, debtor, equivalent)
    
    # 2. Zaloguj incydent
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
    
    # 3. Powiadom uczestników
    await notify_participants([debtor, creditor], 
        message="Trust line frozen due to integrity violation")
```

### 11.6. Okresowe kontrole

#### 11.6.1. Harmonogram

| Kontrola | Częstotliwość | Priorytet |
|----------|---------------|-----------|
| Zero-Sum | Co 5 min | Krytyczny |
| Trust Limits | Co 5 min | Krytyczny |
| Debt Symmetry | Co 15 min | Wysoki |
| State Checksum | Co godzinę | Średni |
| Pełny audyt | Raz na dobę | Niski |

#### 11.6.2. Zadanie w tle

```python
async def integrity_check_task():
    """
    Okresowe zadanie weryfikacji integralności.
    """
    while True:
        try:
            for equivalent in await get_active_equivalents():
                report = await run_integrity_checks(equivalent)
                
                if not report.all_passed:
                    await handle_integrity_failure(report)
                else:
                    await save_checkpoint(equivalent, report.checksum)
            
            await asyncio.sleep(300)  # 5 minut
            
        except Exception as e:
            await alert_admins(severity="ERROR", message=str(e))
            await asyncio.sleep(60)  # Ponów po minucie
```

### 11.7. API weryfikacji

#### 11.7.1. Endpointy

| Metoda | Endpoint | Opis |
|--------|----------|------|
| GET | `/api/v1/integrity/status` | Bieżący status integralności |
| GET | `/api/v1/integrity/checksum/{equivalent}` | Suma kontrolna stanu |
| POST | `/api/v1/integrity/verify` | Ręczne uruchomienie pełnej kontroli |
| GET | `/api/v1/integrity/audit-log` | Dziennik audytu |

#### 11.7.2. Przykład odpowiedzi /integrity/status

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

## Załączniki

### A. Kanoniczny JSON

Do obliczania podpisów używa się kanonicznej formy JSON:

- Klucze posortowane alfabetycznie
- Bez zbędnych spacji
- Kodowanie UTF-8
- Liczby bez niepotrzebnych zer

### B. Alfabet Base58

```
123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
```

(pominięto: 0, O, I, l)

### C. Wersjonowanie protokołu

Format wersji: `MAJOR.MINOR`

| Wersja | Kompatybilność |
|--------|----------------|
| 0.x | Eksperymentalna, może łamać kompatybilność |
| 1.x | Stabilna, wstecznie kompatybilna |

---

## Powiązane dokumenty

- [00-overview.md](00-overview.md) — Przegląd projektu  
- [01-concepts.md](01-concepts.md) — Kluczowe koncepcje  
- [03-architecture.md](03-architecture.md) — Architektura systemu  
- [04-api-reference.md](04-api-reference.md) — Dokumentacja API
