# Architektura MVP GEO dla lokalnej społeczności

**Wariant B, rewizja v0.1: community‑hub + lekkie klienty + protokół GEO v0.1**

Dokument opisuje architekturę minimalnie żywotnego produktu (MVP) systemu GEO dla **jednej lokalnej społeczności** z możliwością ewolucji:

- do **klastra węzłów** wewnątrz społeczności;
- do **rozliczeń między wieloma społecznościami** (cluster‑to‑cluster);
- do częściowego **trybu p2p** między „grubymi” klientami.

Architektura jest zsynchronizowana ze **specyfikacją protokołu GEO v0.1**: trustlines, długi, płatności i clearing są realizowane jako formalne transakcje i maszyny stanów, a community‑hub pełni w MVP rolę koordynatora.

---

## 1. Cele i zakres MVP

### 1.1. Cele

- Zaimplementować **gospodarkę wzajemnego kredytu** wewnątrz jednej lokalnej społeczności (10–500 uczestników):

  - linie zaufania (TrustLines) pomiędzy uczestnikami;
  - jawne krawędzie długu (Debts/Obligations);
  - operacje `PAYMENT` po sieci zaufania;
  - automatyczne wyszukiwanie i wykonanie prostych cykli clearingu (`CLEARING`) długości 3–4.

- Zapewnić **używalny UX** dla nietechnicznej społeczności:

  - klient webowy (przeglądarka desktop/mobile, PWA);
  - możliwość łatwego zbudowania aplikacji mobilnej.

- Zachować **otwartość strukturalną**:

  - w przyszłości — konfiguracje multi‑hub (kilka społeczności, trustlines między hubami);
  - częściowa decentralizacja (własne nody dużych uczestników, komunikacja p2p);
  - potencjalna integracja z blockchainami i bramkami płatniczymi.

### 1.2. Ograniczenia i założenia MVP

- **Jeden community‑hub** na społeczność.
- Brak globalnego blockchaina/ledger’a; jest:

  - lokalna baza danych huba jako źródło prawdy dla danej społeczności;
  - kryptograficzne podpisy uczestników na krytycznych operacjach (trustlines, clearing, stopniowo płatności).

- Prosty, sprawdzony protokół:

  - routing — BFS z lekkim multi‑path;
  - spójność wzdłuż ścieżki — dwufazowy commit (2PC);
  - clearing — wyszukiwanie krótkich cykli 3–4 w lokalnym podgrafie.

- Wymiana między społecznościami:

  - **na poziomie protokołu**: huby są zwykłymi uczestnikami z PID i trustlines;
  - implementacja transportu między hubami (Hub‑to‑Hub) może zostać dodana później bez zmiany podstawowych bytów.

---

## 2. Ogólny przegląd architektury

### 2.1. Widok wysokopoziomowy

```text
+-----------------------------+
|        Uczestnicy          |
|  - Klient mobilny / web    |
|  - Admin / narzędzia dev   |
+--------------+--------------+
               |
               | HTTPS / WebSocket (JSON)
               v
+-----------------------------+
|     API Gateway / BFF       |
+--------------+--------------+
               |
               v
+-----------------------------+
|      Community Hub Core     |
|  - Auth & Identity          |
|  - TrustLines Service       |
|  - RoutingService           |
|  - PaymentEngine (2PC)      |
|  - ClearingEngine (cykle)   |
|  - Reporting & Analytics    |
|  - System dodatków          |
+--------------+--------------+
               |
               v
+-----------------------------+
|         Data Layer          |
|  - PostgreSQL (ACID)        |
|  - Redis (cache, locki)     |
+-----------------------------+

+-----------------------------+
|  Crypto / Key Management    |
|  - Klucze po stronie klienta|
|  - Podpisy operacji         |
+-----------------------------+
```

Hub:

- koordynuje transakcje `PAYMENT` i `CLEARING`;
- przechowuje i indeksuje stan sieci zaufania i długów w ramach społeczności;
- udostępnia spójne API (HTTP/WebSocket) dla klientów i dodatków;
- sam jest uczestnikiem (Participant) na poziomie protokołu (ma PID, trustlines).

