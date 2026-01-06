# Klient GEO PWA — Szczegółowa specyfikacja (Blueprint)

**Wersja:** 0.3
**Status:** Blueprint do realizacji bez „domysłów”
**Stos (MVP):** Vue.js 3 (Vite), Pinia. Warstwa UI: Tailwind CSS (opcjonalnie).

Dokument powiązany z bieżącym kontraktem API: `api/openapi.yaml` + `docs/pl/04-api-reference.md`.

---

## 1. Cele i Zakres (Scope)

### 1.1. Cele (MVP)
- Utworzenie/przywrócenie lokalnego portfela (klucze Ed25519) i połączenie z wybranym Hubem.
- Wyświetlanie sald według ekwiwalentów, dostępności płatności i historii.
- Zarządzanie liniami zaufania (tworzenie/aktualizacja/zamykanie) i kontaktami.
- Wysyłanie płatności przez QR/Deeplink i wyświetlanie szczegółów transakcji.

### 1.2. Nie-cele (w tej wersji)
- Pełny tryb offline dla operacji (w offline tylko pamięć podręczna read-only).
- Zarządzanie wieloma portfelami jednocześnie.

---

## 2. Architektura informacji (Sitemap)

Aplikacja — SPA z dolną nawigacją (Tab Bar).

### 2.1. Mapa ekranów

1) **Auth / Onboarding (poza Tab Bar):**
- `Welcome` — wybór: „Utwórz portfel” / „Przywróć”.
- `KeyGeneration` — generowanie kluczy.
- `SeedBackup` — wyświetlenie frazy/eksportu + test potwierdzający.
- `WalletUnlock` — wprowadzenie PIN/hasła do odszyfrowania lokalnego sekretu.
- `HubConnect` — wprowadzenie Base URL Huba + logowanie (Challenge-Response).
- `SessionExpired` — wygasły JWT/odświeżenie, wymagane ponowne logowanie.

2) **Główne zakładki (Main Tabs):**
- `Home` (Dashboard) — salda według ekwiwalentów + szybki QR.
- `Trust` — lista kontaktów/linii zaufania.
- `Activity` — historia operacji (płatności i powiązane zdarzenia).
- `Settings` — PID/QR profilu, Hub, eksport/backup, bezpieczeństwo.

3) **Ekrany pomocnicze (z zakładek, ale osobne trasy):**
- `ScanQR` — skanowanie QR/wklejanie ciągu znaków.
- `PaymentCompose` — wysyłanie płatności (odbiorca, ekwiwalent, kwota, preflight).
- `PaymentConfirm` — potwierdzenie podpisu i wysłanie.
- `PaymentResult` — sukces/błąd + CTA do szczegółów.
- `TransactionDetails` — szczegóły transakcji według `tx_id`.
- `ContactDetails` — profil uczestnika + linie zaufania (w podziale na ekwiwalenty).
- `TrustlineEdit` — tworzenie/zmiana/zamykanie linii zaufania.
- `EquivalentPicker` — wybór ekwiwalentu (lista + wyszukiwanie + ręczne wprowadzanie).

---

## 3. Przepływy użytkownika (Scenariusze)

### 3.1. Tworzenie portfela i logowanie do Huba
1) `Welcome` → „Utwórz portfel”.
2) `KeyGeneration`: generowana jest para kluczy Ed25519.
3) `SeedBackup`: użytkownik zabezpiecza seed/eksport i przechodzi test.
4) `WalletUnlock`: użytkownik ustawia PIN/hasło (do szyfrowania lokalnego sekretu).
5) `HubConnect`: wprowadzenie `hub_base_url`.
6) Logowanie do Huba przez challenge-response (szczegóły w sekcji mapowania API).
7) Przejście do `Home`.

### 3.2. Przywracanie portfela
1) `Welcome` → „Przywróć”.
2) Import seed/eksportu.
3) `WalletUnlock`: ustawienie nowego PIN/hasła (przeszyfrowanie lokalnego sekretu).
4) `HubConnect`: logowanie przez challenge-response.

