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
- Управление удержаниями эквивалентов: видеть эквиваленты на удержании и снимать удержание с причиной на экране `Integrity` (с 2026-10-07, программа 032 S5; управление инцидентами и force abort удалены тогда же).
- Управление runtime-конфигом и feature flags.
- Управление участниками (freeze/unfreeze; ban/unban удалены 2026-10-07, программа 032 F-5).
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

(2026-10-07, 032 S4: роли в UI нет — переключатель `admin/operator/auditor` и `isReadOnly` существовали только в mock-режиме и удалены. RBAC не реализован, это зафиксированное решение программы 022; доступ определяет admin-токен, его проверяет backend. Раздел описывает намерение, а не текущую реализацию.)

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
- `Integrity` (с удержаниями эквивалентов, см. 4.3)
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

#### ~~Liquidity analytics (Snapshot triage)~~

**Удалено 2026-10-07 (программа 032 S5, решение владельца), F-2:** экран Liquidity, советы оператору и `GET /admin/trustlines/bottlenecks` удалены — аналитика
ликвидности не вела ни к одному действию оператора (маршрута изменения лимитов у админки нет). Суммы по эквиваленту —
строка эквивалента на Dashboard (4.1); узкие места подсвечиваются на экране линий доверия (4.14) и на графе (4.2).

### 3.3. Header
- Breadcrumbs.
- Индикатор состояния Hub (минимум: успешность root `/health` или эквивалентный агрегированный статус).
- Текущая роль/аккаунт (в реализации ролей нет, см. 2.1).
- Logout.

---

## 4. Экраны (требования)

### 4.1. Dashboard (read-only)

Цель: быстрый обзор состояния. **Пересмотрено 2026-10-07 (программа 032 S5, F-3):** карточки API/DB/миграций удалены
(шапка уже опрашивает здоровье хаба, `stores/health.ts`), карточка узких мест с порогом удалена.

Показывает:
- счётчики участников по типам и по статусам (переход на `Participants` с фильтром);
- строку на эквивалент — `GET /admin/liquidity/summary?equivalent={code}` по каждому эквиваленту каталога, включая
  остановленные: число активных линий и суммы лимитов, использованного и доступного **только внутри этого
  эквивалента** (028 F-028-37), с точностью самого эквивалента (`useEquivalentPrecision`, `include_inactive: true`);
- предупреждение об удержаниях эквивалентов (`GET /integrity/summary`, `hold: true`) со ссылкой на `Integrity`;
- последние записи аудита (`GET /admin/audit-log?page=1&per_page=10`).

Ошибки: виджеты деградируют независимо (отказ сводки одного эквивалента печатает прочерки в его строке и называет
причину, остальные строки не затрагивает).

### 4.2. Network Graph

Статус: **Реализовано** (данные с backend-а; fixture-режим удалён 2026-10-07, 032 S4).

Цель: визуализация сети доверия на клиенте (данные берутся из `GET /admin/graph/snapshot` и `GET /admin/graph/ego`).

Роут и навигация:
- Route: `/graph`
- Sidebar: `Network Graph`

Техническая реализация:
- Рендер: Cytoscape.js + layout-плагин `fcose`.
- Источник данных: `GET /api/v1/admin/graph/snapshot` и `GET /api/v1/admin/graph/ego` (`admin-ui/src/api/realApi.ts`). Файлов фикстур (`admin-fixtures/v1/datasets/*.json`) и загрузчика `admin-ui/src/api/fixtures.ts` больше нет.

Модель графа:
- Узлы (nodes): участники, `id = pid`.
- Рёбра (edges): trustlines `from -> to`.
- Данные ребра: `equivalent`, `status`, `limit`, `used`, `available`, `created_at`.

UI (MVP) — что должно быть на странице:
- Панель управления (фильтры/переключатели):
	- `Equivalent`:
		- `ALL` (все)
		- конкретный код (из списка эквивалентов и/или из trustlines)
	- `Status` (multi-select): `active`, `closed` (статуса линии `frozen` нет с 2026-10-04, 028 `F-028-29`)
	- `Threshold` (строка/число, по умолчанию `0.10`): используется для подсветки bottleneck
	- `Layout`: `fcose (force)`, `grid`, `circle`
	- Toggle: `Labels` (показывать/скрывать подписи)
	- Toggle: `Auto labels` (автоматически выключать подписи при большом числе узлов/маленьком зуме)
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
	- `closed` — светло-серый