Kluczowe założenie:

> Hub nie **posiada** środków uczestników ani nie „wydaje wyroków”, tylko egzekwuje reguły protokołu GEO (limity, podpisy, clearing cykli).

---

## 3. Słowniczek bytów protokołu (powiązanie z implementacją)

Zgodnie z dokumentem **GEO v0.1 – basic credit network protocol**:

- **Participant**: w implementacji odpowiada rekordowi w tabeli `participants` (PID, klucze, profil).
- **Equivalent**: rekord w tabeli `equivalents` (kod, precyzja, metadane).
- **TrustLine**: rekord w `trust_lines` (from, to, equivalent, limit, policy, status).
- **Debt**: rekord w `debts` (debtor, creditor, equivalent, amount).
- **Transaction**: rekord w `transactions` (`type`, `state`, `payload`, `signatures`).

Stan logiczny protokołu to:

- zestaw uczestników z ich kluczami;
- zestaw trustlines i długów;
- historia transakcji (do audytu i odtwarzania zmian).

---

## 4. Community‑hub Core — moduły

### 4.1. Auth & Identity

- Rejestracja:

  - przyjmuje `public_key`, dane profilu;
  - tworzy `Participant` z PID;
  - powiązuje z kontem logowania (email/telefon, hasło/OTP).

- Logowanie:

  - email/telefon + hasło lub OTP;
  - zwrot JWT (access + refresh).

- Sesje:

  - przechowywanie w Redis lub DB;
  - integracja z WebSocket (mapowanie token → uczestnik).

### 4.2. TrustLines Service

- Operacje:

  - `TRUST_LINE_CREATE`, `TRUST_LINE_UPDATE`, `TRUST_LINE_CLOSE`:

    - weryfikacja podpisu `from`;
    - walidacja biznesowa (limity, status uczestników, polityki społeczności);
    - modyfikacja tabeli `trust_lines`;
    - zapis transakcji w `transactions`.

- Walidacja przy płatnościach:

  - udostępnianie `available_credit` dla RoutingService i PaymentEngine:

    \[
    available\_credit(A \to B, E) = limit(A \to B, E) - debt[B \to A, E]
    \]

### 4.3. RoutingService (wyszukiwanie ścieżek)

Zadania:

- dla `PAYMENT_REQUEST (A → B, E, S)`:

  - buduje graf skierowany po `trust_lines` i `debts`;
  - wyznacza `available_credit` dla każdej krawędzi;
  - szuka 1–3 ścieżek BFS‑em (limit długości, filtr po politykach);
  - stosuje prosty multi‑path (maks. 2–3 ścieżki) zgodnie z GEO v0.1.

Wyjście:

```json
"routes": [
  { "path": ["A","X1","B"], "amount": 40.0 },
  { "path": ["A","Y1","Y2","B"], "amount": 20.0 }
]
```

### 4.4. PaymentEngine (wykonanie płatności, 2PC)

Fazy stanu:

- `NEW` → `ROUTED` → `PREPARE_IN_PROGRESS` → (`COMMITTED` | `ABORTED`).

Etapy:

1. **Utworzenie `PAYMENT`**:

   - na bazie `PAYMENT_REQUEST`;
   - zapis do `transactions (state=NEW)`;
   - RoutingService → `routes[]` → `state=ROUTED`.

2. **Faza PREPARE**:

   - dla każdego uczestnika na trasach:

     - oblicza lokalne efekty (`delta` na `debt[...]`);
     - sprawdza limity i polityki;
     - rezerwuje zasoby (Redis / tymczasowe rekordy w DB).

   - jeśli wszystko ok → `PREPARE_IN_PROGRESS`;
   - jeśli którakolwiek krawędź odrzucona → `ABORTED`.

3. **Faza COMMIT / ABORT**:

   - przy sukcesie:

     - w transakcji DB aktualizuje `debts`;
     - usuwa rezerwy;
     - `state=COMMITTED`.

   - przy niepowodzeniu:

     - czyści rezerwy;
     - `state=ABORTED`.