### 3.3. Wysyłanie płatności przez QR/Deeplink
1) Użytkownik otwiera `ScanQR` (przycisk na `Home`).
2) Skanuje QR.
3) `PaymentCompose` wypełnia się automatycznie (PID, ekwiwalent/kwota — jeśli są).
4) Przy wprowadzaniu kwoty wykonywany jest preflight (`/payments/capacity`), wyświetla się „Dostępne”.
5) `PaymentConfirm`: ostateczne potwierdzenie → podpis → `POST /payments`.
6) `PaymentResult` → `TransactionDetails`.

### 3.4. Tworzenie/aktualizacja linii zaufania
1) `Trust` → „Dodaj kontakt” (szukaj/QR).
2) `ContactDetails` → „Ustaw limit”.
3) `TrustlineEdit`: wybierz ekwiwalent, ustaw limit, potwierdź podpisem.
4) `POST /trustlines` lub `PATCH /trustlines/{id}`.

---

## 4. Dane i przechowywanie lokalne

### 4.1. Zasady
- Lokalnie przechowywane tylko to, co niezbędne dla UX (cache + zaszyfrowany sekret).
- Klucz prywatny i seed nigdy nie są wysyłane do sieci.

### 4.2. Magazyny
- **IndexedDB** (normatywne):
    - `wallet.v1` — zaszyfrowany sekret + parametry KDF.
    - `cache.v1` — cache read-only (salda, linie zaufania, aktywność) powiązany z `hub_base_url`.
- **Memory** (runtime):
    - odszyfrowany klucz prywatny (na czas sesji)
    - `access_token`

---

## 5. Format QR/Deeplink (v1, Normatywne)

### 5.1. Zasady ogólne
- Payload QR — ciąg UTF-8.
- Wszelkie parametry URL muszą być percent-encoded.
- Klient ma obowiązek walidować format przed podjęciem działań.

### 5.2. Schemat URI

Obsługiwane co najmniej dwa typy:

1) **Profil uczestnika**
```
geo://v1/participant?pid={PID}&hub={HUB_BASE_URL?}
```

2) **Żądanie płatności**
```
geo://v1/pay?to={PID}&equivalent={CODE?}&amount={AMOUNT?}&memo={TEXT?}&hub={HUB_BASE_URL?}
```

Wyjaśnienia:
- `hub` — opcjonalnie. Jeśli podano i różni się od bieżącego, UI powinien pokazać ostrzeżenie i zaproponować przełączenie Huba.
- `amount` — ciąg znaków kwoty (`"100.00"`), lub brak.
- `memo` — opcjonalnie, tylko do wyświetlenia (niekoniecznie wysyłane na serwer).

### 5.3. Błędy formatu
- Jeśli payload nie zaczyna się od `geo://v1/` → pokaż „Nieobsługiwany QR”.
- Jeśli `pid`/`to` nie przechodzi walidacji PID → pokaż „Nieprawidłowy PID”.

---

## 6. Model bezpieczeństwa (Normatywne)

### 6.1. Klucze i identyfikatory
- Para kluczy Ed25519 generowana na kliencie.
- PID obliczany jako `base58(sha256(pubkey))`.
- Uwierzytelnianie serwera: challenge-response (podpisywane kluczem prywatnym).

### 6.2. Co to jest „seed” w kontekście UX
- Jeśli używana jest fraza seed/eksport, jest to materiał prywatny wystarczający do odzyskania klucza prywatnego.
- UX musi nazywać to „Sekretem odzyskiwania” i wyraźnie ostrzegać o ryzyku.

### 6.3. Szyfrowanie sekretu na urządzeniu
- Sekret (seed/privkey) przechowywany **tylko w formie zaszyfrowanej**.
- KDF: PBKDF2-HMAC-SHA256 (WebCrypto), parametry:
    - `salt`: 16+ bajtów (losowe)
    - `iterations`: minimum 200 000 (może być więcej na desktopie)
