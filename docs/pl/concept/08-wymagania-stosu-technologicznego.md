## 8. Wymagania dotyczące stosu technologicznego

Pod cele projektu open‑source „jak Home Assistant”:

- rdzeń ma być w **Pythonie**, z czytelną, modułową architekturą i systemem dodatków;
- klienci — na **wieloplatformowym frameworku** klasy Flutter;
- wszystko ma być maksymalnie „czytelne” i wygodne do rozwoju **z pomocą agenta AI**.

Poniżej warianty architektury z uzasadnieniem, a potem konkretna rekomendacja.

---

## 1. Wymagania wobec stosu (z Twojego opisu)

Zbierzmy je wprost (to będzie punkt odniesienia dalej):

1. **Backend:**
   - język — prosty w czytaniu i zrozumiały dla kontrybutorów;
   - architektura „core + add‑ons”, zbliżona do Home Assistant;
   - dobrze współpracuje z agentem AI (jawne modele danych, mało magii);
   - rozsądna wydajność i wsparcie dla async;
   - standardowa baza danych: PostgreSQL;
   - cache/kolejki: Redis (lub zbliżone).

2. **Frontend / klienci:**
   - nie React (i ogólnie mniej ciężkich frameworków front‑endowych);
   - natywne/wieloplatformowe aplikacje z gotowych modułów (Flutter jako referencja);
   - tak, żeby agent AI mógł generować większość ekranów i logiki.

3. **Open source & modułowość:**
   - wyraźne rozdzielenie core/protocol i rozszerzeń;
   - łatwe do czytania, forkowania i rozwijania;
   - minimum zależności od egzotycznych rozwiązań.

---

## 2. Warianty backendu w Pythonie

### Wariant A. FastAPI + SQLAlchemy + Pydantic (rekomendowany)

**Pomysł:** backend w duchu Home Assistant i nowoczesnych API:

- **FastAPI**:
  - bardzo czytelny, mało „magii”;
  - automatyczna generacja OpenAPI / Swagger (świetne dla agenta AI — widzi typy i endpointy);
  - pełne wsparcie async (ważne dla WebSocket, notyfikacji, procesów w tle).

- **Pydantic**:
  - silnie typizowane modele danych;
  - prosta walidacja i serializacja;
  - modele są czytelne zarówno dla ludzi, jak i agentów AI.

- **SQLAlchemy 2.x + Alembic**:
  - de facto standard w Pythonie;
  - jasne modele ORM, transakcje, migracje.

- **Redis**:
  - cache;
  - prepare‑locki dla płatności;
  - kolejkowanie zadań (np. RQ/Celery/Arq).

- **pytest**:
  - prosty, powszechnie znany framework testowy.

- **Architektura dodatków**:
  - jak w Home Assistant:
    - rdzeń z czytelnymi „domenami” (auth, routing, payments, clearing);
    - integracje/dodatki jako osobne pakiety Pythona, wykrywane przez `entry_points` (`pyproject.toml` / `setup.cfg`);
    - każdy dodatek może:
      - zarejestrować swoje endpointy HTTP/WebSocket;
      - zasubskrybować się na zdarzenia wewnętrzne (np. „PAYMENT.COMMITTED”);
      - dodawać zadania do scheduler’a.

**Dlaczego to dobre dla agenta AI:**

- FastAPI + Pydantic są deklaratywne:

  ```python
  class PaymentRequest(BaseModel):
      from_pid: str
      to_pid: str
      equivalent: str
      amount: Decimal
  ```

  Agent AI łatwo rozumie takie wzorce i potrafi je odtworzyć.

- OpenAPI opisuje cały backend:
  - agent może z niej wywnioskować dostępne operacje, typy, kontrakty.

- Kod jest „zwykłym” Pythonem, nie frameworkowym DSL‑em.

---

### Wariant B. Django + Django REST Framework

Zalety:

- najbardziej znany ekosystem web w Pythonie;
- wbudowany panel admina (szybki, prosty UI administracyjny „z pudełka”).

Wady:

- ciężki monolit, dużo „magii” i warstw;
- REST API przez DRF jest bardziej rozbudowane, mniej przyjazne do generacji kodu przez agenta AI;
- async i WebSocket są mniej naturalne niż w FastAPI.

W kontekście GEO (rdzeń + dodatki + async) Django wydaje się mniej trafnym wyborem.

---

### Wariant C. „Czysty” ASGI (Starlette, aiohttp)

Daje dużo swobody, ale:

- wymaga samodzielnego budowania walidacji i modeli;
- łatwo skończyć z pół‑FastAPI, ale bez jego zalet.

Dla projektu open‑source to niepotrzebna komplikacja.

---

**Wniosek (backend):**  
Najrozsądniejszy wariant to **Python 3.11+ + FastAPI + Pydantic + SQLAlchemy + Redis**, z architekturą rdzeń/dodatki inspirowaną Home Assistant.

---

## 3. Warianty stosu klienckiego

Trzeba rozróżnić:

- **gruby klient dla użytkowników** (mobile/desktop, główny UX);
- **lekki interfejs web** (admin/diagnostyka), który może być bez SPA.

### 3.1. Klient gruby: Flutter

Flutter (Dart) dobrze spełnia Twoje założenia:

- jeden kod na Android, iOS, desktop i nawet web;
- bogaty zestaw gotowych widgetów;
- dobrze strukturyzowane aplikacje (podział na moduły/pakiety UI);
- agent AI współcześnie bardzo dobrze radzi sobie z generowaniem Flutter‑owego kodu (ekrany, formularze, logika stanu);
- brak „magii”: zwykłe klasy, drzewo widgetów, jasne wzorce (BLoC, Riverpod, itp.).

Architektura klienta:

- **core‑app** (szkielet aplikacji: nawigacja, konfiguracja, podstawowe ekrany);
- **moduły/plug‑iny UI** (ekrany/komponenty dla domen: trustlines, payments, clearing, raporty).

Alternatywy:

- **React Native** — odpada ze względu na chęć unikania ciężkiego JS/React.
- **Kotlin Multiplatform (KMM)** — potężne, ale próg wejścia jest wyższy dla społeczności i AI.
- **Qt/QML** — dobre dla desktopu, mobilny UX bywa gorszy, mniejsza „powszechność”.

Sumarycznie Flutter jest najlepszym kompromisem.

### 3.2. Web‑interfejs (admin / diagnostyka)

Zamiast pełnego SPA w JS:

- serwerowe renderowanie HTML (Jinja2) po stronie backendu (FastAPI + Jinja2);
- minimalny JS (HTMX / Alpine.js) dla prostych interakcji.

Zalety:

- prosty, czytelny kod szablonów;
- niewielka liczba zależności frontendowych;
- agent AI łatwo generuje Jinja2 + proste endpointy HTTP.

Ten web‑interfejs może służyć głównie jako:

- panel admina (monitoring, konfiguracja, debugowanie),
- poglądowy UI (np. diagnostyka grafu, metryki).

---

## 4. Jak to złożyć w „rdzeń + dodatki jak Home Assistant”

### 4.1. Rdzeń backendu

- Realizuje:

  - protokół GEO v0.1 (byty: Participant, TrustLine, Debt, Transaction);
  - podstawowe serwisy: routing, payments, clearing;
  - API (REST/WS) dla klientów;
  - wewnętrzny system zdarzeń (event bus).

- Struktura katalogów (przykład):

  ```text
  geo_core/
    api/
      rest/
      websocket/
    domain/
      participants/
      trustlines/
      payments/
      clearing/
      equivalents/
    infra/
      db/
      redis/
      crypto/
    addons/
      loader.py
  ```

### 4.2. Dodatki (add‑ons)

- Osobne pakiety Pythona (np. `geo_addon.reporting_advanced`, `geo_addon.local_currency_X`, `geo_addon.governance`).
- Rejestrowane przez `entry_points` w `pyproject.toml`:

  ```toml
  [project.entry-points."geo.addons"]
  "reporting_advanced" = "geo_addon.reporting_advanced:setup"
  ```

- Rdzeń przy starcie:

  - wykrywa wszystkie entry‑points;
  - wywołuje `setup()` każdego dodatku;
  - dodatek może:
    - zarejestrować endpointy HTTP/WS;
    - zasubskrybować się na zdarzenia (`on_payment_committed`, `on_trustline_changed`, …);
    - dodawać okresowe zadania (scheduler).

To wprost powtarza wzorzec Home Assistant, ale w domenie GEO.

Dla agenta AI to:

- jasny kontrakt: co powinien zrobić nowy dodatek;
- możliwość generowania kompletnego „szkieletu” dodatku na podstawie prostego opisu funkcji.

---

## 5. Ostateczna rekomendacja stosu (backend)

### 5.1. Backend

- **Język:** Python 3.11+
- **Framework web:** FastAPI (ASGI)
- **Modele/DTO:** Pydantic
- **ORM:** SQLAlchemy 2.x
- **Migracje:** Alembic
- **Baza danych:** PostgreSQL
- **Cache / locki / kolejki:** Redis
- **Testy:** pytest
- **Konteneryzacja:** Docker, docker‑compose (później Kubernetes)
- **System dodatków:**
  - entry‑points w `pyproject.toml` / `setup.cfg`;
  - registry dodatków i event‑bus w rdzeniu.

### 5.2. Klienci

- **Główny klient (użytkownik końcowy):** Flutter (Dart)
  - aplikacja mobilna i desktopowa z jednego kodu;
  - podział na moduły domenowe (trustlines, payments, clearing, raporty).
- **Minimalny web‑panel admina:** FastAPI + Jinja2 + HTMX/Alpine.js
  - proste formularze i widoki;
  - bez ciężkiego SPA.

---

## 6. Podsumowanie

Proponowany stos:

- backend w Pythonie (FastAPI + Pydantic + SQLAlchemy + Redis),
- klienci w Flutterze,
- prosty web‑panel admina renderowany na serwerze,
- system dodatków inspirowany Home Assistant,

spełnia wymagania:

- **czytelność i modułowość** kodu (łatwa praca ludzi i agentów AI),
- **otwartość** (łatwe forki i rozszerzenia),
- **realistyczność** dla małych zespołów (MVP w tygodniach, nie w latach),
- **możliwość ewolucji** w stronę bardziej zaawansowanego, rozproszonego GEO (p2p, klastry, integracja z blockchainami).