- Bottleneck:
	- условие: `available/limit < threshold` (только для `active`)
	- стиль: красное ребро, увеличенная толщина
- ~~Incidents overlay~~ — **Удалено 2026-10-07 (программа 032 S5, решение владельца), A-4:** инцидентов нет с программы 019 (коллекция `incidents` графа удалена).

Дополнительные подсветки:
- `Search-hit`: узел временно получает оранжевую рамку.
- `Selected`: выбранный узел имеет пульсирующее «свечение» (overlay), не меняя цвет рамок.
- `Connections`: при выборе связи в drawer подсвечиваются ребро и два узла (зелёным).
- ~~`Cycles`~~ — **Удалено 2026-10-07 (программа 032 S5, решение владельца), F-1:** вкладка циклов и их подсветка удалены вместе с `GET /admin/clearing/cycles`.

Интерактив (MVP):
- Zoom/Pan — средствами Cytoscape.
- Одинарный клик по узлу: выделяет узел и подставляет PID/имя в `Search` (drawer не открывает).
- Двойной клик по узлу: центрирует/зуумит как `Find` и открывает `Drawer` с деталями участника.
- Клик по ребру: открывает `Drawer` с деталями trustline (equivalent/from/to/status/limit/used/available/created_at).

Drawer участника: вкладки «Сводка» (описание узла и net по `balance_rows[].net` каждого эквивалента, без
суммирования между эквивалентами), «Связи» (из графа), «Баланс» (таблица `balance_rows` из
`GET /admin/participants/{pid}/metrics`). ~~Аналитика участника~~ — **Удалено 2026-10-07 (программа 032 S5, решение владельца), F-1:** рейтинг и распределение,
концентрация (HHI), контрагенты, ёмкость, активность 7/30/90, циклы и панель советов оператору удалены вместе с
клиентским расчётом этих метрик из снимка; ответ метрик сужен до `balance_rows`.

Расширения (для последующей модификации):
- Добавить tooltip на hover по ребру (без внешних зависимостей можно реализовать через overlay div).
- Добавить режимы представления (вкладки): `Overview`, `Equivalent Lens`.
- Добавить экспорт PNG и сохранение пресетов фильтров в `localStorage`.

### 4.3. Integrity Dashboard

Цель: видимость инвариантов, запуск проверки и снятие удержаний эквивалентов.

UI:
- Таблица проверок: `name`, `status`, `last_check`, `details`.
- Кнопка «Запустить полную проверку» → подтверждение → запуск.
- **Удержания эквивалентов (с 2026-10-07, программа 032 S5, F-4):** по `GET /integrity/summary` у каждого эквивалента
  отметка «на удержании» (`hold: true`) или «не удерживается»; у удерживаемого — действие «Снять» с обязательной
  причиной (`POST /admin/equivalents/{code}/integrity-hold/clear`, аудит). Подсказка: на удержании отказывают платёж,
  клиринг и новые линии. Отказы показываются текстом (RU/EN): `no_integrity_hold` — «удержание уже снято»;
  `no_later_passed_reconciliation_result` при `latest_status = null` — «сверка ещё не давала результата», при
  `FAILED`/`UNVERIFIABLE` — «последняя сверка не PASSED; дождитесь следующей», при `recheck_status = FAILED` —
  «проверка при снятии не прошла, удержание остаётся», при `UNVERIFIABLE` — «проверить сейчас нельзя»; прочее — общий
  текст с кодом. После любого ответа список удержаний перечитывается. Время и причина удержания не показываются
  (для этого нужно новое поле API; причина видна в логе `debt_reconciliation.integrity_hold_set` и в
  `GET /integrity/status`).

### ~~4.4. Incidents (Incident Management)~~

**Удалено 2026-10-07 (программа 032 S5, решение владельца), F-4, A-4:** экран, `GET /admin/incidents` и `POST /admin/transactions/{tx_id}/abort` удалены — с программы
019 «зависших» платежей нет (платёж — одна транзакция, миграция `030`). Путь `/incidents` ведёт на `Integrity`, где
показаны и снимаются удержания эквивалентов. Строки аудита `admin.transactions.abort` остаются как историческое действие.

### 4.5. Participants

Цель: модерация/операционное управление участниками.

