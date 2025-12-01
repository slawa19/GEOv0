## Część 1. Kluczowe rozgałęzienia w projekcie protokołu (warianty)

### Wariant 1: Kto koordynuje transakcje na ścieżce?

**1A. Koordynator = inicjator płatności**

- Inicjator (płatnik) sam:
  - znajduje trasę;
  - wysyła `PREPARE` do wszystkich na ścieżce;
  - zbiera odpowiedzi i wysyła `COMMIT` / `ABORT`.
- Plusy:
  - maksymalnie p2p;
  - dobrze się skaluje — brak „centralnego punktu”.
- Minusy:
  - trudniejsze do implementacji na słabych klientach i w przeglądarkach;
  - inicjator musi być online do końca transakcji;
  - trudniejsze debugowanie.

**1B. Koordynator = „najbliższy hub” (community‑hub)**

- Klient wysyła prosty request płatności do swojego huba.
- Hub:
  - szuka tras;
  - koordynuje `PREPARE` / `COMMIT`;
  - prowadzi log.
- Plusy:
  - silne uproszczenie klientów;
  - prostsza implementacja i debugowanie;
  - dobrze pasuje do architektury community‑hub, którą wybrałeś.
- Minusy:
  - wewnątrz społeczności pojawia się punkt koordynacji;
  - ale to da się rozwiązać klastrowaniem i podpisami.

👉 **Dla protokołu v0.**  
Wybieramy **1B** jako scenariusz podstawowy, ale protokół opisujemy tak, by *w zasadzie* koordynatorem mógł być dowolny węzeł (potrzebne do ewolucji w stronę p2p).

---

### Wariant 2: Jakie jest „nośne” przedstawienie stanu — krawędzie długu czy saldo linii zaufania?

**2A. Jawne krawędzie długu (Obligations / Debts)**

- Dla każdej pary `(X,Y,E)` trzymamy `debt[X→Y,E]` — ile X jest winien Y w ekwiwalencie E.
- Linia zaufania `A→B` to:
  - limit na `debt[B→A,E]` (czyli ile B może być winien A w tym ekwiwalencie).
- Plusy:
  - wygodnie szukać cykli i robić clearing;
  - bardzo jasno widać, kto komu ile jest winien.
- Minusy:
  - potrzebna osobna tabela na długi.

**2B. Tylko `used` w trust‑line, bez osobnej tabeli**

- Saldo dla pary trzeba wyprowadzać z dwóch skierowanych linii: `A→B` i `B→A`.
- Plusy:
  - mniej tabel;
- Minusy:
  - clearing i routing stają się logicznie bardziej złożone.

👉 **Dla v0:** bierzemy **2A** — osobne krawędzie długu `Obligation` / `Debt`.

---

### Wariant 3: Jak formalizować kliring?

**3A. Specjalny typ transakcji `CLEARING`**

- Istnieje jawny typ transakcji:
  - lista krawędzi cyklu;
  - zmniejszenie długu na każdej krawędzi o tę samą kwotę `S`.
- Plusy:
  - prosta i przejrzysta semantyka;
  - łatwo analizować historię i debugować.
- Minusy:
  - dodatkowy typ operacji (i tak jest potrzebny).

**3B. Kodowanie kliringu jako „serii płatności”**

- Formalnie wykonujemy serię zwykłych `PAYMENT`, aby uzyskać ten sam efekt.
- Plusy:
  - mniej typów transakcji.
- Minusy:
  - nieczytelne i mylące;
  - trudno odtwarzać znaczenie ekonomiczne.

👉 **Dla v0:** jednoznacznie **3A** — osobny `CLEARING`.

---

### Wariant 4: Wymiana między społecznościami

**4A. „Huby jako zwykli uczestnicy”**

- Każdy hub‑społeczność w protokole jest po prostu uczestnikiem (węzłem).
- Między nimi otwierane są linie zaufania jak między dowolnymi uczestnikami.
- Płatności między ludźmi różnych społeczności:
  - są routowane przez odpowiednie huby, ale **protokół pozostaje ten sam** (`PAYMENT`, `CLEARING`).
