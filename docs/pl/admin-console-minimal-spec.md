# GEO Hub — Minimalna specyfikacja konsoli administracyjnej

Cel: opisać **minimum niezbędną** konsolę administracyjną dla MVP, aby:
- zarządzać parametrami (config) i flagami funkcji bez bezpośredniego dostępu do DB;
- wspierać operacje pilotażowe (ekwiwalenty, uczestnicy, incydenty);
- mieć ślad audytowy dla akcji operatora.

Powiązane dokumenty:
- Rejestr parametrów i znaczniki runtime vs restart/migracja: [`docs/pl/config-reference.md`](docs/pl/config-reference.md)
- Sekcje protokołu gdzie mają znaczenie ustawienia multipath/clearing: [`docs/pl/02-protocol-spec.md`](docs/pl/02-protocol-spec.md)
- Wdrożenie (włącznie ze schematem konfiguracji): [`docs/pl/05-deployment.md`](docs/pl/05-deployment.md)

---

## 1. Zasada: "Minimalna implementacja"

### 1.1. Podejście UI (najprostsza opcja)

Obie opcje są dozwolone, wybierz najprostszą dla zespołu:

**Opcja A (zalecana dla MVP): strony renderowane po stronie serwera (SSR)**
- Zaimplementowane wewnątrz aplikacji backendowej (FastAPI + szablony).
- Minimalna infrastruktura frontendowa.
- Mniejsze ryzyka auth/CSRF.

**Opcja B: najprostsze SPA**
- Pojedynczy statyczny bundle + wywołania admin API.
- Nieco bardziej złożone dla auth/CSRF/build, ale również akceptowalne.

W obu opcjach protokół akcji jest taki sam: UI wywołuje endpointy admin, serwer zapisuje audit-log.

---

## 2. Autentykacja/Autoryzacja (minimum)

### 2.1. Role (minimalny zestaw)

- `admin` — pełny dostęp do konsoli administracyjnej.
- `operator` — ograniczony dostęp: uczestnicy (freeze/ban), podgląd transakcji/clearing, podgląd konfiguracji, przełączanie flag funkcji.
- `auditor` — tylko odczyt: audit-log, transakcje, health/metrics, config (tylko odczyt).

### 2.2. Wymagania bezpieczeństwa

- Konsola administracyjna dostępna tylko przez osobną ścieżkę (np. `/admin`) i/lub osobną domenę.
- TLS wymagane.
- Dla SSR: ochrona CSRF dla POST/PUT/DELETE.
- Sesje/tokeny: ponowne użycie istniejącego JWT jest akceptowalne, ale dostęp do endpointów admin musi być sprawdzany według roli.

---

## 3. Zestaw ekranów (minimalna kompozycja)

### 3.1. Dashboard (tylko odczyt)
- Status huba: wersja, uptime, środowisko (dev/prod), podstawowe obciążenie.
- Szybkie linki do sekcji.

### 3.2. Konfiguracja i parametry (mieszane)
**Cel**: podgląd bieżącej konfiguracji i zmiana parametrów runtime.

Ekran powinien wspierać:
- podgląd bieżących wartości;
- podpowiedzi: opis/zakres/wartość domyślna (można załadować z [`docs/pl/config-reference.md`](docs/pl/config-reference.md) jako statyczną referencję lub osadzić minimalnie w backendzie);
- zmianę tylko parametrów runtime (patrz sekcja 4).

Formaty:
- tryb "tabela" (klucz → wartość);
- opcjonalnie: tryb "surowy YAML/JSON" tylko do podglądu.

### 3.3. Flagi funkcji (mutowalne)
- Przełączniki dla:
  - `feature_flags.multipath_enabled`
  - `feature_flags.full_multipath_enabled`
  - `clearing.enabled`
- Wyświetlanie ostrzeżenia: niektóre flagi są eksperymentalne (np. full multipath).

### 3.4. Ekwiwalenty (mieszane)
- Lista ekwiwalentów: kod, opis, precyzja/skala.
- Akcje (mutowalne): tworzenie/edycja/deaktywacja ekwiwalentu.
- Wymagania: wszystkie zmiany logowane do audit-log.

### 3.5. Uczestnicy (mieszane)
- Lista uczestników, filtr według statusu.
- Karta uczestnika: PID, poziom weryfikacji (jeśli używany), statystyki (tylko odczyt).
- Akcje (mutowalne):
  - `freeze` (zawieszenie operacji),
  - `unfreeze`,
  - `ban`/`unban` (jeśli przewidziane przez model).
