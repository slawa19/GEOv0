## GEO v0.1 — podstawowy protokół sieci kredytowej dla lokalnych społeczności i klastrów

*Dokument dostosowany na podstawie idei oryginalnego GEO Protocol, ale uproszczony do poziomu realnego MVP opartego na architekturze community‑hub.*

---

## 0. Przeznaczenie protokołu

**GEO v0.1** to protokół:

- p2p‑gospodarki wzajemnego kredytu:
  - między pojedynczymi osobami i organizacjami;
  - między społecznościami (ich huby występują jako zwykli uczestnicy);
- bez jednej, natywnej waluty i bez globalnego ledger’a:
  - w obiegu są tylko **zobowiązania** (długi) pomiędzy uczestnikami;
  - oraz **linie zaufania** (limity ryzyka kredytowego);
- oparty na:
  - prostych i sprawdzonych algorytmach (BFS, 2PC, wyszukiwanie krótkich cykli);
  - czytelnym modelu wiadomości i stanów transakcji;
  - możliwości ewolucji do pełnego p2p i klastrowych hubów.

---

## 1. Model bytów protokołu

### 1.1. Uczestnik (Participant)

**Participant ID (PID)**:

- tożsamość kryptograficzna uczestnika:
  - para kluczy Ed25519 (`public_key`, `secret_key`);
  - `PID = base58(sha256(public_key))` (szczegóły formatu definiuje implementacja).
- wszystkie byty i operacje odnoszą się do uczestników przez `PID`.

**Rola logiczna:**

- osoba fizyczna;
- firma/organizacja;
- community‑hub (serwer społeczności, który jest też Participantem).

**Zasada lokalności:**

- linia zaufania `A → B` jest **własnością A**;
- długi `X→Y` są częścią kryptograficznie potwierdzonego stanu X i Y (podpisy na transakcjach).

---

### 1.2. Ekwiwalent (Equivalent)

Jednostka rozliczeniowa:

- `code`: np. `"UAH"`, `"HOUR_DEV"`, `"kWh"`;
- `precision`: liczba miejsc po przecinku;
- `metadata`: opis (rodzaj ekwiwalentu: fiat, czas, zasób, jednostka lokalna itd.).

Wszystkie trustlines i długi **zawsze** wskazują ekwiwalent. Nie ma „waluty domyślnej”.

---

### 1.3. Linia zaufania (TrustLine)

Linia zaufania (TL):

- `from`: `PID_from` — kto **udziela zaufania**;
- `to`: `PID_to` — komu ufa;
- `equivalent`: `E`;
- `limit`: maksymalna kwota, na jaką `to` może być winien `from` w `E`;
- `policy` (opcjonalnie):
  - auto‑zgoda na clearing;
  - czy może występować jako pośrednik w routingu;
  - limity dziennego obrotu itp.

Semantyka:

> „A ufa B do limitu L w ekwiwalencie E, tzn. B może być winien A maksymalnie L w E”.

**Niezmiennik limitu:**

\[
debt[B \to A, E] \leq limit(A \to B, E)
\]

czyli aktualny dług B wobec A w `E` **nie może** przekroczyć limitu zaufania A→B.

Operacje na trustline (`CREATE/UPDATE/CLOSE`) są transakcjami `TRUST_LINE_*` podpisanymi przez `from`.

---

### 1.4. Zobowiązanie (Debt / Obligation)

Skierowana krawędź długu:

- `debtor`: PID_X;
- `creditor`: PID_Y;
- `equivalent`: E;
- `amount`: S > 0.

Semantyka:

> „X jest winien Y kwotę S w ekwiwalencie E”.

Dla każdej trójki `(X,Y,E)` dopuszcza się jedno zbiorcze zobowiązanie `debt[X→Y,E]`. Historia pojedynczych transakcji to poziom aplikacji, nie rdzenia protokołu.

Powiązanie z TrustLine:

- jeśli istnieje linia zaufania `A→B (limit=L,E)`, to:

\[
debt[B \to A, E] \leq L
\]

czyli dług B wobec A w E nie może przekroczyć limitu przyznanego przez A.

---

### 1.5. Transakcja (Transaction)

Każda istotna zmiana stanu (`trust_lines`, `debts`) dokonuje się przez transakcję.

Ogólne pola:

- `tx_id`: globalny identyfikator (UUID lub hash payload’u);
- `type`:
  - `TRUST_LINE_CREATE | TRUST_LINE_UPDATE | TRUST_LINE_CLOSE`;
  - `PAYMENT`;
  - `CLEARING`;