- Plusy:
  - maksymalna prostota — jeden protokół dla wszystkich poziomów;
  - łatwo wyjaśnić: „społeczności ufają sobie tak samo jak ludzie”.

- Minusy:
  - wymaga dobrze przemyślanych polityk ryzyka między hubami.

**4B. Specjalny protokół między‑hubowy**

- Osobna „warstwa” między hubami:
  - własne typy wiadomości, być może inny format clearingu (netting).
- Plusy:
  - można zoptymalizować pod duże wolumeny.
- Minusy:
  - trudniejsza specyfikacja;
  - więcej kodu.

👉 **Dla v0:** wybieramy **4A** — huby = zwykli uczestnicy, ten sam protokół.

---

Dalej opisujemy **jednolity protokół GEO v0**, zbudowany na wariantach 1B, 2A, 3A, 4A.

---

## Część 2. Dokument protokołu GEO v0

### 0. Cele protokołu

Protokół GEO v0 służy do:

- **p2p‑gospodarki wzajemnego kredytu**:
  - między pojedynczymi ludźmi/organizacjami;
  - między społecznościami (poprzez ich huby).
- Bez jednej waluty i bez globalnego ledgera:
  - w sieci istnieją tylko zobowiązania uczestników,
  - oraz linie zaufania (limity kredytowego ryzyka).
- Z zapewnieniem:
  - **prostoty implementacji** (minimum bytów, oparcie na klasycznych algorytmach);
  - **skalowalności** (lokalny konsensus, routing w grafie);
  - **rozszerzalności** (klienci p2p, klastry hubów, złożone polityki clearingu).

---

## 1. Model danych protokołu

To opis logiczny — sposób przechowywania w bazie konkretnego huba opisaliśmy osobno. Tu — „język protokołu”.

### 1.1. Tożsamość i klucze

**Participant (węzeł)**:

- Ma jedną lub kilka par kluczy kryptograficznych (w v0 — jedna główna).
- Główna schemat podpisu: **Ed25519**.
- Identyfikator uczestnika (PID) = `base58(sha256(public_key))` lub analog (konkretyzujemy w implementacji).

W protokole każda operacja zmieniająca stan musi być:

- albo zainicjowana przez uczestnika i **podpisana** jego kluczem;
- albo (dla uproszczonego MVP) autoryzowana przez uwierzytelnienie w hubie, ale w modelu **podpisy i tak muszą być obecne** (nawet jeśli chwilowo nie są twardo egzekwowane).

### 1.2. Ekwiwalenty (Equivalent)

Ekwiwalent (E):

- jednostka rozliczeniowa, w której mierzymy wartość zobowiązań i limitów:
  - fiat (UAH, USD);
  - towar (kg pszenicy, kWh);
  - usługa (godzina określonej specjalizacji);
  - koszyk lub indeks.
- Dla danego kontrahenta PARTICIPANT_X można otworzyć **kilka linii zaufania w różnych ekwiwalentach**, ale nie więcej niż jedną na dany ekwiwalent.

### 1.3. Linia zaufania (TrustLine)

Skierowana relacja:

> „A ufa B do limitu L w ekwiwalencie E”.

Semantyka:

- B może **otrzymać od A towary/usługi na kredyt** do sumy L;
- W zamian A dostaje **zobowiązania B** (obietnicę oddania swoich towarów/usług na tę kwotę).

Ważne:

- linia zaufania określa **maksymalne ryzyko A wobec B** dla danego ekwiwalentu;
- bieżące **saldo na linii** — ile z limitu jest już wykorzystane.

### 1.4. Zobowiązanie (Debt / Obligation)

Rekord:

> „X jest winien Y kwotę S w ekwiwalencie E”.

Może:

- powstać przy otrzymaniu towaru/usługi na kredyt;
- zostać przeniesione na osoby trzecie (cesja wierzytelności).

Z natury jest bliżej:

- **obligacjom / wekslom towarowym** (zobowiązanie do dostarczenia towaru),
- niż **pieniędzom** jako uniwersalnemu aktywu:

  - **nie** jest miarą wartości (tą jest wybrany ekwiwalent);
  - **słabo nadaje się do akumulacji** (brak procentu, ryzyko niewypłacalności konkretnego emitenta).

### 1.5. Bilans uczestnika

**Pozycja netto** uczestnika w ramach sieci i ekwiwalentu:

\[
\text{Bilans}_E(U) = \sum \text{zobowiązania innych wobec U} - \sum \text{zobowiązania U wobec innych}
\]

Istotny jest właśnie **bilans netto**, a nie łączna suma wyemitowanych/otrzymanych zobowiązań.

### 1.6. Dostępny kredyt / przepływ płatniczy

Dla pary węzłów \(A\) i \(B\) GEO określa **maksymalną możliwą kwotę**, jaką A może „zapłacić” B:

- z uwzględnieniem wszystkich linii zaufania — bezpośrednich i tranzytywnych;
- z uwzględnieniem już wykorzystanych limitów.

Formalnie jest to zadanie znalezienia maksymalnego przepływu w skierowanym grafie z ograniczeniami na krawędziach.

Przykład:

- D ufa B do 1000;
- D ufa A do 1000;
- A ufa B do 1000.

W rezultacie **przepływ z B do D** może wynieść do 2000 dzięki kombinacji ścieżek bezpośrednich i tranzytywnych.

---

## 2. Architektura sieci GEO

### 2.1. Graf zaufania

Sieć — to skierowany graf:

- wierzchołki — uczestnicy;
- krawędzie — linie zaufania z limitami i bieżącymi saldami.

Każdy uczestnik ma *lokalną* listę:

- wychodzących linii zaufania (komu ufa);
- przychodzących (kto ufa jemu).

### 2.2. Brak globalnego rejestru

Zasadnicza różnica względem systemów blockchain:

- **nie ma globalnego ledgera** wszystkich transakcji, który wszyscy przechowują i walidują;
- przechowywane jest tylko:

  - stanie linii zaufania,
  - sumaryczne zobowiązania,
  - lokalne logi u uczestników zaangażowanych w operacje.

Konsekwencje:

- wysoka skalowalność (każdy przetwarza tylko „swoją” część grafu);
- lepsza prywatność (brak jednego dziennika wszystkich działań);
- bardziej złożony audyt globalny (nie da się „przewinąć blockchaina od genesi”).

### 2.3. Lokalny konsensus zamiast globalnego

Zamiast „jednego konsensusu dla całej sieci” (jak w PoW/PoS):

- **każda transakcja osiąga konsensus tylko między uczestnikami, których dotyczy**:
  - płatnik;
  - odbiorca;
  - węzły pośrednie na ścieżkach płatniczych.

W praktyce jest to rozproszona wersja dwufazowego commitu (2PC) na ścieżce w grafie.

---

## 3. Mechanika transakcji GEO

### 3.1. Rodzaje operacji

1. **Zarządzanie liniami zaufania**
   - otwarcie/zmiana/zamknięcie linii zaufania A → B w ekwiwalencie E;
   - utrwalane u obu uczestników (inicjator i odbiorca zaufania).

2. **Zakup towaru/usługi na kredyt**
   - inicjator wysyła żądanie płatności (podaje kontrahenta, sumę, ekwiwalent);
   - sieć dobiera ścieżki i tworzy łańcuch wzajemnych zobowiązań.

3. **Cesja zobowiązań**
   - wierzyciel przekazuje swoje prawo do spłaty osobie trzeciej;
   - to podstawa płatności tranzytywnych i clearingu.

4. **Operacje clearingowe**
   - specjalne transakcje na zamkniętych cyklach długu (patrz dalej).

