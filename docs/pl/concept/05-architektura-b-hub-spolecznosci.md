# Architektura MVP GEO dla lokalnej społeczności  
**Wariant B: Community‑hub + lekkie klienty**

Dokument opisuje architekturę minimalnie żywotnego produktu (MVP) systemu GEO dla **jednej lokalnej społeczności** z możliwością ewolucji:

- do **klastra węzłów** wewnątrz społeczności;
- do **rozliczeń między wieloma społecznościami** (cluster‑to‑cluster).

Celem jest podanie na tyle szczegółowego opisu komponentów, danych, protokołów i rekomendowanego stosu, aby na tej podstawie można było zaplanować i zaimplementować działający prototyp.

---

## 1. Cele i zakres MVP

### 1.1. Cele

- Zaimplementować **gospodarkę wzajemnego kredytu** wewnątrz jednej lokalnej społeczności (10–500 uczestników):
  - linie zaufania pomiędzy uczestnikami;
  - operacje „kupione na kredyt”;
  - automatyczne wyszukiwanie i uruchamianie prostych cykli clearingu (3–4 węzły, bazowo).
- Zapewnić **wygodny UX**:
  - klient webowy (desktop/mobile browser);
  - w miarę możliwości — aplikację mobilną korzystającą z tego samego API.
- Zapewnić **wystarczającą otwartość architektury**:
  - pod późniejsze przejście do:
    - konfiguracji multi‑hub (kilka społeczności);
    - częściowej decentralizacji (własne nody dużych uczestników, klastry hubów).

### 1.2. Ograniczenia i założenia MVP

- Jeden **community‑hub** na społeczność (na razie bez klastra).
- Brak globalnego blockchaina/ledger’a; istnieje:
  - lokalna baza danych huba;
  - podpisy uczestników na kluczowych zapisach (aby w przyszłości umożliwić migrację).
- Uproszczony protokół:

  - transakcje przechodzą przez hub, który:
    - dobiera trasy;
    - koordynuje potwierdzenia;
    - przechowuje log operacji;
  - jednocześnie uczestnicy **podpisują** krytyczne zmiany (zaufanie, płatności), tak aby dane nie zależały całkowicie od zaufania do administratora huba.

- Wymiana między społecznościami — **poza MVP**, ale architektura przewiduje punkty rozszerzeń.

---

## 2. Ogólny przegląd architektury

### 2.1. Schemat wysokopoziomowy

Logicznie system wygląda tak:

```text
+---------------------------+
|     Użytkownicy (UX)     |
|  - Klient web (SPA/PWA)  |
|  - Mobile (opcjonalnie)  |
+-------------+-------------+
              |
              | HTTPS / WebSocket (JSON/REST)
              v
+---------------------------+
|     API Gateway / BFF     |
+-------------+-------------+
              |
              v
+---------------------------+
|     Community Hub Core    |
|  - Auth & Identity        |
|  - Trust Lines Service    |
|  - Payments Engine        |
|  - Routing (Pathfinding)  |
|  - Clearing Engine        |
|  - Reporting & Metrics    |
+-------------+-------------+
              |
              v
+---------------------------+
|       Data Layer          |
|  - PostgreSQL (główna)    |
|  - Redis (cache, sesje)   |
+---------------------------+

+---------------------------+
| Crypto / Key Management   |
|  - Przechowywanie kluczy  |
|    po stronie klienta     |
|  - Podpisywanie operacji  |
+---------------------------+
```

Ważna zasada:

- **Hub nie powinien być „bankiem”**, który podejmuje decyzje za ludzi;
- jest:

  - węzłem obliczeniowo‑komunikacyjnym;
  - indekserem danych;
  - serwisem koordynującym wykonanie protokołu GEO.

---

## 3. Główne komponenty

### 3.1. Backend Community‑hub

Funkcje:

- Zarządzanie uczestnikami:
  - rejestracja/zaproszenia;
  - profile uczestników;
  - status (aktywny/zamrożony/opuszczony).
- Uwierzytelnianie i sesje:
  - logowanie przez email/telefon + hasło/OTP;
  - powiązanie z kryptograficznymi kluczami uczestnika.
- Zarządzanie liniami zaufania:
  - tworzenie/zmiana/zamykanie;
  - przechowywanie limitów, bieżących sald, statusu.
