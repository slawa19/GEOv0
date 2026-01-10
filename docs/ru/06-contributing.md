# GEO Hub: Как вносить вклад

**Версия:** 0.1  
**Дата:** Ноябрь 2025

---

## Содержание

1. [Начало работы](#1-начало-работы)
2. [Структура проекта](#2-структура-проекта)
3. [Разработка](#3-разработка)
4. [Code Style](#4-code-style)
5. [Тестирование](#5-тестирование)
6. [Pull Request процесс](#6-pull-request-процесс)
7. [Архитектурные решения](#7-архитектурные-решения)
8. [Создание аддонов](#8-создание-аддонов)
9. [Документация](#9-документация)
10. [Сообщество](#10-сообщество)

---

## 1. Начало работы

### 1.1. Требования

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Git
- Docker (рекомендуется)

### 1.2. Fork и клонирование

```bash
# Fork репозитория через GitHub UI

# Клонировать ваш fork
git clone https://github.com/YOUR_USERNAME/geo-hub.git
cd geo-hub

# Добавить upstream
git remote add upstream https://github.com/geo-protocol/geo-hub.git
```

### 1.3. Настройка окружения

```bash
# Создать виртуальное окружение
python3.11 -m venv venv
source venv/bin/activate

# Установить зависимости (включая dev)
pip install -e ".[dev]"

# Настроить pre-commit hooks
pre-commit install
```

### 1.4. Запуск через Docker

```bash
# Запустить БД и Redis
docker compose up -d postgres redis

# Применить миграции (через конфиг из репозитория)
alembic -c migrations/alembic.ini upgrade head

# Запустить приложение
uvicorn app.main:app --reload
```

### 1.5. Проверка установки

```bash
# Запустить тесты
pytest

# Проверить линтеры
ruff check .
mypy app/

# Открыть документацию (Swagger UI)
open http://localhost:8000/docs
```

---

## 2. Структура проекта

```
geo-hub/
├── app/
│   ├── __init__.py
│   ├── main.py              # Точка входа FastAPI
│   ├── config.py            # Конфигурация
│   │
│   ├── api/                 # HTTP/WebSocket endpoints
│   │   ├── v1/              # Версия API
│   │   │   ├── router.py    # Главный роутер
│   │   │   ├── auth.py
│   │   │   ├── participants.py
│   │   │   ├── trustlines.py
│   │   │   ├── payments.py
│   │   │   └── websocket.py
│   │   └── admin/           # Админ панель
│   │
│   ├── core/                # Бизнес-логика
│   │   ├── auth/
│   │   ├── participants/
│   │   ├── trustlines/
│   │   ├── payments/
│   │   ├── clearing/
│   │   └── events/          # Event bus
│   │
│   ├── models/              # Pydantic модели
│   │   ├── participant.py
│   │   ├── trustline.py
│   │   ├── debt.py
│   │   ├── transaction.py
│   │   └── messages.py
│   │
│   ├── db/                  # База данных
│   │   ├── base.py
│   │   ├── session.py
│   │   └── models/          # SQLAlchemy модели
│   │
│   └── addons/              # Система аддонов
│
├── migrations/              # Alembic миграции
├── tests/                   # Тесты
├── docs/                    # Документация
├── docker/                  # Docker файлы
│
├── pyproject.toml           # Зависимости и настройки
├── alembic.ini              # Конфигурация Alembic
└── README.md
```

### 2.1. Слои архитектуры

```
┌─────────────────────────────────────┐
│            API Layer                │  ← HTTP/WS handlers
│  (app/api/)                         │     Валидация, сериализация
├─────────────────────────────────────┤
│           Core Layer                │  ← Бизнес-логика
│  (app/core/)                        │     Services, Engines
├─────────────────────────────────────┤
│          Models Layer               │  ← Pydantic модели
│  (app/models/)                      │     DTO, схемы
├─────────────────────────────────────┤
│            DB Layer                 │  ← SQLAlchemy
│  (app/db/)                          │     ORM модели, сессии
└─────────────────────────────────────┘
```

---

## 3. Разработка

### 3.1. Создание новой ветки

```bash
# Синхронизировать с upstream
git fetch upstream
git checkout main
git merge upstream/main

# Создать feature branch
git checkout -b feature/my-feature
```

### 3.2. Именование веток

| Тип | Формат | Пример |
|-----|--------|--------|
| Feature | `feature/description` | `feature/multi-path-payments` |
| Bugfix | `fix/description` | `fix/routing-cycle-detection` |
| Docs | `docs/description` | `docs/api-examples` |
| Refactor | `refactor/description` | `refactor/payment-engine` |

### 3.3. Запуск в режиме разработки

```bash
# Запустить с hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Логирование в debug режиме
DEBUG=true LOG_LEVEL=DEBUG uvicorn app.main:app --reload
```

### 3.4. Работа с базой данных

```bash
# Создать новую миграцию
alembic -c migrations/alembic.ini revision --autogenerate -m "Add column X to table Y"

# Применить миграции
alembic -c migrations/alembic.ini upgrade head

# Откатить последнюю миграцию
alembic -c migrations/alembic.ini downgrade -1

# Просмотреть историю
alembic -c migrations/alembic.ini history
```

---

## 4. Code Style

### 4.1. Python

Используем:
- **Ruff** — линтер (замена flake8, isort, pyupgrade)
- **Black** — форматирование (через ruff format)
- **mypy** — статическая типизация

```bash
# Проверка
ruff check .
mypy app/

# Автоисправление
ruff check --fix .
ruff format .
```

### 4.2. Конфигурация (pyproject.toml)

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

### 4.3. Правила написания кода

**Сервисы:**
```python
# app/core/payments/service.py

class PaymentEngine:
    """
    Исполнение платежей.
    
    Отвечает за:
    - Маршрутизацию
    - 2PC координацию
    - Применение изменений
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
        Создать и исполнить платёж.
        
        Args:
            from_pid: PID плательщика
            to_pid: PID получателя
            equivalent: Код эквивалента
            amount: Сумма платежа
            max_hops: Максимальная длина пути
            
        Returns:
            Transaction с результатом
            
        Raises:
            InsufficientCapacity: Недостаточно ёмкости
            ParticipantNotFound: Участник не найден
        """
        # Реализация...
```

**Модели Pydantic:**
```python
# app/models/payment.py

class PaymentCreate(BaseModel):
    """Запрос на создание платежа."""
    
    to: str = Field(..., description="PID получателя")
    equivalent: str = Field(..., min_length=1, max_length=16)
    amount: Decimal = Field(..., gt=0, decimal_places=8)
    description: str | None = Field(None, max_length=500)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "to": "5HueCGU8rMjx...",
                "equivalent": "UAH",
                "amount": "100.00",
                "description": "За услуги"
            }
        }
    )
```

**API endpoints:**
```python
# app/api/v1/payments.py

@router.post(
    "",
    response_model=PaymentResponse,
    status_code=201,
    summary="Создать платёж",
    responses={
        201: {"description": "Платёж успешно выполнен"},
        400: {"description": "Недостаточно ёмкости"},
        404: {"description": "Участник не найден"},
    },
)
async def create_payment(
    request: PaymentCreate,
    current_user: Annotated[Participant, Depends(get_current_user)],
    payment_engine: Annotated[PaymentEngine, Depends(get_payment_engine)],
) -> PaymentResponse:
    """
    Создать новый платёж.
    
    Находит маршруты через сеть доверия и исполняет платёж
    с использованием двухфазного коммита.
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

## 5. Тестирование

### 5.1. Структура тестов

```
tests/
├── conftest.py              # Общие fixtures
├── unit/                    # Unit тесты
│   ├── core/
│   │   ├── test_routing.py
│   │   ├── test_payments.py
│   │   └── test_clearing.py
│   └── models/
├── integration/             # Интеграционные тесты
│   ├── test_payment_flow.py
│   └── test_clearing_flow.py
└── e2e/                     # End-to-end тесты
    └── test_api.py
```

### 5.2. Запуск тестов

```bash
# Все тесты
pytest

# С покрытием
pytest --cov=app --cov-report=html

# Конкретный модуль
pytest tests/unit/core/test_routing.py

# По маркерам
pytest -m "not slow"

# Параллельно
pytest -n auto
```

### 5.3. Fixtures

```python
# tests/conftest.py

@pytest.fixture
async def db_session():
    """Тестовая сессия БД с откатом."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()

@pytest.fixture
def participant_factory(db_session):
    """Фабрика для создания участников."""
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

### 5.4. Примеры тестов

```python
# tests/unit/core/test_routing.py

class TestRoutingService:
    """Тесты маршрутизации."""
    
    async def test_find_direct_path(
        self,
        routing_service: RoutingService,
        alice: Participant,
        bob: Participant,
    ):
        """Находит прямой путь между участниками."""
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
        """Не находит путь при недостаточном доверии."""
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

## 6. Pull Request процесс

### 6.1. Подготовка PR

```bash
# Убедиться, что все проверки проходят
ruff check .
mypy app/
pytest

# Commit с понятным сообщением
git commit -m "feat(payments): add multi-path routing support

- Implement light multi-path algorithm
- Add path splitting logic
- Update routing service interface

Closes #123"
```

### 6.2. Commit messages

Используем [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Типы:**
- `feat` — новая функциональность
- `fix` — исправление бага
- `docs` — документация
- `style` — форматирование
- `refactor` — рефакторинг
- `test` — тесты
- `chore` — прочее (зависимости, CI)

### 6.3. Checklist перед PR

- [ ] Код соответствует style guide
- [ ] Добавлены/обновлены тесты
- [ ] Все тесты проходят
- [ ] Обновлена документация (если нужно)
- [ ] Добавлены type hints
- [ ] Нет TODO/FIXME в коде
- [ ] PR описывает изменения

### 6.4. Code Review

После создания PR:
1. CI автоматически запустит проверки
2. Минимум 1 approve от maintainer'а
3. Все комментарии разрешены
4. Ветка актуальна с main

---

## 7. Архитектурные решения

### 7.1. ADR (Architecture Decision Records)

Для значимых решений создаём ADR в `docs/adr/`:

```markdown
# ADR-001: Выбор 2PC для координации платежей

## Статус
Принято

## Контекст
Нужен механизм координации платежей через несколько участников...

## Решение
Используем двухфазный коммит (2PC) с координатором на hub'е...

## Последствия
- Простая реализация
- Возможные блокировки при сбоях
- Нужен механизм таймаутов
```

### 7.2. Принципы дизайна

1. **Простота важнее универсальности**
   - Не добавлять абстракции "на будущее"
   - YAGNI (You Aren't Gonna Need It)

2. **Явное лучше неявного**
   - Никакой магии
   - Чёткие интерфейсы
   - Type hints везде

3. **Тестируемость по умолчанию**
   - Dependency injection
   - Маленькие, изолированные функции
   - Нет глобального состояния

4. **Fail fast**
   - Валидация на входе
   - Явные ошибки
   - Нет silent failures

---

## 8. Создание аддонов

### 8.1. Структура аддона

```
geo_addon_telegram/
├── __init__.py
├── addon.py           # Главный класс аддона
├── handlers.py        # Event handlers
├── routes.py          # Дополнительные API routes
├── config.py          # Конфигурация
└── pyproject.toml
```

### 8.2. Базовый класс

```python
# addon.py
from app.addons.base import AddonBase

class TelegramNotificationsAddon(AddonBase):
    """Telegram уведомления для GEO Hub."""
    
    name = "telegram_notifications"
    version = "1.0.0"
    
    async def on_load(self, app) -> None:
        """Инициализация при загрузке."""
        self.config = TelegramConfig.from_env()
        self.bot = TelegramBot(self.config.token)
        
    async def on_unload(self) -> None:
        """Очистка при выгрузке."""
        await self.bot.close()
    
    def register_hooks(self, event_bus) -> None:
        """Подписка на события."""
        event_bus.subscribe("payment.committed", self.on_payment)
        event_bus.subscribe("trustline.created", self.on_trustline)
    
    async def on_payment(self, event: PaymentEvent) -> None:
        """Отправить уведомление о платеже."""
        await self.bot.send_message(
            chat_id=self._get_chat_id(event.to_pid),
            text=f"💰 Получен платёж: {event.amount} {event.equivalent}",
        )
```

### 8.3. Регистрация в pyproject.toml

```toml
[project.entry-points."geo_hub.addons"]
telegram_notifications = "geo_addon_telegram.addon:TelegramNotificationsAddon"
```

### 8.4. Установка аддона

```bash
pip install geo-addon-telegram

# Или для разработки
pip install -e ./geo-addon-telegram
```

---

## 9. Документация

### 9.1. Структура документации

```
docs/
├── 00-overview.md         # Обзор проекта
├── 01-concepts.md         # Концепции
├── 02-protocol-spec.md    # Спецификация протокола
├── 03-architecture.md     # Архитектура
├── 04-api-reference.md    # API
├── 05-deployment.md       # Развёртывание
├── 06-contributing.md     # Этот файл
└── adr/                   # Architecture Decision Records
```

### 9.2. Docstrings

Используем Google style:

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
    Найти пути для платежа.
    
    Использует BFS для поиска путей через граф доверия.
    Возвращает до 3 путей с достаточной ёмкостью.
    
    Args:
        source: PID источника
        target: PID назначения
        amount: Требуемая сумма
        max_hops: Максимальная длина пути (default: 4)
        
    Returns:
        Список найденных путей, отсортированных по ёмкости
        
    Raises:
        NoRouteFound: Нет пути с достаточной ёмкостью
        ParticipantNotFound: Участник не существует
        
    Example:
        >>> paths = await routing.find_paths("alice", "bob", Decimal("100"))
        >>> print(paths[0].path)
        ['alice', 'charlie', 'bob']
    """
```

### 9.3. API документация

Автоматически генерируется из:
- Pydantic моделей
- Docstrings endpoints
- OpenAPI decorators

---

## 10. Сообщество

### 10.1. Каналы связи

- **GitHub Issues** — баги и фичи
- **GitHub Discussions** — вопросы и идеи
- **Telegram** — @geo_protocol_dev

### 10.2. Как сообщить о баге

1. Проверить, нет ли уже такого issue
2. Создать issue с шаблоном:
   - Описание проблемы
   - Шаги для воспроизведения
   - Ожидаемое поведение
   - Фактическое поведение
   - Версия и окружение

### 10.3. Как предложить фичу

1. Создать Discussion с идеей
2. Обсудить с сообществом
3. Если одобрено — создать Issue
4. Реализовать (или ждать волонтёра)

### 10.4. Code of Conduct

Мы следуем [Contributor Covenant](https://www.contributor-covenant.org/).

Коротко:
- Уважайте друг друга
- Конструктивная критика
- Фокус на том, что лучше для проекта

---

## Связанные документы

- [00-overview.md](00-overview.md) — Обзор проекта
- [03-architecture.md](03-architecture.md) — Архитектура
- [05-deployment.md](05-deployment.md) — Развёртывание

---

**Спасибо за ваш вклад в GEO!** 🙏
