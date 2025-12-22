# GEO Hub — Rejestr konfiguracji (parametry)

Ten dokument jest **jedynym źródłem prawdy** dla parametrów konfiguracyjnych GEO Hub MVP: cel, dozwolone wartości, wartości domyślne i ryzyka.

Powiązane dokumenty:
- Specyfikacja protokołu (włącznie z multipath/full multipath): [`docs/pl/02-protocol-spec.md`](docs/pl/02-protocol-spec.md)
- Wdrożenie i schemat konfiguracji (env + YAML): [`docs/pl/05-deployment.md`](docs/pl/05-deployment.md)
- Minimalna konsola administracyjna do zarządzania parametrami: [`docs/pl/admin-console-minimal-spec.md`](docs/pl/admin-console-minimal-spec.md)

---

## 1. Zasady ogólne

### 1.1. Dwa poziomy konfiguracji

1) **Zmienne środowiskowe (.env)** — infrastruktura/sekrety/integracje (DB, Redis, klucze, itp.).  
2) **Konfiguracja Hub YAML** — parametry protokołu i zachowania (routing/clearing/limits/flags/observability).

W bieżących dokumentach niektóre parametry mogą pojawiać się jako zmienne env (np. limity/timeouty). Dla MVP **konfiguracja YAML jest kanoniczna**, env jest tylko dla infrastruktury i sekretów.

### 1.2. Runtime vs Restart/Migracja

- **Runtime (przez konsolę administracyjną)**: można zmienić w trakcie działania bez restartu (z obowiązkowym audytem). Typowo: `feature_flags.*`, `routing.*`, `clearing.*`, `limits.*`, `observability.*`.
- **Wymagany restart**: zmiana wymaga restartu procesu/poda. Typowo: `protocol.*` (timeouty protokołu) i niektóre `security.*`.
- **Wymagana migracja**: zmiana wymaga migracji/sprawdzenia kompatybilności stanu. Typowo: `database.*` i niektóre `integrity.*` (jeśli wpływają na format/przechowywanie).

---

## 2. Tabela parametrów (według sekcji)

Poniżej: **cel / wartości / domyślna / tryb aplikacji / wpływ i ryzyka**.

---

## 2.1. `feature_flags.*` (runtime)

### `feature_flags.multipath_enabled`
- Cel: włączenie dzielenia płatności na wiele ścieżek (jeśli `false`, routing próbuje znaleźć 1 ścieżkę).
- Wartości: `true|false`
- Domyślna: `true`
- Aplikacja: runtime
- Ryzyka: wyłączenie pogarsza przepustowość płatności w sfragmentowanych sieciach.

### `feature_flags.full_multipath_enabled`
- Cel: włączenie eksperymentalnego **pełnego multipath** (do benchmarków).
- Wartości: `true|false`
- Domyślna: `false`
- Aplikacja: runtime
- Ryzyka: może gwałtownie zwiększyć koszt routingu; włączaj tylko ze skonfigurowanym budżetem/timeoutami i metrykami.

---

## 2.2. `routing.*` (runtime)

### `routing.multipath_mode`
- Cel: wybrany tryb multipath.
- Wartości: `limited|full`
- Domyślna: `limited`
- Aplikacja: runtime
- Ryzyka: `full` — eksperymentalny; musi być ograniczony budżetem/timeoutami. Zaleca się włączanie `full` tylko razem z `feature_flags.full_multipath_enabled`.

### `routing.max_path_length`
- Cel: górna granica długości ścieżki (skoków) dla routingu.
- Wartości: `1..12` (praktyczne: `3..8`)
- Domyślna: `6`
- Aplikacja: runtime
- Ryzyka: zwiększenie wartości podnosi koszt wyszukiwania i pogarsza wyjaśnialność.

### `routing.max_paths_per_payment`
- Cel: maksymalna liczba ścieżek używanych do dzielenia jednej płatności.
- Wartości: `1..10`
- Domyślna: `3`
- Aplikacja: runtime
- Ryzyka: zwiększenie wartości podnosi liczbę uczestników 2PC i prawdopodobieństwo timeoutów/abort; potrzebne do sprawdzeń wydajności.

### `routing.path_finding_timeout_ms`
- Cel: całkowity timeout na znalezienie trasy dla płatności.
- Wartości: `50..5000`
- Domyślna: `500`
- Aplikacja: runtime
- Ryzyka: zbyt niski → wiele odrzuceń; zbyt wysoki → wzrost latencji p99 i obciążenia.

