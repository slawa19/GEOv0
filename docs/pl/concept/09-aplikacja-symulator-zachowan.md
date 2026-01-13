# GEO — Aplikacja‑symulator zachowań użytkowników

> Specyfikacja aplikacji do symulacji, testowania i wizualizacji sieci GEO

---

## Spis treści

1. [Wprowadzenie](#1-wprowadzenie)  
2. [Główny zakres funkcjonalny symulatora](#2-główny-zakres-funkcjonalny-symulatora)  
   - 2.1. [Konfiguracja „świata”](#21-konfiguracja-świata-generacja-sieci)  
   - 2.2. [Scenariusze zachowań](#22-scenariusze-zachowań-modele-interakcji)  
   - 2.3. [Sterowanie obciążeniem](#23-sterowanie-obciążeniem)  
   - 2.4. [Integracja z API jądra](#24-integracja-z-rzeczywistym-api-jądra)  
3. [Wizualizacja i analityka](#3-wizualizacja-i-analityka)  
   - 3.1. [Graf sieci zaufania i długów](#31-graf-sieci-zaufania-i-długów)  
   - 3.2. [Metryki zagregowane](#32-metryki-zagregowane-w-czasie)  
   - 3.3. [Wskaźniki efektywności protokołu](#33-wskaźniki-efektywności-protokołu)  
   - 3.4. [Tryb „replay”](#34-tryb-replay)  
4. [Konfiguracja i scenariusze „pod specyfikację”](#4-konfiguracja-i-scenariusze-pod-specyfikację)  
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
13. [Integracja AI z symulatorem scenariuszy i zachowań](#13-integracja-ai-z-symulatorem-scenariuszy-i-zachowań)  

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
  - średnią długość tras i częstotliwość odrzuconych płatności;
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
    { "id": "p2", "name": "Kawiarnia", "type": "business" }
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

- **wczytywać gotowe scenariusze JSON** (tryb podstawowy dla MVP);
- w trybie rozszerzonym — generować automatycznie sieć według parametrów (po MVP).

### 2.2. Scenariusze zachowań — modele interakcji

Modele opisujące, jak uczestnicy działają w czasie:

- **„losowy rynek”** — uczestnicy losowo wybierają kontrahentów i dokonują płatności;
- **„wymiana klastrowa”** — intensywniejsza wymiana w obrębie podgrup, rzadsza między nimi;
- **„klient‑dostawca”** — jedni głównie sprzedają, inni głównie kupują;
- **scenariusze stresowe**:
  - skoki aktywności;
  - wyłączanie części uczestników (status `suspended`);
  - gwałtowne zmiany limitów na trustlines.

Modele powinny mieć możliwość konfiguracji:

- intensywności transakcji (ile płatności na sekundę/minutę/godzinę);
- rozkładu kwot płatności;
- prawdopodobieństwa modyfikacji trustlines (podnoszenie/obniżanie limitów, zamykanie).

### 2.3. Sterowanie obciążeniem

> **MVP:** jeden „suwak” intensywności (0–100%) zamiast wielu profili.

Możliwości:

- dynamiczna zmiana łącznej liczby aktywnych „wirtualnych użytkowników”;
- **intensywność symulacji** — jeden suwak 0–100%:
  - 0% — pauza symulacji;
  - 25% — spokojny dzień;
  - 50% — normalne obciążenie;
  - 75% — godzina szczytu;
  - 100% — test przeciążeniowy.

W trybie rozszerzonym:

- proporcje typów operacji:
  - % płatności,
  - % zmian trustlines,
  - % ręcznie inicjowanych clearingów.

### 2.4. Integracja z rzeczywistym API jądra

Symulator **nie** powinien manipulować stanem bezpośrednio w bazie danych — korzysta z tych samych interfejsów, co realne klienty:

- rejestracja uczestników przez API;
- tworzenie trustlines przez API;
- wysyłanie `PAYMENT_REQUEST` i oczekiwanie wyniku;
- monitorowanie stanów transakcji.

Dzięki temu testujemy:

- rzeczywistą warstwę protokołu i logiki,
- a nie jedynie wewnętrzne metody.

---

## 3. Wizualizacja i analityka

Kluczowa część — **wizualne pokazanie stanu i dynamiki sieci**.

### 3.1. Graf sieci zaufania i długów

- **Węzły** — uczestnicy;
- **Krawędzie**:
  - trustlines (limity),
  - na nich nałożone bieżące długi.

Atrybuty wizualne:

- kolor i grubość krawędzi:
  - wielkość limitu;
  - stopień wykorzystania (dług/limit).

Możliwości:

- włączanie/wyłączanie:
  - linii zaufania;
  - długów;
- podświetlanie tras konkretnych płatności;
- podgląd, które krawędzie najczęściej występują w clearingu.

### 3.2. Metryki zagregowane w czasie

> **MVP:** 4–5 kluczowych metryk zamiast rozbudowanej tabeli.

**Kluczowe metryki:**

| Metryka                  | Cel                                      |
|--------------------------|------------------------------------------|
| Całkowity wolumen długu  | Skala zadłużenia w sieci                |
| % udanych płatności      | Ocena „sprawności” protokołu            |
| Średnia długość ścieżki  | Efektywność grafu zaufania              |
| Wolumen clearingu        | Jak dużo długu „znika” w clearingu      |
| Top‑5 wąskich gardeł     | Wydajnościowe i ryzykowne węzły         |

**Metryki rozszerzone (po MVP):**

- rozkład długości ścieżek;
- przyczyny odrzuconych płatności:
  - brak ścieżki;
  - niewystarczający limit;
  - timeout 2PC;
- wykresy czasowe, z możliwością odtwarzania.

### 3.3. Wskaźniki efektywności protokołu

- **Efektywność clearingu** — stosunek sumy sklearingowanych kwot do sumy wszystkich płatności;
- **Koncentracja długu** — które węzły stają się wąskimi gardłami (duża koncentracja długów);
- **Stabilność sieci** — jak często docieramy do stanu, w którym nowe płatności nie przechodzą z powodu przeciążenia limitów na krytycznych krawędziach.

### 3.4. Tryb „replay”

> ⚠️ **Nie na MVP** — wymaga złożonej serializacji stanu, planowany po wdrożeniu podstaw.

- Możliwość nagrania przebiegu (lub jego fragmentu);
- odtwarzanie z różną prędkością;
- porównywanie zachowania różnych wersji protokołu/algorytmów.

---

## 4. Konfiguracja i scenariusze „pod specyfikację”

Na potrzeby formalnych specyfikacji aplikacja może wspierać:

- **Silnik scenariuszy:**
  - prosty język skryptowy / DSL lub opis w pliku konfiguracyjnym;
  - definiowanie faz, zdarzeń, polityk zachowań.

- **Parametry scenariusza:**
  - generacja topologii sieci (liczba węzłów, gęstość, rozkład limitów);
  - profile zachowań (z 2.2);
  - harmonogram zdarzeń (np. co 10 minut zmiana parametrów).

- **Powtarzalność eksperymentów:**
  - stałe ziarno losowości (seed);
  - zapisywanie konfiguracji i wyników do późniejszego odtworzenia lub porównania.

- **Obsługa różnych wersji backendu GEO:**
  - możliwość uruchamiania symulacji przeciwko różnym wersjom jądra (np. v0.1, v0.2).

---

## 5. Koncepcja wizualizacji

Aplikacja wizualna może mieć dwa główne tryby:

1. **„Radar społeczno‑ekonomiczny”** — widok całej społeczności:
   - kto jest kim (typ, rola),
   - kto z kim ma relacje zaufania,
   - jak przepływa wartość.

2. **„Pulpit uczestnika”** — widok z perspektywy pojedynczego użytkownika (lub roli):
   - moje linie zaufania,
   - moje długi i należności,
   - moje działania (płatności, zmiany limitów, zgoda na clearing itd.).

Oba tryby dostępne:

- w przeglądarce (web);
- w kliencie natywnym/wieloplatformowym.

---

## 6. Ekran „Mapa społeczności”

### 6.1. Kompozycja ekranu

- **Centrum** — interaktywny graf:  
  węzły = uczestnicy, krawędzie = relacje zaufania/długu.

- **Lewa kolumna** — filtry i warstwy:
  - pokazuj/ukrywaj:
    - linie zaufania,
    - długi;
  - wybór ekwiwalentu;
  - filtrowanie uczestników.

- **Prawa kolumna** — szczegóły zaznaczonego elementu:
  - jeśli wybrany uczestnik: profil, agregaty, dostępne akcje;
  - jeśli wybrane połączenie: trustline + dług.

- **Top Bar** — tryby wizualizacji i status symulacji:
  - „Network” / „Metrics” / (później) „Replay”;
  - wskaźniki połączenia i stanu.

### 6.2. Prezentacja uczestników (węzłów)

Każdy uczestnik to **ikona + etykieta**:

- **Kształt / ikona** odzwierciedla typ:

  | Typ           | Kształt    |
  |---------------|------------|
  | `person`      | koło       |
  | `business`| kwadrat    |
  | `hub`         | sześciokąt |

- **Kolor** odzwierciedla status:

  | Status       | Kolor       |
  |--------------|-------------|
  | `active`     | zielony     |
  | `suspended`  | żółty       |
  | `left`       | szary       |
  | „ryzykowny”  | np. czerwona ramka |

Po najechaniu: tooltip (nazwa, rola, liczba powiązań).  
Po kliknięciu: szczegóły w prawym panelu.

### 6.3. Prezentacja połączeń (TrustLines i długi)

Pomiędzy uczestnikami:

- **TrustLines**:
  - cienkie linie ze strzałką `from → to`;
  - grubość lub nasycenie = wielkość limitu.

- **Długi**:
  - grubsze, bardziej wyraziste linie;
  - kierunek: od dłużnika do wierzyciela;
  - intensywność = rozmiar długu.

Użytkownik może:

- przełączać widok „tylko trustlines” / „tylko długi”;
- podświetlić ego‑graf (wszystkie połączenia wybranego węzła).

---

## 7. Grupowanie uczestników

### 7.1. Po typach i rolach

Grupowanie wg:

- typu (`person`, `business`, `hub`);
- roli (np. „koordynator”, „dostawca usług”, „klient hurtowy”).

Prezentacja:

- kolorami / ikonami;
- z możliwością filtrowania i podświetlania grup.

### 7.2. Po klastrach zaufania

> ⚠️ **MVP:** bez automatycznej klasteryzacji; tylko ręczne grupowanie wg typów (7.1).

W wersji rozszerzonej:

- automatyczne wykrywanie klastrów:

  - grupy silnie powiązanych węzłów,
  - „społeczności w społeczności”.

Wizualnie:

- węzły klastra z tym samym tłem/ramką;
- tryb „zwijania klastrów” do super‑węzłów.

### 7.3. Po parametrach finansowych

Filtry / gradienty:

- wg bilansu netto (dłużnik/kredytor);
- wg aktywności (liczba operacji).

Pozwala to:

- wykryć wąskie gardła;
- zidentyfikować uczestników o wysokim ryzyku kredytowym.

---

## 8. Akcje użytkownika

### 8.1. Akcje na uczestniku (węźle)

**Zwykły uczestnik**, klikając innego uczestnika, może:

- **otworzyć panel profilu** — zobaczyć dane publiczne i relacje;
- **utworzyć/zmienić linię zaufania** — ustawić limit, wybrać ekwiwalent, politykę;
- **zainicjować płatność** — podać kwotę i ekwiwalent, zobaczyć wstępną ścieżkę;
- **zobaczyć historię interakcji** — lista płatności, zmian trustlines.

**Administrator/koordynator** dodatkowo:

- zmienić status (`suspended`, `left`);
- zobaczyć sygnały ryzyka i anomalie;
- przejść do narzędzi rozwiązywania sporów.

### 8.2. Akcje na liniach zaufania i długach

Kliknięcie krawędzi:

- pokazuje:

  - szczegóły trustline `A→B`: limit, polityka, data utworzenia/zmiany;
  - szczegóły długu `B→A`: kwota, historia.

**Zwykły uczestnik** (jeśli to jego trustline):

- może zmienić limit/politykę;
- może zamknąć linię zaufania (zgodnie z zasadami protokołu).

**Administrator**:

- widzi dodatkowe dane techniczne;
- może inicjować analitykę i korekty.

---

## 9. Tryb „Ścieżki płatności i clearing”

Dla przejrzystości pracy protokołu:

- wybór konkretnej płatności (`tx_id`) podświetla **trasę**:
  - węzły i krawędzie;
  - strzałki pokazujące przepływ długu.

**Tryb wizualizacji clearingu**:

- wybór transakcji `CLEARING`:

  - podświetla cykl (np. `A → B → C → A`);
  - przy każdej krawędzi: dług „przed” i „po”.

---

## 10. Tryb listy zagregowanej uczestników

Tabela / lista:

- kolumny:

  - nazwa,
  - typ,
  - bilans netto,
  - liczba wejściowych/wyjściowych trustlines,
  - wskaźniki aktywności i ryzyka.

Akcje:

- filtrowanie, sortowanie;
- kliknięcie → szczegóły uczestnika + podświetlenie na grafie;
- operacje zbiorcze (dla adminów): eksport, wysyłka powiadomień.

---

## 11. Rekomendowany stos technologiczny

### 11.1. Główny stos (React)

Dla przeglądarkowego narzędzia analityczno‑symulacyjnego:

- dojrzały ekosystem;
- wiele gotowych komponentów;
- bardzo dobra obsługa przez agentów AI (React + TS).

**Propozycja:**

| Warstwa       | Technologia           |
|---------------|-----------------------|
| Język         | TypeScript            |
| UI            | React                 |
| UI‑kit        | MUI (Material UI)     |
| Graf sieci    | `react-force-graph-2d`|
| Wykresy       | `Recharts`            |
| Warstwa API   | React Query (TanStack)|
| Bundling      | Vite                  |

Ograniczenia:

- **Rozmiar grafu** — powyżej ok. 100 węzłów przechodzić w tryb uproszczony (agregaty, listy).
- **Częstotliwość odświeżania** — throttling do 2–3 odświeżeń/s.
- **Offline** — praca na zapisanym snapshot’cie.

### 11.2. Alternatywa (Python)

Jeśli chcemy uniknąć JS:

**Dash (Plotly Dash):**

- layout + callbacki w Pythonie;
- `dash-cytoscape` do grafu;
- `plotly` do wykresów.

**Streamlit:**

- bardzo prosty w konfiguracji;
- świetny do prototypów i analizy ad‑hoc;
- dla złożonego grafu wymaga więcej pracy.

---

## 12. Specyfikacja UI ekranu „Mapa społeczności”

Kompaktowa, ale szczegółowa specyfikacja (React/Flutter).

### 12.1. Struktura ogólna ekranu

Ekran podzielony na 4 główne obszary (funkcje AI są wbudowane w istniejący układ, nie przeciążając Main Canvas):

1. **Top Bar** — nawigacja, tryby, status symulacji oraz szybkie akcje AI (wyjaśnij, zaproponuj test stresowy, otwórz panel promptów).
2. **Left Sidebar** — filtry, warstwy, wybór scenariusza/symulacji oraz **panel scenariuszy AI**: wprowadzanie tekstu naturalnego, podgląd wygenerowanych scenariuszy i patchy, historia zapytań.
3. **Main Canvas** — centralne płótno z grafem uczestników, z nałożonymi owerlejami AI (wąskie gardła, obszary ryzyka, podgląd scenariuszy stresowych).
4. **Right Sidebar** — panel szczegółów zaznaczonego elementu, w tym zakładka **„AI Insight”** z lokalnymi wyjaśnieniami i proponowanymi działaniami.

```text
CommunityMapPage
├── TopBar
└── Layout
    ├── LeftSidebar
    │   └── AiScenarioPanel
    ├── MainCanvas
    │   └── GraphView
    └── RightSidebar
        └── DetailsPanels (w tym AI Insight)
```

### 12.2. Top Bar

**Komponent: `TopBar`**

| Element           | Komponent                   | Stany                                  |
|-------------------|-----------------------------|----------------------------------------|
| Logo              | `AppLogo`                  | normalne, kompaktowe                   |
| Tryby widoku      | `ViewModeTabs`             | `activeTab: 'Network' \| 'Metrics'` (⚠️ Replay — nie na MVP) |
| Status            | `SimulationStatusIndicator`| `connectionStatus`, `simulationStatus` |
| Sterowanie sym.   | `SimulationControls`       | Start/Stop/Pause/Resume/Speed          |
| Szybkie akcje AI  | `AiQuickActions`           | `canExplain`, `canSuggestStress`, `isBusy` |

**`AiQuickActions` — przykładowe przyciski:**

- `ExplainCurrentView` — poproś AI o wyjaśnienie aktualnego stanu widocznego fragmentu grafu (`/ai/explain-current-state`).
- `SuggestStressScenarios` — poproś AI o 1–3 scenariusze stresowe dla bieżącego świata (`/ai/generate-stress-scenarios`).
- `OpenAiPanel` — otwórz panel scenariuszy AI w lewym sidebarze i ustaw fokus w polu tekstowym.

**Skróty klawiszowe:**

| Klawisz | Akcja                                                      |
|---------|------------------------------------------------------------|
| Space   | pauza / wznowienie symulacji                              |
| R       | reset scenariusza                                          |
| + / -   | zwiększenie/zmniejszenie prędkości                         |
| Esc     | usunięcie zaznaczenia / zamknięcie paneli modalnych       |
| E       | poproś AI o wyjaśnienie bieżącego stanu (`ExplainCurrentView`) |
| T       | poproś AI o scenariusze stresowe (`SuggestStressScenarios`) |

### 12.3. Left Sidebar

**Komponent: `LeftSidebar`**

Sekcje:

- `QuickStartBanner` — propozycja wczytania scenariusza demo przy pierwszym uruchomieniu.
- `ScenarioSelector` — wybór scenariusza (w tym generowanych przez AI).
- `AiScenarioPanel` — panel interakcji z „pisarzem scenariuszy AI”.
- `LayerToggles` — przełączniki warstw.
- `FiltersPanel` — filtry uczestników.
- `ExportButton` — eksport grafu (PNG/SVG).

**`AiScenarioPanel` zawiera:**

- `AiPromptInput` — wielowierszowe pole tekstowe:
  - placeholder: „Opisz świat, zachowania lub zmiany, które chcesz zasymulować…”
  - przyciski:
    - `GenerateScenario` — stworzenie nowego scenariusza od zera;
    - `ApplyPatch` — zastosowanie zmian do bieżącego scenariusza.
- `AiSummaryBox` — krótkie podsumowanie ostatniej odpowiedzi AI:
  - liczba uczestników/klastrów;
  - główne profile zachowań i ich udział;
  - opis proponowanych zmian/zdarzeń.
- `AiRequestsHistory` — lista ostatnich 3–5 zapytań/odpowiedzi:
  - kliknięcie pozycji → podgląd diff JSON / podgląd na grafie.
- `AiStatusBar` — stan:
  - `idle` / `thinking` / `error`, z komunikatem w razie błędu.

**Stan `AiScenarioPanel`:**

```ts
ai.state: 'idle' | 'thinking' | 'error';

ai.lastRequest?: {
  text: string;
  type: 'scenario' | 'patch';
  createdAt: string;
};

ai.lastResultSummary?: {
  participantsCount: number;
  groupsCount: number;
  mainProfiles: Array<{ id: string; share: number }>;
  mainNotes: string[];
};
```

**`LayerToggles` — stan:**

```ts
showTrustLines: boolean;
showDebts: boolean;
showPaymentRoutes: boolean;
showClusters: boolean;
showAiOverlays: boolean; // owerleje generowane przez AI
```

**`FiltersPanel` — stan:**

```ts
filter.types: { person: boolean; business: boolean; hub: boolean };
filter.statuses: { active: boolean; suspended: boolean; left: boolean };
filter.equivalentCode: string | 'ANY';
filter.netBalanceRange: [number, number];
filter.activityRange: [number, number];
filter.aiFlags?: {
  bottleneckOnly?: boolean;      // tylko „wąskie gardła” wg AI
  overTrustedOnly?: boolean;     // „nadmiernie ufne” węzły
  stressAffectedOnly?: boolean;  // uczestnicy dotknięci scenariuszem stresowym
};
```

### 12.4. Main Canvas

**Komponent: `GraphView`**

Nakładka na np. `react-force-graph` z obsługą owerleji AI:

```ts
type PID = string;

interface AiFlags {
  bottleneck?: boolean;      // wąskie gardło wg AI
  overTrusted?: boolean;     // „zbyt ufny” węzeł
  stressAffected?: boolean;  // dotknięty scenariuszem stresowym
}

interface ParticipantNode {
  id: PID;
  name: string;
  type: 'person' | 'business' | 'hub';
  status: 'active' | 'suspended' | 'left';
  netBalance: number;
  activityScore: number;
  clusterId?: string;
  aiFlags?: AiFlags;
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
  aiFlags?: {
    bottleneck?: boolean;
    stressAffected?: boolean;
  };
}

interface AiHighlight {
  nodeIds?: PID[];
  linkIds?: string[];
  reason?: string; // krótkie objaśnienie do legendy / tooltipa
}
```

**Props:**

- `nodes: ParticipantNode[]`
- `links: LinkEdge[]`
- `viewMode: 'trustlines' | 'debts' | 'combined'`
- `highlightedNodeId?: PID`
- `highlightedLinkId?: string`
- `aiHighlight?: AiHighlight`
- `onNodeClick(nodeId: PID)`
- `onLinkClick(linkId: string)`
- `onBackgroundClick()`
- `onAreaSelect?(bounds: { x1: number; y1: number; x2: number; y2: number })`

**Stany wizualne węzłów:**

| Typ           | Kształt   |
|---------------|-----------|
| `person`      | koło      |
| `business`    | kwadrat   |
| `hub`         | sześciokąt|

| Status       | Kolor  |
|--------------|--------|
| `active`     | zielony|
| `suspended`  | żółty  |
| `left`       | szary  |

Dodatkowe flagi AI:

- `bottleneck` — grubsza ramka, ikona „⚠”;
- `overTrusted` — dodatkowa obwódka;
- `stressAffected` — półprzezroczyste wypełnienie lub pulsowanie przy podglądzie planu.

### 12.5. Right Sidebar

**Komponent: `RightSidebar`**

Podkomponenty:

- `NodeDetailsPanel` — gdy zaznaczony węzeł;
- `LinkDetailsPanel` — gdy zaznaczone połączenie;
- `AiInsightPanel` — zakładka z wyjaśnieniem AI i rekomendacjami;
- `EmptySelectionPanel` — gdy nic nie wybrano.

**`NodeDetailsPanel` — struktura:**

1. Nagłówek z profilem (nazwa, typ, status).
2. Kluczowe metryki (bilans, liczba trustlines).
3. Lista połączeń (zakładki: TrustLines / Debts).
4. Akcje (Create TrustLine, Initiate Payment, View History).
5. Wskaźniki ryzyka wg AI (jeśli `aiFlags`):

   - „Bottleneck”, „Over‑trust”, „Stress‑affected”.

**`LinkDetailsPanel` — struktura:**

1. Tytuł (`A → B`, typ).
2. Pola główne (limit/amount, polityka).
3. Wykres zmian (sparkline).
4. Akcje (Edit Limit, Edit Policy, Close TrustLine).
5. Flagi AI (krytyczna krawędź, wąskie gardło itd.).

**`AiInsightPanel` — struktura:**

1. Nagłówek (`AI Insight for [node/edge/area]`).
2. Tekstowe wyjaśnienie od AI (`/ai/explain-current-state`).
3. Lista czynników:
   - udział w obciążeniu;
   - rola w sukcesach/porażkach;
   - udział w scenariuszach stresowych.
4. **Proponowane plany działań AI** (jeśli dostępne, patrz 13.7):
   - karty z opisem i przewidywanym efektem;
   - przyciski: „Uruchom symulację planu” i „Zastosuj do bieżącej symulacji”.
5. Rekomendacje ręczne:

   - „Zmniejsz limity na tych trustlines”;
   - „Dodaj alternatywne ścieżki z pominięciem tego węzła”;
   - „Zmień profile zachowań części uczestników” (link do `AiScenarioPanel`).

### 12.6. Stany ekranu jako całości

| Stan                  | Zachowanie |
|-----------------------|------------|
| Empty/No‑Data         | GraphView pokazuje stan pusty, `AiScenarioPanel` sugeruje wczytanie demo lub wygenerowanie scenariusza przez AI |
| Loading               | Skeletony / loader, `SimulationStatusIndicator = 'loading'` |
| Error                 | Baner błędu, `SimulationStatusIndicator = 'error'`; `AiScenarioPanel` może pokazywać błąd AI/walidacji |
| AI request in progress| `AiScenarioPanel` i `AiQuickActions` w stanie `thinking`, część przycisków zablokowana |
| AI overlay active     | Podświetlenia wg `aiHighlight`, legenda/Right Sidebar opisuje typ owerleja (wąskie gardła, stres itd.) |
| Action plan selected  | `AiInsightPanel` pokazuje szczegóły planu, `MainCanvas` może wizualizować podgląd patcha przed zastosowaniem |

---

## 13. Integracja AI z symulatorem scenariuszy i zachowań

> Cel: pozwolić użytkownikowi **po ludzku** opisać społeczność, zachowania i „przekosy” (biasy), a AI zamieni to w formalny scenariusz. Dodatkowo: generować wyjaśnienia, scenariusze stresowe oraz **konkretne plany antykryzysowe**.

### 13.1. Rola AI i architektura ogólna

AI nie zastępuje jądra symulatora, a działa jak **„kompilator scenariuszy”**:

- wejście: opis naturalnym językiem (prompt);
- wyjście: scenariusz JSON/DSL:

  - uczestnicy;
  - trustlines;
  - profile zachowań;
  - zdarzenia i scenariusze stresowe.

**Komponenty:**

1. **Simulation Core** (opisany wcześniej):
   - czyta konfigurację (JSON/DSL);
   - uruchamia symulację (tick‑based / event‑based);
   - rozmawia z prawdziwym API GEO.

2. **Scenario Config Store**:
   - przechowuje scenariusze i wersje (`scenarioId`, `version`);
   - przechowuje seedy losowości;
   - metadane: autor, prompt użytkownika, model AI.

3. **AI Scenario & Behavior Engine (nowa usługa)**:
   - REST/gRPC:
     - `POST /ai/scenario-from-text`;
     - `POST /ai/patch-from-text`;
     - `POST /ai/explain-current-state`;
     - `POST /ai/generate-stress-scenarios`;
     - `POST /ai/recommend-crisis-actions`.
   - wewnątrz: LLM + warstwa post‑processingu pilnująca schematu.

4. **UI (klient React)**:
   - panel asystenta AI:
     - pole tekstowe;
     - przyciski: „Generuj scenariusz”, „Zastosuj zmiany”, „Wyjaśnij stan”, „Zaproponuj test stresowy”, „Zaproponuj plan wyjścia z kryzysu”;
   - reszta UI jak w sekcjach 6–12.

### 13.2. Format scenariusza (DSL/JSON) dla AI

Do wiarygodnej integracji potrzebny jest prosty format scenariusza (jak w przykładach powyżej): `participants`, `trustlines`, `behaviorProfiles`, `groups`, `events`.

### 13.3. Przepływy UX z AI

- **Tworzenie scenariusza od zera:** użytkownik opisuje świat tekstowo → `scenario-from-text` → podsumowanie + graf → „Start” symulacji.
- **Modyfikacje w locie:** użytkownik formułuje instrukcje typu „usuń ten hub”, „wzmocnij zaufanie wewnątrz dzielnicy B” → `patch-from-text` → patch JSON → nowa wersja scenariusza.

### 13.4. Patch’e z tekstu (dynamiczne modyfikacje)

Opisane jako logiczne filtry i operacje (`scaleLimitBy`, zmiany profili zachowań), następnie rozwijane na konkretne węzły/krawędzie.

### 13.5. Wyjaśnianie stanu („dlaczego tu jest wąskie gardło?”)

AI na podstawie `snapshot` + pytania generuje:

- tekstową diagnozę (które węzły/krawędzie są krytyczne i dlaczego);
- powiązanie z metrykami (`successRate`, `avgRouteLength`, itp.).

### 13.6. Automatyczna generacja scenariuszy stresowych

AI proponuje zestaw scenariuszy:

- „Liquidity shock”,
- „Panic episode”,
- „Hub failure”,
- „Credit crunch”,

każdy z opisem + patch’em wydarzeń (`events[]`).

### 13.7. Rekomendacje działań antykryzysowych

AI nie tylko opisuje problem, ale także:

- generuje **kilka planów działań** (`actionPlans[]`),
- do każdego planu:
  - patch (`trustlines`, `behaviorProfiles`, `participants`);
  - `expectedImpact` (prognoza metryk);
  - `rationale` (uzasadnienie);
  - opcjonalny `simulationPreviewId` (przegląd „what‑if”).

UI:

- pokazuje plany w `AiInsightPanel` oraz na zakładce „Metrics”;
- pozwala:
  - odpalić symulację planu;
  - (po potwierdzeniu) zastosować patch do bieżącej symulacji.

AI **nigdy** nie stosuje patchy automatycznie — zawsze wymaga świadomej decyzji użytkownika.

### 13.8. Minimalny plan wdrożenia AI

1. Zdefiniować i udokumentować schemat scenariusza (`participants`, `trustlines`, `behaviorProfiles`, `groups`, `events`).
2. Dodać w backendzie API scenariuszy (`/scenario`, `/scenario/:id/patch`, `/run`).
3. Uruchomić prostą usługę AI z `scenario-from-text`.
4. Dodać panel scenariuszy AI w UI.
5. Rozszerzyć usługę AI o `patch-from-text`, `explain-current-state`, `generate-stress-scenarios`, `recommend-crisis-actions`.
6. Rozbudować UI o:
   - szybkie przyciski AI,
   - wyświetlanie wyników AI jako owerleje, filtry, wyjaśnienia,
   - integrację planów działań z `AiInsightPanel` i widokiem metryk.

---

## Podsumowanie

Dokument opisuje pełny symulator‑wizualizator sieci GEO. Dla **MVP** wystarczy:

1. Strona z grafem uczestników,
2. Panel szczegółów po prawej,
3. Przycisk „Start symulacji” (np. scenariusz „random market”),
4. 3–4 podstawowe metryki w czasie rzeczywistym.

Kolejnym krokiem jest warstwa integracji z AI (sekcja 13), która umożliwia:

- opis świata i zachowań językiem naturalnym,
- automatyczną generację i modyfikację scenariuszy,
- otrzymywanie wyjaśnień stanu sieci i wąskich gardeł,
- budowanie scenariuszy stresowych dla testów odporności,
- a także **otrzymywanie konkretnych planów antykryzysowych**, które można bezpiecznie przetestować w symulacji i częściowo przenieść do realnych polityk protokołu GEO.
