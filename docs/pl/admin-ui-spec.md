# GEO Hub Admin Console — Szczegółowa specyfikacja UI (Blueprint)

**Wersja:** 0.2
**Status:** Blueprint do realizacji bez „domysłów”
**Stos (Rekomendacja):** Vue.js 3 (Vite), Element Plus, Pinia.

Dokument zgodny z:
- `docs/pl/admin-console-minimal-spec.md`
- `docs/pl/04-api-reference.md`
- `api/openapi.yaml`

---

## 1. Cele i Zakres (Scope)

### 1.1. Cele (MVP)
- Obserwowalność sieci zaufania (graf + podstawowa analityka).
- Zarządzanie incydentami (zablokowane transakcje → force abort).
- Zarządzanie konfiguracją runtime i flagami funkcji (feature flags).
- Zarządzanie uczestnikami (freeze/unfreeze, jeśli dostępne — ban/unban).
- Audyt działań operatorów.

### 1.2. Nie-cele (w tej wersji)
- Ręczna edycja długów/transakcji.
- Kreator RBAC (tylko ustalone role).

---

## 2. Role i dostęp

### 2.1. Role (Zestaw minimalny)
- `admin` — pełny dostęp.
- `operator` — operacje i konfiguracja, bez działań krytycznych (może być ograniczone polityką).
- `auditor` — tylko odczyt.

### 2.2. Błędy dostępu (Normatywne)
- `401` → brak tokena/wygasł (UI proponuje ponowne zalogowanie).
- `403` → brak uprawnień (UI pokazuje read-only lub „Brak uprawnień”).

---

## 3. Układ i nawigacja

### 3.1. Zasady UI
- Nie wprowadzać własnych „sztywnych” kolorów/czcionek — używać tokenów systemu designu/motywu.
- Tryb ciemny domyślnie dopuszczalny, ale musi być realizowany przez wybraną warstwę UI.

### 3.2. Sidebar (Główne sekcje)

Minimalny zestaw ekranów (odpowiada `admin-console-minimal-spec.md`):
- `Dashboard`
- `Network Graph`
- `Integrity`
- `Incidents`
- `Participants`
- `Config`
- `Feature Flags`
- `Audit Log`
- `Events` (oś czasu)

Opcjonalnie (jeśli włączone w Hub):
- `Equivalents` (zarządzanie słownikiem)
- `Transactions` / `Clearing` (listy globalne)

### 3.3. Header
- Breadcrumbs (okruszki).
- Wskaźnik stanu Hub (minimum: sukces root `/health` lub równoważny status zagregowany).
- Bieżąca rola/konto.
- Logout.

---

## 4. Ekrany (Wymagania)

### 4.1. Dashboard (read-only)

Cel: szybki przegląd stanu.

Musi pokazywać:
- Wersję/środowisko/uptime (jeśli dostępne w health/metrykach).
- Krótkie KPI (minimum jeden ekran bez głębokich filtrów).

Stany:
- Loading / Error / Empty (jeśli metryki niedostępne).

### 4.2. Network Graph

Cel: wizualizacja sieci zaufania.

Funkcje:
- Zoom/Pan.
- Wyszukiwanie węzła po PID.
- Tooltip na krawędzi: `limit`, `debt/used`, `available` (jeśli dostępne w danych).
- Filtr według ekwiwalentu.

### 4.3. Integrity Dashboard

Cel: widoczność niezmienników i uruchamianie sprawdzania.

UI:
- Tabela sprawdzeń: `name`, `status`, `last_check`, `details`.
- Przycisk „Uruchom pełne sprawdzanie” → potwierdzenie → uruchomienie.

### 4.4. Incidents (Zarządzanie incydentami)

Cel: działania operacyjne na zablokowanych transakcjach.

UI:
- Lista transakcji „stuck” (definicja: status pośredni + przekroczony wiek SLA).
- Działanie: `Force Abort` z obowiązkowym podaniem przyczyny.

### 4.5. Participants

Cel: moderacja/zarządzanie operacyjne uczestnikami.