### `routing.route_cache_ttl_seconds`
- Cel: TTL cache wyników routingu.
- Wartości: `0..600`
- Domyślna: `30`
- Aplikacja: runtime
- Ryzyka: wysoki TTL przy szybko zmieniającym się grafie może dawać nieaktualne trasy i dodatkowe aborty.

### `routing.full_multipath_budget_ms`
- Cel: dodatkowy budżet czasu/kosztu dla trybu `full`.
- Wartości: `0..10000`
- Domyślna: `1000`
- Aplikacja: runtime
- Ryzyka: zwiększenie budżetu może przeciążyć CPU i pogorszyć latencje ogona.

### `routing.full_multipath_max_iterations`
- Cel: limit iteracji dla implementacji typu max-flow (jeśli używane).
- Wartości: `0..100000`
- Domyślna: `100`
- Aplikacja: runtime
- Ryzyka: wysoki limit → nieprzewidywalny czas.

### `routing.fallback_to_limited_on_full_failure`
- Cel: jeśli `full` nie mieści się w budżecie/timeout, pozwól na fallback do `limited`.
- Wartości: `true|false`
- Domyślna: `true`
- Aplikacja: runtime
- Ryzyka: może ukryć problemy trybu `full`; wymaga metryk `budget_exhausted`.

---

## 2.3. `clearing.*` (runtime)

### `clearing.enabled`
- Cel: włączenie clearingu.
- Wartości: `true|false`
- Domyślna: `true`
- Aplikacja: runtime
- Ryzyka: wyłączenie łamie kluczową wartość GEO (wzrost długów, gorsza płynność).

### `clearing.trigger_cycles_max_length`
- Cel: maksymalna długość cyklu dla wyszukiwania **wyzwalanego** po transakcji.
- Wartości: `3..6` (dla MVP zalecane `3..4`)
- Domyślna: `4`
- Aplikacja: runtime
- Ryzyka: zwiększenie do `5..6` może gwałtownie podnieść koszt wyszukiwania; parametr potrzebny do sprawdzeń wydajności i musi być chroniony limitami czasu/kandydatów.

### `clearing.min_clearing_amount`
- Cel: minimalna kwota clearingu (filtrowanie "szumowych" cykli).
- Wartości: `0..(zależy od ekwiwalentu)`
- Domyślna: `0.01`
- Aplikacja: runtime
- Ryzyka: zbyt nisko → wiele małych operacji; zbyt wysoko → pominięcie użytecznych clearingów.

### `clearing.max_cycles_per_run`
- Cel: limit transakcji clearingowych na uruchomienie.
- Wartości: `0..100000`
- Domyślna: `200`
- Aplikacja: runtime
- Ryzyka: wysoki limit → szczytowe obciążenie/blokady; niski → powolne "rozładowywanie" długów.

### `clearing.periodic_cycles_5_interval_seconds`
- Cel: okres dla tła wyszukiwania cykli długości 5 (jeśli włączone).
- Wartości: `0..604800` (0 = wyłączone)
- Domyślna: `3600`
- Aplikacja: runtime
- Ryzyka: częste uruchomienia mogą konkurować z płatnościami o zasoby.

### `clearing.periodic_cycles_6_interval_seconds`
- Cel: okres dla tła wyszukiwania cykli długości 6 (jeśli włączone).
- Wartości: `0..604800` (0 = wyłączone)
- Domyślna: `86400`
- Aplikacja: runtime
- Ryzyka: jak wyżej, ale wyższy koszt.

---

## 2.4. `limits.*` (runtime)

Limity produktowe/operacyjne. Ważne: limity powinny uwzględniać `verification_level` (jeśli używany) i ekwiwalenty.

### `limits.max_trustlines_per_participant`
- Cel: górna granica linii zaufania na uczestnika.
- Wartości: `0..10000`
- Domyślna: `50`
- Aplikacja: runtime
- Ryzyka: wysoki limit zwiększa rozmiar grafu i obciążenie routing/clearing; niski limit może pogorszyć UX.

### `limits.default_trustline_limit.*`
- Cel: początkowy limit linii zaufania (jeśli system wspiera auto-default).
- Wartości: liczba ≥ 0 (według typu ekwiwalentu)
- Domyślna: `fiat_like: 100`, `time_like_hours: 2`
- Aplikacja: runtime
- Ryzyka: zbyt wysokie wartości domyślne zwiększają ryzyko niewypłacalności i konfliktów w pilocie.