- `initiator`: PID inicjatora;
- `payload`: obiekt zależny od typu;
- `signatures`: podpisy uczestników (rola → podpis);
- `timestamp`: czas inicjacji.

---

### 1.6. Stany transakcji (state machine)

Aby było jasno i spójnie z p2p, każda klasa transakcji ma prostą maszynę stanów.

**PAYMENT:**

- `NEW` — żądanie płatności zostało utworzone;
- `ROUTED` — znaleziono trasę/trasy;
- `PREPARE_IN_PROGRESS` — w trakcie fazy PREPARE (rezerwacje, walidacje);
- `COMMITTED` — wszystkie węzły zastosowały zmiany;
- `ABORTED` — płatność odrzucona/zawinięta.

**CLEARING:**

- `NEW` — kandydat cyklu został znaleziony;
- `PROPOSED` — (opcjonalnie) propozycja clearingu rozesłana do uczestników;
- `WAITING_CONFIRMATIONS` — oczekiwanie na ich `ACCEPT/REJECT`;
- `COMMITTED` — clearing zastosowany;
- `REJECTED` — przynajmniej jeden uczestnik odrzucił.

**TRUST_LINE_*:**

- `PENDING` — w trakcie sprawdzania / zatwierdzania;
- `COMMITTED` — zmiany zastosowane;
- `FAILED` — odmowa (błąd, brak podpisu, naruszenie reguł).

---

## 2. Operacje protokołu

### 2.1. Operacje na liniach zaufania

#### 2.1.1. TRUST_LINE_CREATE / UPDATE

Wiadomość (logiczny format protokołu):

```json
{
  "msg_type": "TRUST_LINE_CREATE",
  "tx_id": "…",
  "from": "PID_A",
  "payload": {
    "from": "PID_A",
    "to": "PID_B",
    "equivalent": "UAH",
    "limit": 1000.0,
    "policy": { }
  },
  "signature": "sig_A"
}
```

Koordynator (hub lub węzeł):

1. Weryfikuje podpis A.
2. Sprawdza polityki / reguły społeczności.
3. Tworzy/aktualizuje `TrustLine(A→B,E,L)`.
4. Zapisuje transakcję `TRUST_LINE_CREATE/UPDATE` jako `COMMITTED`.

#### 2.1.2. TRUST_LINE_CLOSE

Podobnie jak UPDATE, ale:

- może wymagać braku długu `debt[B→A,E]` (lub zdefiniowanej procedury wygaszania długu).

---

### 2.2. Płatność (PAYMENT)

#### 2.2.1. PAYMENT_REQUEST (klient → koordynator)

Żądanie z poziomu aplikacji (np. HTTP/WS):

```json
{
  "msg_type": "PAYMENT_REQUEST",
  "from": "PID_A",
  "payload": {
    "to": "PID_B",
    "equivalent": "UAH",
    "amount": 60.0,
    "constraints": {
      "max_hops": 4
    }
  },
  "signature": "sig_A"
}
```

Koordynator:

- waliduje żądanie;
- tworzy transakcję `PAYMENT` (`state=NEW`);
- przekazuje do RoutingService.

#### 2.2.2. Routing (wyszukiwanie ścieżek)

Dla każdej krawędzi `A→B` i ekwiwalentu `E` definiujemy:

\[
available\_credit(A \to B, E) = limit(A \to B, E) - debt[B \to A, E]
\]

Krawędź jest użyteczna, jeśli `available_credit > 0`.

**Algorytm light multi‑path:**

1. Szukamy pierwszej ścieżki `P1` z A do B (BFS, max_hops).
2. Liczymy `c1 = min(available_credit(e))` po krawędziach `P1`.
3. Jeśli `c1 ≥ S` (kwota płatności) — wystarczy jedna ścieżka.
4. Jeśli `c1 < S`:
   - tymczasowo zmniejszamy `available_credit` na krawędziach `P1` o `c1`;
   - szukamy `P2` (alternatywna ścieżka, nieużywająca „wyczerpanych” krawędzi);
   - liczymy `c2`.
   - jeżeli `c1 + c2 ≥ S`, dzielimy płatność na `amount_1` i `amount_2`.

RoutingService zwraca:

```json
"routes": [
  {
    "path": ["A", "X1", "B"],
    "amount": 40.0
  },
  {
    "path": ["A", "Y1", "Y2", "B"],
    "amount": 20.0
  }
]
```

Transakcja `PAYMENT` przechodzi do `ROUTED`.