UI:
- Поиск по PID.
- Действия: Freeze/Unfreeze (с причиной).
- Для `auditor` — только просмотр (в реализации ролей нет, см. 2.1).

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
- Подсказка у `multipath_enabled`: при `false` платёжная маршрутизация ищет не больше одного пути (как `ROUTING_MAX_PATHS=1`); `GET /payments/max-flow` флаг не меняет.
- Подсказка у `full_multipath_enabled`: только включает поле `paths` в ответе `GET /payments/max-flow`; маршрутизацию платежей и `max_amount` не меняет (033 A, пункт 7).

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

**Не планируется:** экран Liquidity удалён 2026-10-07 (программа 032 S5, F-2), см. 3.2.

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
- Правило: `available/limit < threshold` — только у **активной** линии с `limit > 0` (032 S7, 2026-10-07).
- `threshold` задаётся в UI (default 0.10); значение вне `[0, 1]` подсветку не включает.
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
- Редактирование разрешено только если роль позволяет (минимум: `admin`/`operator`) — намерение; в реализации ролей нет, см. 2.1.
- После успешного `PATCH` UI обновляет таблицу конфигурации и отображает список обновлённых ключей.

### 6.2. Feature Flags
**Удалено 2026-10-07 (программа 032, F-6).** `GET`/`PATCH /admin/feature-flags` убраны: флаги
`FEATURE_FLAGS_MULTIPATH_ENABLED`, `FEATURE_FLAGS_FULL_MULTIPATH_ENABLED`, `CLEARING_ENABLED` читаются и меняются
как ключи `GET`/`PATCH /admin/config` (6.1). Эффект флагов мультипути: `FEATURE_FLAGS_MULTIPATH_ENABLED=false` равносилен
`ROUTING_MAX_PATHS=1` для платёжной маршрутизации; `FEATURE_FLAGS_FULL_MULTIPATH_ENABLED=true` лишь включает поле `paths`
в ответе `GET /payments/max-flow` (`app/core/payments/router.py`, `app/core/payments/service.py`), маршрутизацию не меняет.

UI правила:
- Любая операция изменения должна требовать явного подтверждения (минимум: confirm dialog).

### 6.3. Participants
- `POST /admin/participants/{pid}/freeze` (body: `{reason}`)
- `POST /admin/participants/{pid}/unfreeze` (body: `{reason?}`)
- `ban`/`unban` удалены 2026-10-07 (программа 032, F-5). Матрица: freeze только из `active`, unfreeze только из
  `suspended`, иначе `409` `status_transition_not_allowed` (`docs/ru/09-decisions-and-defaults.md`, 1.6).

UI правила:
- `reason` обязателен для freeze.
- После действия UI показывает toast + записывает факт в локальный UI log (не заменяет audit log).

### 6.4. Audit Log / Events
- `GET /admin/audit-log` (paginated)
 

Поля (ориентиры для отображения, согласованы с `api/openapi.yaml`):
- AuditLogEntry: `id`, `timestamp`, `actor_id`, `actor_role`, `action`, `object_type`, `object_id`, `reason`, `before_state`, `after_state`, `request_id`, `ip_address`.
- DomainEvent: `event_id`, `event_type`, `timestamp`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, `payload`.

### 6.5. Graph / Integrity
- `GET /admin/trustlines?equivalent={code}&creditor={pid}&debtor={pid}&status={active|closed}`
- `GET /admin/graph/snapshot`, `GET /admin/graph/ego` (`include` — `audit_log`, `transactions`)
- `GET /admin/participants/{pid}/metrics?equivalent={code}` → `{pid, equivalent, balance_rows}`
- `GET /admin/liquidity/summary?equivalent={code}` → `{equivalent, updated_at, active_trustlines, total_limit, total_used, total_available}`
- `GET /integrity/status`, `GET /integrity/summary`
- `POST /integrity/verify`
- `POST /admin/equivalents/{code}/integrity-hold/clear` (body: `{reason}`)

UI правила:
- Graph: фильтр `equivalent` обязателен.
- Integrity check: действие должно требовать подтверждения.
- Снятие удержания: `reason` обязателен; отказ показывается текстом (4.3).
- ~~`POST /admin/transactions/{tx_id}/abort`~~ — удалён 2026-10-07 (программа 032 S5).

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
- Не планируется (экран удалён 2026-10-07, программа 032 S5).

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