- Wymagania: każda zmiana statusu — przez audit-log z powodem.

### 3.6. Transakcje (tylko odczyt)
- Wyszukiwanie po `tx_id`, PID, typie (`PAYMENT`, `CLEARING`, ...), statusie, zakresie dat.
- Podgląd szczegółów: payload, trasy, podpisy (jeśli wyświetlane), timeline zdarzeń.
- Akcje: tylko odczyt (w MVP).

### 3.7. Zdarzenia clearingowe (tylko odczyt)
- Lista transakcji `CLEARING`, filtry.
- Podgląd: cykl, kwota, tryb zgody, powód odrzucenia (jeśli jest).
- Akcje: tylko odczyt (w MVP).

### 3.8. Log audytowy (tylko odczyt)
- Wyszukiwanie po czasie, aktorze, typie akcji, obiekcie.
- Podgląd szczegółów zdarzenia (przed/po).

### 3.9. Health i metryki (tylko odczyt)
- Endpointy health (agregacja): `/health`, `/ready` i kluczowe zależności (DB/Redis).
- Link do `/metrics` (jeśli włączone) i krótkie KPI: latencja p95/p99, error rate.

---

## 4. Co jest tylko do odczytu vs co można zmienić

Kanoniczne znaczniki — w [`docs/pl/config-reference.md`](docs/pl/config-reference.md). Dla MVP ustalamy minimum:

### 4.1. Runtime mutowalne (przez konsolę administracyjną)
Dozwolone do zmiany:
- `feature_flags.*`
- `routing.*` (ważne dla sprawdzeń wydajności: `routing.max_paths_per_payment`, `routing.multipath_mode`)
- `clearing.*` (ważne: `clearing.trigger_cycles_max_length`)
- `limits.*`
- `observability.*` (np. `log_level`)

### 4.2. Tylko do odczytu (wymaga restartu/migracji)
Tylko podgląd:
- `protocol.*`
- `security.*` (domyślnie)
- `database.*`
- `integrity.*` (domyślnie)

---

## 5. Jakie akcje muszą być logowane (audit-log)

### 5.1. Wymagane zdarzenia

Zawsze logowane:
- login/logout (lub wydanie sesji admin)
- zmiana dowolnych parametrów runtime i flag funkcji
- tworzenie/edycja/deaktywacja ekwiwalentów
- freeze/unfreeze/ban/unban uczestnika
- dowolne "operacje kompensacyjne" (jeśli dodane później)

### 5.2. Minimalny schemat zdarzenia audit-log

Zalecany format (log + tabela w DB):
- `event_id`
- `timestamp`
- `actor` (user id / serwis)
- `actor_role`
- `action` (enum)
- `object_type` (config/feature_flag/participant/equivalent/...)
- `object_id`
- `reason` (wymagany dla freeze/ban i krytycznych zmian limitów)
- `before` / `after` (diff)
- `request_id` / `ip` / `user_agent`

---

## 6. Minimalne endpointy admin API (opcjonalne)

UI może być SSR i nie wymagać publicznego admin API, ale dla wygody testowania warto ustalić minimalne grupy endpointów:

- `GET /admin/config` (odczyt)
- `PATCH /admin/config` (aktualizacja podzbioru runtime)
- `GET /admin/feature-flags`
- `PATCH /admin/feature-flags`
- `GET /admin/participants`
- `POST /admin/participants/{pid}/freeze`
- `POST /admin/participants/{pid}/unfreeze`
- `POST /admin/participants/{pid}/ban`
- `POST /admin/participants/{pid}/unban`
- `GET /admin/equivalents`
- `POST /admin/equivalents`
- `PATCH /admin/equivalents/{code}`
- `GET /admin/transactions`
- `GET /admin/transactions/{tx_id}`
- `GET /admin/clearing`
- `GET /admin/audit-log`

Wszystkie mutujące endpointy muszą zapisywać do audit-log.

---

## 7. Ograniczenia MVP (jawne)

Aby uniknąć komplikacji:
- brak "ręcznej edycji długów/transakcji" w konsoli administracyjnej;
- brak złożonego konstruktora RBAC — tylko ustalone role;
- brak pełnego "edytora konfiguracji YAML" z walidacją schematu — tylko tabela kluczy i ograniczony zestaw pól.
