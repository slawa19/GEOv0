# GEO Hub Admin Console — Детальная UI-спецификация (Blueprint)

**Версия:** 0.3  
**Статус:** Blueprint для реализации без «додумывания»  
**Стек (рекомендация):** Vue.js 3 (Vite), Element Plus, Pinia.  

Документ согласован с:
- `docs/ru/admin-ui/README.md`
- `docs/ru/admin-ui/specs/archive/admin-console-minimal-spec.md`
- `docs/ru/04-api-reference.md`
- `api/openapi.yaml`

---

## 1. Цели и границы (Scope)

### 1.1. Цели (MVP)
- Наблюдаемость сети доверия (таблица trustlines + базовая диагностика).
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

См. также нормативный документ по типографике:
- `docs/ru/admin-ui/typography.md`

### 3.2. Sidebar (основные разделы)

Минимальный состав экранов (соответствует `docs/ru/admin-ui/specs/archive/admin-console-minimal-spec.md`):
- `Dashboard`
- `Liquidity analytics` (Snapshot triage)
- `Integrity`
- `Incidents`
- `Trustlines`
- `Network Graph` (реализовано в прототипе)
- `Participants`
- `Config`
- `Feature Flags`
- `Audit Log`

Опционально (если включено в Hub):
- `Equivalents` (управление справочником)

Phase 2:
- `Events` (timeline)
- `Transactions` / `Clearing` (глобальные списки)

#### Liquidity analytics (Snapshot triage)

Цель: дать оператору «ситуационный центр» по ликвидности сети, чтобы быстро понять состояние и приоритизировать расследование.

Позиционирование (чтобы не было дублирования):
- `Liquidity analytics` = обзор + watchlist + советы.
- `Trustlines`/`Graph` = drill-down и диагностика конкретных рёбер/узлов.

MVP (без историчности):
- Источник данных: `GraphSnapshot` (в mock режиме — fixtures).
- Управляющие параметры: `equivalent` (или `ALL`), `threshold` (bottleneck).
- KPI: active trustlines / bottlenecks / incidents over SLA / total limit-used-available (суммы decimal-safe).
- Watchlist: top bottleneck edges, top net positions (из `debts`).
- Operator Advice: детерминированные советы + быстрые переходы в `Trustlines`, `Incidents`, `Graph`, `Participants`.

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

MVP дополнение: **Network Health виджеты** (быстрый статус ключевых зависимостей).
- Источники:
	- `GET /health` (доступность API)
	- `GET /health/db` (доступность БД)
	- `GET /admin/migrations` (статус миграций; требует `X-Admin-Token`)
	- `GET /admin/audit-log?page=1&per_page=20` (последняя активность; требует `X-Admin-Token`)
- Обновление: автообновление каждые 10–30 секунд + кнопка `Refresh`.
- Ошибки: виджеты деградируют независимо (ошибка в audit-log не ломает health).

### 4.2. Network Graph

Статус: **Реализовано в прототипе (fixture-based)**.

Цель: визуализация сети доверия на клиенте **без изменений бэкенда** (данные берутся из фикстур).

Роут и навигация:
- Route: `/graph`
- Sidebar: `Network Graph`

Техническая реализация:
- Рендер: Cytoscape.js + layout-плагин `fcose`.
- Источник данных (фикстуры):
	- `admin-fixtures/v1/datasets/participants.json`
	- `admin-fixtures/v1/datasets/trustlines.json`
	- `admin-fixtures/v1/datasets/incidents.json`
	- `admin-fixtures/v1/datasets/equivalents.json`
- Загрузка: модуль `admin-ui/src/api/fixtures.ts` (кэширует `fetch` по относительному пути).

Модель графа:
- Узлы (nodes): участники, `id = pid`.
- Рёбра (edges): trustlines `from -> to`.
- Данные ребра: `equivalent`, `status`, `limit`, `used`, `available`, `created_at`.