- Silnik płatności:
  - przyjmowanie żądań płatności;
  - dobór tras;
  - wykonanie protokołu `prepare/commit`;
  - weryfikacja podpisów.
- Silnik clearingu:
  - wyszukiwanie cykli (3–4 węzły w MVP);
  - formowanie transakcji clearingu;
  - koordynacja potwierdzeń.
- Raportowanie:
  - dane zagregowane o uczestnikach;
  - bilanse, obroty;
  - analityka operacyjna.

### 3.2. API Gateway / BFF (Backend For Frontend)

- HTTP(S) REST + WebSocket (lub SSE) dla:

  - operacji w czasie rzeczywistym (płatności, powiadomienia);
  - aktualizacji UI bez ciągłego pollingu.

- BFF może być zaimplementowany w tym samym procesie, co core (na etapie MVP).

### 3.3. Data Layer

**Główna baza danych: PostgreSQL**

- Tabele:

  - `participants` — uczestnicy;
  - `equivalents` — ekwiwalenty (UAH, kWh, „godzina pracy”, …);
  - `trust_lines` — linie zaufania;
  - `obligations` lub `edges` — bieżące zobowiązania/długi pomiędzy parami uczestników;
  - `transactions` — fakty operacji;
  - `transaction_participants` — uczestnicy transakcji wraz z rolami;
  - `clearing_cycles` — zapisane cykle clearingu.

- Powody wyboru:

  - transakcyjna spójność (ACID);
  - dobry zestaw narzędzi do złożonych zapytań (część wyszukiwania cykli można robić SQL + logika aplikacji);
  - niezawodność, dojrzały ekosystem.

**Cache / sesje: Redis**

- Dane krótkotrwałe:

  - sesje użytkowników;
  - tymczasowe rezerwy na liniach zaufania podczas `prepare`;
  - kolejki zadań (zamiast odrębnego brokera wiadomości, Redis często wystarczy dla MVP).

### 3.4. Aplikacje klienckie

**Klient web (SPA/PWA)**

- Realizuje:

  - UI dla:

    - profilu uczestnika;
    - zarządzania liniami zaufania;
    - tworzenia płatności;
    - przeglądania historii i bilansów;
    - podglądu sieci zaufania (proste grafy);

  - lokalne przechowywanie klucza prywatnego (w przeglądarce — WebCrypto + IndexedDB);
  - podpisywanie ważnych operacji (linia zaufania, płatność).

- Rekomendowane technologie:

  - React albo Vue (w zależności od zespołu);
  - TypeScript;
  - wsparcie PWA (tryb offline, instalacja na telefonie).

**Klient mobilny (opcjonalnie)**

- React Native / Flutter / Capacitor (obudowa wokół klienta webowego).
- Powiela funkcje klienta webowego z dostosowanym UX mobilnym.

### 3.5. Crypto / Key Management

Zasady:

- **Klucze prywatne użytkowników przechowywane są po stronie klienta**, nie na serwerze.
- Serwer:

  - widzi tylko klucze publiczne;
  - otrzymuje podpisy z klientów;
  - weryfikuje je przy każdej operacji.

Implementacja:

- `libsodium` / `TweetNaCl` lub pokrewne biblioteki:
  - podpis: `Ed25519` (szeroko wspierany, wygodny);
  - szyfrowanie: `XChaCha20-Poly1305` (w razie potrzeby).

Format identyfikatorów uczestnika:

- `participant_id` może być:

  - UUID (z mapowaniem do klucza publicznego),
  - albo funkcją od publicznego klucza (np. `base58(SHA-256(pubkey))`).

### 3.6. Routing & Clearing Engine

**Routing (dobór ścieżek płatności)**

- Budowanie ścieżek z A do B:

  - ograniczenie długości (np. do 4–5 węzłów w MVP);
  - preferowanie bardziej „wiarygodnych” węzłów (reputacja/likwidność).

- Implementacja:

  - w pierwszej wersji — BFS z filtracją:

    - po dostępnych limitach (pozostały kredyt),
    - po ekwiwalencie,
    - po politykach (np. blacklist).

  - później —:

    - k‑najkrótszych ścieżek,
    - max‑flow dla złożonych płatności.

**Clearing (wyszukiwanie cykli i wzajemny clearing)**

- Algorytmy dla cykli długości 3–4:

  - na wcześniej budowanych podgrafach,
  - lub poprzez SQL + logikę aplikacji:
    - trójki/czwórki `(A,B,C)`/`(A,B,C,D)` z niezerowymi wzajemnymi długami.