- Szyfr: AES-GCM:
    - `iv`: 12 bajtów (losowe)
    - `ciphertext`: bajty

W IndexedDB przechowywać obiekt (przykład logiczny):
```json
{
    "version": 1,
    "kdf": {"name": "PBKDF2", "hash": "SHA-256", "salt": "base64...", "iterations": 200000},
    "cipher": {"name": "AES-GCM", "iv": "base64..."},
    "ciphertext": "base64..."
}
```

### 6.4. Tokeny
- `access_token` przechowywać w pamięci.
- `refresh_token` przechowywać w IndexedDB (najlepiej szyfrować tym samym kluczem co sekret).
- Przy `401` klient wykonuje jedną próbę `POST /auth/refresh`, następnie albo ponawia próbę, albo przechodzi do `SessionExpired`.

---

## 7. Mapowanie API (Ekran → Endpoint → Pola → Cache)

Uwaga: OpenAPI definiuje kopertę `{success,data}`. Klient musi posiadać ujednolicony `unwrap()` do wyciągania `data`.

### 7.1. Auth / Hub

**HubConnect (logowanie):**
- `POST /auth/challenge` → `{challenge, expires_at}`
- `POST /auth/login` (podpis challenge) → `{access_token, refresh_token, expires_in, participant}`

**Rejestracja uczestnika (pierwsze uruchomienie):**
- `POST /participants` → `{pid, display_name, status, created_at}`

**Odświeżanie tokena:**
- `POST /auth/refresh` → `{access_token, refresh_token, expires_in}`

Cache:
- `hub_base_url` (ustawienie)
- `participant` (w cache.v1)

### 7.2. Home (Dashboard)

Dane:
- `GET /balance` → `BalanceSummary.equivalents[]`:
    - `code` (kod)
    - `net_balance`
    - `available_to_spend`, `available_to_receive`

Cache:
- `balance_summary` na `hub_base_url`, TTL 60s (przy online odświeżanie pull-to-refresh).

### 7.3. Activity (Historia)

Lista:
- `GET /payments?direction=all&equivalent={code?}&status={...}&page={...}` → `PaymentResult[]`

Szczegóły:
- `GET /payments/{tx_id}` → `PaymentResult`

Cache:
- strony activity cache'ować (read-only), TTL 5–10 minut.

### 7.4. ScanQR / PaymentCompose / PaymentConfirm

Preflight (sprawdzenie możliwości):
- `GET /payments/capacity?to={pid}&equivalent={code}&amount={amount}` →
    - `can_pay`, `max_amount`, `routes_count`, `estimated_hops`

Diagnostyka (na żądanie użytkownika, nie domyślnie):
- `GET /payments/max-flow?to={pid}&equivalent={code}` → `max_amount` + `paths[]` + `bottlenecks[]`

Tworzenie:
- `POST /payments` → `PaymentResult` (w tym `status`, `routes[]` przy sukcesie, lub `error` przy ABORTED)

### 7.5. Trust / ContactDetails / TrustlineEdit

Kontakty/uczestnicy:
- `GET /participants/search?q={...}&page={...}&per_page={...}`
- `GET /participants/{pid}`

Linie zaufania:
- `GET /trustlines?direction={...}&equivalent={...?}&status={...?}&page={...}`
- `POST /trustlines`
- `PATCH /trustlines/{id}`
- `DELETE /trustlines/{id}`

### 7.6. Settings

Profil:
- `GET /participants/me`
- `PATCH /participants/me`

### 7.7. WebSocket (Czas rzeczywisty)

Łączenie:
- `wss://{hub_base_url}/api/v1/ws?token={access_token}`

Subskrypcja zdarzeń (zestaw minimalny):
- `payment.received` — płatność przychodząca
- `trustline.updated` — zmiana linii zaufania
- `clearing.completed` — wykonano kliring