UI (MVP) — что должно быть на странице:
- Панель управления (фильтры/переключатели):
	- `Equivalent`:
		- `ALL` (все)
		- конкретный код (из `equivalents.json` и/или из trustlines)
	- `Status` (multi-select): `active`, `frozen`, `closed`
	- `Threshold` (строка/число, по умолчанию `0.10`): используется для подсветки bottleneck
	- `Layout`: `fcose (force)`, `grid`, `circle`
	- Toggle: `Labels` (показывать/скрывать подписи)
	- Toggle: `Auto labels` (автоматически выключать подписи при большом числе узлов/маленьком зуме)
	- Toggle: `Incidents` (включить/выключить наложение инцидентов)
	- Toggle: `Hide isolates` (скрыть узлы без рёбер после фильтрации)
	- `Search` (PID или имя) + `Find` (центрировать/зуум на узел)
	- `Focus` (эго‑граф): `Focus Mode` on/off + `Depth 1/2` + `Use selected` + `Clear`
	- Кнопки: `Fit` (вписать граф), `Re-layout` (перезапуск layout)

Стили и подсветки (MVP):
- Цвет узлов определяется статусом участника (заливка):
	- `active` — зелёный
	- `frozen/suspended` — оранжевый
	- `banned` — красный
	- `deleted` — серый
	- `business` отличается формой/размером (увеличенный скруглённый прямоугольник), без отдельной рамки.
- Цвет рёбер по статусу trustline:
	- `active` — синий
	- `frozen` — серый
	- `closed` — светло-серый
- Bottleneck:
	- условие: `available/limit < threshold` (только для `active`)
	- стиль: красное ребро, увеличенная толщина
- Incidents overlay:
	- источник: `incidents.json`
	- узел-инициатор (`initiator_pid`) подсвечивается (border)
	- рёбра, исходящие от инициатора, выделяются пунктиром

Дополнительные подсветки (fixtures-first прототип):
- `Search-hit`: узел временно получает оранжевую рамку.
- `Selected`: выбранный узел имеет пульсирующее «свечение» (overlay), не меняя цвет рамок.
- `Connections`: при выборе связи в drawer подсвечиваются ребро и два узла (зелёным).
- `Cycles`: по клику на цикл подсвечиваются рёбра/узлы, входящие в цикл (оранжевым).

Интерактив (MVP):
- Zoom/Pan — средствами Cytoscape.
- Одинарный клик по узлу: выделяет узел и подставляет PID/имя в `Search` (drawer не открывает).
- Двойной клик по узлу: центрирует/зуумит как `Find` и открывает `Drawer` с деталями участника.
- Клик по ребру: открывает `Drawer` с деталями trustline (equivalent/from/to/status/limit/used/available/created_at).

Operator Advice (MVP+; реализовать сейчас):
- В `Drawer → Summary` должна быть панель **Operator advice**: контекстные рекомендации по bottlenecks/capacity/concentration.
- Рекомендации детерминированы (фиксированные правила) и объясняют «почему» + дают быстрые переходы на существующие экраны.
- Детальная спецификация правил и UX: `docs/ru/admin-ui/specs/operator-advice-spec.md`.

Расширения (для последующей модификации):
- Добавить tooltip на hover по ребру (без внешних зависимостей можно реализовать через overlay div).
- Добавить режимы представления (вкладки): `Overview`, `Equivalent Lens`, `Incidents Overlay`.
- Добавить визуальную «тепловую карту» по SLA: `age_seconds/sla_seconds` (градиент/интенсивность).
- Добавить экспорт PNG и сохранение пресетов фильтров в `localStorage`.

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

Примечание:
- Operator Advice может вести на `Participants` как на следующий шаг диагностики (например, открыть карточку участника после выявления bottleneck-ребра).

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

Статус: **Phase 2** (в MVP убирается: отдельного endpoint нет, базовый аудит закрывается `/admin/audit-log`).

UI:
- Фильтры: `event_type`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, диапазон дат.
- Таблица/таймлайн.

### 4.10. Equivalents (MVP)