- Planer (cron/worker):

  - po każdej transakcji — szukanie krótkich cykli w jej okolicy;
  - okresowo (co godzinę/dobę) — szersze skanowanie sieci.

- Wykonanie clearingu:

  - utworzenie specjalnej transakcji „wirtualnej płatności po cyklu”;
  - prośba o potwierdzenie od wszystkich uczestników cyklu (opcje auto‑zgody);
  - aktualizacja bilansów.

---

## 4. Model danych (poziom logiczny)

### 4.1. Participant (Uczestnik)

Główne pola:

- `id`: UUID / hash(pubkey).
- `public_key`: klucz publiczny (Ed25519).
- `display_name`: nazwa wyświetlana.
- `profile`: metadane (opis, kontakty, typ uczestnika — osoba, organizacja, hub itd.).
- `status`: `active | suspended | left | deleted`.
- Dane zagregowane (oddzielne tabele lub widoki materializowane):

  - `total_incoming_trust` (suma limitów zaufania _do niego_);
  - `total_outgoing_trust` (suma limitów zaufania _od niego_);
  - `total_debt` (jego dług wobec innych);
  - `total_credit` (długi innych wobec niego);
  - `net_balance` (pozycja netto).

### 4.2. Equivalent (Ekwiwalent)

- `id`: UUID.
- `code`: string (np. `"UAH"`, `"HOUR_DEV"`, `"kWh"`).
- `description`: opis przyjazny dla człowieka.
- `precision`: liczba miejsc po przecinku.

### 4.3. TrustLine (Linia zaufania)

- `id`: UUID.
- `from_participant_id`: kto **udziela zaufania**.
- `to_participant_id`: komu ufa.
- `equivalent_id`.
- `limit`: maksymalna kwota zaufania.
- `used`: aktualnie wykorzystana część (jako _netto_ dług `to` wobec `from` w danym ekwiwalencie, ograniczony limitem).
- `status`: `active | frozen | closed`.
- Metadane:

  - `created_at`, `updated_at`;
  - `policies` (np. auto‑clearing, reguły ryzyka itd.).

Ważne:

- `used` to nie to samo, co całkowity dług \(to \to from\); to konkretnie **wykorzystanie tej linii** w wyniku operacji.
- Pozycję netto między dwiema stronami w jednym ekwiwalencie można liczyć jako funkcję kilku linii (jeśli architektura na to pozwala), ale w MVP sensownie jest założyć **jedną linię na parę+ekwiwalent**.

### 4.4. Obligation / Debt (Zobowiązanie)

Dwa podejścia:

1. **Jawna tabela długów (krawędzi)**

   - `from_participant_id` — dłużnik;
   - `to_participant_id` — wierzyciel;
   - `equivalent_id`;
   - `amount`: aktualny dług (dodatni, ewentualnie model symetryczny, ale to komplikacja);
   - powiązanie z transakcjami (np. w tabeli `transaction_effects`).

   Wówczas:

   - linię zaufania `A→B` można interpretować jako:
     - górny limit dla `debt[B→A]` (lub odwrotnie, wg przyjętej konwencji).

2. **Tylko `used` w `trust_lines` i liczenie netto „w locie”**

   - mniej tabel, bardziej skomplikowana logika cycling/routingu.

Dla MVP sensownie jest **pójść ścieżką 1**:

- osobna tabela `debts` (lub `obligations`), w której jawnie trzymamy dług netto dla każdej pary i ekwiwalentu;
- `trust_lines` wyznaczają dopuszczalne granice wartości dla tych długów.

### 4.5. Transaction (Transakcja)

- `id`: UUID.
- `type`: `TRUST_LINE_CREATE | TRUST_LINE_UPDATE | PAYMENT | CLEARING | OTHER`.
- `status`: `PENDING | COMMITTED | ROLLED_BACK | FAILED`.
- `created_at`, `committed_at`.
- `initiator_id`: kto zainicjował transakcję.
- `payload`: JSON z danymi biznesowymi (ustrukturyzowany format).
- `signatures`: tablica podpisów cyfrowych (uczestników, dla których transakcja jest krytyczna).

Osobna tabela:

- `transaction_participants`:

  - `transaction_id`;
  - `participant_id`;
  - `role`: `PAYER | PAYEE | INTERMEDIATE | TRUSTOR | TRUSTEE | CLEARING_MEMBER | ...`;
  - `signature` (jeśli przechowujemy podpisy per rola).

