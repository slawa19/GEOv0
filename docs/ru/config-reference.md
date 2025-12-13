# GEO Hub — Config reference (реестр параметров)

Этот документ — **единый источник правды** по параметрам конфигурации GEO Hub для MVP: назначение, допустимые значения, дефолты и риски.

Связанные документы:
- Спецификация протокола (в т.ч. multipath/full multipath): [`docs/ru/02-protocol-spec.md`](docs/ru/02-protocol-spec.md:1)
- Развёртывание и общая схема конфигурации (env + YAML): [`docs/ru/05-deployment.md`](docs/ru/05-deployment.md:1)
- Минимальная админка для управления параметрами: [`docs/ru/admin-console-minimal-spec.md`](docs/ru/admin-console-minimal-spec.md:1)

---

## 1. Общие принципы

### 1.1. Два уровня конфигурации

1) **Переменные окружения (.env)** — инфраструктура/секреты/интеграции (БД, Redis, ключи и т.п.).  
2) **YAML конфиг хаба** — параметры протокола и поведения (routing/clearing/limits/flags/observability).

В текущих документах некоторые параметры могут фигурировать как env-переменные (например, лимиты/таймауты). Для MVP **каноничным** считается YAML-конфиг, а env оставляем для инфраструктуры и секретов.

### 1.2. Runtime vs restart/migration

- **Runtime (через админку)**: можно менять в рантайме без рестарта (с обязательным аудитом). Типично: `feature_flags.*`, `routing.*`, `clearing.*`, `limits.*`, `observability.*`.
- **Restart required**: изменение требует рестарта процесса/подов. Типично: `protocol.*` (таймауты протокола) и часть `security.*`.
- **Migration required**: изменение требует миграций/проверки совместимости состояния. Типично: `database.*` и часть `integrity.*` (если влияет на формат/хранение).

---

## 2. Таблица параметров (по секциям)

Ниже: **назначение / значения / дефолт / режим применения / влияние и риски**.

---

## 2.1. `feature_flags.*` (runtime)

### `feature_flags.multipath_enabled`
- Назначение: включить multi-path разбиение платежа (если `false`, маршрутизация старается найти 1 путь).
- Значения: `true|false`
- Дефолт: `true`
- Применение: runtime
- Риски: отключение ухудшает проходимость платежей в фрагментированной сети.

### `feature_flags.full_multipath_enabled`
- Назначение: включить экспериментальный **full multipath** (для бенчмарков).
- Значения: `true|false`
- Дефолт: `false`
- Применение: runtime
- Риски: может резко увеличить стоимость маршрутизации; включать только при настроенных budget/таймаутах и метриках.

---

## 2.2. `routing.*` (runtime)

### `routing.multipath_mode`
- Назначение: выбранный режим multipath.
- Значения: `limited|full`
- Дефолт: `limited`
- Применение: runtime
- Риски: `full` — экспериментальный; должен быть ограничен budget/таймаутами. Рекомендуется включать `full` только совместно с [`feature_flags.full_multipath_enabled`](docs/ru/config-reference.md:1).

### `routing.max_path_length`
- Назначение: верхняя граница длины пути (hops) для маршрутизации.
- Значения: `1..12` (практически: `3..8`)
- Дефолт: `6`
- Применение: runtime
- Риски: рост значения увеличивает стоимость поиска и ухудшает объяснимость.

### `routing.max_paths_per_payment`
- Назначение: максимум путей, используемых для разбиения одного платежа.
- Значения: `1..10`
- Дефолт: `3`
- Применение: runtime
- Риски: рост значения увеличивает число участников 2PC и вероятность таймаутов/abort; нужно для перф‑проверок.

### `routing.path_finding_timeout_ms`
- Назначение: общий таймаут на поиск маршрутов для платежа.
- Значения: `50..5000`
- Дефолт: `500`
- Применение: runtime
- Риски: слишком низкий → много отказов; слишком высокий → рост p99 latency и нагрузка.

