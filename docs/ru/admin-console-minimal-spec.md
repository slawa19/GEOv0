# GEO Hub — минимальная спецификация админки (Admin Console)

Цель документа: описать **минимально необходимую** админку для MVP, чтобы:
- управлять параметрами (конфигом) и feature flags без прямого доступа к БД;
- обслуживать пилот (эквиваленты, участники, инциденты);
- иметь аудит действий оператора.

Связанные документы:
- Реестр параметров и пометки runtime vs restart/migration: [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)
- Места в протоколе, где важны настройки multipath/clearing: [`docs/ru/02-protocol-spec.md`](docs/ru/02-protocol-spec.md:1)
- Развёртывание (в т.ч. схема конфигурации): [`docs/ru/05-deployment.md`](docs/ru/05-deployment.md:1)

---

## 1. Принцип «минимальная реализация»

### 1.1. UI подход

Для MVP выбран **Вариант B: SPA (Single Page Application)**.
- **Стек:** Python (FastAPI) + Vue.js 3 (Composition API) + Tailwind CSS.
- **Библиотека компонентов:** Element Plus (рекомендуется для админок).
- **Визуализация графов:** v-network-graph или D3.js.

Такой подход позволит создать отзывчивый интерфейс для мониторинга транзакций и визуализации сети доверия.

---

## 2. Аутентификация/авторизация (минимум)

### 2.1. Роли (минимальный набор)

- `admin` — полный доступ к админке.
- `operator` — ограниченный доступ: участники (freeze/ban), просмотр транзакций/клиринга, просмотр конфигурации, включение/выключение feature flags.
- `auditor` — только чтение: аудит-лог, транзакции, health/метрики, конфиг (read-only).

### 2.2. Требования безопасности

- Админка доступна только по отдельному пути (например, `/admin`) и/или отдельному домену.
- Обязателен TLS.
- Для SSR: защита CSRF для POST/PUT/DELETE.
- Сессии/токены: допустимо reuse существующего JWT, но доступ к admin endpoints должен проверяться по роли.

---

## 3. Набор экранов (минимальный состав)

### 3.1. Dashboard (read-only)
- Статус хаба: версия, uptime, окружение (dev/prod), базовая нагрузка.
- Быстрые ссылки на разделы.

### 3.2. Конфигурация и параметры (mixed)
**Цель**: видеть текущую конфигурацию и менять runtime-параметры.

Экран должен поддерживать:
- просмотр текущих значений;
- подсказку: описание/диапазон/дефолт (можно грузить из [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1) как статический справочник или встроить минимально в backend);
- изменение только runtime-параметров (см. раздел 4).

Форматы:
- «табличный» режим (ключ → значение);
- опционально: режим «raw YAML/JSON» только для просмотра.

### 3.3. Feature flags (mutable)
- Переключатели для:
  - `feature_flags.multipath_enabled`
  - `feature_flags.full_multipath_enabled`
  - `clearing.enabled`
- Отображение предупреждения: некоторые флаги экспериментальны (например, full multipath).

### 3.4. Эквиваленты (mixed)
- Список эквивалентов: код, описание, precision/масштаб.
- Действия (mutable): создать/редактировать/деактивировать эквивалент.
- Требования: все изменения логируются в audit-log.

### 3.5. Участники (mixed)
- Список участников, фильтр по статусу.
- Карточка участника: PID, verification level (если используется), статистика (read-only).
- Действия (mutable):
  - `freeze` (заморозить операции),
  - `unfreeze`,
  - `ban`/`unban` (если предусмотрено моделью).
- Требования: любое изменение статуса — через audit-log с причиной.

### 3.6. Транзакции (read-only)
- Поиск по `tx_id`, PID, типу (`PAYMENT`, `CLEARING`, ...), статусу, интервалу дат.
- Просмотр деталей: payload, маршруты, подписи (если показываем), timeline событий.
- Действия: только read-only (в MVP).

### 3.7. Клиринговые события (read-only)
- Список `CLEARING` транзакций, фильтры.
- Просмотр: цикл, сумма, режим согласия, причина отказа (если есть).
- Действия: read-only (в MVP).

### 3.8. Аудит-лог (read-only)
- Поиск по времени, actor, типу действия, объекту.
- Просмотр деталей события (до/после).