W MVP wszystkie działania PREPARE/COMMIT mogą być „wewnętrzne” w hubie; protokół wiadomości z GEO v0.1 (`PAYMENT_PREPARE`, `PAYMENT_PREPARE_ACK`, `PAYMENT_COMMIT/ABORT`) jest zachowany semantycznie, co umożliwi p2p/hub‑to‑hub później.

### 4.5. ClearingEngine (wyszukiwanie i realizacja cykli)

Zgodnie z GEO v0.1:

- stany `CLEARING`: `NEW → [PROPOSED → WAITING_CONFIRMATIONS →] COMMITTED | REJECTED`.

Zadania:

1. **Lokalnie‑podwyzwalany wyszukiwacz cykli**:

   - po każdej `PAYMENT.COMMITTED` bierze krawędzie, które się zmieniły;
   - buduje mały podgraf (promień 2–3);
   - szuka cykli długości 3–4 po `debts[X→Y,E] > 0`.

2. **Wyznaczenie kwoty clearingu**:

   \[
   S = \min(debt[V_i \to V_{i+1}], i = 0..k-1)
   \]

3. **Transakcja `CLEARING`**:

   - `payload`:

     ```json
     {
       "equivalent": "E",
       "cycle": ["A","B","C","A"],
       "amount": S
     }
     ```

   - w trybie auto‑zgody:

     - od razu przechodzi do 2PC nad `debts` (PREPARE/COMMIT);

   - w trybie ręcznej zgody:

     - wysyła `CLEARING_PROPOSE` do uczestników;
     - zbiera `ACCEPT/REJECT`;
     - przy pełnej zgodzie – COMMIT, w przeciwnym wypadku REJECT.

---

## 5. Data Layer: szczegóły

### 5.1. PostgreSQL — główne tabele

```sql
CREATE TABLE participants (
  id UUID PRIMARY KEY,
  public_key BYTEA NOT NULL,
  display_name TEXT,
  profile JSONB,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE equivalents (
  id UUID PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  precision INT NOT NULL,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE trust_lines (
  id UUID PRIMARY KEY,
  from_participant_id UUID REFERENCES participants(id),
  to_participant_id UUID REFERENCES participants(id),
  equivalent_id UUID REFERENCES equivalents(id),
  limit NUMERIC NOT NULL,
  policy JSONB,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (from_participant_id, to_participant_id, equivalent_id)
);

CREATE TABLE debts (
  id UUID PRIMARY KEY,
  debtor_id UUID REFERENCES participants(id),
  creditor_id UUID REFERENCES participants(id),
  equivalent_id UUID REFERENCES equivalents(id),
  amount NUMERIC NOT NULL CHECK (amount > 0),
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (debtor_id, creditor_id, equivalent_id)
);

CREATE TABLE transactions (
  id UUID PRIMARY KEY,
  type TEXT NOT NULL,
  initiator_id UUID REFERENCES participants(id),
  payload JSONB NOT NULL,
  state TEXT NOT NULL,
  signatures JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
```

Opcjonalnie:

```sql
CREATE TABLE transaction_participants (
  transaction_id UUID REFERENCES transactions(id),
  participant_id UUID REFERENCES participants(id),
  role TEXT,
  signature BYTEA,
  PRIMARY KEY (transaction_id, participant_id, role)
);
```

### 5.2. Redis

Zastosowania:

- sesje (Auth, WebSocket);
- `prepare_locks`:

  - np. klucz `prepare:{tx_id}` → mapa `edge_key → reserved_amount`;
  - `edge_key` = `(debtor,creditor,equivalent)`.

- kolejki / harmonogram zadań dla ClearingEngine.

---

## 6. Klienty i UX (z rewizją v0.1)

### 6.1. Klient web (SPA/PWA)

Główne widoki:

1. **Dashboard**:

   - bilans netto;
   - przychodzące/wychodzące trustlines;
   - wskaźniki (ile jestem winien, ile są winni mnie).

