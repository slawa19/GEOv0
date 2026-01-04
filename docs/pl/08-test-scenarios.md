# GEOv0-PROJECT — Scenariusze testowe MVP (v0.1)

**Cel:** dostarczyć odtwarzalny zestaw scenariuszy do weryfikacji MVP end-to-end i przyszłej automatyzacji (testy e2e/integracyjne).

Format scenariusza:

- **ID**
- **Nazwa**
- **Warunki wstępne**
- **Kroki**
- **Oczekiwany wynik**
- **Sprawdzenie niezmienników / Uwagi**

---

## 0. Wspólna notacja

Uczestnicy:

- Alice = `PID_ALICE`
- Bob = `PID_BOB`
- Carol = `PID_CAROL`
- Dave = `PID_DAVE`

Ekwiwalent:

- `UAH` (precyzja 2)

Przez "saldo" rozumiemy wartości dostępne przez API salda i historię płatności/transakcji.

---

## 1. Onboarding i tożsamość

### TS-01. Rejestracja nowego uczestnika (z kluczami Ed25519)

**Warunki wstępne:** brak

**Kroki:**
1. Klient generuje parę kluczy Ed25519.
2. Klient wysyła rejestrację (z podpisem zgodnie z protokołem).
3. Otrzymuje `pid` i profil.

**Oczekiwany wynik:**
- Uczestnik utworzony ze statusem `active`.
- Klucz publiczny zapisany.
- Ponowne wysłanie tego samego żądania z tym samym `tx_id` — idempotentność (brak duplikatu).

**Sprawdzenia:** podpisy, dryft czasu, idempotentność.

### TS-02. Logowanie (pobranie JWT) i dostęp do chronionych endpointów

**Warunki wstępne:** `PID_ALICE` jest zarejestrowany

**Kroki:**
1. Alice wykonuje challenge-response.
2. Otrzymuje JWT.
3. Żąda `/balance`.

**Oczekiwany wynik:**
- JWT jest ważny.
- `/balance` zwraca poprawną strukturę.

### TS-03. Wygaśnięcie JWT i odświeżenie

**Warunki wstępne:** `PID_ALICE` jest autoryzowany

**Kroki:**
1. Poczekać na wygaśnięcie access tokena.
2. Ponowić żądanie.
3. Użyć refresh.

**Oczekiwany wynik:**
- Serwis zwraca 401 dla wygasłego access tokena.
- Refresh wydaje nowy access token.

---

## 2. Uczestnicy i profile

### TS-04. Wyszukiwanie uczestnika po części nazwy / filtrach

**Warunki wstępne:** utworzono ≥ 3 uczestników

**Kroki:**
1. Wykonać wyszukiwanie po zapytaniu.
2. Zastosować filtr `type`.

**Oczekiwany wynik:**
- Wyniki ograniczone przez `limit`.
- Filtr jest poprawny.

---

## 3. Linie zaufania (TrustLines)

### TS-05. Utworzenie trustline Alice → Bob

**Warunki wstępne:** Alice i Bob są zarejestrowani

**Kroki:**
1. Alice tworzy trustline do Bob z `limit=1000.00 UAH` i podpisem.

**Oczekiwany wynik:**
- Trustline utworzony.
- `used=0.00`.
- Ponowne wysłanie z tym samym `tx_id` nie tworzy duplikatu.

### TS-06. Aktualizacja trustline Alice → Bob (zwiększenie limitu)

**Warunki wstępne:** trustline Alice → Bob istnieje

**Kroki:**
1. Alice aktualizuje limit do 1500.00.

**Oczekiwany wynik:**
- Nowy limit zastosowany.
- Niezmienniki zachowane.

### TS-07. Próba zmniejszenia limitu poniżej bieżącego `used`

**Warunki wstępne:** istnieje dług na linii Alice→Bob (used>0)

**Kroki:**
1. Alice próbuje ustawić `limit < used`.

**Oczekiwany wynik:**
- Operacja odrzucona (błąd domenowy/HTTP zwrócony poprawnie).

### TS-08. Zamknięcie trustline przy `used=0`

**Warunki wstępne:** trustline Alice → Bob istnieje, `used=0`

**Kroki:**
1. Alice zamyka trustline.

**Oczekiwany wynik:**
- Trustline zamknięty/usunięty zgodnie z regułami.
- Zdarzenie zapisane w historii.

### TS-09. Zamknięcie trustline przy `used>0`

**Warunki wstępne:** trustline Alice → Bob istnieje, `used>0`

**Kroki:**
1. Alice zamyka trustline.

**Oczekiwany wynik:**
- Operacja odrzucona.

---

## 4. Płatności: pojemność, max-flow, single-path

### TS-10. Sprawdzenie pojemności na konkretną kwotę

**Warunki wstępne:** istnieje ścieżka Alice → Bob z dostępną pojemnością ≥ 100.00

**Kroki:**
1. Alice wywołuje `GET /payments/capacity?to=PID_BOB&equivalent=UAH&amount=100.00`.

**Oczekiwany wynik:**
- `can_pay=true`.
- `max_amount` ≥ 100.00.
- `routes_count` i `estimated_hops` są rozsądne.

### TS-11. Obliczenie maksymalnej płatności (Max-flow)

**Warunki wstępne:** utworzono graf trustlines

**Kroki:**
1. Alice wywołuje `GET /payments/max-flow?to=PID_BOB&equivalent=UAH`.

**Oczekiwany wynik:**
- `max_amount` odpowiada oszacowaniu algorytmu.
- `paths` i `bottlenecks` obecne i spójne.

### TS-12. Utworzenie płatności single-path (sukces)