#### 2.2.3. Struktura transakcji PAYMENT

```json
{
  "tx_id": "…",
  "type": "PAYMENT",
  "initiator": "PID_A",
  "payload": {
    "from": "PID_A",
    "to": "PID_B",
    "equivalent": "UAH",
    "total_amount": 60.0,
    "routes": [
      { "path": ["A","X1","B"], "amount": 40.0 },
      { "path": ["A","Y1","Y2","B"], "amount": 20.0 }
    ]
  },
  "signatures": {
    "from": "sig_A"
  }
}
```

#### 2.2.4. Wykonanie (2PC – PaymentEngine)

**Faza 1. PREPARE**

Koordynator tworzy dla każdego uczestnika X lokalną listę zmian (`local_effects`):

```json
{
  "debtor": "PID_X",
  "creditor": "PID_Y",
  "equivalent": "UAH",
  "delta": +10.0
}
```

Następnie wysyła do X wiadomość protokołu:

```json
{
  "msg_type": "PAYMENT_PREPARE",
  "tx_id": "…",
  "from": "COORD_PID",
  "to": "PID_X",
  "payload": {
    "equivalent": "UAH",
    "local_effects": [ ... ]
  },
  "signature": "sig_COORD"
}
```

Węzeł X:

1. Sprawdza, że po zastosowaniu `delta`:

   - żaden `debt[...]` nie przekroczy limitów trustline;
   - nie złamane zostaną polityki lokalne (np. whitelist/blacklist).

2. Jeśli wszystko ok:

   - rezerwuje zasoby (np. w pamięci/Redis);
   - odpowiada `PAYMENT_PREPARE_ACK` z `status="OK"`.

3. Jeśli nie:

   - odpowiada z `status="FAIL"` (i powodem).

Koordynator:

- jeśli wszystkie odpowiedzi `OK` → `PREPARE_IN_PROGRESS` i przechodzi do COMMIT;
- jeśli przynajmniej jedna `FAIL` → usuwa rezerwy i przechodzi do `ABORTED`.

**Faza 2. COMMIT / ABORT**

Przy sukcesie PREPARE:

- koordynator wysyła `PAYMENT_COMMIT` do wszystkich uczestników;
- każdy uczestnik:
  - aplikuje `local_effects` do swoich `debts`;
  - usuwa rezerwy;
  - zapisuje stan transakcji jako `COMMITTED`.

Przy niepowodzeniu:

- wysyła `PAYMENT_ABORT`;
- uczestnicy usuwają rezerwy bez zmian w `debts`.

**Idempotentność:**

- ponowne `COMMIT` dla tego samego `tx_id` nie może zmienić stanu (węzeł pamięta, że transakcja już `COMMITTED`).

---

### 2.3. Clearing (CLEARING)

#### 2.3.1. Wyszukiwanie cykli

ClearingEngine:

- po każdej `PAYMENT.COMMITTED` analizuje zmienione krawędzie;
- buduje lokalny podgraf (promień 2–3 od tych krawędzi);
- szuka cykli:

  - 3‑węzłowych: `A → B → C → A`,
  - 4‑węzłowych: `A → B → C → D → A`,

  z `debt[...] > 0` w ekwiwalencie E.

Dla cyklu C = `(V0, V1, ..., V(k-1), V0)`:

\[
S = \min(debt[V_i \to V_{i+1}])
\]

Jeśli `S > ε` (np. 0.01), kandydat nadaje się do clearingu.

#### 2.3.2. Struktura transakcji CLEARING

```json
{
  "tx_id": "…",
  "type": "CLEARING",
  "initiator": "COORD_PID",
  "payload": {
    "equivalent": "UAH",
    "cycle": ["A","B","C","A"],
    "amount": 15.0
  },
  "signatures": {
    "initiator": "sig_COORD"
  }
}
```

Efekt:

- dla każdej krawędzi `Vi → V(i+1)`:

  \[
  debt[Vi \to V(i+1),E] \mathrel{-=} S
  \]

#### 2.3.3. Zgoda uczestników

Dwa tryby:

1. **Auto‑zgoda** (domyślnie, jeśli polityka trustlines zezwala):

   - zmniejszenie długu jest postrzegane jako „gołym okiem korzystne”;
   - koordynator może pominąć osobne `PROPOSE/ACCEPT` i od razu wykonać 2PC nad `debts`.