2. **TrustLines**:

   - lista linii, które daję innym i które inni dają mnie;
   - formularz tworzenia/edycji:
     - wybór uczestnika, ekwiwalent, limit, polityka;
   - podpis operacji.

3. **Płatności**:

   - formularz „Zapłać”:
     - odbiorca, kwota, ekwiwalent;
   - podgląd wyniku (‘ok / błąd’, suma, ewentualnie uproszczona ścieżka).

4. **Clearing**:

   - powiadomienia o proponowanych clearingach;
   - panel zgody / szczegóły cyklu.

5. **Historia**:

   - lista transakcji (`TRUST_LINE_*`, `PAYMENT`, `CLEARING`);
   - filtry, szczegóły.

### 6.2. Zarządzanie kluczami

- Przy pierwszym użyciu:

  - generacja pary Ed25519 (WebCrypto / biblioteka JS);
  - zapis w zabezpieczonym storage (IndexedDB).

- Funkcje:

  - eksport seed/klucza prywatnego (do backupu);
  - import przy zmianie urządzenia.

- Podpisy:

  - operacje krytyczne (`TRUST_LINE_*`, `CLEARING` i docelowo `PAYMENT`) podpisywane lokalnie;
  - serwer weryfikuje podpisy wg przechowywanego `public_key`.

---

## 7. Bezpieczeństwo, prywatność, odporność — doprecyzowanie

Zgodnie z docelową specyfikacją GEO:

- **Bezpieczeństwo**:

  - 2PC i idempotentność po `tx_id` eliminują podwójne wydatkowanie w ramach lokalnego huba;
  - ewentualne przejście do p2p/Hub‑to‑Hub zachowuje tę samą semantykę transakcji (jest tylko inny transport).

- **Prywatność**:

  - w MVP hub widzi pełną historię, ale warstwa protokołu nie wymaga globalnego ledgera;
  - możliwe jest późniejsze przeniesienie części treści transakcji do kanałów szyfrowanych end‑to‑end.

- **Odporność**:

  - utrata huba = przerwa operacyjna społeczności, ale:
    - stan jest w spójnej bazie ACID z backupami,
    - w przyszłości — klaster z replikacją / konsensusem (architektura C).

---

## 8. Rozszerzalność i dodatki

Community‑hub posiada wewnętrzny event‑bus (np. na bazie Python / FastAPI + system zdarzeń):

- zdarzenia typu:

  - `TRUST_LINE_COMMITTED`;
  - `PAYMENT_COMMITTED`;
  - `CLEARING_COMMITTED`.

Dodatki (plugins):

- mogą subskrybować zdarzenia i:

  - budować zaawansowane raporty;
  - wysyłać powiadomienia do zewnętrznych systemów (mail, komunikatory);
  - integrować się z księgowością / CRM.

To pozwala rozwijać ekosystem bez modyfikowania jądra protokołu.

---

## 9. Podsumowanie rewizji v0.1

W porównaniu z pierwszym szkicem architektury B, rewizja v0.1:

- **ściśle powiązuje** implementację z protokołem GEO v0.1:
  - jawne byty: Participant, TrustLine, Debt, Transaction;
  - proste maszyny stanów dla `PAYMENT` i `CLEARING`;
  - mechanizm 2PC;
- **upraszcza** wymagania dla MVP:

  - BFS + multi‑path „light” zamiast od razu max‑flow;
  - clearing tylko dla krótkich cykli 3–4;
  - pojedynczy hub zamiast klastra;

- **zostawia ścieżkę rozwoju**:

  - klastry hubów z konsensusem nad dziennikiem transakcji;
  - federacja społeczności przez trustlines między hubami;
  - grubi klienci (własne nody) i częściowy tryb p2p;
  - integracja z blockchainami (kotwiczenie stanu, bramki fiat/krypto).

W efekcie architektura jest:

- dość prosta, by zrealizować **realny MVP** dla 1 społeczności w rozsądnym czasie;
- wystarczająco czytelna i formalna, by w przyszłości ewoluować w stronę pełnego, rozproszonego GEO Protocol.
