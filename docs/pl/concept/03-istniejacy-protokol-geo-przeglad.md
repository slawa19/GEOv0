# GEO: gospodarka wolnej wymiany
**Zbiorczy kontekst projektu do dalszej dyskusji rozwoju i implementacji**

## 1. Co wiadomo o istniejącym GEO Protocol (bardzo krótko)

Na podstawie publicznych materiałów GEO Protocol:

- jest pozycjonowany jako **druga warstwa (second‑layer)** do skalowania i łączenia różnych blockchainów i sieci wartości („Internet of Value”), a nie tylko jako lokalny system wzajemnego kredytu [ref:2,5];
- wykorzystuje **trustlines** i **sieć kredytową** pomiędzy uczestnikami, bardzo blisko tego, o czym tu mówimy;
- został zaprojektowany jako **protokół off‑chain**: główna logika poza blockchainami, z możliwością zakotwiczenia na nich [ref:2,4,5];
- podkreśla:
  - brak globalnego ledger’a;
  - lokalny zapis na poziomie węzłów i linii zaufania;
  - możliwość **dzielenia płatności na wiele ścieżek** (multi‑path payments) i dzięki temu efektywnego wykorzystania sieci zaufania [ref:1,2,5];
- był rozważany jako ogólny protokół dla dApps, rozwiązań płatniczych, wymiany między sieciami itp., a nie tylko dla lokalnych spółdzielni [ref:2,4,10].

Szczegóły techniczne ze specyfikacji (wysoki poziom):

- własny binarny / sieciowy protokół nad TCP/UDP;
- wiele typów wiadomości (inicjalizacja trustline, routing transakcji, payments, cycle closing itd.);
- złożona wewnętrzna maszyna stanów dla każdej transakcji (wiele stanów pośrednich i odpowiedzi);
- silny nacisk na **aggressive routing** (wiele ścieżek, wiele kroków) i „idealny” clearing.

---

## 2. Przypomnienie: czym jest moja wersja GEO v0 (community‑hub)

Krótko, proponowany protokół v0:

- **model danych**:
  - uczestnicy (PID + klucze),
  - ekwiwalenty,
  - trustlines (`from → to`, limit),
  - jawne krawędzie długu `debt[X→Y,E]`,
  - transakcje: `TRUST_LINE_*`, `PAYMENT`, `CLEARING`.
- **transakcje**:
  - lokalny dwufazowy commit (2PC): `PREPARE` / `COMMIT` / `ABORT`;
  - w MVP koordynatorem jest **community‑hub**, ale protokół abstrakcyjny pozwala w zasadzie każdemu węzłowi być koordynatorem.
- **routing**:
  - prosty BFS po grafie z ograniczeniem długości ścieżki,
  - na start — jedna ścieżka (lub mała kombinacja), bez ciężkich algorytmów max‑flow.
- **clearing**:
  - osobny typ transakcji `CLEARING`;
  - wyszukiwanie cykli długości 3–4 i zmniejszanie długów o minimum po cyklu.
- **poziom wdrożenia**:
  - MVP: jeden hub na społeczność (web‑serwis + DB),
  - dalej — klastry, trustlines między hubami itd.

---

## 3. Kluczowe różnice według głównych osi

### 3.1. Skala i obszar docelowy

**Oryginalny GEO Protocol**

- Projektowany jako **uniwersalna warstwa Internet of Value**:
  - łączenie różnych blockchainów i systemów płatniczych;
  - cross‑chain transfery, uniwersalne IOU między różnymi aktywami [ref:2,5,8].
- Scenariusze: od systemów lokalnych po globalne sieci i integracje z infrastrukturą krypto.

**GEO v0 (mój wariant)**

- Jawnie ukierunkowany na:
  - **lokalne społeczności** (spółdzielnie, klastry małego biznesu);
  - rozliczenia między społecznościami na bazie trustlines między hubami;
  - bez bezpośredniego przywiązania do blockchainów (mogą być dodane później jako bramy / kotwice, ale nie są częścią protokołu v0).

**Wniosek:**

- oryginał — „internet wartości” ponad/pomiędzy blockchainami;
- v0 — „gospodarka lokalnych społeczności” z możliwością rozszerzenia w górę.

---

### 3.2. Architektura sieciowa i role

**Oryginalny GEO Protocol**

- Od początku jako **w pełni p2p‑sieć**:
  - każdy węzeł przechowuje swoje trustlines;
  - komunikuje się bezpośrednio z sąsiadami;
  - broadcasty / routing są realizowane w samym protokole [ref:1,2].
- Huby i serwisy (typu GEOpay) — osobna warstwa *ponad* protokołem.

**GEO v0**

- W wersji bazowej:
  - istnieje jawny **community‑hub** (serwer / klaster),
  - większość klientów to **cienkie** aplikacje (web/mobile), nie implementują pełnego sieciowego protokołu;
  - hub jest koordynatorem transakcji i routingu wewnątrz społeczności.
- Jednocześnie protokół wiadomości jest opisany tak, by:
  - koordynatorem *mógł* być dowolny węzeł,
  - ale to jest już krok „v1+”.