2. **Jawne potwierdzenie:**

   - `CLEARING_PROPOSE` → uczestnicy;
   - `CLEARING_ACCEPT` / `CLEARING_REJECT` → koordynator;
   - przy pełnej zgodzie → `NEW → PROPOSED → WAITING_CONFIRMATIONS → COMMITTED`;
   - przy braku zgody / timeout → `REJECTED`.

W obu przypadkach aktualizacja długów jest wykonywana podobnie jak w `PAYMENT` (2PC), ale z `delta` ujemną.

---

## 3. Wymiana między społecznościami (hub‑to‑hub)

Każdy community‑hub:

- ma własny `PID`;
- może otwierać trustlines z innymi hubami:

  - `Hub1 → Hub2 (limit=L12, E)`,
  - `Hub2 → Hub1 (limit=L21, E)`.

Płatność `A@Hub1 → B@Hub2`:

- logiczna trasa:

  - `A → ... → Hub1 → Hub2 → ... → B`.

Koordynator:

- w MVP typowo Hub1 (gdzie jest `from`);
- w przyszłości możliwe inne schematy (np. współkoordynacja hubów).

Protokół pozostaje ten sam:

- `PAYMENT_REQUEST` z dodatkowymi krokami przez huby;
- 2PC obejmuje wszystkie węzły, w tym huby;
- długi między hubami (Debts) można później redukować przez `CLEARING` lub netting.

Nie jest potrzebny oddzielny, zupełnie inny protokół między‑hubowy — w GEO v0.1 wszystko jest tymi samymi bytami.

---

## 4. Wiadomości protokołu i transport

### 4.1. Typy wiadomości

- **TrustLines:**
  - `TRUST_LINE_CREATE`, `TRUST_LINE_UPDATE`, `TRUST_LINE_CLOSE`.
- **Płatności:**
  - `PAYMENT_REQUEST` (klient → koordynator),
  - `PAYMENT_PREPARE`, `PAYMENT_PREPARE_ACK`,
  - `PAYMENT_COMMIT`, `PAYMENT_ABORT`.
- **Clearing:**
  - `CLEARING_PROPOSE`, `CLEARING_ACCEPT`, `CLEARING_REJECT`,
  - `CLEARING_PREPARE`, `CLEARING_COMMIT`, `CLEARING_ABORT`.

Ogólny format wiadomości:

```json
{
  "msg_id": "UUID",
  "msg_type": "STRING",
  "tx_id": "UUID or null",
  "from": "PID",
  "to": "PID or HUB or null",
  "payload": { },
  "signature": "BASE64(ed25519_signature)"
}
```

### 4.2. Transport

- **Wewnątrz community‑hub:**
  - wiadomości mogą być zrealizowane jako wywołania serwisów + zapisy w DB (REST/WS dla klientów).
- **Pomiędzy hubami / przyszłe p2p:**
  - te same typy wiadomości mogą iść:
    - po WebSocket,
    - po gRPC,
    - po innych kanałach sieciowych.

Kluczowe założenie:

> Format i semantyka wiadomości są niezależne od transportu. To pozwala dodać p2p bez zmiany „języka protokołu”.

---

## 5. Idempotentność i obsługa błędów

- `tx_id` jest unikalny w domenie koordynatora:

  - ponowne `COMMIT` / `ABORT` z tym samym `tx_id` nie zmienia stanu, jeśli transakcja jest już w stanie końcowym.

Rekomendacja:

- każdy węzeł (w tym hub) trzyma lokalną tabelę `transactions_local_state`:

  - `tx_id`, `state`, `last_update`.

Przy problemach sieciowych:

- koordynator może ponownie wysłać `PREPARE`/`COMMIT`;
- uczestnik, widząc już znany `tx_id`, odpowiada zgodnie ze swoim stanem (`PREPARED/COMMITTED/ABORTED`).

---

## 6. Rozszerzalność protokołu v0.1

Protokół jest celowo minimalny, ale:

- pozwala uszczegółowić:

  - routing (k‑najkrótszych ścieżek, max‑flow w podgrafie),
  - zaawansowane polityki trustlines (np. dynamiczne limity, scoring ryzyka),
  - tryb p2p pełnych węzłów (bez pośrednictwa huba).

- przygotowuje grunt pod:

  - klastry hubów (Raft/Tendermint nad logiem `transactions`);
  - integracje z blockchainami (zakotwiczenie stanu, arbitraż on‑chain).

Dzięki temu GEO v0.1:

- jest wystarczająco prosty, by zbudować **realny MVP** (community‑hub + klienci),
- a jednocześnie wystarczająco formalny, by stać się fundamentem pełnego, rozproszonego GEO Protocol w kolejnych wersjach.