Heartbeat:
- Klient wysyła `ping` co 30 sekund przy braku ruchu wychodzącego.
- Jeśli przez 90 sekund brak wiadomości przychodzących — uznać połączenie za zerwane.

Strategia Reconnect (Normatywne, zgodne z `04-api-reference.md` §7.6):
- Ponowne łączenie z wykładniczym opóźnieniem (exponential backoff): 1s, 2s, 5s, 10s, 30s.
- Po ponownym połączeniu: ponownie `subscribe`, weryfikacja stanu przez REST.
- Gotowość na duplikaty i pominięcia zdarzeń (dostarczanie at-most-once).

---

## 8. Wybór Ekwiwalentu (Źródło danych)

### 8.1. Źródło (Priorytet)
1) `GET /equivalents` (jeśli zaimplementowane na Hubie) — normalny słownik.
2) W przeciwnym razie: suma kodów z `GET /balance`, `GET /trustlines` (lokalnie znane ekwiwalenty).
3) W przeciwnym razie: ręczne wprowadzanie (użytkownik wpisuje dowolny kod).

### 8.2. Walidacja kodu ekwiwalentu
- `CODE` musi być ciągiem o długości 2..16.
- Dozwolone znaki: łacińskie/cyfry/`_`/`-`.
- Rekomendacja: pokaż ostrzeżenie, jeśli kod nie pochodzi ze słownika.

---

## 9. Macierz stanów UI (Normatywne)

### 9.1. Stany ogólne
- **Loading:** szkielet/loader + blokada działań wymagających danych.
- **Empty:** tekst wyjaśniający + CTA.
- **Error:** czytelny komunikat + „Ponów”.
- **Offline:** baner + blokada operacji zapisu.

### 9.2. Według ekranów (Zestaw minimalny)

**Home:**
- Loading: „Ładowanie salda…”
- Empty: „Brak ekwiwalentów. Utwórz linię zaufania lub otrzymaj płatność.” → CTA: „Dodaj Zaufanie”
- Error: „Nie udało się załadować salda” → CTA: „Ponów”

**Trust:**
- Empty: „Brak linii zaufania” → CTA: „Dodaj Kontakt”

**Activity:**
- Empty: „Brak operacji”

**PaymentCompose:**
- Jeśli `can_pay=false`: pokaż „Niewystarczająca przepustowość” + przycisk „Pokaż maksimum” (wywołuje `/payments/max-flow`).

**SessionExpired:**
- Tekst: „Sesja wygasła. Zaloguj się ponownie.”
- CTA: „Odblokuj portfel” → `WalletUnlock` → `HubConnect`.

---

## 10. Instrukcje dla AI (Prompts)

Zasada: generujemy komponenty nie narzucając „nowych kolorów/motywów”, używamy podstawowej palety i istniejących tokenów designu projektu.

### 10.1. Dashboard
> "Stwórz komponent Vue 3 Dashboard. Na górze — lista kart według ekwiwalentów (kod, saldo netto, dostępne do wydania). Poniżej — przycisk 'Skanuj QR' i lista ostatnich operacji (do 5). Dodaj stany loading/empty/error i obsługę offline (read-only)."

### 10.2. Payment compose
> "Stwórz komponent PaymentCompose. Pola: odbiorca PID (readonly), ekwiwalent (przez EquivalentPicker), kwota. Przy wprowadzaniu kwoty wykonaj zapytanie /payments/capacity i pokaż can_pay/max_amount/routes_count/estimated_hops. Przycisk 'Wyślij' prowadzi do PaymentConfirm."

### 10.3. Key / wallet storage
> "Napisz usługę w TypeScript do przechowywania sekretu portfela w IndexedDB w formie zaszyfrowanej: PBKDF2(SHA-256) + AES-GCM. Funkcje: setupPin(pin, secret), unlock(pin) -> secret, lock(), isUnlocked(), rotatePin(oldPin,newPin)."