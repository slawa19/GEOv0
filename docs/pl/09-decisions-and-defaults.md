# GEO v0.1 — Kluczowe decyzje i wartości domyślne dla MVP

**Wersja:** 0.1  
**Data:** Grudzień 2025

Podsumowanie kluczowych decyzji architektonicznych i zalecanych wartości domyślnych dla MVP.

---

## 1. Kluczowe decyzje MVP

### 1.1. Architektura i węzły

| Decyzja | Wybór MVP |
|---------|-----------|
| **Model węzła** | Hub-centryczny (uczestnik = konto w hubie) |
| **Podpisywanie operacji** | Wymagane po stronie klienta (Ed25519) |
| **Koordynacja 2PC** | Serwer hub (stan + blokady transakcyjne w DB) |
| **Główny klient** | PWA (Web Client) |
| **Mobile/desktop** | Flutter — odroczony do v1.0+ |

### 1.2. Clearing

| Decyzja | Wybór MVP |
|---------|-----------|
| **Tryb** | Automatyczny (planowany + wyzwalany) |
| **Zgoda uczestnika** | Domyślnie `auto_clearing: true` w polityce TrustLine |
| **Długość cyklu (wyzwalany)** | 3–4 węzły |
| **Długość cyklu (periodyczny)** | 5–6 węzłów (opcjonalnie) |

### 1.3. Routing

| Decyzja | Wybór MVP |
|---------|-----------|
| **Tryb podstawowy** | Ograniczony multipath (k-najkrótszych ścieżek) |
| **Max ścieżek** | 3 |
| **Max skoków** | 6 |
| **Pełny multipath** | Wyłączony domyślnie (flaga funkcji do benchmarków) |

### 1.4. Ekwiwalenty

| Decyzja | Wybór MVP |
|---------|-----------|
| **Kto tworzy** | Tylko admin/operator |
| **Zestaw startowy** | UAH, HOUR, kWh |

### 1.5. Weryfikacja

| Decyzja | Wybór MVP |
|---------|-----------|
| **KYC** | Nie zaimplementowane w MVP |
| **Poziomy weryfikacji** | 0–3 (limity i uprawnienia według poziomu) |
| **Moderacja** | Ręczna (admin/operator) |

### 1.6. Uprawnienia operatora

| Decyzja | Wybór MVP |
|---------|-----------|
| **Akcje** | freeze/unfreeze, ban/unban, analiza, operacje kompensacyjne |
| **Audyt** | Obowiązkowy audit-log dla wszystkich akcji |
| **Role** | admin, operator, auditor |

### 1.7. Maszyna stanów transakcji (internal)

Model w bazie przechowuje wewnętrzne wartości `Transaction.state` dla niezawodności operacyjnej (recovery, idempotency, księgowanie 2PC/blokad). Te stany **nie są tym samym** co publiczny „status wyniku” płatności.

**Dozwolone stany wewnętrzne (ograniczenie DB):** `NEW`, `ROUTED`, `PREPARE_IN_PROGRESS`, `PREPARED`, `COMMITTED`, `ABORTED`, `PROPOSED`, `WAITING`, `REJECTED`.

**PAYMENT (przepływ typu 2PC, implementacja MVP):**

| Stan | Znaczenie (MVP) |
|------|------------------|
| `NEW` | Rekord transakcji utworzony; routing policzony i zapisany w `payload` |
| `PREPARED` | Blokady segmentów utworzone (faza 1) |
| `COMMITTED` | Długi zaktualizowane i blokady usunięte (faza 2) |
| `ABORTED` | Błąd terminalny; blokady usunięte (best-effort) i zapisany `error` |

Uwagi:
- `ROUTED` i `PREPARE_IN_PROGRESS` są zarezerwowane jako stany internal, ale obecny MVP payment engine ich nie ustawia.
- `REJECTED` jest zarezerwowany jako stan terminalny dla przyszłych jawnych odrzuceń; w MVP traktowany jako terminalny.

**CLEARING (MVP):**

| Stan | Znaczenie (MVP) |
|------|------------------|
| `NEW` | Utworzono transakcję clearingu |
| `COMMITTED` | Clearing zastosowany pomyślnie |
| `ABORTED` | Błąd terminalny |

**Recovery (MVP):** transakcje utknięte w „aktywnych” stanach internal dłużej niż dozwolony budżet czasu są abortowane, a powiązane blokady czyszczone.

**Publiczny status płatności:** `PaymentResult.status` jest wynikiem finalnym i zwraca tylko `COMMITTED` lub `ABORTED`.

---

## 2. Wartości domyślne i limity

### 2.1. Timeouty 2PC

| Parametr | Domyślnie | Zakres |
|----------|-----------|--------|
| `protocol.transaction_timeout_seconds` | 10 | 5–30 |
| `protocol.prepare_timeout_seconds` | 3 | 1–10 |
| `protocol.commit_timeout_seconds` | 5 | 2–15 |
| `protocol.lock_ttl_seconds` | 60 | 30–300 |

### 2.2. Routing

| Parametr | Domyślnie | Zakres |
|----------|-----------|--------|
| `routing.max_path_length` | 6 | 3–10 |
| `routing.max_paths_per_payment` | 3 | 1–10 |
| `routing.path_finding_timeout_ms` | 500 | 100–2000 |
| `routing.multipath_mode` | `limited` | limited, full |

### 2.3. Clearing

| Parametr | Domyślnie | Zakres |
|----------|-----------|--------|
| `clearing.enabled` | true | true/false |
| `clearing.trigger_cycles_max_length` | 4 | 3–6 |
| `clearing.periodic_cycles_max_length` | 6 | 4–8 |
| `clearing.min_clearing_amount` | 1.00 | 0.01–100 |
| `clearing.max_cycles_per_run` | 100 | 10–1000 |
| `clearing.trigger_interval_seconds` | 0 (natychmiast) | 0–60 |
| `clearing.periodic_interval_minutes` | 60 | 5–1440 |

### 2.4. Limity

| Parametr | Domyślnie | Zakres |
|----------|-----------|--------|
| `limits.default_trust_line_limit` | 1000.00 | 0–∞ |
| `limits.max_trust_line_limit` | 100000.00 | 1000–∞ |
| `limits.max_payment_amount` | 50000.00 | 100–∞ |
| `limits.daily_payment_limit` | 100000.00 | 1000–∞ |

### 2.5. Flagi funkcji

| Parametr | Domyślnie | Opis |
|----------|-----------|------|
| `feature_flags.multipath_enabled` | true | Włączony ograniczony multipath |
| `feature_flags.full_multipath_enabled` | false | Tryb pełny do benchmarków |
| `feature_flags.inter_hub_enabled` | false | Interakcja między hubami |

---

## 3. Wielkość pilota (cel projektowy)

| Metryka | Wartość docelowa |
|---------|------------------|
| **Uczestnicy** | 50–200 |
| **Transakcje/dzień** | do 1000 |
| **Szczytowe obciążenie** | 5–10 tx/sek |
| **Ekwiwalenty** | 1–3 |

---

## Powiązane dokumenty

- [config-reference.md](config-reference.md) — pełny rejestr parametrów z opisami
- [02-protocol-spec.md](02-protocol-spec.md) — specyfikacja protokołu
- [03-architecture.md](03-architecture.md) — architektura systemu
- [admin-console-minimal-spec.md](admin-console-minimal-spec.md) — specyfikacja konsoli administracyjnej