### 3.2. Routowanie płatności (wyszukiwanie ścieżek)

Przy płatności A → Z:

1. Klient A formuje żądanie:

   - ID odbiorcy Z;
   - kwota i ekwiwalent;
   - opcjonalne ograniczenia (dopuszczalna długość ścieżki, minimalne zaufanie do pośredników).

2. Moduł routingu GEO:

   - szuka ścieżek z A do Z o długości do 6 węzłów;
   - oblicza dostępny przepływ na każdej ścieżce (minimum limitów minus już zajęte);
   - łączy kilka ścieżek, aby uzyskać wymaganą kwotę.

3. Wynik: zestaw płatniczych ścieżek z przypisanymi wolumenami.

### 3.3. Protokół potwierdzania (lokalny konsensus)

Dla wybranego zestawu ścieżek:

1. **Faza przygotowania (prepare)**

   - do każdego uczestnika łańcucha wysyłane jest żądanie:
     - „czy jesteś gotów zmienić saldo na linii z sąsiadem o ΔS w ramach transakcji T?”
   - uczestnik:
     - sprawdza lokalne warunki (limity, bieżące obciążenie, swoje reguły ryzyka);
     - tymczasowo **rezerwuje** tę kwotę;
     - odpowiada `OK` lub `FAIL`.

2. **Faza zatwierdzenia (commit)**

   - jeśli wszyscy na wszystkich ścieżkach odpowiedzieli `OK`:
     - inicjator rozsyła komendę `COMMIT(T)`;
     - każdy węzeł aktualizuje swoje saldo na odpowiedniej linii i log transakcji.
   - jeśli choć jeden `FAIL` lub brak odpowiedzi w zadanym czasie:
     - rozsyłane jest `ROLLBACK(T)`;
     - wszyscy uczestnicy zwalniają rezerwy, salda nie ulegają zmianie.

**Właściwości:**

- **Atomowość:** nie ma częściowo przeprowadzonych transakcji.
- **Izolacja:** transakcje równoległe widzą się tylko przez mechanizm rezerw, co zapobiega kolizjom.
- **Spójność:** wynik udanej transakcji jest zgodny u wszystkich uczestników.

### 3.4. Kto co wie o transakcji

- Pełną trasę (A → … → Z) zna:
  - inicjator;
  - komponent routingu (jeśli jest wydzielony).
- Każdy węzeł pośredni wie:
  - od kogo do kogo „przeniósł ryzyko” po linii;
  - ale **nie musi** znać końcowego odbiorcy i inicjatora (jeśli protokół wyższego poziomu tego nie wymaga).
- W logu węzła zapisują się tylko:
  - jego własne zmiany,
  - ID sąsiada po linii,
  - ID transakcji / podpisy.

Globalnego dziennika „kto komu co kupił” nie ma.

---

## 4. Clearing (wzajemne kompensowanie długów)

### 4.1. Zadanie clearingu

W sieci nieustannie powstają cykle:

\[
A \to B \to C \to ... \to A
\]

De facto wszyscy uczestnicy w cyklu mogliby **wzajemnie zredukować część zobowiązań**, nie zmieniając faktu, że ktoś zużył czyjąś pracę/towar.

**Cele clearingu:**

- zmniejszać sumaryczny nominalny dług w sieci;
- zmniejszać liczbę krawędzi z niezerowym długiem;
- poprawiać „płynność” i odporność sieci.

### 4.2. Mechanika clearingu w GEO

Z dokumentów i korespondencji:

- klarowane są cykle o długości **3, 4, 5 i 6 uczestników**;
- cykle 3–4 węzły są szukane po każdej operacji;
- cykle 5–6 — raz na dobę (z powodu złożoności obliczeniowej).

Ogólny schemat:

1. **Wyszukiwanie cyklu**
   - na podstawie bieżącego grafu zobowiązań (a nie samych limitów);
   - wykorzystywany jest specjalizowany algorytm (klasy `Cycle3`, `Cycle4`, `Cycle5`, `Cycle6` w oryginalnym GEO).