### 3.9. Health и метрики (read-only)
- Health endpoints (агрегация): `/health`, `/ready` и ключевые зависимости (DB/Redis).
- Ссылка на `/metrics` (если включены) и короткие KPI: latency p95/p99, error rate.

### 3.10. Визуализация графа (Network Graph)
- Интерактивная карта узлов (участников) и ребер (линий доверия).
- Фильтрация по эквиваленту.
- Подсветка "узких мест" (где лимит почти исчерпан).
- Поиск узла по PID.

### 3.11. Панель целостности (Integrity Dashboard)
- Статус проверки инвариантов (Zero-Sum check по эквивалентам).
- Сверка балансов (сумма долгов vs лимиты).
- Кнопка "Запустить полную проверку" (Full Integrity Scan).

### 3.12. Управление инцидентами (Incident Management)
- Список транзакций, зависших в промежуточных состояниях (`PREPARE_IN_PROGRESS`).
- Детальный лог попыток подтверждения (2PC).
- Действие: "Force Abort" (принудительная отмена) для разблокировки лимитов.

### 3.13. Аналитика ликвидности
- Графики объема долгов по времени.
- Эффективность клиринга: сколько долгов списано за период.
- Топ-10 участников по объему предоставленного/полученного доверия.

---

## 4. Что обязательно read-only vs что можно менять

Каноничные пометки — в [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1). Для MVP фиксируем минимум:

### 4.1. Runtime mutable (через админку)
Разрешено менять:
- `feature_flags.*`
- `routing.*` (важно для перф‑проверок: `routing.max_paths_per_payment`, режим `routing.multipath_mode`)
- `clearing.*` (важно: `clearing.trigger_cycles_max_length`)
- `limits.*`
- `observability.*` (например, `log_level`)

### 4.2. Read-only (требует рестарта/миграций)
Только просмотр:
- `protocol.*`
- `security.*` (по умолчанию)
- `database.*`
- `integrity.*` (по умолчанию)

---

## 5. Какие действия должны логироваться (audit-log)

### 5.1. Обязательные события

Логируются всегда:
- login/logout (или выдача админской сессии)
- изменение любых runtime-параметров и feature flags
- создание/изменение/деактивация эквивалентов
- freeze/unfreeze/ban/unban участника
- любые «компенсирующие операции» (если появятся позже)

### 5.2. Минимальная схема audit-log события

Рекомендуемый формат записи (лог + таблица в БД):
- `event_id`
- `timestamp`
- `actor` (user id / service)
- `actor_role`
- `action` (enum)
- `object_type` (config/feature_flag/participant/equivalent/...)
- `object_id`
- `reason` (обязателен для freeze/ban и изменения критичных лимитов)
- `before` / `after` (diff)
- `request_id` / `ip` / `user_agent`

---

## 6. Минимальные admin API endpoints (опционально)

UI может быть SSR и не требовать публичных admin API, но для простоты тестирования полезно зафиксировать минимальные endpoint группы:

- `GET /admin/config` (read)
- `PATCH /admin/config` (update runtime subset)
- `GET /admin/feature-flags`
- `PATCH /admin/feature-flags`
- `GET /admin/participants`
- `POST /admin/participants/{pid}/freeze`
- `POST /admin/participants/{pid}/unfreeze`
- `POST /admin/participants/{pid}/ban`
- `POST /admin/participants/{pid}/unban`
- `GET /admin/equivalents`
- `POST /admin/equivalents`
- `PATCH /admin/equivalents/{code}`
- `GET /admin/transactions`
- `GET /admin/transactions/{tx_id}`
- `GET /admin/clearing`
- `GET /admin/audit-log`
- `GET /admin/analytics/graph` (данные для визуализации сети)
- `GET /admin/analytics/stats` (метрики клиринга и ликвидности)
- `GET /admin/integrity/status` (результаты проверок)
- `POST /admin/integrity/check` (запуск проверки)
- `POST /admin/transactions/{tx_id}/abort` (принудительная отмена)

Все mutating endpoints обязаны писать audit-log.

---

## 7. Ограничения MVP (явно)

Чтобы не переусложнять:
- нет «ручной правки долгов/транзакций» в админке;
- нет сложного RBAC конструктора — только фиксированные роли;
- нет полноценного «конфиг-редактора YAML» с валидацией схем — только таблица ключей и ограниченный набор полей.