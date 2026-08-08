# GEO Hub: Jak wnosić wkład

**Wersja:** 0.1  
**Data:** Listopad 2025

---

## Spis treści

1. [Rozpoczęcie pracy](#1-rozpoczęcie-pracy)  
2. [Struktura projektu](#2-struktura-projektu)  
3. [Development](#3-development)  
4. [Code Style](#4-code-style)  
5. [Testowanie](#5-testowanie)  
6. [Proces Pull Request](#6-proces-pull-request)  
7. [Decyzje architektoniczne](#7-decyzje-architektoniczne)  
8. [Tworzenie addonów](#8-tworzenie-addonów)  
9. [Dokumentacja](#9-dokumentacja)  
10. [Społeczność](#10-społeczność)

---

## 1. Rozpoczęcie pracy

### 1.1. Wymagania

- Python 3.11+  
- PostgreSQL 15+  
- Redis 7+  
- Git  
- Docker (zalecany)

### 1.2. Fork i klonowanie

```bash
# Fork repozytorium przez GitHub UI

# Sklonuj swój fork
git clone https://github.com/YOUR_USERNAME/geo-hub.git
cd geo-hub

# Dodaj upstream
git remote add upstream https://github.com/geo-protocol/geo-hub.git
```

### 1.3. Konfiguracja środowiska

```bash
# Utwórz wirtualne środowisko
python3.11 -m venv venv
source venv/bin/activate

# Zainstaluj zależności (w tym dev)
pip install -e ".[dev]"

# Skonfiguruj pre-commit hooks
pre-commit install
```

### 1.4. Uruchomienie przez Docker

```bash
# Lokalny development zawsze używa pliku bazowego oraz nakładki dev
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db redis

# Uruchom aplikację
# Uwaga: migracje są uruchamiane automatycznie przy starcie kontenera.
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

### 1.5. Weryfikacja instalacji

```bash
# Uruchom testy
pytest

# Sprawdź lintery
ruff check .
mypy app/

# Otwórz dokumentację (Swagger UI)
open http://localhost:8000/docs
# (lub http://127.0.0.1:18000/docs przy uruchomieniu lokalnym przez scripts/run_local.ps1)
```

---

## 2. Struktura projektu

```text
geo-hub/
├── app/
│   ├── __init__.py
│   ├── main.py              # Punkt wejścia FastAPI
│   ├── config.py            # Konfiguracja
│   │
│   ├── api/                 # Endpointy HTTP/WebSocket
│   │   ├── v1/              # Wersja API
│   │   │   ├── router.py    # Główny router
│   │   │   ├── auth.py
│   │   │   ├── participants.py
│   │   │   ├── trustlines.py
│   │   │   ├── payments.py
│   │   │   └── websocket.py
│   │   └── admin/           # Panel admina
│   │
│   ├── core/                # Logika biznesowa
│   │   ├── auth/
│   │   ├── participants/
│   │   ├── trustlines/
│   │   ├── payments/
│   │   ├── clearing/
│   │   └── events/          # Event bus
│   │
│   ├── models/              # Modele Pydantic
│   │   ├── participant.py
│   │   ├── trustline.py
│   │   ├── debt.py
│   │   ├── transaction.py
│   │   └── messages.py
│   │
│   ├── db/                  # Baza danych
│   │   ├── base.py
│   │   ├── session.py
│   │   └── models/          # Modele SQLAlchemy
│   │
│   └── addons/              # System addonów
│
├── migrations/              # Migracje Alembic
├── tests/                   # Testy
├── docs/                    # Dokumentacja
├── docker/                  # Pliki Docker
│
├── pyproject.toml           # Zależności i konfiguracja
├── alembic.ini              # Konfiguracja Alembic
└── README.md
```

### 2.1. Warstwy architektury

```text
┌─────────────────────────────────────┐
│            API Layer                │  ← Handlery HTTP/WS
│  (app/api/)                         │     Walidacja, serializacja
├─────────────────────────────────────┤
│           Core Layer                │  ← Logika biznesowa
│  (app/core/)                        │     Services, Engines
├─────────────────────────────────────┤
│          Models Layer               │  ← Modele Pydantic
│  (app/models/)                      │     DTO, schematy
├─────────────────────────────────────┤
│            DB Layer                 │  ← SQLAlchemy
│  (app/db/)                          │     Modele ORM, sesje
└─────────────────────────────────────┘
```

---

## 3. Development

### 3.1. Tworzenie nowej gałęzi

```bash
# Synchronizacja z upstream
git fetch upstream
git checkout main
git merge upstream/main

# Utwórz gałąź feature
git checkout -b feature/my-feature
```

### 3.2. Nazewnictwo gałęzi

| Typ     | Format                 | Przykład                         |
|--------|------------------------|----------------------------------|
| Feature | `feature/description` | `feature/multi-path-payments`    |
| Bugfix  | `fix/description`     | `fix/routing-cycle-detection`    |
| Docs    | `docs/description`    | `docs/api-examples`              |
| Refactor| `refactor/description`| `refactor/payment-engine`        |

### 3.3. Uruchomienie w trybie deweloperskim

```bash
export ENV=dev

# Uruchom z hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 18000

# Logowanie w trybie debug
DEBUG=true LOG_LEVEL=DEBUG uvicorn app.main:app --reload --host 0.0.0.0 --port 18000
```

### 3.4. Praca z bazą danych

```bash
# Utwórz nową migrację
alembic -c migrations/alembic.ini revision --autogenerate -m "Add column X to table Y"

# Zastosuj migracje
alembic -c migrations/alembic.ini upgrade head

# Cofnij ostatnią migrację
alembic -c migrations/alembic.ini downgrade -1

# Pokaż historię
alembic -c migrations/alembic.ini history
```

---

## 4. Code Style

### 4.1. Python

Używamy:

- **Ruff** — linter (zastępuje flake8, isort, pyupgrade)  
- **Black** — formatowanie (przez `ruff format`)  
- **mypy** — statyczne typowanie  

```bash
# Sprawdzenie
ruff check .
mypy app/

# Automatyczne poprawki
ruff check --fix .
ruff format .
```

### 4.2. Konfiguracja (pyproject.toml)

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
]

[tool.mypy]
python_version = "3.11"
strict = true
```

### 4.3. Zasady pisania kodu

**Serwisy:**
```python
# app/core/payments/service.py

class PaymentEngine:
    """
    Wykonywanie płatności.
    
    Odpowiada za:
    - Routing
    - Koordynację 2PC
    - Zastosowanie zmian
    """
    
    def __init__(
        self,
        db: AsyncSession,
        routing: RoutingService,
        event_bus: EventBus,
    ) -> None:
        self._db = db
        self._routing = routing
        self._events = event_bus
    
    async def create_payment(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        amount: Decimal,
        *,
        max_hops: int = 4,
    ) -> Transaction:
        """
        Utworzyć i wykonać płatność.
        
        Args:
            from_pid: PID nadawcy
            to_pid: PID odbiorcy
            equivalent: Kod ekwiwalentu
            amount: Kwota płatności
            max_hops: Maksymalna długość ścieżki
            
        Returns:
            Transaction z wynikiem
        
        Raises:
            InsufficientCapacity: Za mała pojemność sieci
            ParticipantNotFound: Uczestnik nie znaleziony
        """
        # Implementacja...
```

**Modele Pydantic:**
```python
# app/models/payment.py

class PaymentCreate(BaseModel):
    """Żądanie utworzenia płatności."""
    
    to: str = Field(..., description="PID odbiorcy")
    equivalent: str = Field(..., min_length=1, max_length=16)
    amount: Decimal = Field(..., gt=0, decimal_places=8)
    description: str | None = Field(None, max_length=500)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "to": "5HueCGU8rMjx...",
                "equivalent": "UAH",
                "amount": "100.00",
                "description": "Za usługi"
            }
        }
    )
```

**Endpointy API:**
```python
# app/api/v1/payments.py

@router.post(
    "",
    response_model=PaymentResponse,
    status_code=201,
    summary="Utworzyć płatność",
    responses={
        201: {"description": "Płatność wykonana pomyślnie"},
        400: {"description": "Niewystarczająca pojemność"},
        404: {"description": "Uczestnik nie znaleziony"},
    },
)
async def create_payment(
    request: PaymentCreate,
    current_user: Annotated[Participant, Depends(get_current_user)],
    payment_engine: Annotated[PaymentEngine, Depends(get_payment_engine)],
) -> PaymentResponse:
    """
    Utworzyć nową płatność.
    
    Znajduje ścieżki w sieci zaufania i wykonuje płatność
    z użyciem dwufazowego commita.
    """
    tx = await payment_engine.create_payment(
        from_pid=current_user.pid,
        to_pid=request.to,
        equivalent=request.equivalent,
        amount=request.amount,
    )
    return PaymentResponse.from_transaction(tx)
```

---

## 5. Testowanie

### 5.1. Struktura testów

```text
tests/
├── conftest.py              # Wspólne fixtures
├── unit/                    # Testy jednostkowe
│   ├── core/
│   │   ├── test_routing.py
│   │   ├── test_payments.py
│   │   └── test_clearing.py
│   └── models/
├── integration/             # Testy integracyjne
│   ├── test_payment_flow.py
│   └── test_clearing_flow.py
└── e2e/                     # Testy end-to-end
    └── test_api.py
```

### 5.2. Uruchamianie testów

```bash
# Wszystkie testy
pytest

# Z pokryciem
pytest --cov=app --cov-report=html

# Konkretny moduł
pytest tests/unit/core/test_routing.py

# Po markerach
pytest -m "not slow"

# Równolegle
pytest -n auto
```

### 5.3. Fixtures

```python
# tests/conftest.py

@pytest.fixture
async def db_session():
    """Testowa sesja DB z rollbackiem."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()

@pytest.fixture
def participant_factory(db_session):
    """Fabryka do tworzenia uczestników."""
    async def _create(
        display_name: str = "Test User",
        **kwargs,
    ) -> Participant:
        p = Participant(
            pid=generate_pid(),
            public_key=generate_keypair()[0],
            display_name=display_name,
            **kwargs,
        )
        db_session.add(p)
        await db_session.flush()
        return p
    
    return _create
```

### 5.4. Przykłady testów

```python
# tests/unit/core/test_routing.py

class TestRoutingService:
    """Testy routingu."""
    
    async def test_find_direct_path(
        self,
        routing_service: RoutingService,
        alice: Participant,
        bob: Participant,
    ):
        """Znajduje bezpośrednią ścieżkę między uczestnikami."""
        # Arrange
        await create_trust_line(alice, bob, limit=1000)
        
        # Act
        paths = await routing_service.find_paths(
            source=alice.pid,
            target=bob.pid,
            equivalent="UAH",
            amount=Decimal("100"),
        )
        
        # Assert
        assert len(paths) == 1
        assert paths[0].path == [alice.pid, bob.pid]
        assert paths[0].capacity >= Decimal("100")
    
    async def test_no_path_when_insufficient_trust(
        self,
        routing_service: RoutingService,
        alice: Participant,
        bob: Participant,
    ):
        """Nie znajduje ścieżki przy zbyt małym zaufaniu."""
        # Arrange
        await create_trust_line(alice, bob, limit=50)
        
        # Act & Assert
        with pytest.raises(NoRouteFound):
            await routing_service.find_paths(
                source=alice.pid,
                target=bob.pid,
                equivalent="UAH",
                amount=Decimal("100"),
            )
```

---

## 6. Proces Pull Request

### 6.1. Przygotowanie PR

```bash
# Upewnij się, że wszystkie checki przechodzą
ruff check .
mypy app/
pytest

# Commit z czytelną wiadomością
git commit -m "feat(payments): add multi-path routing support

- Implement light multi-path algorithm
- Add path splitting logic
- Update routing service interface

Closes #123"
```

### 6.2. Commit messages

Stosujemy [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Typy:**

- `feat` — nowa funkcjonalność  
- `fix` — naprawa błędu  
- `docs` — dokumentacja  
- `style` — formatowanie  
- `refactor` — refaktoryzacja  
- `test` — testy  
- `chore` — inne (zależności, CI itp.)  

### 6.3. Checklist przed PR

- [ ] Kod zgodny z wytycznymi stylu  
- [ ] Dodane/zaktualizowane testy  
- [ ] Wszystkie testy przechodzą  
- [ ] Zaktualizowana dokumentacja (jeśli dotyczy)  
- [ ] Dodane type hints  
- [ ] Brak TODO/FIXME w kodzie  
- [ ] PR jasno opisuje zmiany  

### 6.4. Code Review

Po utworzeniu PR:

1. CI automatycznie uruchamia testy i lintery  
2. Wymagany co najmniej 1 approve od maintainer'a  
3. Wszystkie komentarze muszą zostać rozwiązane  
4. Gałąź musi być aktualna względem `main`  

---

## 7. Decyzje architektoniczne

### 7.1. ADR (Architecture Decision Records)

Dla istotnych decyzji tworzymy ADR w `docs/adr/`:

```markdown
# ADR-001: Wybór 2PC do koordynacji płatności

## Status
Przyjęte

## Kontekst
Potrzebny mechanizm koordynacji płatności przez wielu uczestników...

## Decyzja
Używamy dwufazowego commita (2PC) z koordynatorem w hubie...

## Konsekwencje
- Prosta implementacja
- Możliwe blokady przy awariach
- Konieczność stosowania timeoutów
```

### 7.2. Zasady projektowe

1. **Prostota ponad uniwersalność**  
   - Nie dodajemy abstrakcji „na wszelki wypadek”  
   - YAGNI (You Aren't Gonna Need It)

2. **Jawność ponad niejawność**  
   - Brak magii  
   - Jasne interfejsy  
   - Type hints wszędzie  

3. **Testowalność domyślnie**  
   - Dependency injection  
   - Małe, izolowane funkcje  
   - Brak globalnego stanu  

4. **Fail fast**  
   - Walidacja na wejściu  
   - Jawne błędy  
   - Brak „cichych” porażek  

---

## 8. Tworzenie addonów

### 8.1. Struktura addona

```text
geo_addon_telegram/
├── __init__.py
├── addon.py           # Główna klasa addona
├── handlers.py        # Event handlers
├── routes.py          # Dodatkowe trasy API
├── config.py          # Konfiguracja
└── pyproject.toml
```

### 8.2. Klasa bazowa

```python
# addon.py
from app.addons.base import AddonBase

class TelegramNotificationsAddon(AddonBase):
    """Powiadomienia Telegram dla GEO Hub."""
    
    name = "telegram_notifications"
    version = "1.0.0"
    
    async def on_load(self, app) -> None:
        """Inicjalizacja przy załadowaniu."""
        self.config = TelegramConfig.from_env()
        self.bot = TelegramBot(self.config.token)
        
    async def on_unload(self) -> None:
        """Czyszczenie przy wyładowaniu."""
        await self.bot.close()
    
    def register_hooks(self, event_bus) -> None:
        """Subskrypcja zdarzeń."""
        event_bus.subscribe("payment.committed", self.on_payment)
        event_bus.subscribe("trustline.created", self.on_trustline)
    
    async def on_payment(self, event: PaymentEvent) -> None:
        """Wysłanie powiadomienia o płatności."""
        await self.bot.send_message(
            chat_id=self._get_chat_id(event.to_pid),
            text=f"💰 Otrzymano płatność: {event.amount} {event.equivalent}",
        )
```

### 8.3. Rejestracja w pyproject.toml

```toml
[project.entry-points."geo_hub.addons"]
telegram_notifications = "geo_addon_telegram.addon:TelegramNotificationsAddon"
```

### 8.4. Instalacja addona

```bash
pip install geo-addon-telegram

# Lub w trybie deweloperskim
pip install -e ./geo-addon-telegram
```

---

## 9. Dokumentacja

### 9.1. Struktura dokumentacji

```text
docs/
├── 00-overview.md         # Przegląd projektu
├── 01-concepts.md         # Koncepcje
├── 02-protocol-spec.md    # Specyfikacja protokołu
├── 03-architecture.md     # Architektura
├── 04-api-reference.md    # API
├── 05-deployment.md       # Wdrożenie
├── 06-contributing.md     # Ten plik
└── adr/                   # Architecture Decision Records
```

### 9.2. Docstrings

Stosujemy styl Google:

```python
def find_paths(
    self,
    source: str,
    target: str,
    amount: Decimal,
    *,
    max_hops: int = 4,
) -> list[PaymentPath]:
    """
    Znajduje ścieżki dla płatności.
    
    Wykorzystuje BFS do szukania ścieżek w grafie zaufania.
    Zwraca do 3 ścieżek o wystarczającej pojemności.
    
    Args:
        source: PID źródła
        target: PID celu
        amount: Wymagana kwota
        max_hops: Maksymalna długość ścieżki (domyślnie: 4)
        
    Returns:
        Lista znalezionych ścieżek, posortowanych wg pojemności
        
    Raises:
        NoRouteFound: Brak ścieżki o wystarczającej pojemności
        ParticipantNotFound: Uczestnik nie istnieje
        
    Example:
        >>> paths = await routing.find_paths("alice", "bob", Decimal("100"))
        >>> print(paths[0].path)
        ['alice', 'charlie', 'bob']
    """
```

### 9.3. Dokumentacja API

Generowana automatycznie na podstawie:

- Modeli Pydantic  
- Docstrings endpointów  
- Dekoratorów OpenAPI (FastAPI)  

---

## 10. Społeczność

### 10.1. Kanały komunikacji

- **GitHub Issues** — bugi i feature requesty  
- **GitHub Discussions** — pytania i dyskusje  
- **Telegram** — @geo_protocol_dev  

### 10.2. Zgłaszanie bugów

1. Sprawdź, czy issue już nie istnieje  
2. Utwórz issue zgodnie z template:

   - Opis problemu  
   - Kroki reprodukcji  
   - Oczekiwane zachowanie  
   - Faktyczne zachowanie  
   - Wersja i środowisko  

### 10.3. Proponowanie funkcji

1. Utwórz wątek w Discussions z opisem pomysłu  
2. Omów z zespołem/społecznością  
3. Po akceptacji — utwórz issue  
4. Zaimplementuj (lub poczekaj na wolontariusza)

### 10.4. Code of Conduct

Stosujemy [Contributor Covenant](https://www.contributor-covenant.org/).

W skrócie:

- Szanuj innych  
- Udzielaj konstruktywnego feedbacku  
- Skupiaj się na tym, co najlepsze dla projektu  

---

## Powiązane dokumenty

- [00-overview.md](00-overview.md) — Przegląd projektu  
- [03-architecture.md](03-architecture.md) — Architektura  
- [05-deployment.md](05-deployment.md) — Wdrożenie  

---

**Dziękujemy za Twój wkład w GEO!** 🙏