2. **Określenie możliwej kwoty clearingu**
   - załóżmy, że w cyklu długi:
     \[
     A \to B = a,\; B \to C = b,\; ...,\; X \to A = x
     \]
   - maksymalna kwota wzajemnego kompensowania:
     \[
     S = \min(a, b, ..., x)
     \]

3. **Utworzenie transakcji clearingowej**
   - tworzona jest wirtualna płatność typu „A płaci A po cyklu (A → B → C → … → A) na sumę S”;
   - dla wszystkich krawędzi cyklu:
     - dług zmniejsza się o S;
   - lokalne logi są aktualizowane u wszystkich uczestników cyklu.

4. **Potwierdzenie przez uczestników**
   - jak przy zwykłej transakcji, wszyscy uczestnicy muszą się zgodzić;
   - jeśli ktoś odmawia — cykl nie jest klarowany (lub próbowana jest mniejsza kwota).

### 4.3. Przykład na 5 węzłach (A, B, C, D, E)

Struktura długów (po szeregu zakupów i jednym clearingu):

- A → B : 20
- B → C : 20
- C → D : 10
- D → E : 10
- E → A : 0

**Clearing na 40** już zaszedł (patrz nasze wcześniejsze rozważania). Gdyby E → A wynosiło 40, cykl o długości 5 węzłów pozwoliłby zmniejszyć wszystkie długi o 40.

### 4.4. Kto inicjuje clearing

- Cykle mogą **wyszukiwać zarówno same węzły**, analizując lokalne podgrafy, jak i:
  - wyspecjalizowane serwisy/huby,
  - które otrzymują dane zagregowane (bez szczegółów konkretnych transakcji).
- Zainicjować transakcję clearingu może:
  - dowolny uczestnik cyklu;
  - lub serwis, w imieniu jednego z uczestników (według ustalonych reguł).

Ważne:  
**przymusowego clearingu nie ma** — redukcja długów po cyklu jest możliwa tylko przy zgodzie wszystkich, choć ekonomicznie jest to dla nich korzystne.

---

## 5. Bezpieczeństwo, zaufanie i ryzyka

### 5.1. Lokalizacja ryzyka kredytowego

W odróżnieniu od systemu bankowego:

- nie ma „trzeciej strony”, która koncentruje ryzyka i podejmuje decyzje za wszystkich;
- każdy sam decyduje, **komu i na jaką kwotę zaufać**.

Ryzyko określają:

- limity zaufania ustawione przez uczestnika;
- struktura grafu (ryzyka pośrednie przy operacjach tranzytywnych).

Filozofia GEO:  
protokół **nie chroni przed złym wyborem kontrahentów** — jedynie ogranicza skalę szkody limitem zaufania.

### 5.2. Podwójne wydatkowanie

Dokument „Docelowe parametry GEO” podkreśla:

- Sposób budowy topologii i lokalny konsensus **eliminują problem podwójnego wydatkowania** niezależnie od mocy obliczeniowych pojedynczych węzłów.
- Mechanizm:
  - rezerwacja na liniach zaufania w fazie `prepare`;
  - brak zatwierdzenia przy jakimkolwiek konflikcie;
  - brak wspólnego salda, które można „sfałszować” np. atakiem 51%.

### 5.3. Odporność na awarie i „śmierć” węzłów

**Tymczasowe wypadnięcie węzła:**

- Wszystkie transakcje, w których uczestniczył i zdążyły się zakończyć, są już zapisane u innych.
- Nowe transakcje nie mogą używać ścieżek przezeń przechodzących.
- Reszta sieci działa dalej.

**Trwałe zniknięcie węzła (przykład z D w cyklu A–B–C–D–E):**

