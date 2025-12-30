# GEO Hub Admin Console — Детальная UI-спецификация (Blueprint)

**Версия:** 0.2  
**Статус:** Blueprint для реализации без «додумывания»  
**Стек (рекомендация):** Vue.js 3 (Vite), Element Plus, Pinia.  

Документ согласован с:
- `docs/ru/admin-console-minimal-spec.md`
- `docs/ru/04-api-reference.md`
- `api/openapi.yaml`

---

## 1. Цели и границы (Scope)

### 1.1. Цели (MVP)
- Наблюдаемость сети доверия (граф + базовая аналитика).
- Управление инцидентами (зависшие транзакции → force abort).
- Управление runtime-конфигом и feature flags.
- Управление участниками (freeze/unfreeze, при наличии — ban/unban).
- Аудит действий операторов.

### 1.2. Не-цели (в этой версии)
- Редактирование долгов/транзакций вручную.
- Конструктор RBAC (только фиксированные роли).

---

## 2. Роли и доступ

### 2.1. Роли (минимальный набор)
- `admin` — полный доступ.
- `operator` — операции и конфиг, без критичных действий (может быть ограничено политикой).
- `auditor` — только чтение.

### 2.2. Ошибки доступа (нормативно)
- `401` → токен отсутствует/истёк (UI предлагает заново войти).
- `403` → недостаточно прав (UI показывает read-only либо «Недостаточно прав»).

---

## 3. Layout и навигация

### 3.1. Принципы UI
- Не вводить собственные «жёсткие» цвета/шрифты — использовать токены дизайн-системы/темы.
- Тёмный режим по умолчанию допустим, но должен быть реализован средствами выбранного UI-слоя.

### 3.2. Sidebar (основные разделы)

Минимальный состав экранов (соответствует `admin-console-minimal-spec.md`):
- `Dashboard`
- `Network Graph`
- `Integrity`
- `Incidents`
- `Participants`
- `Config`
- `Feature Flags`
- `Audit Log`
- `Events` (timeline)

Опционально (если включено в Hub):
- `Equivalents` (управление справочником)
- `Transactions` / `Clearing` (глобальные списки)

### 3.3. Header
- Breadcrumbs.
- Индикатор состояния Hub (минимум: успешность root `/health` или эквивалентный агрегированный статус).
- Текущая роль/аккаунт.
- Logout.

---

## 4. Экраны (требования)

### 4.1. Dashboard (read-only)

Цель: быстрый обзор состояния.

Должно показывать:
- Версию/окружение/uptime (если отдаётся в health/метриках).
- Краткие KPI (минимум один экран без глубоких фильтров).

Состояния:
- Loading / Error / Empty (если метрики недоступны).

### 4.2. Network Graph

Цель: визуализация сети доверия.

Функции:
- Zoom/Pan.
- Поиск узла по PID.
- Tooltip по ребру: `limit`, `debt/used`, `available` (если доступны в данных).
- Фильтр по эквиваленту.

### 4.3. Integrity Dashboard

Цель: видимость инвариантов и запуск проверки.

UI:
- Таблица проверок: `name`, `status`, `last_check`, `details`.
- Кнопка «Запустить полную проверку» → подтверждение → запуск.

### 4.4. Incidents (Incident Management)

Цель: операционные действия по зависшим транзакциям.

UI:
- Список «stuck» транзакций (определение: статус промежуточный + превышен SLA возраста).
- Действие: `Force Abort` с обязательным вводом причины.

### 4.5. Participants

Цель: модерация/операционное управление участниками.

UI:
- Поиск по PID.
- Действия: Freeze/Unfreeze (с причиной).
- Для `auditor` — только просмотр.

### 4.6. Config

Цель: просмотр и изменение runtime-конфига.

UI:
- Таблица «ключ → значение → описание/дефолт/ограничения».
- Изменение только runtime subset (как минимум — предупреждение, если ключ не runtime).
- После `PATCH` показывать список `updated[]`.

### 4.7. Feature Flags

UI:
- Переключатели:
  - `feature_flags.multipath_enabled`
  - `feature_flags.full_multipath_enabled`
  - `clearing.enabled` (из секции `clearing.*`, отображается как «Clearing enabled»)
- Для экспериментальных (например, `full_multipath_enabled`) — warning.

Примечание: `clearing.enabled` технически находится в секции `clearing.*` конфига (см. `config-reference.md`), но для удобства UI отображается вместе с feature flags.

### 4.8. Audit Log

UI:
- Таблица с пагинацией: `timestamp`, `actor`, `role`, `action`, `object`, `reason`.
- Детальная панель записи: `before_state`/`after_state`.

### 4.9. Events (timeline)

UI:
- Фильтры: `event_type`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, диапазон дат.
- Таблица/таймлайн.

### 4.10. Equivalents (MVP)

Цель: управление справочником эквивалентов (входит в MVP согласно `admin-console-minimal-spec.md` §3.4).

UI:
- Таблица: `code`, `description`, `precision`, `is_active`.
- Действия:
	- Create
	- Edit
	- Activate/Deactivate

Требования:
- Любые изменения должны попадать в audit-log.

### 4.11. Transactions (optional / Phase 2)

Цель: операционный обзор транзакций всех типов.