**Plusy oryginału:**

- wyższa decentralizacja i odporność na awarie (brak pojedynczego punktu);
- bliższy ideologii GEO jako całkowicie p2p‑sieci kredytowej.

**Plusy v0:**

- znacząco prostsza implementacja i utrzymanie dla pierwszych realnych społeczności;
- znajomy stos deweloperski (HTTP/WebSocket + baza danych), bez skomplikowanej p2p‑topologii na starcie.

---

### 3.3. Model stanu

**Oryginalny GEO Protocol**

- Operuje:
  - trustlines i saldami na nich;
  - lokalnym stanem w każdym węźle (brak wspólnego ledgera).
- Długi i limity zakodowane są głównie w stanie trustlines (dwukierunkowo), szczegóły zależą od implementacji.

**GEO v0**

- Wyraźnie rozdziela:
  1. **TrustLine** (limit ryzyka `from → to` po ekwiwalencie),
  2. **Debt** (`debt[debtor→creditor,E]`) — jawna krawędź długu.
- To trochę „grubszy” model, ale:
  - upraszcza wyszukiwanie cykli i clearing;
  - czyni stan bardziej czytelnym (jasno widać, kto komu ile jest winny).

**Plusy oryginału:**

- mniej rozbudowana warstwa danych;
- bliżej klasycznym modelom credit network (jak Ripple).

**Plusy v0:**

- łatwiejsza analityka, clearing i debugowanie;
- lepiej pasuje do spółdzielni, gdzie ważna jest przejrzystość długu.

---

### 3.4. Algorytm routingu i płatności

**Oryginalny GEO Protocol**

- Silny nacisk na:
  - **multi‑path routing** (dzielenie płatności na wiele tras, by zwiększyć przepustowość sieci);
  - bardziej „inteligentne” algorytmy wyszukiwania ścieżek i maksymalnego przepływu;
  - wieloetapowy protokół z bogatym zestawem stanów i typów wiadomości.
- Celem jest maksymalne „wyciśnięcie” likwidności z sieci zaufania.

**GEO v0**

- W v0:
  - prosty BFS po grafie z ograniczeniem długości ścieżki;
  - startowo — jedna ścieżka (jeśli jej pojemność ≥ kwoty płatności);
  - rozbicie na kilka ścieżek — następny krok, ale bez agresywnego max‑flow.
- Koordynacja:
  - klasyczny 2PC na koordynatorze (hubie),
  - minimalny zestaw stanów: `PREPARE`, `ACK`, `COMMIT` / `ABORT`.

**Plusy oryginału:**

- lepiej wykorzystuje sieć przy dużej liczbie węzłów i połączeń;
- bardziej „naukowy” i zoptymalizowany routing.

**Minusy oryginału:**

- znacznie trudniejszy do implementacji i weryfikacji;
- trudniejszy do wpasowania w MVP dla realnej społeczności, gdzie ważniejsza jest prostota niż maksymalny przepływ.

**Plusy v0:**

- dużo mniej kodu i scenariuszy na start;
- łatwiej wyjaśnić, wizualizować i debugować.

---

### 3.5. Clearing (cycle closing)

**Oryginalny GEO Protocol**

- Z dokumentów wynika, że clearing cykli jest ważną częścią projektu:
  - wyszukiwanie cykli o określonej długości;
  - specjalne transakcje zamykające cykl;
  - różne typy cykli (3,4,5,6) i algorytmy wyszukiwania [ref:1,10].
- Protokół clearingu jest przedstawiony jako część ogólnego protokołu sieciowego — z osobnymi typami wiadomości i maszynami stanów.

**GEO v0**

- Jawnie definiuje `CLEARING` jako **oddzielny typ transakcji**:
  - lista węzłów cyklu,
  - ekwiwalent,
  - kwota S = minimum po krawędziach cyklu.
- Wyszukiwanie cykli ograniczone dla v0:
  - długość 3–4 (na początek),
  - wyszukiwanie w okolicy ostatnio zmienionych krawędzi.
- Wykorzystuje ten sam 2PC:
  - `PREPARE` / `COMMIT` / `ABORT`,
  - z możliwością automatycznej zgody (jeśli polityka uczestnika na to pozwala).

**Różnica:**

- oryginalny protokół projektowano z myślą o masowym i regularnym clearingu przy dużych wolumenach;
- mój v0 celowo **ogranicza** złożoność clearingu, by MVP nie utonął w algorytmach.

---

### 3.6. Integracja z blockchainami i systemami zewnętrznymi

**Oryginalny GEO Protocol**

- Wprost opisywany jako **second‑layer protokół dla blockchainów**, off‑chain scaling solution [ref:2,5,8]:
  - może działać nad dowolnym publicznym blockchainem;
  - może łączyć kilka blockchainów w jedną sieć (cross‑chain).
- W ekosystemie zakładano:
  - bramy dla fiata/kryptowalut;
  - kotwiczenie stanu/sporów w blockchainach.

**GEO v0**

