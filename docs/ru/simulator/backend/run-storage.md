# Хранение состояния и артефактов прогонов (MVP)

**Статус:** done (2026-01-28)

Цель: зафиксировать, что и где мы храним для real mode, чтобы UI был DB-first, а «артефакты» оставались export.

## 1) Классы данных

### 1.1 Run state (control-plane)
Примеры: `RunStatus`, counters, last_error.

**Назначение:** UI вкладка Run, история прогонов, мониторинг.

### 1.2 Events (data plane)
Поток событий для анимации и понимания «что происходит».

### 1.3 Snapshots
Снимки состояния графа (`SimulatorGraphSnapshot`).

### 1.4 Metrics / Bottlenecks
Готовые агрегаты для UI графиков и списка узких мест.

### 1.5 Artifacts (export)
Файлы для воспроизводимости/инцидентов: `summary.json`, `events.ndjson`, `bundle.zip`.

## 2) MVP архитектура хранения (рекомендуемая)

### 2.1 In-memory
- `run state (оперативное)` — текущий статус, очередь, counters.
- `events ring buffer` — последние N событий или последние T минут.

**TTL:** минуты/часы, в зависимости от объёма.

### 2.2 Postgres (DB-first для UI)
Храним то, что UI показывает как «истину»:
- список прогонов и их статус/ошибки
- метрики time-series
- bottlenecks (top-N)
- индекс артефактов (ссылки на экспорт)

Raw events по умолчанию **не храним** в Postgres (только in-memory + export при необходимости).

#### 2.2.1 Предложение DDL (MVP)
Ниже — целевая схема (названия и поля фиксируем как ориентир для будущей alembic-миграции).
В БД используем UUID для `run_id`, но наружу это строка.

```sql
-- Scenarios (upload/store)
CREATE TABLE IF NOT EXISTS simulator_scenarios (
  scenario_id TEXT PRIMARY KEY,
  name TEXT NULL,
  description TEXT NULL,
  schema_version TEXT NOT NULL,
  base_equivalent_code TEXT NULL,
  equivalents_codes TEXT[] NULL,
  scenario_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_simulator_scenarios_created_at
  ON simulator_scenarios (created_at);

-- Runs (control-plane source of truth)
CREATE TABLE IF NOT EXISTS simulator_runs (
  run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scenario_id TEXT NOT NULL REFERENCES simulator_scenarios(scenario_id),
  mode TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at TIMESTAMPTZ NULL,
  stopped_at TIMESTAMPTZ NULL,
  sim_time_ms BIGINT NULL CHECK (sim_time_ms >= 0),
  intensity_percent INT NULL CHECK (intensity_percent >= 0 AND intensity_percent <= 100),
  ops_sec DOUBLE PRECISION NULL CHECK (ops_sec >= 0),
  queue_depth INT NULL CHECK (queue_depth >= 0),
  errors_total INT NULL CHECK (errors_total >= 0),
  errors_last_1m INT NULL CHECK (errors_last_1m >= 0),
  last_event_type TEXT NULL,
  current_phase TEXT NULL,
  last_error JSONB NULL,
  summary JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- mode/state enums фиксируем check-constraint'ами (совпадают с UI контрактом)
ALTER TABLE simulator_runs
  ADD CONSTRAINT chk_simulator_runs_mode
  CHECK (mode IN ('fixtures', 'real'));

ALTER TABLE simulator_runs
  ADD CONSTRAINT chk_simulator_runs_state
  CHECK (state IN ('idle', 'running', 'paused', 'stopping', 'stopped', 'error'));

CREATE INDEX IF NOT EXISTS idx_simulator_runs_created_at
  ON simulator_runs (created_at);
CREATE INDEX IF NOT EXISTS idx_simulator_runs_scenario_created_at
  ON simulator_runs (scenario_id, created_at);
CREATE INDEX IF NOT EXISTS idx_simulator_runs_state
  ON simulator_runs (state);

-- Metrics points (normalized time-series)
CREATE TABLE IF NOT EXISTS simulator_run_metrics (
  run_id UUID NOT NULL REFERENCES simulator_runs(run_id) ON DELETE CASCADE,
  equivalent_code TEXT NOT NULL,
  key TEXT NOT NULL,
  t_ms BIGINT NOT NULL CHECK (t_ms >= 0),
  -- 007 / T715 (2026-08-20): деньги остаются точным decimal.
  -- Старые float-строки архивированы в simulator_run_metrics_float_archive
  -- (значение сохранено как текст), а не сконвертированы.
  value NUMERIC(20, 8) NULL,
  PRIMARY KEY (run_id, equivalent_code, key, t_ms)
);

ALTER TABLE simulator_run_metrics
  ADD CONSTRAINT chk_simulator_run_metrics_key
  CHECK (key IN ('success_rate', 'avg_route_length', 'total_debt', 'clearing_volume', 'bottlenecks_score'));

CREATE INDEX IF NOT EXISTS idx_simulator_run_metrics_run_key
  ON simulator_run_metrics (run_id, key);

-- Bottlenecks (top-N at computed_at)
CREATE TABLE IF NOT EXISTS simulator_run_bottlenecks (
  run_id UUID NOT NULL REFERENCES simulator_runs(run_id) ON DELETE CASCADE,
  equivalent_code TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 1),
  reason_code TEXT NOT NULL,
  details JSONB NULL,
  PRIMARY KEY (run_id, equivalent_code, computed_at, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_simulator_run_bottlenecks_run_equiv_time
  ON simulator_run_bottlenecks (run_id, equivalent_code, computed_at DESC);

-- Artifacts index (browser-friendly URLs)
CREATE TABLE IF NOT EXISTS simulator_run_artifacts (
  run_id UUID NOT NULL REFERENCES simulator_runs(run_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  content_type TEXT NULL,
  size_bytes BIGINT NULL CHECK (size_bytes >= 0),
  sha256 TEXT NULL,
  storage_url TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (run_id, name)
);

CREATE INDEX IF NOT EXISTS idx_simulator_run_artifacts_created_at
  ON simulator_run_artifacts (created_at);
```

