# Analiza projektu GEO: raport kompleksowy

**Data analizy:** 29 listopada 2025  
**Wersja:** 1.0

---

## Spis treści

1. [Krótki opis projektu](#1-krótki-opis-projektu)
2. [Analiza bieżącego stanu](#2-analiza-bieżącego-stanu)
3. [Rekomendacje dotyczące ulepszeń](#3-rekomendacje-dotyczące-ulepszeń)
4. [Plan ewolucji fazowej](#4-plan-ewolucji-fazowej)
5. [Założenia i pytania](#5-założenia-i-pytania)

---

## 1. Krótki opis projektu

### 1.1. Jaki problem rozwiązuje GEO

GEO to **protokół zdecentralizowanej sieci kredytowej** do organizacji gospodarki wzajemnego kredytu bez udziału tradycyjnych pośredników finansowych.

**Kluczowe problemy, które projekt adresuje:**

1. **Odpływ płynności z lokalnych gospodarek** — pieniądze wydane w lokalnym biznesie szybko „wypływają" do dużych korporacji, banków i innych regionów
2. **Oprocentowanie jako bariera** — odsetki od kredytów tworzą minimalny próg rentowności projektów i czynią kredyt niedostępnym dla małego biznesu
3. **Centralizacja systemu monetarnego** — zależność od banków i regulatorów tworzy punkty awarii i kontroli
4. **Deficyt płynności „na ziemi"** — sytuacje, gdy istnieją wzajemne potrzeby i możliwości, ale brakuje „pieniędzy" jako pośrednika

### 1.2. Dla kogo jest przeznaczony

**Segmenty docelowe:**
- Społeczności lokalne (spółdzielnie, komuny, zrzeszenia mieszkańców)
- Klastry małego biznesu
- Sieci profesjonalne (freelancerzy, rzemieślnicy)
- Alternatywne eksperymenty gospodarcze (banki czasu, systemy LETS)

**Skala MVP:** 10–500 uczestników w jednej społeczności z możliwością łączenia społeczności.

### 1.3. Główne funkcje (bieżące i planowane)

**Realizowane w MVP (v0.1):**
- Rejestracja uczestników z tożsamością kryptograficzną (Ed25519)
- Zarządzanie liniami zaufania (TrustLines) — jednostronne limity kredytowe
- Płatności przez sieć zaufania (routing BFS, multi-path light)
- Automatyczny kliring krótkich cykli (3–4 węzły)
- Prosta sprawozdawczość i analityka

**Oczekiwane w przyszłości:**
- Wymiana między społecznościami (hub-to-hub)
- Rozszerzony kliring (cykle 5–6 węzłów)
- Częściowy tryb p2p (grube klienty)
- Integracja z blockchainami (kotwiczenie, bramy)
- Złożone polityki zaufania i scoring ryzyka

### 1.4. Kluczowa filozofia

W przeciwieństwie do systemów blockchain, GEO:
- **Nie ma własnej waluty/tokenu** — w obiegu tylko zobowiązania uczestników
- **Nie ma globalnego ledgera** — tylko lokalne stany i podpisy
- **Skupia się na klirngu** — automatyczne kompensowanie długów jako główna wartość
- **Lokalizuje ryzyka** — każdy sam decyduje, komu i ile zaufać

---

## 2. Analiza bieżącego stanu

### 2.1. Protokół interakcji komponentów

#### 2.1.1. Model danych

**Jednostki protokołu GEO v0.1:**

| Jednostka | Opis | Kluczowe pola |
|-----------|------|---------------|
| **Participant** | Uczestnik sieci | PID (sha256→base58 od pubkey), public_key, profile, status |
| **Equivalent** | Jednostka rozliczeniowa | code, precision, metadata |
| **TrustLine** | Linia zaufania | from, to, equivalent, limit, policy, status |
| **Debt** | Zobowiązanie | debtor, creditor, equivalent, amount |
| **Transaction** | Jednostka zmian | tx_id, type, initiator, payload, signatures, state |

**Niezmiennik:** `debt[B→A, E] ≤ limit(A→B, E)` — dług nie może przekroczyć ustalonego limitu zaufania.

#### 2.1.2. Typy transakcji i stany

**PAYMENT:**
```
NEW → ROUTED → PREPARE_IN_PROGRESS → COMMITTED | ABORTED
```

**CLEARING:**
```
NEW → [PROPOSED → WAITING_CONFIRMATIONS →] COMMITTED | REJECTED
```

**TRUST_LINE_*:**
```
PENDING → COMMITTED | FAILED
```

#### 2.1.3. Protokół wymiany wiadomości

**Typy wiadomości:**
- `TRUST_LINE_CREATE/UPDATE/CLOSE` — zarządzanie zaufaniem
- `PAYMENT_REQUEST` → `PAYMENT_PREPARE` → `PAYMENT_PREPARE_ACK` → `PAYMENT_COMMIT/ABORT`
- `CLEARING_PROPOSE` → `CLEARING_ACCEPT/REJECT` → `CLEARING_COMMIT/ABORT`

**Format podstawowy:**
```json
{
  "msg_id": "UUID",
  "msg_type": "STRING",
  "tx_id": "UUID",
  "from": "PID",
  "to": "PID",
  "payload": { ... },
  "signature": "BASE64(ed25519_signature)"
}
```

#### 2.1.4. Konsensus i koordynacja

- **Mechanizm:** Dwufazowe zatwierdzenie (2PC)
- **Koordynator w MVP:** Community-hub
- **Idempotentność:** Według `tx_id` — powtórne operacje są bezpieczne

### 2.2. Architektura

#### 2.2.1. Architektura MVP (community-hub)

```
┌─────────────────────────────┐
│    Klienty (Flutter)        │
│  - Aplikacja mobilna        │
│  - Klient desktopowy        │
└──────────────┬──────────────┘
|                │ HTTPS / WebSocket (JSON)
|                ▼
┌─────────────────────────────┐
│   API Gateway / FastAPI     │
└──────────────┬──────────────┘
|                ▼
┌─────────────────────────────┐
│    Community Hub Core       │
│  ┌─────────────────────────┐│
│  │ Auth & Identity         ││
│  │ TrustLines Service      ││
│  │ RoutingService (BFS)    ││
│  │ PaymentEngine (2PC)     ││
│  │ ClearingEngine (cykle)  ││
│  │ Reporting & Analytics   ││
│  │ System dodatkow         ││
│  └─────────────────────────┘│
└──────────────┬──────────────┘
|                ▼
┌─────────────────────────────┐
│       Data Layer            │
│  - PostgreSQL (ACID)        │
│  - Redis (cache, blokady)   │
└─────────────────────────────┘
```

#### 2.2.2. Podział odpowiedzialności

| Komponent | Odpowiedzialność |
|-----------|------------------|
| **Auth & Identity** | Rejestracja, tokeny JWT, powiązanie pubkey↔participant |
| **TrustLines Service** | CRUD linii zaufania, weryfikacja podpisów |
| **RoutingService** | Wyszukiwanie ścieżek (BFS), obliczanie available_credit |
| **PaymentEngine** | Fazy PREPARE/COMMIT, rezerwowanie, atomowość |
| **ClearingEngine** | Wyszukiwanie cykli, kliring wyzwalany i okresowy |
| **Dodatki** | Rozszerzenia przez entry_points (styl Home Assistant) |

#### 2.2.3. Przechowywanie danych

**PostgreSQL (główne przechowywanie):**
- participants, equivalents, trust_lines, debts, transactions
- Integralność transakcyjna (ACID)

**Redis (dane operacyjne):**
- Sesje użytkowników
- prepare_locks dla faz płatności
- Kolejki zadań (RQ/Celery/Arq)

### 2.3. Stos technologiczny

#### 2.3.1. Wybrany stos (dokumentacja)

| Warstwa | Technologia | Uzasadnienie |
|---------|-------------|--------------|
| **Język backend** | Python 3.11+ | Czytelność, ekosystem, próg wejścia |
| **Framework web** | FastAPI | Async, Pydantic, OpenAPI |
| **ORM** | SQLAlchemy 2.x + Alembic | Niezawodność, migracje |
| **BD** | PostgreSQL | ACID, dojrzałość |
| **Cache/kolejki** | Redis | Uniwersalność |
| **Testy** | pytest | Standard Python |
| **Konteneryzacja** | Docker, docker-compose | Przenośność |
| **Klienty** | Flutter (Dart) | Wieloplatformowość |
| **Web-admin** | Jinja2 + HTMX/Alpine.js | Prostota, bez SPA |
| **Kryptografia** | libsodium / tweetnacl (Ed25519) | Niezawodność, post-kwantowość |

### 2.4. Porównanie z oryginalnym GEO Protocol

#### 2.4.1. Co zaczerpnięto z oryginalnego protokołu

| Koncepcja | Oryginał | GEO v0.1 |
|-----------|----------|----------|
| **TrustLines** | Tak (Twin State) | Tak (uproszczone) |
| **Płatności tranzytywne** | Tak | Tak |
| **Multi-path** | Agresywny | Light (2–3 ścieżki) |
| **Kliring cykli** | 3–6 węzłów | 3–4 węzły (MVP) |
| **Kryptografia** | Lamport (post-kwantowa) | Ed25519 (prostota) |
| **Koordynacja** | p2p | Koordynator-hub |
| **Observers** | Arbitraż sporów | Nie w MVP |

#### 2.4.2. Co zostało uproszczone

1. **Usunięto protokół p2p** — wszystko przez HTTP/WebSocket do hub-a
2. **Usunięto podpis Lamport** — zbyt uciążliwy (klucze 16KB, podpis 8KB)
3. **Uproszczono maszyny stanów** — mniej stanów pośrednich
4. **Usunięto arbitraż Observer** — odłożony na następne wersje
5. **Uproszczono routing** — BFS zamiast złożonego max-flow

### 2.5. Mocne strony bieżących rozwiązań

1. **Minimalizm i zrozumiałość**
   - Jasne jednostki i ich semantyka
   - Proste algorytmy (BFS, 2PC, wyszukiwanie krótkich cykli)
   - Dobrze udokumentowane

2. **Realizm dla MVP**
   - Klasyczny stos (Python + PostgreSQL)
   - Niski próg wejścia dla współtwórców
   - Zrozumiała ścieżka od dokumentacji do kodu

3. **Elastyczność architektury**
   - Protokół oddzielony od transportu
   - Przygotowanie na tryb p2p i między-hub
   - System dodatków do rozszerzeń

4. **Fokus na praktykę**
   - Orientacja na społeczności lokalne
   - Rezygnacja z „globalnego Internet of Value" w pierwszej wersji

### 2.6. Słabe strony i ryzyka

#### 2.6.1. Wydajność

| Problem | Wpływ | Krytyczność |
|---------|-------|-------------|
| BFS na każdą płatność | Złożoność liniowa względem grafu | Średnia (do 500 uczestników — akceptowalne) |
| Wyszukiwanie cykli w BD | JOIN-y SQL dla 3–4 węzłów | Niska (optymalizowane indeksami) |
| Brak cache'owania grafu | Powtórne zapytania do BD | Średnia |

#### 2.6.2. Skalowalność

| Problem | Wpływ | Krytyczność |
|---------|-------|-------------|
| Jeden hub = jeden punkt obciążenia | Sufit RPS | Średnia (rozwiązywane klastrowaniem) |
| PostgreSQL na zapis | Wąskie gardło przy wysokim obciążeniu | Niska dla MVP |

#### 2.6.3. Niezawodność

| Problem | Wpływ | Krytyczność |
|---------|-------|-------------|
| Hub = single point of failure | Społeczność offline przy awarii | **Wysoka** |
| Brak mechanizmu arbitrażu | Spory rozwiązywane „ręcznie" | Średnia |
| Brak formalnej weryfikacji 2PC | Potencjalne edge cases | Niska |

#### 2.6.4. Prostota rozwoju i wsparcia

| Problem | Wpływ | Krytyczność |
|---------|-------|-------------|
| Wiele dokumentów z przecięciami | Trudno znaleźć „prawdę" | Średnia |
| Brak specyfikacji OpenAPI | Brak kontraktu dla klientów | Średnia |
| Brak testów (na razie) | Regresje przy refaktoringu | Wysoka |

#### 2.6.5. Wdrażanie programistów

| Problem | Wpływ | Krytyczność |
|---------|-------|-------------|
| Wiele dokumentów koncepcyjnych | Długie wejście w kontekst | Średnia |
| Brak przykładu „Hello World" | Niejasne od czego zacząć | Średnia |

---

## 3. Rekomendacje dotyczące ulepszeń

### 3.1. Ulepszenia lokalne (niskokosztowe)

#### 3.1.1. Konsolidacja dokumentacji

**Działanie:** Połączenie dokumentów w jednolitą strukturę z jasną hierarchią.

**Proponowana struktura:**
```
docs/
├── 00-overview.md           # Krótki opis projektu
├── 01-concepts.md           # Kluczowe koncepcje (TL;DR)
├── 02-protocol-spec.md      # Formalna specyfikacja protokołu
├── 03-architecture.md       # Architektura MVP
├── 04-api-reference.md      # OpenAPI + kontrakty WebSocket
├── 05-deployment.md         # Instrukcje wdrożenia
├── 06-contributing.md       # Jak wnosić wkład
└── legacy/                  # Archiwum starych dokumentów
```

**Efekt:** Przyśpieszenie wdrażania, jedno źródło prawdy.

#### 3.1.2. Specyfikacja OpenAPI

**Działanie:** Opisać REST API w formacie OpenAPI 3.0.

```yaml
# Przykład
/api/v1/payments:
  post:
    summary: Utwórz płatność
    requestBody:
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/PaymentRequest'
    responses:
      '201':
        description: Płatność utworzona
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/PaymentResponse'
```

**Efekt:** Autogeneracja klientów, dokumentacja API, walidacja.

#### 3.1.3. Cache'owanie grafu zaufania

**Działanie:** Dodanie cache in-memory dla często zapytywanych danych.

```python
# Przykład z Redis
class GraphCache:
    def get_available_credit(self, from_pid, to_pid, eq) -> Decimal:
        key = f"credit:{from_pid}:{to_pid}:{eq}"
        cached = redis.get(key)
        if cached:
            return Decimal(cached)
        # Fallback to DB
        credit = self._calculate_from_db(from_pid, to_pid, eq)
        redis.setex(key, 60, str(credit))  # TTL 60s
        return credit
```

**Efekt:** Przyspieszenie routingu 5–10 razy.

#### 3.1.4. Podstawowy zestaw testów

**Działanie:** Pokrycie krytycznych ścieżek testami unit i integration.

**Priorytety:**
1. TrustLines Service — CRUD, weryfikacja podpisów
2. PaymentEngine — happy path, edge cases
3. ClearingEngine — wyszukiwanie cykli, zastosowanie
4. Endpointy API — walidacja, autoryzacja

**Efekt:** Pewność przy refaktoringu, wczesne wykrywanie błędów.

#### 3.1.5. Health checks i monitoring

**Działanie:** Dodanie endpointów do sprawdzania stanu.

```python
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "db": await check_db(),
        "redis": await check_redis(),
        "version": "0.1.0"
    }

@app.get("/metrics")
async def metrics():
    return {
        "participants_count": await count_participants(),
        "active_payments": await count_active_payments(),
        "pending_clearings": await count_pending_clearings()
    }
```

**Efekt:** Przejrzystość operacyjna, szybka diagnostyka.

### 3.2. Ulepszenia średnioterminowe

#### 3.2.1. Ulepszenie algorytmu routingu

**Bieżące:** BFS z ograniczeniem głębokości, 1–3 ścieżki.

**Ulepszenie:** k-najkrótsze ścieżki z wagami na podstawie:
- Available credit (pojemność)
- Historycznej niezawodności węzła
- Czasu odpowiedzi

```python
def find_k_paths(graph, source, target, k=3):
    """
    Algorytm Yen-a dla k najkrótszych ścieżek
    z uwzględnieniem capacity jako „długości" krawędzi
    """
    paths = []
    # ... implementacja
    return paths[:k]
```

**Efekt:** Lepsze wykorzystanie pojemności sieci.

#### 3.2.2. Rozszerzony kliring

**Bieżące:** Cykle 3–4 węzły, wyzwalany + okresowy.

**Ulepszenie:**
1. Cykle 5–6 węzłów (nocny proces batch)
2. Priorytetyzacja cykli według sumy (najpierw duże)
3. Równoległe wyszukiwanie w podgrafach

**Efekt:** Więcej „skompensowanych" długów, mniej napięcia w sieci.

#### 3.2.3. Backup i odzyskiwanie

**Działanie:** Automatyczny backup stanu uczestnika.

```python
class ParticipantBackup:
    def export_state(self, pid: str) -> dict:
        """Export podpisanego stanu"""
        return {
            "pid": pid,
            "trust_lines": [...],
            "debts": [...],
            "signatures": [...],
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def verify_and_restore(self, backup: dict) -> bool:
        """Weryfikacja podpisów i odzyskiwanie"""
        ...
```

**Efekt:** Odporność na utratę danych, migracja między hubami.

#### 3.2.4. WebSocket dla czasu rzeczywistego

**Bieżące:** Prawdopodobnie polling lub SSE.

**Ulepszenie:** Full-duplex WebSocket dla:
- Powiadomień o płatnościach
- Aktualizacji stanu TrustLines
- Propozycji klirngu

```python
@app.websocket("/ws/{participant_id}")
async def websocket_endpoint(websocket: WebSocket, participant_id: str):
    await manager.connect(participant_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await handle_message(participant_id, data)
    except WebSocketDisconnect:
        manager.disconnect(participant_id)
```

**Efekt:** Natychmiastowy UX, mniej obciążenia od polling.

### 3.3. Głęboka ewolucja architektury

#### 3.3.1. Klastrowanie Hub-a

**Kiedy:** >500 uczestników lub wymagania dostępności.

**Jak:**
1. Kilka instancji FastAPI za balancerem
2. PostgreSQL z replikacją (primary + replica)
3. Redis Cluster dla rozproszonego cache
4. Sticky sessions dla WebSocket

```
                    ┌─────────────┐
                    │    Nginx    │
                    │ (balancer)  │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │   Hub #1   │  │   Hub #2   │  │   Hub #3   │
    └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
          │               │               │
          └───────────────┼───────────────┘
                          ▼
              ┌──────────────────────┐
              │ PostgreSQL Primary   │
              │  + Read Replicas     │
              └──────────────────────┘
```

**Efekt:** Skalowanie horyzontalne, odporność na awarie.

**Koszty:** Średnie (infrastruktura, DevOps).

#### 3.3.2. Protokół między społecznościami

**Kiedy:** Kilka społeczności chce wymieniać.

**Jak:** Hub-to-Hub przez ten sam protokół:
1. Każdy Hub — Participant z PID
2. TrustLines między Hub-ami
3. Płatności routowane przez Hub-y
4. Kliring między Hub-ami

```
┌───────────────┐         ┌───────────────┐
│ Społeczność A │         │ Społeczność B │
│  ┌─────────┐  │         │  ┌─────────┐  │
│  │ Hub A   │◄─┼─────────┼─►│ Hub B   │  │
│  └────┬────┘  │TrustLine│  └────┬────┘  │
│       │       │         │       │       │
│  [użytkownicy A]        │  [użytkownicy B]    │
└───────────────┘         └───────────────┘
```

**Efekt:** Federacja społeczności bez centrum.

**Koszty:** Niskie (protokół już gotowy).

#### 3.3.3. Częściowy tryb p2p

**Kiedy:** Pojawiają się „grubi" cczęstnicy (organizacje z serwerami).

**Jak:**
1. Gruby klient przechowuje swój stan lokalnie
2. Synchronizuje się z Hub przez extended API
3. Uczestniczy w 2PC bezpośrednio (nie przez Hub)

**Efekt:** Mniejsza zależność od Hub, rozprowadzanie obciążenia.

**Koszty:** Wysokie (nowy klient, złożona synchronizacja).

#### 3.3.4. Dziennik konsensusu (Raft/Tendermint)

**Kiedy:** Krytyczne wymagania konsystentności między Hub-ami.

**Jak:** Zastąpić PostgreSQL rozproszonym dziennikiem:
1. Klaster Raft z węzłów Hub
2. Wszystkie transakcje przez konsensus
3. Gwarantowana replikacja

**Efekt:** Maksymalna niezawodność.

**Koszty:** Bardzo wysokie (przepisanie data layer).

**Rekomendacja:** Nie dla MVP, tylko przy wyraźnej potrzebie.

---

## 4. Plan ewolucji fazowej

### Faza 0: Przygotowanie (2–4 tygodnie)

| Zadanie | Priorytet | Nakład pracy |
|---------|-----------|---------------|
| Konsolidacja dokumentacji | Wysoki | 3–5 dni |
| Utworzenie specyfikacji OpenAPI | Wysoki | 2–3 dni |
| Konfiguracja CI/CD (GitHub Actions) | Wysoki | 1–2 dni |
| Podstawowy README z quickstart | Wysoki | 1 dzień |

### Faza 1: MVP Backend (4–6 tygodni)

| Zadanie | Priorytet | Nakład pracy |
|---------|-----------|---------------|
| Szkielet FastAPI + struktura projektu | Wysoki | 2–3 dni |
| Modele SQLAlchemy (participants, TL, debts, tx) | Wysoki | 3–5 dni |
| Auth & Identity (rejestracja, JWT) | Wysoki | 3–4 dni |
| TrustLines Service (CRUD + podpisy) | Wysoki | 4–5 dni |
| RoutingService (BFS) | Wysoki | 3–4 dni |
| PaymentEngine (2PC) | Wysoki | 5–7 dni |
| ClearingEngine (cykle 3–4) | Średni | 4–5 dni |
| Testy unit krytycznych ścieżek | Wysoki | 3–5 dni |

### Faza 2: MVP Frontend (4–6 tygodni)

| Zadanie | Priorytet | Nakład pracy |
|---------|-----------|---------------|
| Szkielet Flutter + nawigacja | Wysoki | 2–3 dni |
| Ekran rejestracji/autoryzacji | Wysoki | 3–4 dni |
| Dashboard (saldo, metryki) | Wysoki | 3–4 dni |
| Zarządzanie TrustLines | Wysoki | 4–5 dni |
| Tworzenie płatności | Wysoki | 4–5 dni |
| Historia transakcji | Średni | 2–3 dni |
| Powiadomienia (WebSocket) | Średni | 3–4 dni |

### Faza 3: Stabilizacja i pilot (4–8 tygodni)

| Zadanie | Priorytet | Nakład pracy |
|---------|-----------|---------------|
| Testy integration | Wysoki | 5–7 dni |
| Dokumentacja dla użytkowników | Wysoki | 3–5 dni |
| Uruchomienie pilotowe (10–20 uczestników) | Wysoki | 2–4 tygodnie |
| Poprawki błędów na podstawie feedbacku | Wysoki | Ciągłe |
| Health checks + podstawowy monitoring | Średni | 2–3 dni |

### Faza 4: Skalowanie (6–12 tygodni)

| Zadanie | Priorytet | Nakład pracy |
|---------|-----------|---------------|
| Cache'owanie grafu (Redis) | Wysoki | 3–5 dni |
| Rozszerzony kliring (5–6 węzłów) | Średni | 5–7 dni |
| Web-admin (Jinja2 + HTMX) | Średni | 5–7 dni |
| Klastrowanie Hub (Nginx + replicas) | Średni | 7–10 dni |
| Wymiana między-hub (pilot) | Niski | 5–7 dni |

### Faza 5: Zaawansowane możliwości (w razie potrzeby)

| Zadanie | Priorytet | Nakład pracy |
|---------|-----------|---------------|
| Gruby klient (tryb p2p) | Niski | 3–4 tygodnie |
| Arbitraż Observer | Niski | 2–3 tygodnie |
| Integracja z blockchain | Niski | 4–6 tygodni |
| Algorytm k-najkrótsze ścieżki | Średni | 1–2 tygodnie |

---

## 5. Założenia i pytania

### 5.1. Założenia przyjęte w analizie

1. **Stos technologiczny jest finalnie wybrany**
   - Zakładany Python + FastAPI + PostgreSQL + Flutter
   - Jeśli zespół rozważa alternatywy (Go, TypeScript), rekomendacje będą wymagały korekty

2. **MVP ukierunkowane na jedną społeczność**
   - Wymiana między-hub — następny etap
   - Jeśli to krytyczne od pierwszego dnia, architektura będzie wymagała przeglądu

3. **Zaufanie do Hub-a w ramach społeczności**
   - Uczestnicy ufają administratorowi Hub-a
   - Podpisy kryptograficzne — dodatkowa ochrona, ale nie główna ochrona

4. **Skala 10–500 uczestników**
   - Dla tysięcy użytkowników potrzebne będą inne podejścia do routingu i przechowywania

5. **Brak twardych wymagań regulacyjnych**
   - System nie pozycjonuje się jako „pieniądz elektroniczny" w sensie prawnym
   - Jeśli wymagany compliance, znacznie skomplikuje to architekturę

### 5.2. Miejsca z niewystarczającą informacją

| Pytanie | Kontekst | Wpływ |
|---------|----------|-------|
| Jaka jest docelowa przepustowość (TPS)? | Brak wyraźnych NFR w dokumentacji | Wpływa na wybór między sync/async, cache'owanie |
| Czy są wymagania dotyczące opóźnienia płatności? | Ważne dla UX | Wpływa na timeout-y 2PC, multi-path |
| Jak będą rozwiązywane spory? | Observer odłożony | Potrzebny przynajmniej proces ręczny |
| Kto administruje Hub? | Nie opisane | Wpływa na bezpieczeństwo, dostępy |
| Jak przebiega KYC/weryfikacja? | Rejestracja uproszczona | Ważne dla rzeczywistych społeczności |

### 5.3. Sprzeczności między kodem a dokumentacją

> **Uwaga:** Kod jeszcze nie jest napisany, więc sprzeczności nie ma. Jednak jest kilka miejsc, gdzie dokumenty różnią się między sobą:

1. **Format podpisów**
   - W `GEO v0.1` wspomina się Ed25519
   - W starych dokumentach (Twin Spark) — Lamport
   - **Rekomendacja:** Wyraźnie ustalić Ed25519 dla v0.1

2. **Stos technologii**
   - W jednym dokumencie wspomina się TypeScript + NestJS
   - W innym — Python + FastAPI
   - **Rekomendacja:** Sfinalizować wybór w README

3. **Rola koordynatora**
   - W protokole — „każdy węzeł może być koordynatorem"
   - W architekturze — „koordynator = Hub"
   - **Rekomendacja:** Dla MVP wyraźnie wskazać, że Hub — jedyny koordynator

### 5.4. Pytania do zespołu projektu

1. **Strategiczne:**
   - Jaki jest horyzont planowania? (6 mies / 1 rok / 3 lata)
   - Czy jest konkretna społeczność do pilotu?
   - Jaki budżet na infrastrukturę?

2. **Techniczne:**
   - Czy finalnie wybrany Python + FastAPI?
   - Czy potrzebna obsługa trybu offline w kliencie mobilnym?
   - Jakie SLA planowane dla Hub-a (uptime)?

3. **Produktowe:**
   - Jakie ekwiwalenty będą używane w pilocie?
   - Jak uczestnicy będą się rejestrować (invite-only, otwarte)?
   - Czy potrzebna integracja z istniejącymi systemami (księgowość, CRM)?

---

## Podsumowanie

### Co już jest dobre

Projekt GEO v0.1 wykazuje dojrzałe i pragmatyczne podejście:
- Jasno określona dziedzina
- Wybrana zrozumiała architektura MVP
- Protokół opiera się na sprawdzonych wzorcach (2PC, BFS)
- Założona możliwość ewolucji

### Główne ryzyka

1. **Single point of failure** — Hub jako jedyny punkt koordynacji
2. **Brak testów** — krytyczne dla stabilności
3. **Wiele dokumentów** — spowalnia wdrażanie

### Priorytety na najbliższy czas

1. **Konsolidować dokumentację** (1 tydzień)
2. **Utworzyć szkielet backend-u** (2–3 tygodnie)
3. **Pokryć testami krytyczne ścieżki** (równolegle)
4. **Uruchomić pilot** (za 2–3 miesiące)

### Czego NIE robić

- Nie budować protokołu p2p przed udanym pilotem z Hub-em
- Nie wdrażać złożonych algorytmów (max-flow, Lamport) w MVP
- Nie skupiać się na „globalnej sieci" przed lokalnym sukcesem

---

*Raport przygotowany na podstawie analizy dokumentacji w `docs/`, materiałów źródłowych w `sources/` oraz badania oryginalnego GEO Protocol na GitHub.*