### `routing.route_cache_ttl_seconds`
- Назначение: TTL кэша результатов маршрутизации.
- Значения: `0..600`
- Дефолт: `30`
- Применение: runtime
- Риски: высокий TTL при быстро меняющемся графе может давать устаревшие маршруты и лишние abort.

### `routing.full_multipath_budget_ms`
- Назначение: дополнительный budget времени/стоимости для режима `full`.
- Значения: `0..10000`
- Дефолт: `1000`
- Применение: runtime
- Риски: увеличение budget может перегружать CPU и ухудшать хвостовые задержки.

### `routing.full_multipath_max_iterations`
- Назначение: лимит итераций для max-flow-like реализаций (если используются).
- Значения: `0..100000`
- Дефолт: `100`
- Применение: runtime
- Риски: высокий лимит → непредсказуемое время.

### `routing.fallback_to_limited_on_full_failure`
- Назначение: если `full` не уложился в budget/таймаут, разрешить fallback на `limited`.
- Значения: `true|false`
- Дефолт: `true`
- Применение: runtime
- Риски: может скрывать проблемы `full` режима; требует метрик `budget_exhausted`.

---

## 2.3. `clearing.*` (runtime)

### `clearing.enabled`
- Назначение: включить клиринг.
- Значения: `true|false`
- Дефолт: `true`
- Применение: runtime
- Риски: отключение ломает ключевую ценность GEO (рост долгов, хуже ликвидность).

### `clearing.trigger_cycles_max_length`
- Назначение: максимальная длина цикла для **триггерного** поиска после транзакции.
- Значения: `3..6` (для MVP рекомендуется `3..4`)
- Дефолт: `4`
- Применение: runtime
- Риски: увеличение до `5..6` может резко увеличить стоимость поиска; параметр нужен для перф‑проверок и должен быть защищён лимитами по времени/кол-ву кандидатов.

### `clearing.min_clearing_amount`
- Назначение: минимальная сумма клиринга (фильтр «шумовых» циклов).
- Значения: `0..(зависит от эквивалента)`
- Дефолт: `0.01`
- Применение: runtime
- Риски: слишком низкий → много мелких операций; слишком высокий → упускаем полезные клиринги.

### `clearing.max_cycles_per_run`
- Назначение: ограничение числа клиринговых транзакций за один прогон.
- Значения: `0..100000`
- Дефолт: `200`
- Применение: runtime
- Риски: высокий лимит → пик нагрузки/блокировки; низкий → медленная «разрядка» долгов.

### `clearing.periodic_cycles_5_interval_seconds`
- Назначение: период фонового поиска циклов длиной 5 (если включён).
- Значения: `0..604800` (0 = выключено)
- Дефолт: `3600`
- Применение: runtime
- Риски: частый запуск может конкурировать с платежами за ресурсы.

### `clearing.periodic_cycles_6_interval_seconds`
- Назначение: период фонового поиска циклов длиной 6 (если включён).
- Значения: `0..604800` (0 = выключено)
- Дефолт: `86400`
- Применение: runtime
- Риски: как выше, но сильнее по стоимости.

---

## 2.4. `limits.*` (runtime)

Здесь предполагаются продуктовые/операционные лимиты. Важно: лимиты должны учитывать `verification_level` (если используется) и эквиваленты.

### `limits.max_trustlines_per_participant`
- Назначение: верхняя граница числа trust lines на участника.
- Значения: `0..10000`
- Дефолт: `50`
- Применение: runtime
- Риски: высокий лимит увеличивает размер графа и нагрузку на routing/clearing; низкий лимит может ухудшить UX.

### `limits.default_trustline_limit.*`
- Назначение: стартовый лимит trust line (если система поддерживает авто‑дефолт).
- Значения: число ≥ 0 (по типу эквивалента)
- Дефолт: `fiat_like: 100`, `time_like_hours: 2`
- Применение: runtime
- Риски: слишком высокие дефолты увеличивают риск дефолтов и конфликтов в пилоте.