Цель: управление справочником эквивалентов (входит в MVP согласно `docs/ru/admin-ui/specs/archive/admin-console-minimal-spec.md` §3.4).

UI:
- Таблица: `code`, `description`, `precision`, `is_active`.
- Действия:
	- Create
	- Edit
	- Activate/Deactivate
	- Delete (только "safe delete" — см. ниже)

Доп. UX (реализовано в прототипе):
- Ленивый бейдж "Used by X TL / Y Inc" (подгружается при hover и кэшируется).

Safe delete (нормативно):
- Удаление разрешено только если:
	- equivalent неактивен (`is_active = false`)
	- equivalent нигде не используется (usage counts == 0)
- UI обязан запросить `reason`.
- Если equivalent используется → backend должен вернуть `409 Conflict` с деталями usage.

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

### 4.14. Trustlines (Network overview) (MVP)

Цель: операторский обзор «состояния сети» без отдельного analytics pipeline.

Источник данных:
- `GET /admin/trustlines` (заголовок `X-Admin-Token`).
- Query filters:
  - `equivalent`, `creditor` (trustline `from`), `debtor` (trustline `to`), `status`.
- Pagination: `page/per_page`.

Таблица (минимум колонок):
- `equivalent`
- creditor: `from` + `from_display_name` (если есть)
- debtor: `to` + `to_display_name` (если есть)
- `limit`, `used`, `available`
- `status`, `created_at`

Подсветка «узких мест» (нормативно):
- Правило: `available/limit < threshold`.
- `threshold` задаётся в UI (default 0.10).
- Числа приходят как decimal string; UI **не должен** использовать float для порогов/отношений.
- Если `limit == 0` или значение невалидно → подсветку не применять.

Drill-down ребра:
- По клику по строке открыть панель/модалку с `limit/used/available`, участниками, `policy` (как JSON-view).

Состояния:
- `403` → токен отсутствует/неверный (UI предлагает ввести/обновить токен).
- Empty → «No trustlines match filters».

---

## 5. Глобальное состояние и клиент API

### 5.1. Pinia stores (минимум)
- `useAdminAuthStore`: токен, роль, user info.
- `useAdminConfigStore`: конфиг + last updated keys.
- `useAdminGraphStore`: данные графа по эквиваленту.

### 5.2. API client
- Base URL: `/api/v1`.
- Для `/admin/*` endpoints: заголовок `X-Admin-Token`.
- Для `/integrity/*` endpoints: `Authorization: Bearer`.
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
 

Поля (ориентиры для отображения, согласованы с `api/openapi.yaml`):
- AuditLogEntry: `id`, `timestamp`, `actor_id`, `actor_role`, `action`, `object_type`, `object_id`, `reason`, `before_state`, `after_state`, `request_id`, `ip_address`.
- DomainEvent: `event_id`, `event_type`, `timestamp`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, `payload`.

### 6.5. Graph / Integrity / Incidents
- `GET /admin/trustlines?equivalent={code}&creditor={pid}&debtor={pid}&status={active|frozen|closed}`
- `GET /integrity/status`
- `POST /integrity/verify`
- `POST /admin/transactions/{tx_id}/abort` (body: `{reason}`)

UI правила:
- Graph: фильтр `equivalent` обязателен.
- Integrity check: действие должно требовать подтверждения.
- Abort: `reason` обязателен; после abort UI должен предложить перейти в Events и проверить связанный `tx_id`.

### 6.6. Equivalents
- `GET /admin/equivalents` (query: `include_inactive`)
- `POST /admin/equivalents` (body: AdminEquivalentUpsert)
- `PATCH /admin/equivalents/{code}` (body: AdminEquivalentUpsert)
- `GET /admin/equivalents/{code}/usage` → `{ code, trustlines, debts, integrity_checkpoints }`
- `DELETE /admin/equivalents/{code}` (body: `{ reason }`) → `{ deleted: true }`

Ошибки (нормативно):
- `409 Conflict` — equivalent in-use или попытка удаления активного эквивалента.

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