### `limits.max_trustline_limit_without_admin_approval.*`
- Cel: limit linii zaufania bez jawnej akceptacji admina.
- Wartości: liczba ≥ 0
- Domyślna: `fiat_like: 1000`, `time_like_hours: 10`
- Aplikacja: runtime
- Ryzyka: zbyt wysoko → nadużycia/spam; zbyt nisko → bottleneck admina.

### `limits.max_payment_amount.*`
- Cel: górna granica kwoty płatności.
- Wartości: liczba ≥ 0
- Domyślna: `fiat_like: 200`, `time_like_hours: 4`
- Aplikacja: runtime
- Ryzyka: wysoko → zwiększone ryzyka i koszt multipath; nisko → gorszy UX.

---

## 2.5. `protocol.*` (wymagany restart)

Sekcja `protocol.*` opisuje parametry wpływające na **reguły protokołu** (2PC/walidacja/terminy) i zwykle wymaga restartu.

### `protocol.prepare_timeout_ms`
- Cel: timeout dla fazy PREPARE w 2PC.
- Wartości: `100..60000`
- Domyślna: `3000`
- Aplikacja: restart
- Ryzyka: zbyt niski → wiele abortów przy opóźnieniach sieciowych; zbyt wysoki → długie blokady.

### `protocol.commit_timeout_ms`
- Cel: timeout dla fazy COMMIT w 2PC.
- Wartości: `100..60000`
- Domyślna: `5000`
- Aplikacja: restart
- Ryzyka: zbyt niski → niepowodzenia commit; zbyt wysoki → przetrzymywanie zasobów.

### `protocol.lock_ttl_seconds`
- Cel: TTL blokad prepare.
- Wartości: `10..600`
- Domyślna: `60`
- Aplikacja: restart
- Ryzyka: zbyt niski → blokady wygasają przed zakończeniem; zbyt wysoki → długie blokowanie przy awariach.

### `protocol.max_clock_drift_seconds`
- Cel: dozwolony dryft zegarów między uczestnikami.
- Wartości: `0..60`
- Domyślna: `5`
- Aplikacja: restart
- Ryzyka: zbyt niski → fałszywe błędy dryfu; zbyt wysoki → okno ataku replay.

---

## 2.6. `security.*` (restart/migracja)

### `security.jwt_access_token_ttl_minutes`
- Cel: czas życia access tokena.
- Wartości: `5..1440`
- Domyślna: `60`
- Aplikacja: restart
- Ryzyka: zbyt krótki → częste odświeżanie; zbyt długi → okno bezpieczeństwa.

### `security.jwt_refresh_token_ttl_days`
- Cel: czas życia refresh tokena.
- Wartości: `1..365`
- Domyślna: `7`
- Aplikacja: restart
- Ryzyka: zbyt krótki → tarcie UX; zbyt długi → okno kompromitacji tokena.

### `security.challenge_ttl_seconds`
- Cel: czas życia challenge dla auth.
- Wartości: `60..600`
- Domyślna: `300`
- Aplikacja: restart
- Ryzyka: zbyt krótki → niepowodzenia auth; zbyt długi → okno replay.

---

## 2.7. `observability.*` (runtime)

### `observability.log_level`
- Cel: poziom logowania.
- Wartości: `DEBUG|INFO|WARNING|ERROR`
- Domyślna: `INFO`
- Aplikacja: runtime
- Ryzyka: DEBUG na prod → wpływ na wydajność i wolumen logów.

### `observability.metrics_enabled`
- Cel: włączenie endpointu metryk Prometheus.
- Wartości: `true|false`
- Domyślna: `true`
- Aplikacja: runtime
- Ryzyka: minimalne; wyłączenie traci obserwowalność.

### `observability.structured_logging`
- Cel: włączenie strukturalnego logowania JSON.
- Wartości: `true|false`
- Domyślna: `true`
- Aplikacja: runtime
- Ryzyka: brak znaczących.

---

## 3. Przykład pliku konfiguracyjnego

```yaml
feature_flags:
  multipath_enabled: true
  full_multipath_enabled: false

routing:
  multipath_mode: limited
  max_path_length: 6
  max_paths_per_payment: 3
  path_finding_timeout_ms: 500

clearing:
  enabled: true
  trigger_cycles_max_length: 4
  min_clearing_amount: 0.01
  max_cycles_per_run: 200

limits:
  max_trustlines_per_participant: 50
  max_payment_amount:
    fiat_like: 200
    time_like_hours: 4

protocol:
  prepare_timeout_ms: 3000
  commit_timeout_ms: 5000
  lock_ttl_seconds: 60

observability:
  log_level: INFO
  metrics_enabled: true
```