UI:
- Wyszukiwanie po PID.
- Działania: Freeze/Unfreeze (z przyczyną).
- Dla `auditor` — tylko podgląd.

### 4.6. Config

Cel: przegląd i zmiana konfiguracji runtime.

UI:
- Tabela „klucz → wartość → opis/domyślne/ograniczenia”.
- Edycja tylko podzbioru runtime (co najmniej — ostrzeżenie, jeśli klucz nie jest runtime).
- Po `PATCH` pokaż listę `updated[]`.

### 4.7. Feature Flags

UI:
- Przełączniki:
  - `feature_flags.multipath_enabled`
  - `feature_flags.full_multipath_enabled`
  - `clearing.enabled` (z sekcji `clearing.*`, wyświetlane jako „Clearing enabled”)
- Dla eksperymentalnych (np. `full_multipath_enabled`) — ostrzeżenie.

Uwaga: `clearing.enabled` technicznie znajduje się w sekcji `clearing.*` konfiguracji (patrz `config-reference.md`), ale dla wygody UI wyświetlany jest razem z flagami funkcji.

### 4.8. Audit Log

UI:
- Tabela z stronicowaniem: `timestamp`, `actor`, `role`, `action`, `object`, `reason`.
- Panel szczegółów wpisu: `before_state`/`after_state`.

### 4.9. Events (timeline)

UI:
- Filtry: `event_type`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, zakres dat.
- Tabela/oś czasu.

### 4.10. Equivalents (MVP)

Cel: zarządzanie słownikiem ekwiwalentów (wchodzi w skład MVP zgodnie z `admin-console-minimal-spec.md` §3.4).

UI:
- Tabela: `code`, `description`, `precision`, `is_active`.
- Działania:
	- Create
	- Edit
	- Activate/Deactivate

Wymagania:
- Wszelkie zmiany muszą trafiać do audit-log.

### 4.11. Transactions (opcjonalne / Faza 2)

Cel: przegląd operacyjny transakcji wszystkich typów.

UI:
- Filtry: `tx_id`, `initiator_pid`, `type`, `state`, `equivalent`, zakres dat.
- Tabela: `tx_id`, `type`, `state`, `initiator_pid`, `created_at`.
- Szczegóły: `payload`, `error`, `signatures`.

### 4.12. Clearing (opcjonalne / Faza 2)

Cel: oddzielna lista transakcji kliringowych.

UI:
- Filtry: `state`, `equivalent`, zakres dat.
- Tabela/szczegóły: jak w Transactions.

### 4.13. Liquidity analytics (opcjonalne / Faza 2)

Cel: zagregowane wykresy/tabele płynności i efektywności kliringu.

UI:
- Filtry: `equivalent` (opcjonalnie), zakres dat.
- Widoki:
	- summary (KPI)
	- series (szeregi czasowe)

---

## 5. Stan globalny i klient API

### 5.1. Pinia stores (minimum)
- `useAdminAuthStore`: token, rola, user info.
- `useAdminConfigStore`: config + ostatnio zaktualizowane klucze.
- `useAdminGraphStore`: dane grafu według ekwiwalentu.

### 5.2. Klient API
- Base URL: `/api/v1`.
- Authorization: `Bearer`.
- Jednolite przetwarzanie koperty `{success,data}` i błędów `{success:false,error:{code,message,details}}`.

### 5.3. Sesja i przechowywanie tokenów (Normatywne)
- Konsola administratora musi działać tylko przez TLS.
- Tokeny muszą być przechowywane w pamięci (runtime). Trwałe przechowywanie (localStorage) nie jest wymaganiem MVP.
- Przy `401` UI musi przenieść użytkownika do stanu „wymagane logowanie”.
- Przy `403` UI musi pokazać „Brak uprawnień” i nie próbować ponawiać żądania.

---

## 6. Mapowanie API (Ekran → Endpoint → Pola → Błędy)

Uwaga: szczegółowe kontrakty muszą odpowiadać `api/openapi.yaml`. Jeśli endpointu brakuje w OpenAPI — jest to uważane za wymaganie backendowe i musi zostać dodane do kontraktu.

