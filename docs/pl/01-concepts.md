# Protokół GEO: Kluczowe koncepcje

**Wersja:** 0.1  
**Data:** Listopad 2025

---

## Spis treści

1. [Uczestnik (Participant)](#1-uczestnik-participant)
2. [Ekwiwalent (Equivalent)](#2-ekwiwalent-equivalent)
3. [Linia zaufania (TrustLine)](#3-linia-zaufania-trustline)
4. [Dług (Debt)](#4-dług-debt)
5. [Płatność (Payment)](#5-płatność-payment)
6. [Kliring (Clearing)](#6-kliring-clearing)
7. [Transakcja (Transaction)](#7-transakcja-transaction)
8. [Hub i koordynacja](#8-hub-i-koordynacja)

---

## 1. Uczestnik (Participant)

**Uczestnik** to dowolna jednostka, która może uczestniczyć w ekonomii GEO:
- Osoba fizyczna
- Organizacja lub przedsiębiorstwo
- Hub (węzeł społeczności — także uczestnik na poziomie federacji)

### Tożsamość

Każdy uczestnik jest identyfikowany kryptograficznie:

```
PID = base58(sha256(public_key))
```

- **public_key** — klucz publiczny Ed25519 (32 bajty)
- **PID** — pochodny identyfikator uczestnika

### Właściwości uczestnika

| Pole | Opis |
|------|----------|
| `pid` | Unikalny identyfikator (pochodny od pubkey) |
| `public_key` | Klucz publiczny Ed25519 |
| `display_name` | Nazwa czytelna dla człowieka |
| `profile` | Metadane (typ, kontakty, opis) |
| `status` | `active`, `suspended`, `left`, `deleted` |
| `verification_level` | Poziom weryfikacji (zależny od społeczności) |

### Zasada własności

- Klucz prywatny **nigdy nie jest przekazywany** na serwer
- Wszystkie operacje krytyczne są **podpisywane** przez właściciela klucza
- Hub przechowuje tylko klucze publiczne i weryfikuje podpisy

---

## 2. Ekwiwalent (Equivalent)

**Ekwiwalent** to jednostka rozrachunku, w której mierzone są zobowiązania.

### Przykłady ekwiwalentów

| Kod | Opis | Precision |
|-----|----------|-----------|
| `UAH` | Hrywna ukraińska | 2 |
| `USD` | Dolar amerykański | 2 |
| `HOUR` | Godzina pracy | 2 |
| `HOUR_DEV` | Godzina pracy programisty | 2 |
| `kWh` | Kilowatogodzina | 3 |
| `LOCAL` | Wewnętrzna jednostka społeczności | 2 |

### Właściwości ekwiwalentu

| Pole | Opis |
|------|----------|
| `code` | Unikalny kod tekstowy |
| `precision` | Liczba miejsc po przecinku |
| `description` | Opis czytelny dla człowieka |
| `metadata` | Dodatkowe dane (typ, powiązanie) |

### Ważne

- W systemie **nie ma domyślnej waluty**
- Wszystkie operacje wyraźnie określają ekwiwalent
- GEO **nie konwertuje** między ekwiwalentami automatycznie
- Jeden uczestnik może mieć linie zaufania w różnych ekwiwalentach

---

## 3. Linia zaufania (TrustLine)

**Linia zaufania** to deklaracja jednego uczestnika o gotowości przyjęcia ryzyka na drugiego.

### Semantyka

```
TrustLine: A → B (limit=1000, equivalent=UAH)
```

Oznacza: **„A pozwala B być mu dłużnym do 1000 UAH"**

### Kluczowe właściwości

| Właściwość | Opis |
|----------|----------|
| **Kierunkowość** | A→B ≠ B→A (to różne linie) |
| **Jednostronność** | Tworzona przez właściciela (A) bez zgody B |
| **Ograniczoność** | Określa maksymalny dług B wobec A |
| **Specyficzność** | Powiązana z konkretnym ekwiwalentem |

### Struktura TrustLine

| Pole | Opis |
|------|----------|
| `from` | PID tego, kto daje zaufanie (wierzyciel) |
| `to` | PID tego, komu ufają (potencjalny dłużnik) |
| `equivalent` | Ekwiwalent |
| `limit` | Maksymalna kwota długu |
| `policy` | Polityki (auto-kliring, ograniczenia tras) |
| `status` | `active`, `frozen`, `closed` |

### Podstawowy niezmiennik

```
debt[B→A, E] ≤ limit(A→B, E)
```

Dług B wobec A nie może przekroczyć ustalonego limitu.

### Przykład

```
Alicja tworzy TrustLine: Alicja → Bob (limit=500, UAH)

To oznacza:
- Bob może być dłużny Alicji do 500 UAH
- Alicja ponosi ryzyko niespłaty
- Bob może używać tego zaufania do zakupów
```

---

## 4. Dług (Debt)

**Dług** to faktyczne zobowiązanie jednego uczestnika wobec drugiego.

### Semantyka

```
Debt: X → Y (amount=300, equivalent=UAH)
```

Oznacza: **„X jest dłużny Y 300 UAH"**

### Struktura Debt

| Pole | Opis |
|------|----------|
| `debtor` | PID dłużnika |
| `creditor` | PID wierzyciela |
| `equivalent` | Ekwiwalent |
| `amount` | Bieżąca kwota długu (> 0) |

### Związek z TrustLine

- TrustLine określa **pułap** możliwego długu
- Debt to **faktyczny stan**
- Jedna TrustLine może „obsługiwać" wiele transakcji

### Agregacja

- Dla każdej trójki `(debtor, creditor, equivalent)` przechowywany jest **jeden zagregowany wpis**
- Protokół nie przechowuje historii każdej transakcji — tylko wynikowy dług
- Historia jest rejestrowana w transakcjach

---

## 5. Płatność (Payment)

**Płatność** to przeniesienie „wartości" od płatnika do odbiorcy przez sieć zaufania.

### Jak działa płatność

```
A chce zapłacić C 100 UAH
Bezpośrednia TrustLine A→C nie istnieje

Ale jest łańcuch:
A → B (limit=200) → C (limit=150)

Płatność przechodzi przez łańcuch:
1. A staje się dłużny B +100
2. B staje się dłużny C +100
3. C otrzymał „płatność" od A
```

### Dostępny kredyt (Available Credit)

Dla każdej skierowanej krawędzi:

```
available_credit(A→B, E) = limit(A→B, E) - debt[B→A, E]
```

To maksimum, które można „przeprowadzić" przez daną krawędź.

### Routing

1. **Wyszukiwanie ścieżek**: BFS z ograniczeniem głębokości (do 6 ogniw)
2. **Obliczanie pojemności**: min(available_credit) dla wszystkich krawędzi ścieżki
3. **Multi-path**: podział płatności na 2–3 trasy w razie potrzeby

### Przykład multi-path

```
A → C na 100 UAH

Znalezione ścieżki:
- A → X → C (pojemność 60)
- A → Y → Z → C (pojemność 50)

Płatność zostaje podzielona:
- 60 przez ścieżkę 1
- 40 przez ścieżkę 2
```

---

## 6. Kliring (Clearing)

**Kliring** to automatyczne wzajemne rozliczenie długów w zamkniętym cyklu.

### Przykład cyklu

```
A jest dłużny B: 100
B jest dłużny C: 100
C jest dłużny A: 100

To zamknięty cykl. Ekonomicznie — nikt nikomu nie jest dłużny.

Kliring zmniejsza wszystkie długi o min(100, 100, 100) = 100:
A jest dłużny B: 0
B jest dłużny C: 0
C jest dłużny A: 0
```

### Jak to działa

1. **Wyszukiwanie cykli**: Algorytm znajduje zamknięte łańcuchy długów
2. **Obliczanie kwoty**: S = min(dług na każdej krawędzi cyklu)
3. **Zastosowanie**: Zmniejszenie wszystkich długów o S

### Długość cykli

| Długość | Częstotliwość wyszukiwania | Złożoność |
|-------|----------------|-----------|
| 3 węzły | Po każdej transakcji | Niska |
| 4 węzły | Po każdej transakcji | Niska |
| 5 węzłów | Okresowo (raz na godzinę) | Średnia |
| 6 węzłów | Okresowo (raz na dobę) | Wysoka |

### Dlaczego kliring jest potrzebny

- **Zwalnia limity**: Po kliringu TrustLines są ponownie dostępne
- **Zmniejsza ryzyko**: Mniej „wiszących" długów w systemie
- **Zwiększa płynność**: Więcej możliwych tras dla płatności

---

## 7. Transakcja (Transaction)

**Transakcja** to atomowa jednostka zmian w systemie.

### Typy transakcji

| Typ | Opis |
|-----|----------|
| `TRUST_LINE_CREATE` | Utworzenie linii zaufania |
| `TRUST_LINE_UPDATE` | Zmiana limitu lub polityki |
| `TRUST_LINE_CLOSE` | Zamknięcie linii |
| `PAYMENT` | Płatność przez sieć |
| `CLEARING` | Wzajemne rozliczenie po cyklu |

### Struktura transakcji

| Pole | Opis |
|------|----------|
| `tx_id` | Unikalny identyfikator (UUID lub hash) |
| `type` | Typ transakcji |
| `initiator` | PID inicjatora |
| `payload` | Typowane dane operacji |
| `signatures` | Podpisy uczestników |
| `state` | Bieżący stan |
| `created_at` | Czas utworzenia |

### Stany PAYMENT

```
NEW → ROUTED → PREPARE_IN_PROGRESS → COMMITTED
                                   ↘ ABORTED
```

### Stany CLEARING

```
NEW → PROPOSED → WAITING_CONFIRMATIONS → COMMITTED
                                       ↘ REJECTED
```

### Idempotentność

- Powtórny `COMMIT` na już zakończonej transakcji — bezpieczny
- `tx_id` gwarantuje brak duplikatów

---

## 8. Hub i koordynacja

### Rola Hub'a

Hub pełni funkcje **koordynatora**, ale nie jest bankiem:

| Funkcja | Opis |
|---------|----------|
| **Przechowywanie** | Stan uczestników, TrustLines, długów |
| **Koordynacja** | Fazy PREPARE/COMMIT transakcji |
| **Routing** | Wyszukiwanie ścieżek dla płatności |
| **Kliring** | Wyszukiwanie i wykonywanie cykli |
| **API** | Dostęp dla klientów |

### Czego Hub NIE robi

- Nie przechowuje kluczy prywatnych uczestników
- Nie wykonuje operacji bez podpisu właściciela
- Nie „posiada" środków uczestników
- Nie podejmuje jednostronnych decyzji o saldach

### Koordynacja transakcji (2PC)

**Dwufazowy commit** gwarantuje atomowość:

```
Faza 1: PREPARE
- Hub wysyła wszystkim uczestnikom żądanie rezerwacji
- Każdy sprawdza warunki i rezerwuje zasoby
- Wszyscy odpowiadają OK lub FAIL

Faza 2: COMMIT lub ABORT
- Jeśli wszyscy OK → COMMIT (zastosowanie zmian)
- Jeśli ktoś FAIL → ABORT (zwolnienie rezerw)
```

### Administracja Hub'a

Hub jest zarządzany przez **operatora społeczności** (spółdzielnię, NGO itp.):

| Rola | Uprawnienia |
|------|-------|
| `admin` | Zarządzanie konfiguracją, rolami, dodatkami |
| `operator` | Monitoring, pomoc użytkownikom, rozstrzyganie sporów |
| `auditor` | Tylko odczyt logów i raportów |

### Federacja hubów

Huby różnych społeczności mogą się łączyć:

```
┌───────────────┐         ┌───────────────┐
│  Community A  │         │  Community B  │
│  ┌─────────┐  │ Trust-  │  ┌─────────┐  │
│  │ Hub A   │◄─┼─Line────┼─►│ Hub B   │  │
│  └────┬────┘  │         │  └────┬────┘  │
│  [users A]    │         │  [users B]    │
└───────────────┘         └───────────────┘

Hub A i Hub B — zwykli uczestnicy protokołu
Między nimi otwarte TrustLines
Płatności między społecznościami są routowane przez huby
```

---

---

## 9. Model ekonomiczny

### 9.1. Ukryty demurrage (Implicit Demurrage)

W przeciwieństwie do jawnego demurrage (podatek na gromadzenie pieniędzy), GEO tworzy **ukryty demurrage** poprzez samą strukturę systemu.

**Co to jest demurrage?**

Demurrage to mechanizm, który czyni przechowywanie pieniędzy „kosztownym". W tradycyjnych systemach może to być jawny podatek (np. -2% miesięcznie na saldach). W GEO demurrage jest **ukryty** — wynika naturalnie ze struktury systemu.

**Dlaczego w GEO nie można „zgromadzić bogactwa"?**

W GEO nie ma pieniędzy jako takich — są tylko zobowiązania (długi) między uczestnikami. „Bogactwo" w GEO oznacza, że dużo ci są dłużni. Ale żeby ci mogli być dłużni, ktoś musi otworzyć TrustLine do ciebie. A żeby otwierali TrustLines do ciebie, musisz sam być aktywnym uczestnikiem sieci — kupować, sprzedawać, otwierać TrustLines innym.

**Mechanizm ukrytego demurrage działa tak:**

1. **Żeby otrzymywać płatności** — potrzebujesz, żeby do ciebie były otwarte TrustLines (limit przychodzący)
2. **Żeby do ciebie otwierano TrustLines** — musisz być znany, niezawodny, użyteczny dla sieci
3. **Żeby być użytecznym dla sieci** — musisz sam otwierać TrustLines (przyjmować ryzyko), uczestniczyć w kliringu, być pośrednikiem
4. **Otworzenie TrustLine = ryzyko** — możesz stracić do kwoty limitu, jeśli dłużnik nie odda
5. **Rezultat:** Im więcej chcesz „zgromadzić", tym więcej ryzyka musisz przyjąć

**Porównanie z tradycyjnymi pieniądzmi:**

| Aspekt | Tradycyjne pieniądze | GEO |
|--------|---------------------|-----|
| Gromadzenie | Można gromadzić bez końca | Ograniczone TrustLines do ciebie |
| Ryzyko gromadzącego | Tylko inflacja | Ryzyko niespłacenia długów |
| Bodziec do wydawania | Tylko inflacja | Blokada przychodzących płatności przy wysokim saldzie |
| Dochód pasywny | Odsetki od depozytu | Brak — tylko aktywne uczestnictwo |

#### Mechanizm ukrytego demurrage

```
┌────────────────────────────────────────────────────────────┐
│              Ukryty demurrage w GEO                        │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Zwiększenie TrustLine = Zwiększenie osobistego ryzyka     │
│                                                            │
│  Jeśli otwieram TrustLine na 1000 UAH:                    │
│  • Przyjmuję ryzyko, że nie odzyskam do 1000 UAH         │
│  • To jest „cena" za możliwość uczestnictwa w sieci      │
│  • Więcej zaufania = więcej ryzyka = ukryta „opłata"     │
│                                                            │
│  Rezultat:                                                │
│  • Naturalne ograniczenie gromadzenia „bogactwa"         │
│  • Bodziec do równoważenia pozycji                       │
│  • Cyrkulacja zamiast gromadzenia                        │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 9.2. Bodźce do zerowego salda

Optymalna strategia uczestnika to **dążenie do zerowego salda**.

#### Konsekwencje dodatniego salda (dużo ci są dłużni)

| Efekt | Opis |
|--------|----------|
| **Blokada płynności** | TrustLines do ciebie są w 100% wykorzystane — nie mogą ci płacić |
| **Spadek użyteczności** | Uczestnik „wypada" z sieci jako odbiorca |
| **Konsekwencje reputacyjne** | Postrzegany jako „gromadzący" |
| **Ryzyko niespłacenia** | Zgromadzone długi mogą nie zostać spłacone |

#### Konsekwencje ujemnego salda (dużo jesteś dłużny)

| Efekt | Opis |
|--------|----------|
| **Ograniczenie zakupów** | Brak wolnego kredytu na nowe zakupy |
| **Ryzyko odwołania TrustLines** | Wierzyciele mogą zamknąć linie |
| **Konsekwencje reputacyjne** | Spadek zaufania do uczestnika |
| **Konieczność spłaty** | Presja na dostarczenie towarów/usług |

#### Optymalny stan — około zera

```
┌────────────────────────────────────────────────────────────┐
│                  Saldo około zera                          │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ✓ Maksymalna elastyczność dla zakupów i sprzedaży       │
│  ✓ Dobra reputacja w społeczności                        │
│  ✓ Stabilne relacje z kontrahentami                      │
│  ✓ Minimalne ryzyka                                      │
│  ✓ Aktywne uczestnictwo w kliringu                      │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 9.3. Metryki zdrowia ekonomicznego

Do oceny zdrowia ekonomicznego uczestnika używa się czterech kluczowych metryk:

1. **Saldo netto (net_balance)** — różnica między tym, ile ci są dłużni, a tym, ile jesteś dłużny. Wartość idealna — około zera.

2. **Odchylenie od zera (balance_deviation)** — wartość bezwzględna salda netto. Im mniejsze — tym lepsze.

3. **Wykorzystanie przychodzących TrustLines** — jaki procent limitów kredytowych otwartych DO uczestnika jest wykorzystany (długi jemu). Jeśli 100% — już mu nie mogą płacić.

4. **Wykorzystanie wychodzących TrustLines** — jaki procent limitów otwartych OD uczestnika jest wykorzystany (jego długi). Jeśli 100% — nie może kupować.

**Obliczanie wskaźnika zdrowia (0-100):**
- Zaczynamy od 100 punktów
- Odejmujemy karę za odchylenie od zera (do 50 punktów)
- Odejmujemy karę za wykorzystanie powyżej 70% (do 30 punktów)

```python
class EconomicHealthMetrics:
    """Metryki zdrowia ekonomicznego uczestnika"""
    
    # Saldo netto (są ci dłużni - jesteś dłużny)
    net_balance: Decimal
    
    # Jak blisko zera (0 = idealnie)
    balance_deviation: Decimal = abs(net_balance)
    
    # Wykorzystanie przychodzących TrustLines (%)
    incoming_utilization: Decimal  # debt_to_me / total_incoming_limit
    
    # Wykorzystanie wychodzących TrustLines (%)
    outgoing_utilization: Decimal  # my_debt / total_outgoing_limit
    
    # Zdrowie salda (0-100)
    @property
    def health_score(self) -> int:
        # Kara za odchylenie od zera
        balance_penalty = min(self.balance_deviation / 1000, 50)
        
        # Kara za wysokie wykorzystanie
        utilization_penalty = max(
            self.incoming_utilization - 0.7,
            self.outgoing_utilization - 0.7,
            0
        ) * 100
        
        return max(0, 100 - balance_penalty - utilization_penalty)
```

### 9.4. Algorytm oceny zachowania ekonomicznego

Analiza zachowania ekonomicznego ocenia, jak bardzo uczestnik stosuje się do optymalnej strategii — dążenia do zerowego salda.

**Algorytm analizy:**

1. **Zbieranie historii** — pobieramy wszystkie zapisy salda uczestnika za okres (domyślnie 30 dni)
2. **Obliczanie średniego salda** — jeśli jest daleki od zera, to zły znak
3. **Obliczanie zmienności** — zbyt stabilne duże saldo jest gorsze niż wahania wokół zera
4. **Określanie trendu** — czy uczestnik zmierza ku zeru czy od niego
5. **Liczenie udziału w kliringu** — aktywni uczestnicy kliringu dostają bonus

**Obliczanie końcowego wskaźnika (0-100):**
- Bazowe 100 punktów
- Kara do 30 punktów za duże średnie saldo
- Kara 20 punktów za trend od zera, lub bonus 10 za trend ku zeru
- Bonus do 20 punktów za aktywny udział w kliringu

```python
class EconomicBehaviorAnalyzer:
    """Analizator zachowania ekonomicznego uczestnika"""
    
    async def analyze_participant(
        self,
        pid: str,
        period_days: int = 30
    ) -> BehaviorAnalysis:
        """
        Analiza zachowania ekonomicznego.
        
        Ocenia, na ile uczestnik stosuje się do optymalnej strategii
        (dążenia do zerowego salda).
        """
        # Pobranie historii sald
        balance_history = await self.get_balance_history(pid, period_days)
        
        # Średnie saldo za okres
        avg_balance = sum(b.net_balance for b in balance_history) / len(balance_history)
        
        # Zmienność salda
        balance_volatility = statistics.stdev(
            b.net_balance for b in balance_history
        )
        
        # Trend (porusza się ku zeru czy od niego?)
        trend = self.calculate_trend(balance_history)
        
        # Częstotliwość kliringu
        clearing_participation = await self.count_clearings(pid, period_days)
        
        return BehaviorAnalysis(
            pid=pid,
            avg_balance=avg_balance,
            balance_volatility=balance_volatility,
            trend_direction=trend,  # "toward_zero" | "away_from_zero" | "stable"
            clearing_participation=clearing_participation,
            behavior_score=self.calculate_score(avg_balance, trend, clearing_participation)
        )
    
    def calculate_score(
        self,
        avg_balance: Decimal,
        trend: str,
        clearing_count: int
    ) -> int:
        """
        Wskaźnik zachowania (0-100).
        
        Wysoki wskaźnik = dobre zachowanie ekonomiczne.
        """
        score = 100
        
        # Kara za duże średnie saldo
        score -= min(abs(avg_balance) / 100, 30)
        
        # Kara za ruch od zera
        if trend == "away_from_zero":
            score -= 20
        elif trend == "toward_zero":
            score += 10
        
        # Bonus za udział w kliringu
        score += min(clearing_count * 2, 20)
        
        return max(0, min(100, score))
```

---

## 10. System reputacyjny

### 10.1. Przeznaczenie

Reputacja w GEO odzwierciedla **niezawodność i użyteczność** uczestnika dla sieci.

### 10.2. Składniki reputacji

Wskaźnik reputacji uczestnika składa się z czterech grup metryk:

**1. Podstawowe metryki zaufania:**
- Całkowita kwota limitów TrustLines otwartych DO uczestnika (ile mu ufają w pieniądzach)
- Liczba różnych uczestników, którzy mu ufają (szerokość zaufania)

**2. Metryki aktywności:**
- Ile płatności wysłał i otrzymał uczestnik
- Procent udanych płatności (nie przerwanych)

**3. Metryki zachowania ekonomicznego:**
- Jak często uczestniczy w kliringu (pomaga sieci)
- Jak blisko zera jest jego saldo (zdrowe zachowanie)

**4. Metryki wkładu w sieć:**
- Ile razy był pośrednikiem w płatnościach innych uczestników
- Jaka wartość przeszła przez niego (wartość dla sieci)

**5. Weryfikacja i staż:**
- Poziom weryfikacji tożsamości (0-3)
- Jak dawno jest członkiem społeczności

```python
class ReputationScore:
    """Wskaźnik reputacji uczestnika"""
    
    # === Podstawowe metryki ===
    
    # Suma przychodzących TrustLines (ile ufają uczestnikowi)
    trust_received: Decimal
    
    # Liczba uczestników, którzy otworzyli TrustLine do niego
    trustees_count: int
    
    # === Metryki aktywności ===
    
    # Liczba udanych płatności (wysłanych)
    payments_sent: int
    
    # Liczba otrzymanych płatności
    payments_received: int
    
    # Skuteczność płatności (% COMMITTED ze wszystkich zainicjowanych)
    payment_success_rate: Decimal
    
    # === Metryki zachowania ekonomicznego ===
    
    # Udział w kliringu (ile razy uczestniczył)
    clearing_participation: int
    
    # Średnie saldo (bliskość zera)
    avg_balance_deviation: Decimal
    
    # === Metryki wkładu w sieć ===
    
    # Ile razy był pośrednikiem w płatnościach
    intermediary_count: int
    
    # Wartość „przepompowana" przez siebie płatności
    intermediary_volume: Decimal
    
    # === Weryfikacja ===
    
    # Poziom weryfikacji (0-3)
    verification_level: int
    
    # Długość członkostwa w społeczności
    member_since: datetime
```

### 10.3. Algorytm obliczania reputacji

Wskaźnik reputacji to liczba od 0 do 100, obliczana jako ważona suma znormalizowanych metryk.

**Wagi składników:**
- **Otrzymane zaufanie (20%)** — ile łącznie ci ufają w pieniądzach
- **Liczba ufających (10%)** — ilu różnych ludzi ci ufa
- **Skuteczność płatności (15%)** — jaki procent płatności przeszedł pomyślnie
- **Udział w kliringu (10%)** — jak często uczestniczysz we wzajemnych rozliczeniach
- **Zdrowie salda (15%)** — jak blisko zera jest saldo
- **Wkład w sieć (15%)** — ile płatności przepompowałeś jako pośrednik
- **Weryfikacja (10%)** — poziom potwierdzenia tożsamości
- **Staż (5%)** — jak dawno jesteś w społeczności

**Normalizacja metryk:**
- Każda metryka jest doprowadzona do skali 0-100
- Dla kwot pieniężnych używa się skali logarytmicznej (1000 = ~50, 10000 = ~80)
- Dla liczności — liniowa z nasyceniem (np. 50 ufających = 100)
- Dla stażu — 1 rok = 100 punktów

**Obliczanie końcowe:**
```
score = Σ (normalized[i] × weight[i])
```

```python
class ReputationService:
    """Serwis obliczania reputacji"""
    
    # Wagi składników
    WEIGHTS = {
        "trust_received": 0.20,       # 20%
        "trustees_count": 0.10,       # 10%
        "payment_success": 0.15,      # 15%
        "clearing_participation": 0.10, # 10%
        "balance_health": 0.15,       # 15%
        "network_contribution": 0.15, # 15%
        "verification": 0.10,         # 10%
        "tenure": 0.05                # 5%
    }
    
    async def calculate_reputation(
        self,
        pid: str
    ) -> ReputationResult:
        """
        Obliczenie wskaźnika reputacji (0-100).
        """
        metrics = await self.gather_metrics(pid)
        
        # Normalizacja każdej metryki do 0-100
        normalized = {
            "trust_received": self.normalize_trust(metrics.trust_received),
            "trustees_count": self.normalize_count(metrics.trustees_count, max_expected=50),
            "payment_success": metrics.payment_success_rate * 100,
            "clearing_participation": self.normalize_count(metrics.clearing_participation, max_expected=100),
            "balance_health": 100 - min(metrics.avg_balance_deviation / 10, 100),
            "network_contribution": self.normalize_contribution(metrics.intermediary_volume),
            "verification": metrics.verification_level * 33.33,
            "tenure": self.normalize_tenure(metrics.member_since)
        }
        
        # Suma ważona
        total_score = sum(
            normalized[key] * weight
            for key, weight in self.WEIGHTS.items()
        )
        
        return ReputationResult(
            pid=pid,
            score=round(total_score),
            breakdown=normalized,
            calculated_at=datetime.utcnow()
        )
    
    def normalize_trust(self, trust_received: Decimal) -> Decimal:
        """
        Normalizacja otrzymanego zaufania.
        
        Skala logarytmiczna (1000 UAH = ~50, 10000 = ~80, 100000 = ~100)
        """
        if trust_received <= 0:
            return Decimal("0")
        
        import math
        return min(100, Decimal(math.log10(float(trust_received) + 1) * 25))
    
    def normalize_contribution(self, volume: Decimal) -> Decimal:
        """Normalizacja wkładu jako pośrednik"""
        if volume <= 0:
            return Decimal("0")
        
        import math
        return min(100, Decimal(math.log10(float(volume) + 1) * 20))
    
    def normalize_tenure(self, member_since: datetime) -> Decimal:
        """Normalizacja stażu członkostwa"""
        days = (datetime.utcnow() - member_since).days
        
        # 1 rok = 100
        return min(100, Decimal(days / 365 * 100))
```

### 10.4. Wykorzystanie reputacji

| Kontekst | Jak jest używana |
|----------|------------------|
| **Otwieranie TrustLine** | Rekomendacja limitu na podstawie reputacji odbiorcy |
| **Routing** | Preferencja tras przez wysoko reputacyjnych pośredników |
| **Filtrowanie spamu** | Minimalna reputacja do kontaktu z uczestnikiem |
| **Rozstrzyganie sporów** | Uwzględnienie reputacji przy arbitrażu |
| **Gateway** | Minimalna reputacja do rejestracji Gateway |

### 10.5. API reputacji

```python
# Endpoint: GET /api/v1/participants/{pid}/reputation

{
  "pid": "5HueCGU8rMjx...",
  "score": 78,
  "level": "trusted",  # "new" | "basic" | "trusted" | "established" | "pillar"
  "breakdown": {
    "trust_received": 85,
    "trustees_count": 60,
    "payment_success": 95,
    "clearing_participation": 70,
    "balance_health": 80,
    "network_contribution": 65,
    "verification": 66,
    "tenure": 40
  },
  "badges": ["active_trader", "good_intermediary", "clearing_champion"],
  "calculated_at": "2025-11-30T12:00:00Z"
}
```

### 10.6. Poziomy reputacji

| Poziom | Wskaźnik | Opis |
|---------|------|----------|
| `new` | 0-20 | Nowy uczestnik, ograniczone możliwości |
| `basic` | 21-40 | Podstawowy uczestnik, standardowe możliwości |
| `trusted` | 41-60 | Zaufany, rozszerzone limity |
| `established` | 61-80 | Ugruntowany, może być Gateway |
| `pillar` | 81-100 | Filar społeczności, maksymalne przywileje |

---

## Słownik

| Termin | Definicja |
|--------|-------------|
| **PID** | Participant ID — identyfikator uczestnika |
| **TrustLine** | Linia zaufania — limit kredytowy od A do B |
| **Debt** | Dług — faktyczne zobowiązanie |
| **Equivalent** | Ekwiwalent — jednostka rozrachunku |
| **Available Credit** | Dostępny kredyt — reszta limitu minus dług |
| **Clearing** | Kliring — wzajemne rozliczenie długów po cyklu |
| **2PC** | Two-Phase Commit — dwufazowy commit |
| **Hub** | Węzeł społeczności — koordynator i magazyn |
| **Demurrage** | Demurrage — „opłata" za przechowywanie środków |
| **Reputation** | Reputacja — miara niezawodności uczestnika |

---

## Powiązane dokumenty

- [00-overview.md](00-overview.md) — Przegląd projektu
- [02-protocol-spec.md](02-protocol-spec.md) — Pełna specyfikacja protokołu
- [03-architecture.md](03-architecture.md) — Architektura systemu
