# Protokół GEO: Przegląd projektu

**Wersja:** 0.1  
**Data:** Listopad 2025

---

## Czym jest GEO

**GEO** to otwarty protokół zdecentralizowanej sieci kredytowej do organizacji ekonomii wzajemnego kredytu wewnątrz społeczności lokalnych i między nimi.

### Kluczowa idea

Zamiast używania tradycyjnych pieniędzy jako pośrednika, uczestnicy sieci GEO:
- Otwierają sobie nawzajem **linie zaufania** (limity kredytowe)
- Dokonują **płatności** przez sieć zaufania (poprzez łańcuchy połączeń)
- Automatycznie **wzajemnie rozliczają długi** w zamkniętych cyklach (kliring)

### Czym to NIE jest

- **Nie kryptowaluta** — brak tokenów GEO, miningu lub spekulacji
- **Nie blockchain** — brak globalnego rejestru wszystkich transakcji
- **Nie bank** — hub nie posiada środków i nie podejmuje decyzji za uczestników

---

## Jakie problemy rozwiązuje

### 1. Niedobór płynności „na miejscu"

**Problem:** Istnieją wzajemne potrzeby i możliwości, ale brak „pieniędzy" jako pośrednika.

**Rozwiązanie:** GEO pozwala na wymianę opartą na wzajemnym zaufaniu, nie czekając na „zewnętrzne" pieniądze.

### 2. Odpływ wartości z lokalnych ekonomii

**Problem:** Pieniądze wydane w lokalnym biznesie szybko wypływają do banków, korporacji, w inne regiony.

**Rozwiązanie:** Zobowiązania w GEO pozostają w społeczności i są spłacane lokalnymi towarami/usługami.

### 3. Oprocentowanie kredytu jako bariera

**Problem:** Oprocentowanie kredytów czyni wiele projektów nierentownymi.

**Rozwiązanie:** Wzajemny kredyt w GEO jest bezoprocentowy z natury.

### 4. Centralizacja i podatność na awarie

**Problem:** Zależność od banków i regulatorów tworzy punkty awarii.

**Rozwiązanie:** Każda społeczność zarządza swoim węzłem; federacja społeczności — bez centrum.

---

## Dla kogo jest przeznaczone

### Segmenty docelowe

| Segment | Przykłady zastosowania |
|---------|----------------------|
| **Społeczności lokalne** | Spółdzielnie, zrzeszenia mieszkańców, komuny |
| **Małe przedsiębiorstwa** | Klastry dostawców i klientów, wzajemne rozliczenia B2B |
| **Sieci profesjonalne** | Freelancerzy, rzemieślnicy, konsultanci |
| **Ekonomie alternatywne** | Banki czasu, systemy LETS, waluty lokalne |

### Skala

- **MVP:** 10–500 uczestników w jednej społeczności
- **Rozwój:** do 1–2 tysięcy uczestników na hub
- **Federacja:** połączenie kilku społeczności przez połączenia międzyhubowe

---

## Kluczowe możliwości

### Realizowane w MVP (v0.1)

| Możliwość | Opis |
|-------------|----------|
| **Tożsamość kryptograficzna** | Rejestracja z kluczami Ed25519 |
| **Linie zaufania** | Jednostronne limity kredytowe między uczestnikami |
| **Płatności przez sieć** | Routing po łańcuchach zaufania |
| **Automatyczny kliring** | Wzajemne rozliczenie długów w cyklach 3–6 węzłów |
| **Płatności multi-path** | Dzielenie płatności na 2–3 trasy |
| **Ekwiwalenty** | Dowolne jednostki rozrachunku (hrywna, godzina, kWh) |

### Planowane możliwości

| Możliwość | Opis |
|-------------|----------|
| **Wymiana międzyhubowa** | Płatności między uczestnikami różnych społeczności |
| **Klastrowanie** | Kilka instancji hub dla odporności na awarie |
| **Grubi klienci** | Tryb p2p dla dużych uczestników |
| **Rozszerzona analityka** | Scoring ryzyka, wizualizacja sieci |

---

## Architektura (przegląd wysokiego poziomu)

```
┌─────────────────────────────┐
│     Klienci (Flutter)       │
│  - Aplikacja mobilna        │
│  - Klient desktopowy        │
└──────────────┬──────────────┘
               │ HTTPS / WebSocket
               ▼
┌─────────────────────────────┐
│    Community Hub            │
│  - API (FastAPI)            │
│  - Protokół GEO v0.1        │
│  - System dodatków          │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│    Przechowywanie danych    │
│  - PostgreSQL               │
│  - Redis                    │
└─────────────────────────────┘
```

### Rola Hub'a

Hub to **koordynator i indeksator**, a nie bank:
- Przechowuje stan sieci zaufania i długów
- Koordynuje transakcje (PREPARE/COMMIT)
- Wyszukuje trasy i cykle do kliringu
- Zapewnia API dla klientów

Przy tym:
- Klucze prywatne uczestników są przechowywane tylko na ich urządzeniach
- Operacje krytyczne są podpisywane przez uczestników
- Hub nie może wykonać operacji bez podpisu właściciela

---

## Stos technologiczny

| Komponent | Technologia |
|-----------|------------|
| **Backend** | Python 3.11+, FastAPI, Pydantic |
| **Baza danych** | PostgreSQL |
| **Cache/kolejki** | Redis |
| **Klienci** | Flutter (Dart) |
| **Kryptografia** | Ed25519 (libsodium) |
| **Konteneryzacja** | Docker |

---

## Wymagania niefunkcjonalne

### Wydajność

| Metryka | Wartość docelowa |
|---------|------------------|
| **Średnie obciążenie** | 0.1–1 transakcja/sek |
| **Szczytowe obciążenie** | do 10 transakcji/sek |
| **Zapas (test stresu)** | 50–100 żądań/sek |

### Opóźnienie płatności

| Etap | Czas docelowy |
|------|---------------|
| **Wyszukiwanie trasy** | < 200–300 ms |
| **Faza PREPARE** | < 500 ms |
| **Czas całkowity** | < 2 sek (95%), < 5 sek (99%) |
| **Timeout 2PC** | 3–5 sekund |

### Dostępność

| Metryka | Wartość docelowa |
|---------|------------------|
| **Uptime (MVP)** | 99% (do 7 godzin przestoju/miesiąc) |
| **Uptime (dojrzały)** | 99.5% (do 3.5 godziny przestoju/miesiąc) |
| **RPO** | 0 (regularne kopie zapasowe) |
| **RTO** | 1–2 godziny |

---

## Powiązane dokumenty

| Dokument | Opis |
|----------|----------|
| [01-concepts.md](01-concepts.md) | Kluczowe koncepcje |
| [02-protocol-spec.md](02-protocol-spec.md) | Specyfikacja protokołu |
| [03-architecture.md](03-architecture.md) | Architektura systemu |
| [04-api-reference.md](04-api-reference.md) | Dokumentacja API |
| [05-deployment.md](05-deployment.md) | Wdrożenie |
| [06-contributing.md](06-contributing.md) | Udział w rozwoju |

---

## Licencja i społeczność

GEO to projekt open source. Kod, dokumentacja i specyfikacje są dostępne na otwartej licencji.

Zapraszamy:
- Programistów chcących wnieść wkład do kodu
- Społeczności gotowe do uruchomienia pilotażu
- Ekonomistów i badaczy do analizy modelu
- Projektantów do poprawy UX