### Точность `simulator_run_metrics.value` (007 / T715, 2026-08-20)

Колонка — `NUMERIC(20, 8)`, а `MetricPoint.v` на проводе — decimal string. Серии
`total_debt` и `clearing_volume` — деньги, и они **точны только на PostgreSQL**.

**Известное ограничение SQLite.** SQLite не имеет нативного decimal: SQLAlchemy
проводит `Numeric` через binary float, поэтому `Decimal("12345678901.12345678")`
читается обратно как `Decimal("12345678901.12345695")`. Провод при этом отдаёт
decimal string, то есть форму, зарезервированную за точными деньгами. SQLite остаётся
поддерживаемым бэкендом сознательно — это дефолт `DATABASE_URL` (`app/config.py`) и
документированный путь локальной разработки без Docker (`README.md`), — но ограничение
объявлено, а не спрятано: `app/core/simulator/storage.py` пишет **один WARNING за
прогон** (`simulator.storage.sqlite_money_metrics_are_not_exact`), когда денежная серия
фактически персистится на SQLite. Ровно так же, как §4 AGENTS.md разводит SQLite и
Postgres по семантике блокировок: денежные метрики симулятора точны только на Postgres.

**Потолок `NUMERIC(20, 8)`** — 12 целых цифр. Значение `total_debt >= 10^12` даёт
`numeric field overflow`; писатель best-effort, он логирует отказ и откатывает
**только свой SAVEPOINT**, не транзакцию вызывающего тика.

**Архив старых float-значений.** Миграция `018_simulator_run_metrics_numeric_value`
не конвертирует старые строки, а переносит их в `simulator_run_metrics_float_archive`
текстом (`value_text`), с `extra_float_digits = 3`, зафиксированным на транзакцию
миграции, и проверкой, что каждая строка кастуется обратно в тот же `float8`.

Примечания:
- `last_error` хранится как JSONB и повторяет структуру `RunStatus.last_error`.
- `summary` — расширяемый JSONB для отчёта по прогону (для вкладки Run/Artifacts).
- Метрики допускают `value = NULL` для «пустых бакетов».

**Retention:** 7–30 дней (настраиваемо). Удаление run каскадно удаляет metrics/bottlenecks/artifacts index.

### 2.3 Redis (опционально)
- Нужен только если:
  - несколько инстансов backend
  - несколько UI клиентов на один run
  - нужно fan-out/буферизация вне процесса

Варианты:
- Pub/Sub для live
- Streams для гарантированной доставки/offset

## 3) Что считать «артефактом», а что «данными в БД»

### 3.1 В БД
- Всё, что UI показывает как основную информацию: список прогонов, статусы, summary, метрики, bottlenecks, ссылки на export.

### 3.2 Артефакты (export)
- Всё, что нужно для воспроизводимости/разбора вне UI:
  - полные события
  - исторические снапшоты
  - bundle.zip

UI в браузере не «открывает папки», поэтому артефакты должны быть доступны как URL на скачивание.

## 4) Очистка
- `in-memory` очищается TTL.
- `postgres` — периодическая job очистки по retention.
- `artifacts` — отдельный retention (дни/недели) или ручное удаление.

### 4.1 Рекомендованный retention job (SQL)
```sql
-- удалить старые прогоны (каскадно чистит metrics/bottlenecks/artifacts index)
DELETE FROM simulator_runs
WHERE created_at < NOW() - INTERVAL '30 days';
```

### 4.2 Очистка файлов артефактов
Артефакты физически лежат в файловом хранилище (локально или в S3-совместимом). Для локального MVP:

- Layout (текущая реализация): `.local-run/simulator/runs/<run_id>/artifacts/<name>`
- В БД в `simulator_run_artifacts.storage_url` кладём URL вида:
  - `/api/v1/simulator/runs/<run_id>/artifacts/<name>`

Файловую очистку делаем отдельной job:
- находит runs старше retention
- удаляет папку `.local-run/simulator/runs/<run_id>`
- (опционально) удаляет записи `simulator_runs` (если ещё не удалены)

## 5) TODO (для закрытия документа)
- Replay buffer для `Last-Event-ID` уже реализован как in-memory ring-buffer (best-effort).
  - env: `SIMULATOR_EVENT_BUFFER_SIZE`, `SIMULATOR_EVENT_BUFFER_TTL_SEC`, `SIMULATOR_SSE_SUB_QUEUE_MAX`
  - replay correctness безусловна: любой cursor, который нельзя полностью
    восстановить (invalid/future/pruned/oversized), возвращает `HTTP 410`;
  - переполнение subscriber queue закрывает stream, после чего клиент
    восстанавливает gap через reconnect/replay.