- W wersji bazowej **nie jest przywiązany do blockchainów**:
  - cały stan w hubach / węzłach;
  - blockchainy mogą być dodane później:
    - jako mechanizm zabezpieczenia / depozytów,
    - jako mechanizm arbitrażu / kotwiczenia,
    - jako bramy między różnymi sieciami GEO.

**Wniosek:**

- oryginalny GEO — od razu „most” do istniejącej infrastruktury krypto;
- v0 — „czysta” sieć kredytowa, możliwa do wdrożenia nawet bez krypto, a blockchainy podłączamy w razie potrzeby.

---

### 3.7. Złożoność implementacji i przydatność dla MVP

**Oryginalny GEO Protocol**

**Zalety:**

- silna koncepcyjna spójność: jeden protokół od sieci lokalnych po globalny Internet of Value [ref:2,4,5];
- pełny p2p‑model, brak obowiązkowych centrów;
- zaawansowane algorytmy routingu i clearingu ukierunkowane na dużą skalę;
- wbudowana orientacja na integrację z blockchainami i operacje cross‑chain [ref:2,5,8].

**Wady (z punktu widzenia MVP dla spółdzielni):**

- duża złożoność dla zespołu, który chce **od zera** uruchomić lokalny prototyp;
- konieczność p2p‑infrastruktury sieciowej, co poważnie komplikuje start;
- znacznie większe ryzyko „przegrzania” pierwszych wersji i niedotarcia do działającego produktu.

---

**GEO v0 (community‑hub protokół)**

**Zalety:**

- prosty, „płaski” zestaw bytów: Participants, TrustLines, Debts, Transactions;
- proste i znane algorytmy:
  - BFS dla routingu,
  - 2PC dla transakcji,
  - wyszukiwanie krótkich cykli dla clearingu;
- wygodny dla **lokalnych społeczności** i spółdzielni — mniejsze obciążenie poznawcze;
- realistyczny dla zespołu 2–5 devów, którzy chcą w 3–6 miesięcy doprowadzić do działającego serwisu;
- pozostawia „uchylone drzwi” dla:
  - przejścia do p2p;
  - podłączenia blockchainów;
  - komplikowania routingu i clearingu.

**Wady:**

- mniej zdezcentralizowany na starcie (zależność od community‑hub w obrębie społeczności);
- w bazowej formie nie rozwiązuje od razu zadania globalnego Internet of Value (to raczej następna warstwa);
- część pomysłów oryginalnego GEO (agresywny multi‑path, zaawansowany routing, ścisła integracja z blockchainami) jest odłożona na „później”.

---

## 4. Podsumowanie: plusy i minusy obu podejść

### Oryginalny GEO Protocol

**Plusy:**

- silnie przemyślana, „duża” wizja od lokalnych sieci po globalny Internet of Value;
- pełne p2p, brak wymuszonych centrów;
- zaawansowane mechanizmy routingu i clearingu dla dużej skali;
- natywna integracja z blockchainami i światem krypto.

**Minusy (dla lokalnego MVP):**

- zbyt wysoki próg wejścia dla zespołów budujących małe, realne wdrożenia;
- duże skomplikowanie sieciowe;
- ryzyko, że zanim powstanie cokolwiek używalnego w rzeczywistych społecznościach, projekt ugrzęźnie w inżynieryjnej złożoności.

---

### GEO v0 (community‑hub)

**Plusy:**

- prosta architektura: hub + lekkie klienty;
- łatwe wdrożenie w jednym konkretnym kooperatywie / społeczności;
- możliwość szybkiego uzyskania działającego pilota i zebrania feedbacku;
- naturalna ścieżka ewolucji:
  - klastry hubów,
  - federacja społeczności,
  - stopniowe dołączanie elementów p2p i blockchainów.

**Minusy:**

- kompromis w zakresie decentralizacji (hub jest pojedynczym punktem koordynacji);
- część „ambicji globalnych” oryginalnego GEO jest odsunięta na kolejne wersje.

---

## 5. Jak można zbliżyć oba światy

Jeśli celem jest **lokalny MVP teraz**, ale bez zamykania drogi do „prawdziwego GEO” w przyszłości, logiczne jest:

1. Wziąć **v0 jako bazę** dla community‑hub.
2. Stopniowo przenosić z oryginalnego GEO:
   - bogatszy clearing (więcej typów cykli, optymalizacje);
   - multi‑path routing w wersji mocniejszej niż „light”;
   - p2p‑komunikację między dużymi węzłami;
   - kotwiczenie stanu / sporów w blockchain (jeśli i kiedy to potrzebne).
3. Trzymać model danych (format transakcji, trustlines, długów) jak najbliżej ducha oryginalnego GEO:
   - te same koncepcje, podobne stany;
   - możliwość mapowania między implementacjami.

W praktyce oznacza to: najpierw budujemy prostą, lokalną „gospodarkę GEO” na community‑hub, uczymy się na pilocie, a dopiero potem dokładamy elementy globalnego, w pełni p2p‑owego GEO Protocol.