---

## 5. Przepływy (flows)

### 5.1. Rejestracja uczestnika i klucze

1. Użytkownik otwiera klienta webowego.
2. Klient lokalnie generuje parę kluczy (Ed25519) i zapisuje klucz prywatny:
   - w `IndexedDB`/secure storage + opcjonalny eksport seed‑frazy.
3. Użytkownik uzupełnia profil (imię/nazwa itd.) i wysyła na serwer:
   - `public_key`,
   - dane profilu,
   - opcjonalnie podpisany komunikat `REGISTER` tym kluczem (dla spójności kryptograficznej).
4. Serwer:
   - tworzy rekord `participant`;
   - wiąże go z `public_key`;
   - zwraca `participant_id` i dane profilu.
5. Klient zapisuje `participant_id` i powiązanie z kluczem.

Warianty:

- rejestracja przez zaproszenia;
- podstawowa procedura KYC (zależnie od społeczności).

### 5.2. Tworzenie/zmiana linii zaufania

**Scenariusz:** A udziela B zaufania 100 w ekwiwalencie UAH.

1. W UI A wybiera B, wpisuje limit.
2. Klient A tworzy żądanie:

   - `from = A.id`,
   - `to = B.id`,
   - `equivalent = UAH`,
   - `limit = 100`,
   - `nonce` / `timestamp`,

   i **podpisuje** je swoim kluczem prywatnym.

3. Żądanie trafia na serwer.
4. Serwer:

   - weryfikuje podpis A;
   - sprawdza polityki (domyślne limity, progi itp.);
   - tworzy lub aktualizuje rekord w `trust_lines`;
   - tworzy transakcję typu `TRUST_LINE_CREATE/UPDATE` z podpisem A;
   - opcjonalnie powiadamia B (WebSocket/push).

5. Dla zmian wymagających zgody B (np. złożone polityki):

   - serwer wysyła ofertę do B;
   - B w swoim kliencie podpisuje zgodę.

W MVP można zacząć od **jednostronnej linii zaufania** bez podpisu drugiej strony (A sam bierze na siebie ryzyko).

### 5.3. Płatność (zakup na kredyt)

**Scenariusz:** A płaci C 60 w UAH.

Wersja uproszczona (jedna ścieżka, krótka):

1. A w UI wybiera:

   - odbiorcę C;
   - kwotę i ekwiwalent.

2. Klient A wysyła na serwer żądanie `CreatePayment` (bez podpisu lub z podpisem – w MVP można uprościć).
3. Serwer:

   - znajduje jedną lub kilka tras \(A → N_1 → ... → C\);
   - sprawdza limity i dostępne salda na liniach zaufania i długach;
   - tworzy projekt transakcji `PAYMENT`:

     - listę zmian długów na krawędziach;
     - uczestników i ich role.

4. Serwer rozsyła `PREPARE` do uczestników tras:

   - każdy węzeł (przez swojego klienta albo — w MVP — przez logikę serwerową) sprawdza:
     - czy mieści się w limitach;
     - czy nie łamie wewnętrznych polityk;
   - w wariancie docelowym: klient każdego węzła **podpisuje** swoją zgodę.

5. Jeśli wszyscy odpowiedzą `OK`:

   - serwer stosuje zmiany w `debts`/`trust_lines.used` w DB;
   - nadaje transakcji status `COMMITTED`;
   - wysyła powiadomienia uczestnikom.

6. Jeśli którakolwiek odpowiedź `FAIL`:

   - transakcja dostaje status `ROLLED_BACK`;
   - rezerwy są uwalniane.

Dla MVP dopuszczalne jest:

- **nie implementować pełnego 2PC z potwierdzeniami po stronie klientów**, bazując na weryfikacji po stronie huba (zwłaszcza w zaufanej społeczności).
- Ale warto od razu przewidzieć:

  - pole `signatures`;
  - możliwość przeniesienia części logiki potwierdzania do klientów w przyszłości.

### 5.4. Clearing (zamknięty cykl 3–4 węzłów)

**Przykład cyklu:** A → B → C → A.

1. Po każdej transakcji lub wg harmonogramu serwer:

   - buduje podgraf wokół uczestniczących w węzłów;
   - szuka cykli 3–4 węzłowych z niezerowymi wzajemnymi zobowiązaniami.