### `limits.max_trustline_limit_without_admin_approval.*`
- Назначение: предел лимита trust line без явного одобрения админом.
- Значения: число ≥ 0
- Дефолт: `fiat_like: 1000`, `time_like_hours: 10`
- Применение: runtime
- Риски: слишком высокий → злоупотребления/спам; слишком низкий → админ‑бутылочное горлышко.

### `limits.max_payment_amount.*`
- Назначение: верхняя граница суммы платежа.
- Значения: число ≥ 0
- Дефолт: `fiat_like: 200`, `time_like_hours: 4`
- Применение: runtime
- Риски: высокий → рост рисков и стоимости multipath; низкий → ухудшение UX.

---

## 2.5. `protocol.*` (restart required)

Секция `protocol.*` описывает параметры, влияющие на **правила протокола** (2PC/валидация/дедлайны) и обычно требует рестарта.

### `protocol.prepare_timeout_ms`
- Назначение: таймаут фазы PREPARE в 2PC.
- Значения: `100..60000`
- Дефолт: `3000`
- Применение: restart required
- Риски: низкий → много abort; высокий → долгие зависания/блокировки.

### `protocol.commit_timeout_ms`
- Назначение: таймаут фазы COMMIT (и/или ожидания подтверждений применения).
- Значения: `100..60000`
- Дефолт: `5000`
- Применение: restart required
- Риски: как выше.

### `protocol.max_clock_skew_seconds`
- Назначение: допустимая рассинхронизация часов для подписанных сообщений.
- Значения: `0..3600`
- Дефолт: `300`
- Применение: restart required
- Риски: слишком низкий → ложные отказы; слишком высокий → больше окно replay-рисков.

---

## 2.6. `security.*` (mixed: часть restart required)

### `security.jwt_access_token_expire_minutes`
- Назначение: срок жизни access token.
- Значения: `1..1440`
- Дефолт: `60`
- Применение: restart required (рекомендуется)
- Риски: слишком длинный → повышает риск компрометации; слишком короткий → ухудшает UX.

### `security.jwt_refresh_token_expire_days`
- Назначение: срок жизни refresh token.
- Значения: `1..365`
- Дефолт: `30`
- Применение: restart required (рекомендуется)
- Риски: как выше.

### `security.rate_limits.*`
- Назначение: rate limiting (ключи зависят от реализации).
- Значения: строка формата `N/minute` или структура (в зависимости от реализации).
- Дефолт: `auth_login: 5/minute`, `payments: 30/minute`, `default: 100/minute`
- Применение: runtime (если rate-limit хранится в конфиг‑хранилище), иначе restart required
- Риски: слишком мягко → DoS/спам; слишком жёстко → ложные блокировки.

---

## 2.7. `database.*` (restart/migration)

### `database.pool_size`
- Назначение: размер пула соединений.
- Значения: `1..500`
- Дефолт: `20`
- Применение: restart required
- Риски: слишком мало → очереди; слишком много → перегруз БД.

### `database.max_overflow`
- Назначение: дополнительный overflow пула.
- Значения: `0..500`
- Дефолт: `10`
- Применение: restart required
- Риски: как выше.

### `database.pool_timeout_seconds`
- Назначение: таймаут ожидания соединения из пула.
- Значения: `1..300`
- Дефолт: `30`
- Применение: restart required
- Риски: низкий → ошибки при пиках; высокий → долгие подвисания.

---

## 2.8. `integrity.*` (restart required)

### `integrity.check_interval_seconds`
- Назначение: периодическая проверка инвариантов/целостности (если включено).
- Значения: `0..86400` (0 = выключено)
- Дефолт: `300`
- Применение: runtime или restart (в зависимости от реализации планировщика)
- Риски: слишком часто → нагрузка на БД; слишком редко → позднее выявление проблем.

