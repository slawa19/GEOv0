# GEO — Aplikacja‑symulator zachowań użytkowników

> Specyfikacja aplikacji do symulacji, testowania i wizualizacji sieci GEO

---

## Spis treści

1. [Wprowadzenie](#1-wprowadzenie)  
2. [Główny zakres funkcjonalny symulatora](#2-główny-zakres-funkcjonalny-symulatora)  
   - 2.1. [Konfiguracja „świata”](#21-konfiguracja-świata---generacja-sieci)  
   - 2.2. [Scenariusze zachowań](#22-scenariusze-zachowań---modele-interakcji)  
   - 2.3. [Sterowanie obciążeniem](#23-sterowanie-obciążeniem)  
   - 2.4. [Integracja z API jądra](#24-integracja-z-rzeczywistym-api-jądra)  
3. [Wizualizacja i analityka](#3-wizualizacja-i-analityka)  
   - 3.1. [Graf sieci zaufania i długów](#31-graf-sieci-zaufania-i-długów)  
   - 3.2. [Metryki zagregowane](#32-metryki-zagregowane-w-czasie)  
   - 3.3. [Wskaźniki efektywności protokołu](#33-wskaźniki-efektywności-protokołu)  
   - 3.4. [Tryb „replay”](#34-tryb-replay)  
4. [Konfiguracja i scenariusze „pod TZ”](#4-konfiguracja-i-scenariusze-pod-tz)  
5. [Koncepcja wizualizacji](#5-koncepcja-wizualizacji)  
6. [Ekran „Mapa społeczności”](#6-ekran-mapa-społeczności)  
   - 6.1. [Kompozycja ekranu](#61-kompozycja-ekranu)  
   - 6.2. [Prezentacja uczestników](#62-prezentacja-uczestników-węzłów)  
   - 6.3. [Prezentacja połączeń](#63-prezentacja-połączeń-trustlines-i-długi)  
7. [Grupowanie uczestników](#7-grupowanie-uczestników)  
   - 7.1. [Po typach i rolach](#71-po-typach-i-rolach)  
   - 7.2. [Po klastrach zaufania](#72-po-klastrach-zaufania)  
   - 7.3. [Po parametrach finansowych](#73-po-parametrach-finansowych)  
8. [Akcje użytkownika](#8-akcje-użytkownika)  
   - 8.1. [Akcje na uczestniku](#81-akcje-na-uczestniku-węźle)  
   - 8.2. [Akcje na liniach zaufania i długach](#82-akcje-na-liniach-zaufania-i-długach)  
9. [Tryb „Ścieżki płatności i clearing”](#9-tryb-ścieżki-płatności-i-clearing)  
10. [Tryb listy uczestników](#10-tryb-listy-zagregowanej-uczestników)  
11. [Rekomendowany stos technologiczny](#11-rekomendowany-stos-technologiczny)  
    - 11.1. [Główny stos (React)](#111-główny-stos-react)  
    - 11.2. [Alternatywa (Python)](#112-alternatywa-python)  
12. [Specyfikacja UI ekranu „Mapa społeczności”](#12-specyfikacja-ui-ekranu-mapa-społeczności)  
    - 12.1. [Struktura ogólna](#121-struktura-ogólna-ekranu)  
    - 12.2. [Top Bar](#122-top-bar)  
    - 12.3. [Left Sidebar](#123-left-sidebar)  
    - 12.4. [Main Canvas](#124-main-canvas)  
    - 12.5. [Right Sidebar](#125-right-sidebar)  
    - 12.6. [Stany ekranu](#126-stany-ekranu-jako-całości)  

---

## 1. Wprowadzenie

**Osobna aplikacja, która imituje żywą społeczność**, generuje obciążenie na jądro GEO i wizualizuje wyniki. To jednocześnie:

- generator ruchu i scenariuszy zachowań,
- narzędzie testów wydajnościowych,
- laboratorium do eksperymentów nad protokołem (routing, clearing, polityki zaufania).

### Ogólna idea

Aplikacja łączy się z API huba jako wiele „wirtualnych użytkowników” (uczestników), tworzy nimi i zarządza, otwiera między nimi trustlines, generuje płatności, inicjuje/reaguje na clearing. De facto jest to **symulacja gospodarki społeczności** działająca ponad prawdziwym jądrem GEO.

**Cele:**

- sprawdzić wydajność i odporność implementacji;
- zweryfikować poprawność logiki (szczególnie clearingu, ograniczeń limitów, idempotentności);
- zbadać, jak różne topologie zaufania i modele zachowań wpływają na:
  - efektywność wzajemnych kompensat (clearing),
  - rozkład długów/kredytów,
  - średnią długość tras i częstotliwość odrzucanych płatności;
- wykryć błędy i wąskie gardła jeszcze przed wdrożeniem dla „prawdziwych” użytkowników.

---

## 2. Główny zakres funkcjonalny symulatora

### 2.1. Konfiguracja „świata” — generacja sieci

> **MVP:** zacząć od konfiguracji w JSON (lista uczestników + lista trustlines). Generację losową dodać później.

Możliwość opisania parametrów sieci poprzez plik JSON:

```json
{
  "participants": [
    { "id": "p1", "name": "Jan", "type": "person" },
    { "id": "p2", "name": "Kawiarnia", "type": "organization" }
  ],
  "trustlines": [
    { "from": "p1", "to": "p2", "limit": 1000 }
  ]
}
```

Parametry:

- liczba uczestników;
- typy uczestników (zwykli, „centralni” węzłowie, hub‑y itd.);
- lista trustlines z limitami;
- **jeden ekwiwalent** (np. `UAH`) — na MVP wystarczy.

Symulator potrafi:

- **wczytywać gotowe scenariusze JSON** (tryb podstawowy),
- w trybie rozszerzonym — generować automatycznie sieć według parametrów (po MVP).

### 2.2. Scenariusze zachowań — modele interakcji

Modele opisujące, jak uczestnicy działają w czasie:

- **„losowy rynek”** — uczestnicy losowo wybierają kontrahentów i dokonują płatności;
- **„wymiana klastrowa”** — intensywniejsza wymiana w obrębie podgrup, rzadsza między nimi;
- **„klient‑dostawca”** — jedni głównie sprzedają, inni głównie kupują;
- **scenariusze stresowe**:
  - skoki aktywności,
  - wyłączanie części uczestników (status `suspended`),
  - gwałtowne zmiany limitów na trustlines.

Parametry modeli:

- intensywność transakcji (ile płatności na sekundę/minutę/godzinę);
- rozkład kwot płatności;
- prawdopodobieństwo modyfikacji trustlines (podnoszenie/obniżanie limitów, zamykanie).

### 2.3. Sterowanie obciążeniem

> **MVP:** jeden „suwak” intensywności (0–100%) zamiast wielu profili.

Możliwość dynamicznego sterowania:

- łączną liczbą aktywnych „wirtualnych użytkowników”;
- **intensywnością symulacji**:

  - 0% — pauza;
  - 25% — spokojny dzień;
  - 50% — normalne obciążenie;
  - 75% — godzina szczytu;
  - 100% — test przeciążeniowy.

Dla trybu rozszerzonego:

- proporcje typów operacji:
  - % płatności,
  - % zmian trustlines,
  - % ręcznie inicjowanych clearingów.

### 2.4. Integracja z rzeczywistym API jądra

Symulator **nie** powinien manipulować stanem bezpośrednio w bazie danych, tylko używać takich samych interfejsów jak realne klienty:

- rejestracja uczestników przez API;
- tworzenie trustlines przez API;
- wysyłanie `PAYMENT_REQUEST` i oczekiwanie na wynik;
- śledzenie stanów transakcji (polling/WS).

Dzięki temu testujemy:

- prawdziwą warstwę protokołu i logiki,
- a nie tylko wewnętrzne funkcje.

---

## 3. Wizualizacja i analityka

Kluczowa część — **wizualne pokazanie stanu i dynamiki sieci**.

### 3.1. Graf sieci zaufania i długów

- **Węzły** — uczestnicy.
- **Krawędzie**:
  - trustlines (limity),
  - na nich nałożone bieżące długi.

Atrybuty wizualne:

- kolor i grubość krawędzi:
  - wielkość limitu;
  - stopień wykorzystania (dług/limit).
- Możliwość:
  - włączania/wyłączania:
    - linii zaufania (limity),
    - długów (realne zobowiązania);
  - podświetlania ścieżek konkretnej płatności;
  - oglądania, które krawędzie najczęściej występują w clearingu.

### 3.2. Metryki zagregowane w czasie

> **MVP:** 4–5 kluczowych metryk zamiast rozbudowanej tablicy.

**Kluczowe metryki:**

| Metryka                | Cel                                      |
|------------------------|------------------------------------------|
| Całkowity wolumen długu| Skala zadłużenia w sieci                |
| % udanych płatności    | Ocena „sprawności” protokołu            |
| Średnia długość ścieżki| Efektywność grafu zaufania               |
| Wolumen clearingu      | Jak dużo długu „znika” dzięki clearingowi|
| Top‑5 wąskich gardeł   | Wydajnościowe i ryzykowne węzły          |

**Metryki rozszerzone (po MVP):**

- rozkład długości ścieżek;
- przyczyny odrzuconych płatności:
  - brak ścieżki,
  - niewystarczający limit,
  - timeout 2PC;
- wykresy czasowe (time series), z możliwością odtwarzania.

### 3.3. Wskaźniki efektywności protokołu

- **Efektywność clearingu** — stosunek sumy „sklearingowanych” kwot do sumy wszystkich płatności;
- **Koncentracja długu** — które węzły stają się wąskimi gardłami (na nich koncentrują się długi);
- **Stabilność sieci** — jak często docieramy do stanu, w którym nowe płatności się nie udają głównie przez przeciążenie limitów.

### 3.4. Tryb „replay”

> ⚠️ **Nie na MVP** — wymaga złożonej serializacji stanu. Zaplanowane po podstawowej wersji.

- Możliwość nagrania przebiegu (lub jego fragmentu);
- Odtwarzanie z różną prędkością;
- Porównywanie zachowania różnych wersji protokołu/algorytmów dla tego samego scenariusza.

---

## 4. Konfiguracja i scenariusze „pod TZ”

Na potrzeby formalnych wymagań (TЗ) aplikacja może obsługiwać:

- **silnik scenariuszy**:
  - prosty język skryptowy / DSL lub opis w pliku konfiguracyjnym;
  - definiowanie faz, zdarzeń, polityk zachowań.

- **Parametry scenariuszy:**
  - generacja topologii sieci (liczba węzłów, gęstość, rozkład limitów);
  - profile zachowań (scenariusze 2.2);
  - harmonogram zdarzeń (np. co 10 minut zmiana parametrów).

- **Powtarzalność eksperymentów**:
  - stałe ziarno losowości (seed);
  - zapisywanie konfiguracji i wyników, by dało się eksperyment powtórzyć lub porównać z innym.

---

## 5. Koncepcja wizualizacji

Aplikacja wizualna może mieć dwa główne tryby:

1. **„Radar społeczno‑ekonomiczny”** — widok całej społeczności:
   - kto jest kim (typy, role),
   - kto z kim jest połączony,
   - jak przepływa wartość.

2. **„Pulpit uczestnika”** — widok w perspektywie pojedynczego użytkownika (lub roli):
   - moje linie zaufania,
   - moje długi i należności,
   - moje akcje (płatności, zmiany limitów, zgoda na clearing).

Oba tryby mogą być dostępne:

- w przeglądarce (web),
- w natywnym/wieloplatformowym kliencie.

---

## 6. Ekran „Mapa społeczności”

### 6.1. Kompozycja ekranu

Wideo:

- **centrum** — interaktywny graf:

  - węzły = uczestnicy,
  - krawędzie = relacje zaufania/długu.

- **lewy panel** — filtry i warstwy:

  - pokazywanie/ukrywanie:
    - linii zaufania,
    - długów;
  - wybór ekwiwalentu;
  - podstawowe filtry uczestników.

- **prawy panel** — szczegóły zaznaczonego elementu:

  - jeśli wybrany uczestnik: karta profilu, agregaty, możliwe akcje;
  - jeśli wybrane połączenie: szczegóły trustline i długu.

- **górny pasek** — tryby wizualizacji i status symulacji:

  - „Network” / „Metrics” / (w przyszłości) „Replay”,
  - wskaźniki połączenia i stanu symulacji.

### 6.2. Prezentacja uczestników (węzłów)

Każdy uczestnik to **ikona + etykieta**:

- **Kształt / ikona** ~ typ:

  | Typ           | Kształt      |
  |---------------|--------------|
  | `person`      | okrąg        |
  | `organization`| kwadrat      |
  | `hub`         | sześciokąt   |

- **Kolor** ~ status:

  | Status       | Kolor    |
  |--------------|----------|
  | `active`     | zielony  |
  | `suspended`  | żółty    |
  | `left`       | szary    |
  | „ryzykowny”  | np. czerwona ramka |

Po najechaniu: tooltip (nazwa, rola, liczba powiązań). Po kliknięciu: prawa sekcja z pełnymi informacjami.

### 6.3. Prezentacja połączeń (TrustLines i długi)

Pomiędzy uczestnikami:

- **TrustLines**:
  - cienkie linie ze strzałką `from → to`;
  - grubość lub nasycenie — w zależności od limitu.

- **Debts**:
  - grubsze, wyraźniejsze linie;
  - kierunek: od dłużnika do wierzyciela;
  - intensywność — wielkość długu.

Użytkownik może:

- włączać/wyłączać warstwę trustlines / długów;
- podświetlić wszystkie połączenia konkretnego węzła (ego‑graph).

---

## 7. Grupowanie uczestników

### 7.1. Po typach i rolach

Grupowanie wg:

- typu (`person`, `organization`, `hub`);
- roli (np. „koordynator”, „dostawca”, „klient hurtowy”).

Wizualnie:

- kolory / ikony;
- możliwość filtracji i podświetlania grup.

### 7.2. Po klastrach zaufania

> ⚠️ **MVP:** bez automatycznej klasteryzacji. Na początek tylko ręczne grupowanie wg typów (7.1).

W rozszerzonym trybie:

- automatyczne wykrywanie klastrów w grafie:

  - skupiska silnie powiązanych węzłów;
  - lokalne „społeczności w społeczności”.

Wizualizacja:

- węzły w klastrze z tym samym tłem/ramką;
- tryb „spłaszczania klastrów” do super‑węzłów.

### 7.3. Po parametrach finansowych

Filtry / gradienty:

- wg bilansu netto (plus/minus);
- wg aktywności (liczba operacji w okresie).

Pozwala zauważyć:

- wąskie gardła (węzły z wysokim ruchem i zadłużeniem);
- uczestników z dużym ryzykiem kredytowym.

---

## 8. Akcje użytkownika

### 8.1. Akcje na uczestniku (węźle)

**Zwykły uczestnik**, klikając innego uczestnika, może:

- **otworzyć profil** — zobaczyć dane publiczne i relacje (trustlines);
- **utworzyć/zmienić linię zaufania** — ustawić limit, wybrać ekwiwalent, politykę;
- **zainicjować płatność** — zadeklarować kwotę i ekwiwalent;
- **zobaczyć historię interakcji** — lista operacji `PAYMENT`, `TRUST_LINE_*`, `CLEARING`.

**Administrator/koordynator** dodatkowo:

- zmienić status (`suspended`, `left`);
- zobaczyć flagi ryzyka i anomalie;
- przejść do narzędzi zarządzania sporami.

### 8.2. Akcje na liniach zaufania i długach

Kliknięcie krawędzi:

- pokazuje:

  - parametry trustline `A→B`: limit, polityka, data utworzenia;
  - parametry długu `B→A`: suma, historia zmian.

**Zwykły uczestnik** (jeśli to jego trustline):

- może zmienić limit/politykę;
- może zamknąć trustline (zgodnie z regułami protokołu).

**Administrator**:

- ma wgląd w dodatkowe dane techniczne;
- może inicjować analitykę/korekty (w miarę zasad społeczności).

---

## 9. Tryb „Ścieżki płatności i clearing”

Dla transparentności:

- wybór konkretnej płatności (`tx_id`) podświetla **trasę**:

  - węzły i krawędzie udział biorące;
  - strzałki pokazujące kierunek przepływu długu.

**Clearing:**

- wybór transakcji `CLEARING`:

  - podświetla cykl (np. `A → B → C → A`);
  - przy każdej krawędzi: dług „przed” i „po” (ile zostało „sklearingowane”).

---

## 10. Tryb listy zagregowanej uczestników

Tabela / lista:

- kolumny:

  - nazwa,
  - typ,
  - bilans netto,
  - liczba trustlines wejściowych/wyjściowych,
  - wskaźniki aktywności / ryzyka.

Akcje:

- filtracja, sortowanie;
- kliknięcie — przejście do szczegółów uczestnika i podświetlenie na grafie;
- operacje grupowe (dla admina): eksport, powiadomienia itd.

---

## 11. Rekomendowany stos technologiczny

### 11.1. Główny stos (React)

Dla webowego narzędzia analityczno‑symulacyjnego React zapewnia:

- dojrzałe ekosystemy,
- wiele gotowych komponentów,
- bardzo dobrą obsługę przez agentów AI (React + TS to dzisiaj „język ojczysty” wielu modeli).

**Propozycja:**

| Warstwa      | Technologia                 |
|--------------|-----------------------------|
| Język        | TypeScript                  |
| UI           | React                       |
| UI‑kit       | MUI (Material UI)           |
| Graf sieci   | `react-force-graph-2d`      |
| Wykresy      | `Recharts`                  |
| API          | React Query (TanStack)      |
| Bundling     | Vite                        |

Ograniczenia:

- **Rozmiar grafu** — powyżej ~100 węzłów przełączać w tryb uproszczony (listy, agregaty).
- **Częstotliwość odświeżania** — throttling do 2–3 odświeżeń na sekundę dla płynności.
- Możliwość pracy offline na zapisanym snapshot’cie.

### 11.2. Alternatywa (Python)

Jeśli chcemy całkowicie uniknąć frontendu JS:

**Dash (Plotly Dash):**

- layout + callbacki w Pythonie;
- komponent `dash-cytoscape` do grafu;
- `plotly` do wykresów.

**Streamlit:**

- bardzo prosty w konfiguracji;
- dobre do prototypów i analizy ad‑hoc;
- dla złożonego grafu potrzeba pracy dodatkowej.

---

## 12. Specyfikacja UI ekranu „Mapa społeczności”

### 12.1. Struktura ogólna ekranu

Hierarchia:

```text
CommunityMapPage
├── TopBar
└── Layout
    ├── LeftSidebar
    ├── MainCanvas
    │   └── GraphView
    └── RightSidebar
```

### 12.2. Top Bar

**Komponent: `TopBar`**

Elementy:

- logo (`AppLogo`);
- zakładki trybów (`ViewModeTabs`): `'Network' | 'Metrics'`;
- wskaźnik połączenia / stanu symulacji (`SimulationStatusIndicator`);
- przyciski sterujące (`SimulationControls`): start/stop/pause, suwak prędkości.

Skróty klawiszowe:

| Klawisz | Akcja                      |
|---------|----------------------------|
| Space   | pauza / wznowienie        |
| R       | reset scenariusza         |
| + / -   | zmiana prędkości          |
| Esc     | odznaczenie zaznaczenia   |

### 12.3. Left Sidebar

**Komponent: `LeftSidebar`**

Sekcje:

- `QuickStartBanner` — propozycja wczytania demo‑scenariusza;
- `ScenarioSelector` — wybór scenariusza;
- `LayerToggles` — przełączniki warstw (trustlines, długi, ścieżki, klastry);
- `FiltersPanel` — filtry uczestników;
- `ExportButton` — eksport grafu (PNG/SVG).

### 12.4. Main Canvas

**Komponent: `GraphView`**

Typy:

```ts
type PID = string;

interface ParticipantNode {
  id: PID;
  name: string;
  type: 'person' | 'organization' | 'hub';
  status: 'active' | 'suspended' | 'left';
  netBalance: number;
  activityScore: number;
  clusterId?: string;
}

interface LinkEdge {
  id: string;
  from: PID;
  to: PID;
  kind: 'trustline' | 'debt';
  equivalent: string;
  limit?: number;
  amount?: number;
  utilization?: number;
}
```

Props:

- `nodes: ParticipantNode[]`
- `links: LinkEdge[]`
- `viewMode: 'trustlines' | 'debts' | 'combined'`
- `highlightedNodeId?: PID`
- `highlightedLinkId?: string`
- `onNodeClick(nodeId: PID)`
- `onLinkClick(linkId: string)`
- `onBackgroundClick()`

### 12.5. Right Sidebar

**Komponent: `RightSidebar`**

Widoki:

- `NodeDetailsPanel` — gdy zaznaczony węzeł;
- `LinkDetailsPanel` — gdy zaznaczone połączenie;
- `EmptySelectionPanel` — gdy nic nie zaznaczono.

`NodeDetailsPanel` zawiera:

1. Nagłówek z profilem.
2. Kluczowe metryki (bilans, liczba trustlines).
3. Listę powiązań (zakładki: TrustLines / Debts).
4. Akcje (utworzenie trustline, inicjowanie płatności, widok historii).

`LinkDetailsPanel`:

1. Tytuł (`A → B`, typ).
2. Pola główne (limit/amount, polityka).
3. Sparkline zmienności w czasie.
4. Akcje (edycja limitu/polityki, zamknięcie linii).

### 12.6. Stany ekranu jako całości

- **Brak danych** — „empty state” z sugestią wczytania scenariusza.
- **Ładowanie** — skeletony bądź loader.
- **Błąd** — baner błędu i stan `SimulationStatusIndicator = 'error'`.

---

## Podsumowanie

Ten dokument opisuje pełny symulator‑wizualizator sieci GEO.  
Dla **MVP** wystarczy:

1. Strona z grafem uczestników,
2. Panel szczegółów po prawej,
3. Przycisk „start symulacji” (np. scenariusz „random market”),
4. 3–4 podstawowe metryki w czasie rzeczywistym.

Reszta sekcji stanowi rozszerzenia, które mogą być dodawane etapami, w miarę potrzeb społeczności i rozwoju protokołu GEO.