UI:
- Фильтры: `tx_id`, `initiator_pid`, `type`, `state`, `equivalent`, диапазон дат.
- Таблица: `tx_id`, `type`, `state`, `initiator_pid`, `created_at`.
- Детали: `payload`, `error`, `signatures`.

### 4.12. Clearing (optional / Phase 2)

Цель: отдельный список клиринговых транзакций.

UI:
- Фильтры: `state`, `equivalent`, диапазон дат.
- Таблица/детали: как в Transactions.

### 4.13. Liquidity analytics (optional / Phase 2)

Цель: агрегированные графики/таблицы по ликвидности и эффективности клиринга.

UI:
- Фильтры: `equivalent` (опционально), диапазон дат.
- Представления:
	- summary (KPI)
	- series (тайм-серия)

---

## 5. Глобальное состояние и клиент API

### 5.1. Pinia stores (минимум)
- `useAdminAuthStore`: токен, роль, user info.
- `useAdminConfigStore`: конфиг + last updated keys.
- `useAdminGraphStore`: данные графа по эквиваленту.

### 5.2. API client
- Base URL: `/api/v1`.
- Authorization: `Bearer`.
- Единая обработка envelope `{success,data}` и ошибок `{success:false,error:{code,message,details}}`.

### 5.3. Сессия и хранение токенов (нормативно)
- Админка должна работать только по TLS.
- Токены должны храниться в памяти (runtime). Персистентное хранение (localStorage) не является требованием MVP.
- При `401` UI должен переводить пользователя в состояние «требуется вход».
- При `403` UI должен показывать «Недостаточно прав» и не пытаться повторять запрос.

---

## 6. API Mapping (экран → endpoint → поля → ошибки)

Примечание: детальные контракты должны соответствовать `api/openapi.yaml`. Если endpoint отсутствует в OpenAPI — он считается требованием к backend и должен быть добавлен в контракт.

### 6.1. Config
- `GET /admin/config` → объект с ключами конфигурации.
- `PATCH /admin/config` → `{updated: string[]}`.

UI правила:
- Редактирование разрешено только если роль позволяет (минимум: `admin`/`operator`).
- После успешного `PATCH` UI обновляет таблицу конфигурации и отображает список обновлённых ключей.

### 6.2. Feature Flags
- `GET /admin/feature-flags`
- `PATCH /admin/feature-flags`

Примечание: endpoint возвращает/принимает `multipath_enabled`, `full_multipath_enabled`, `clearing_enabled`. Параметр `clearing_enabled` технически соответствует `clearing.enabled` в конфиге, но для удобства UI объединён с feature flags.

UI правила:
- Любая операция изменения должна требовать явного подтверждения (минимум: confirm dialog).

### 6.3. Participants
- `POST /admin/participants/{pid}/freeze` (body: `{reason}`)
- `POST /admin/participants/{pid}/unfreeze` (body: `{reason?}`)
- `POST /admin/participants/{pid}/ban` (body: `{reason}`)
- `POST /admin/participants/{pid}/unban` (body: `{reason}`)

UI правила:
- `reason` обязателен для freeze.
- После действия UI показывает toast + записывает факт в локальный UI log (не заменяет audit log).

### 6.4. Audit Log / Events
- `GET /admin/audit-log` (paginated)
- `GET /admin/events` (paginated)

Поля (ориентиры для отображения, согласованы с `api/openapi.yaml`):
- AuditLogEntry: `id`, `timestamp`, `actor_id`, `actor_role`, `action`, `object_type`, `object_id`, `reason`, `before_state`, `after_state`, `request_id`, `ip_address`.
- DomainEvent: `event_id`, `event_type`, `timestamp`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, `payload`.

### 6.5. Graph / Integrity / Incidents
- `GET /admin/analytics/graph?equivalent={code}`
- `GET /admin/integrity/status`
- `POST /admin/integrity/check`
- `POST /admin/transactions/{tx_id}/abort` (body: `{reason}`)

UI правила:
- Graph: фильтр `equivalent` обязателен.
- Integrity check: действие должно требовать подтверждения.
- Abort: `reason` обязателен; после abort UI должен предложить перейти в Events и проверить связанный `tx_id`.

### 6.6. Equivalents (optional / Phase 2)
- `GET /admin/equivalents` (query: `include_inactive`)
- `POST /admin/equivalents` (body: AdminEquivalentUpsert)
- `PATCH /admin/equivalents/{code}` (body: AdminEquivalentUpsert)

### 6.7. Transactions / Clearing (optional / Phase 2)
- `GET /admin/transactions` (paginated)
- `GET /admin/transactions/{tx_id}`
- `GET /admin/clearing` (paginated)

### 6.8. Liquidity analytics (optional / Phase 2)
- `GET /admin/analytics/stats`

---

## 7. Матрица UI-состояний (нормативно)

Для каждого экрана обязательно:
- Loading (skeleton/spinner)
- Error (с retriable CTA)
- Empty (объясняющее сообщение)

Минимальные тексты:
- `403`: «Недостаточно прав для просмотра этого раздела»
- `401`: «Сессия истекла. Войдите снова»

---

## 8. Prompts для генерации (ИИ)

> "Создай Vue 3 компонент для админки GEO Hub на Element Plus (script setup). Компонент: [Название экрана]. Реализуй loading/empty/error, извлечение данных из envelope {success,data}, обработку 401/403. Эндпоинт(ы): [список]."