2. Po znalezieniu cyklu:

   - oblicza \(S = \min(\text{dług na każdej krawędzi})\);
   - tworzy projekt clearingu:
     - „zmniejszyć dług na każdej krawędzi cyklu o S”.

3. Tworzy transakcję typu `CLEARING` ze statusem `PENDING`.
4. Wysyła powiadomienia do uczestników cyklu:

   - w MVP można zastosować:
     - tryb **auto‑zgody domyślnie** (z możliwością opt‑out),
     - lub ręczną akceptację w UI.

5. Po akceptacji wszystkich:

   - stosuje zmiany w `debts`;
   - ustawia status transakcji na `COMMITTED`.

6. Jeśli ktokolwiek odrzuci/nie odpowie:

   - transakcja `FAILED`;
   - można zaproponować clearing na mniejszą kwotę (opcjonalnie).

---

## 6. Bezpieczeństwo, prywatność i odporność

### 6.1. Bezpieczeństwo

- Hasła przechowujemy wyłącznie jako hashe `bcrypt/argon2`.
- Autoryzacja:

  - tokeny JWT o krótkim czasie życia;
  - refresh‑tokeny o dłuższym;
  - wszędzie TLS.

- Podpisy:

  - wszystkie operacje zarządzania zaufaniem i clearingiem można (stopniowo) uczynić **wymagającymi podpisu kryptograficznego**;
  - w MVP sensowne jest zacząć od podpisywania przynajmniej operacji trustline.

- Ochrona przed SQLi, CSRF, XSS:

  - standardowe praktyki web‑security;
  - CSP, tokeny CSRF itd.

### 6.2. Prywatność

- Hub widzi wszystkie transakcje: to **świadomy kompromis w MVP**.
- Aby zminimalizować ryzyka:

  - ograniczamy logowanie danych wrażliwych;
  - definiujemy klarowną politykę dostępu administratorów;
  - opcjonalnie szyfrujemy część pól (opisy transakcji, komentarze).

- W przyszłości można:

  - przenosić część logiki i danych do p2p pomiędzy klientami;
  - stosować szyfrowanie end‑to‑end dla treści transakcji.

### 6.3. Odporność

- Kopie zapasowe:

  - regularne backupy PostgreSQL (pełne + przyrostowe);
  - backup konfiguracji i metadanych.

- Skalowanie „w górę” na etapie MVP:

  - pojedynczy hub dla 50–500 uczestników spokojnie wystarczy.

- Przygotowanie do klastrowania (kierunek architektury C):

  - wyraźne rozdzielenie:

    - warstwy „dziennika operacji”;
    - warstwy „projekcji/indeksów”;

  - tak aby w przyszłości można było dołożyć repliki, a następnie konsensus między nimi.

---

## 7. Rekomendowany stos technologiczny

### 7.1. Backend

Wariant 1 (bardzo popularny):

- **Język:** TypeScript
- **Framework:** NestJS lub Fastify (architektura modułowa).
- **DB:** PostgreSQL (przez TypeORM/Prisma/knex).
- **Cache/kolejki:** Redis.
- **Testy:** Jest.
- **Infrastruktura:** Docker, docker‑compose dla dev; Kubernetes lub bare‑metal dla produkcji.

Zalety:

- ogromny ekosystem;
- wielu developerów;
- łatwa integracja z frontendem JS.

Wariant 2 (dla fanów wysokiej wydajności/funkcyjności):

- **Język:** Go lub Elixir (Phoenix).
- Podobny stos DB + Redis.

Dla MVP sensownie jest wybrać **TypeScript + NestJS**, o ile zespół nie ma silnych preferencji.

### 7.2. Frontend

- **React + TypeScript**:

  - UI‑kit: MUI / Chakra UI / TailwindCSS;
  - biblioteka do komunikacji z API: React Query / TanStack Query;
  - graf (wizualizacja sieci zaufania): `vis-network`, `d3`, `react-force-graph` (wg gustu).

- Konfiguracja PWA dla „quasi‑mobilnego” doświadczenia.

### 7.3. Kryptografia

- Biblioteki:

  - `libsodium` lub ich wrappery (`tweetnacl`, `libsodium-wrappers`);
  - Standardy:

    - podpis: Ed25519;
    - hash: SHA‑256/512.

- Przechowywanie kluczy prywatnych:

  - przeglądarka: WebCrypto + IndexedDB;
  - mobile: secure storage (Keychain/Keystore).