**Warunki wstępne:** pojemność trasy ≥ kwota

**Kroki:**
1. Alice wykonuje `POST /payments` na 100.00.

**Oczekiwany wynik:**
- `status=COMMITTED`.
- `routes` zawiera 1 ścieżkę z amount=100.00.
- Salda/used przeliczone poprawnie.

### TS-13. Utworzenie płatności single-path (niewystarczająca pojemność)

**Warunki wstępne:** pojemność < kwota

**Kroki:**
1. Alice wykonuje `POST /payments` na 100.00 gdy dostępne tylko 50.00.

**Oczekiwany wynik:**
- `status=ABORTED`.
- Błąd `Insufficient capacity`, `requested` i `available` poprawne.

---

## 5. Płatności: ograniczony multipath (2–3 trasy)

### TS-14. Płatność multipath: kwota dzielona na 2 trasy

**Warunki wstępne:** istnieją 2 niezależne trasy o łącznej pojemności ≥ kwota

**Kroki:**
1. Alice wykonuje `POST /payments` na 120.00.

**Oczekiwany wynik:**
- `status=COMMITTED`.
- `routes` zawiera 2 trasy.
- Suma tras = 120.00.
- Niezmienniki zachowane.

### TS-15. Płatność multipath: 3 trasy (limit max_paths_per_payment)

**Warunki wstępne:** włączony ograniczony multipath; dostępne 3 trasy

**Kroki:**
1. Alice wykonuje płatność na kwotę wymagającą 3 tras.

**Oczekiwany wynik:**
- `routes` ≤ `max_paths_per_payment`.
- Jeśli wymagane >3, płatność powinna być odrzucona lub częściowo niemożliwa zgodnie z polityką (w MVP — odrzucenie).

---

## 6. Eksperyment: pełny multipath (flaga funkcji)

### TS-16. Włączenie pełnego multipath i ponowne max-flow

**Warunki wstępne:** dostęp admin, włączenie `feature_flags.full_multipath_enabled`

**Kroki:**
1. Włączyć flagę w konsoli administracyjnej.
2. Alice wywołuje `GET /payments/max-flow`.

**Oczekiwany wynik:**
- `algorithm` odzwierciedla włączony tryb (np. `full_multipath`).
- `max_amount` może wzrosnąć (nie musi), ale kontrakt zachowany.

---

## 7. Clearing

### TS-17. Automatyczny clearing cyklu długości 3

**Warunki wstępne:** utworzono cykl długów długości 3

**Kroki:**
1. Utworzyć długi tworzące cykl A→B→C→A.
2. Poczekać na wyzwolony clearing.

**Oczekiwany wynik:**
- Długi zmniejszone/wyzerowane zgodnie z clearingiem.
- Zdarzenie zapisane w historii.

### TS-18. Wpływ parametru trigger_cycles_max_length

**Warunki wstępne:** istnieje cykl długości 5

**Kroki:**
1. Ustawić `clearing.trigger_cycles_max_length=4`.
2. Sprawdzić, że cykl 5 nie jest czyszczony przez trigger.
3. Włączyć periodyczne wyszukiwanie długości 5.
4. Sprawdzić, że cykl 5 jest czyszczony periodycznie.

**Oczekiwany wynik:**
- Zachowanie odpowiada konfiguracji.

---

## 8. WebSocket

### TS-19. Subskrypcja zdarzeń i odbiór powiadomienia o płatności

**Warunki wstępne:** WS dostępny, Alice subskrybuje

**Kroki:**
1. Alice subskrybuje `payment.received`.
2. Bob wykonuje płatność do Alice.

**Oczekiwany wynik:**
- Alice otrzymuje zdarzenie WS.
- Zdarzenie odpowiada schematowi.

---

## 9. Operator/konsola administracyjna (minimum)

### TS-20. Zamrożenie uczestnika i blokada operacji

**Warunki wstępne:** dostęp admin, Bob jest aktywny

**Kroki:**
1. Admin zamraża Bob.
2. Bob próbuje utworzyć płatność lub trustline.

**Oczekiwany wynik:**
- Operacja odrzucona.
- Log audytowy zawiera wpis o zamrożeniu i próbie operacji.

### TS-21. Zmiana parametrów w konsoli administracyjnej: max_paths_per_payment

**Warunki wstępne:** dostęp admin

**Kroki:**
1. Zmienić `routing.max_paths_per_payment` na 2.
2. Wykonać scenariusz TS-15.

**Oczekiwany wynik:**
- Teraz `routes` ≤ 2.
- Log audytowy rejestruje zmianę.

---

## 10. Niezawodność i idempotentność

### TS-22. Ponowienie POST /payments z tym samym tx_id

**Warunki wstępne:** sieć/klient symuluje ponowienie

**Kroki:**
1. Wysłać płatność.
2. Powtórzyć to samo żądanie z tym samym `tx_id`.

**Oczekiwany wynik:**
- Brak duplikatów.
- Odpowiedź jest spójna.

### TS-23. Równoczesne płatności na tym samym wąskim gardle

**Warunki wstępne:** ograniczona pojemność

**Kroki:**
1. Uruchomić 2 płatności równolegle konkurujące o tę samą linię zaufania.

**Oczekiwany wynik:**
- Jedna może zostać zatwierdzona, druga powinna otrzymać poprawny błąd/abort.
- Niezmienniki zachowane.

---

## 11. Top-5 scenariuszy dla minimalnego zestawu e2e

- TS-01 Rejestracja
- TS-05 Utworzenie trustline
- TS-12 Płatność single-path sukces
- TS-14 Płatność multipath 2 trasy
- TS-17 Automatyczny clearing cyklu długości 3