### `integrity.checkpoint_interval_seconds`
- Назначение: период создания checkpoint/снимков состояния (если поддерживается).
- Значения: `0..86400` (0 = выключено)
- Дефолт: `3600`
- Применение: runtime или restart
- Риски: нагрузка на storage; требует политики retention.

---

## 2.9. `observability.*` (runtime)

### `observability.log_level`
- Назначение: уровень логирования.
- Значения: `DEBUG|INFO|WARNING|ERROR`
- Дефолт: `INFO`
- Применение: runtime
- Риски: `DEBUG` может утекать чувствительная информация и увеличивать нагрузку.

### `observability.metrics_enabled`
- Назначение: включить экспорт метрик.
- Значения: `true|false`
- Дефолт: `true`
- Применение: runtime
- Риски: минимальны.

### `observability.slow_query_threshold_ms`
- Назначение: порог для логирования медленных запросов.
- Значения: `0..600000`
- Дефолт: `1000`
- Применение: runtime
- Риски: низкий порог увеличивает шум.

---

## 3. Пример `geo-hub-config.yaml` (рекомендуемые дефолты для пилота)

```yaml
# geo-hub-config.yaml

feature_flags:
  multipath_enabled: true
  full_multipath_enabled: false

routing:
  multipath_mode: limited            # limited | full
  max_path_length: 6
  max_paths_per_payment: 3
  path_finding_timeout_ms: 500
  route_cache_ttl_seconds: 30

  # Full multipath (экспериментально; включать только для бенчмарков)
  full_multipath_budget_ms: 1000
  full_multipath_max_iterations: 100
  fallback_to_limited_on_full_failure: true

clearing:
  enabled: true
  trigger_cycles_max_length: 4
  periodic_cycles_5_interval_seconds: 3600
  periodic_cycles_6_interval_seconds: 86400
  min_clearing_amount: 0.01
  max_cycles_per_run: 200

limits:
  max_trustlines_per_participant: 50
  default_trustline_limit:
    fiat_like: 100
    time_like_hours: 2
  max_trustline_limit_without_admin_approval:
    fiat_like: 1000
    time_like_hours: 10
  max_payment_amount:
    fiat_like: 200
    time_like_hours: 4

protocol:
  prepare_timeout_ms: 3000
  commit_timeout_ms: 5000
  max_clock_skew_seconds: 300

security:
  jwt_access_token_expire_minutes: 60
  jwt_refresh_token_expire_days: 30
  rate_limits:
    auth_login: 5/minute
    payments: 30/minute
    default: 100/minute

database:
  pool_size: 20
  max_overflow: 10
  pool_timeout_seconds: 30

integrity:
  check_interval_seconds: 300
  checkpoint_interval_seconds: 3600

observability:
  log_level: INFO
  metrics_enabled: true
  slow_query_threshold_ms: 1000
```

---

## 4. Какие параметры должны быть доступны в админке

См. минимальную спецификацию админки: [`docs/ru/admin-console-minimal-spec.md`](docs/ru/admin-console-minimal-spec.md:1).

### 4.1. Runtime editable (через админку)
- `feature_flags.*`
- `routing.*`
- `clearing.*`
- `limits.*`
- `observability.*`

### 4.2. Read-only в админке (только просмотр)
- `protocol.*` (изменение требует рестарта и согласованного выката)
- `security.*` (часть может быть runtime, но по умолчанию считаем read-only для MVP)
- `database.*`
- `integrity.*` (по умолчанию read-only)

---

## 5. Минимальные метрики для перф‑проверок routing/clearing

Рекомендуемые метрики (минимум для пилота и бенчмарков):
- `routing_duration_ms` (p50/p95/p99) и разрез по `routing.multipath_mode`
- `routes_count` и `unique_nodes_in_routes`
- `routing_budget_exhausted_total`
- `routing_insufficient_capacity_total`
- `clearing_cycles_found_total` и `clearing_cycles_applied_total`
- `clearing_duration_ms` и `clearing_cycles_per_run`