---

## 8. Ewolucja architektury (szkice na przyszłość)

### 8.1. Klasteryzacja huba

Przejście od jednego huba do wielu:

- Dodajemy:

  - 2–3 instancje backendu;
  - wspólną bazę PostgreSQL (pierwszy krok);
  - load balancer (Nginx/Traefik).

- Dalej opcjonalnie:

  - repliki PostgreSQL (hot standby);
  - rozdzielenie odczyt/zapis;
  - w perspektywie — wprowadzenie dziennika konsensusowego (Raft itd.).

### 8.2. Współpraca między społecznościami

Aby połączyć kilka hubów w klaster:

- Dodajemy byt **Community / Hub**:

  - każdy hub – uczestnik‑węzeł w meta‑sieci GEO;
  - między hubami powstają linie zaufania (na poziomie podmiotów/organizacji).

- Wprowadzamy protokół:

  - `Hub-to-Hub`:

    - żądania płatności między uczestnikami różnych hubów;
    - clearing zbiorczy pomiędzy hubami.

- Na poziomie MVP wystarczy zaprojektować kontrakty API:

  - `POST /interhub/payment-request`
  - `POST /interhub/clearing-proposal`
  - itd., bez pełnej implementacji.

### 8.3. Częściowe przejście do P2P

Dla dużych / technicznych uczestników:

- opcja uruchomienia **własnego węzła**, który:

  - przechowuje lokalnie cały stan właściciela;
  - synchronizuje się z hubem przez rozszerzone API;
  - w wybranych operacjach może komunikować się bezpośrednio z innymi węzłami (w przyszłości).

To wymaga:

- dalszego rozwijania protokołu (ID, podpisy, routing);

ale już teraz:

- ważne jest **nie przyspawać logiki do jednego serwera**;
- zawsze przechowywać kryptograficzne potwierdzenia działań uczestników.

---

## 9. Zakres MVP (co wchodzi / co nie wchodzi)

### 9.1. Wchodzi do MVP

- Rejestracja uczestników i podstawowy profil.
- Zarządzanie liniami zaufania (minimum — jednostronne limity).
- Tworzenie płatności:

  - bezpośrednich (A → B);
  - przez jednego pośrednika (A → X → B) — minimum.

- Prosty silnik wyszukiwania ścieżek (BFS z limitem długości).
- Wyszukiwanie i wykonywanie clearingu dla cykli 3–4 uczestników.
- Klient web z głównymi ekranami:

  - dashboard bilansu;
  - lista i konfiguracja linii zaufania;
  - formularz płatności;
  - historia operacji;
  - (opcjonalnie) prosta wizualizacja sieci zaufania.

### 9.2. Nie wchodzi (ale architektura przewiduje)

- Pełny tryb P2P między klientami.
- Złożony protokół między‑hubowy i clearing między społecznościami.
- Formalny konsensus pomiędzy wieloma hubami (mini‑ledger).
- Złożone polityki ekonomiczne (dynamiczne limity, scoring ryzyka itd.).
- Głęboka integracja z zewnętrznymi systemami płatniczymi/fiatem.

---

## 10. Podsumowanie

Proponowana architektura **community‑hub + lekkie klienty**:

- daje realistyczną ścieżkę do **działającego MVP** dla jednej lokalnej społeczności;
- zachowuje kluczowe idee GEO:

  - linie zaufania i wzajemny kredyt;
  - wyszukiwanie ścieżek płatności;
  - automatyczny clearing cykli;

- jednocześnie:

  - nie wprowadza przedwcześnie złożonych mechanizmów konsensusu;
  - wyraźnie oddziela protokół (podpisy, byty) od konkretnej implementacji (jeden serwer);
  - pozostawia przestrzeń na ewolucję do:

    - klastra hubów w społeczności;
    - sieci międzyspołecznościowej;
    - częściowo p2p‑architektury.

Dalsze kroki:

1. Zawęzić stos technologiczny do konkretnych bibliotek i wersji.
2. Przygotować **listę user stories** i wymagań funkcjonalnych dla MVP (backlog).
3. Opisać **konkretne endpointy API** (REST/WS) i formaty wiadomości dla:

   - linii zaufania;
   - płatności;
   - clearingu.

Następnie można przejść do projektowania schematu bazy danych i rozpocząć implementację prototypu.