- Sąsiedzi pozostają z ostatnimi uzgodnionymi zobowiązaniami:
  - C jest winien D — dług staje się praktycznie nieściągalny;
  - D jest winien E — E traci wierzytelność wobec D.
- To **strata kredytowa kontrahentów**, a nie awaria protokołu.
- Graf po prostu się „przerzedza”: znikają krawędzie, spada płynność, ale inni uczestnicy funkcjonują.

**Masowe awarie:**

- Sieć może rozpaść się na kilka komponentów spójności:
  - w każdej z nich transakcje i clearing działają normalnie;
  - między komponentami — tymczasowo brak tras.
- Nie ma pojedynczego punktu awarii, jak centralny serwer czy globalny blockchain.

---

## 6. Prywatność i dane

### 6.1. Dane publiczne

Zgodnie ze specyfikacją GEO publicznie dostępne są:

- PID węzła;
- łączny wolumen **przychodzących** linii zaufania;
- łączny wolumen **wychodzących** linii zaufania;
- łączny wolumen zobowiązań węzła wobec kontrahentów;
- łączny wolumen zobowiązań kontrahentów wobec węzła;
- bilans netto zobowiązań.

To pozwala:

- oceniać względną „wiarygodność” i skalę uczestnika;
- budować mechanizmy reputacyjne na poziomie protokołu/nadbudówek.

### 6.2. Dane prywatne

- Historia konkretnych transakcji na linii A–B:
  - przechowywana jest tylko u A i B (oraz ewentualnych wybranych przez nich serwisów‑pośredników).
- Przy operacji tranzytywnej:
  - węzeł pośredni widzi **tylko swój odcinek** (np. zmianę między B i C), ale nie całą ścieżkę i nie wszystkie kwoty na innych segmentach.

### 6.3. Kompromis z wygodą (serwisy typu GEOpay)

- Nadbudówki w rodzaju GEOpay:
  - ułatwiają interakcję (profile, social logins, wygodny UI);
  - ale pogarszają prywatność, bo serwer wie więcej.
- Użytkownik sam wybiera:
  - pracować bezpośrednio przez protokół;
  - czy korzystać z wygody kosztem części prywatności.

---

## 7. Porównanie GEO z innymi systemami

### 7.1. Klasyczny system bankowy

| Cecha                      | Banki                         | GEO                                             |
|----------------------------|-------------------------------|-------------------------------------------------|
| Emisja                     | Bank centralny + komercyjne   | Każdy uczestnik emituje własne zobowiązania    |
| Procent                    | Jest                          | Brak (bezprocentowy wzajemny kredyt)           |
| Centralizacja              | Wysoka                        | Brak — graf p2p                                 |
| Rejestr transakcji         | Centralne bazy danych         | Lokalne logi, brak globalnego rejestru         |
| Ryzyko strony trzeciej     | Wysokie                       | Brak centralnego pośrednika                     |
| Prywatność                 | Niska (banki + państwo)       | Wyższa: brak wspólnej bazy wszystkich operacji |

### 7.2. Ripple / Stellar (XRPL, Stellar Network)

Cecha wspólna:

- linie zaufania (trustlines);
- tranzytywność zaufania;
- możliwość używania IOU (zobowiązań) jako środka wymiany.

Kluczowe różnice GEO:

1. **Brak wewnętrznej waluty**
   - Ripple/Stellar: mają XRP/XLM, obowiązkowe do działania (rejestracja, opłaty).
   - GEO: nie ma „monety GEO”; brak opłat w samym protokole.

2. **Rejestr i konsensus**
   - Ripple/Stellar:
     - globalny rozproszony ledger wszystkich transakcji;
     - konsensus wśród walidatorów (UNL, SCP itd.).
   - GEO:
     - brak globalnego ledgera;
     - lokalny konsensus tylko między uczestnikami danej operacji.

3. **Clearing**
   - Ripple/Stellar:
     - clearing jest efektem ubocznym konkretnej transakcji płatniczej;
     - brak osobnego procesu wyszukiwania i „s