### 6.1. Config
- `GET /admin/config` → obiekt kluczy konfiguracji.
- `PATCH /admin/config` → `{updated: string[]}`.

Zasady UI:
- Edycja dozwolona tylko, jeśli rola pozwala (minimum: `admin`/`operator`).
- Po udanym `PATCH` UI aktualizuje tabelę konfiguracji i wyświetla listę zaktualizowanych kluczy.

### 6.2. Feature Flags
- `GET /admin/feature-flags`
- `PATCH /admin/feature-flags`

Uwaga: endpoint zwraca/przyjmuje `multipath_enabled`, `full_multipath_enabled`, `clearing_enabled`. Parametr `clearing_enabled` technicznie odpowiada `clearing.enabled` w konfiguracji, ale dla wygody UI jest połączony z flagami funkcji.

Zasady UI:
- Każda operacja zmiany musi wymagać wyraźnego potwierdzenia (minimum: dialog potwierdzenia).

### 6.3. Participants
- `POST /admin/participants/{pid}/freeze` (body: `{reason}`)
- `POST /admin/participants/{pid}/unfreeze` (body: `{reason?}`)
- `POST /admin/participants/{pid}/ban` (body: `{reason}`)
- `POST /admin/participants/{pid}/unban` (body: `{reason}`)

Zasady UI:
- `reason` obowiązkowe dla freeze.
- Po działaniu UI pokazuje toast + zapisuje fakt w lokalnym logu UI (nie zastępuje audit log).

### 6.4. Audit Log / Events
- `GET /admin/audit-log` (stronicowane)
- `GET /admin/events` (stronicowane)

Pola (wskazówki do wyświetlania, zgodne z `api/openapi.yaml`):
- AuditLogEntry: `id`, `timestamp`, `actor_id`, `actor_role`, `action`, `object_type`, `object_id`, `reason`, `before_state`, `after_state`, `request_id`, `ip_address`.
- DomainEvent: `event_id`, `event_type`, `timestamp`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, `payload`.

### 6.5. Graph / Integrity / Incidents
- `GET /admin/analytics/graph?equivalent={code}`
- `GET /admin/integrity/status`
- `POST /admin/integrity/check`
- `POST /admin/transactions/{tx_id}/abort` (body: `{reason}`)

Zasady UI:
- Graph: filtr `equivalent` obowiązkowy.
- Integrity check: działanie musi wymagać potwierdzenia.
- Abort: `reason` obowiązkowe; po abort UI powinien zasugerować przejście do Events i sprawdzenie powiązanego `tx_id`.

### 6.6. Equivalents (opcjonalne / Faza 2)
- `GET /admin/equivalents` (query: `include_inactive`)
- `POST /admin/equivalents` (body: AdminEquivalentUpsert)
- `PATCH /admin/equivalents/{code}` (body: AdminEquivalentUpsert)

### 6.7. Transactions / Clearing (opcjonalne / Faza 2)
- `GET /admin/transactions` (stronicowane)
- `GET /admin/transactions/{tx_id}`
- `GET /admin/clearing` (stronicowane)

### 6.8. Liquidity analytics (opcjonalne / Faza 2)
- `GET /admin/analytics/stats`

---

## 7. Macierz stanów UI (Normatywne)

Obowiązkowe dla każdego ekranu:
- Loading (szkielet/spinner)
- Error (z możliwością ponowienia - CTA)
- Empty (komunikat wyjaśniający)

Minimalne teksty:
- `403`: „Brak uprawnień do przeglądania tej sekcji”
- `401`: „Sesja wygasła. Zaloguj się ponownie”

---

## 8. Instrukcje dla AI (Prompts)

> "Stwórz komponent Vue 3 dla panelu administracyjnego GEO Hub na Element Plus (script setup). Komponent: [Nazwa Ekranu]. Zaimplementuj loading/empty/error, wyciąganie danych z koperty {success,data}, obsługę 401/403. Endpoint(y): [lista]."