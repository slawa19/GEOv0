# Runbook: GEO Hub dev-стенд в Windows 11 + WSL2 без Docker Desktop

Этот документ описывает самый простой способ поднять dev-стенд GEO Hub в окружении Windows 11 + WSL2, используя Docker Engine внутри WSL2 (без Docker Desktop).

Опирается на:
- docker-compose базовый: `docker-compose.yml`
- dev override (hot-reload + инструменты): `docker-compose.dev.yml`
- переменные окружения: `.env.example`
- быстрый старт/тесты: README.md

---

## 0) Рекомендованный принцип: держите репозиторий внутри файловой системы WSL

Важно для производительности (особенно hot-reload из compose dev override):

- Рекомендуется: `~/projects/GEOv0-PROJECT` внутри WSL (ext4).
- Не рекомендуется: `/mnt/c/...` (Windows-диск). На нём файловые события и скорость I/O часто хуже, uvicorn `--reload` может работать медленно/нестабильно.

---

## 1) Предварительные проверки WSL2 (один раз)

В PowerShell (Windows):

- Проверить, что у вас WSL2:
  - `wsl -l -v` (в колонке VERSION должно быть 2)

Дальше все команды из runbook выполняйте в терминале WSL (Ubuntu/Debian и т.д.).

---

## 2) Вариант A (рекомендуется): Docker Engine в WSL2 через systemd

### 2.1 Включить systemd в WSL (один раз)
Внутри WSL:

1) Открыть `/etc/wsl.conf` и добавить:

```
[boot]
systemd=true
```

2) Перезапустить WSL из Windows PowerShell:
- `wsl --shutdown`
- затем снова открыть WSL.

### 2.2 Установить Docker Engine + Compose plugin

Дальше команды зависят от дистрибутива, но общая цель одна:
- установить `docker` (client) + `dockerd` (engine)
- установить `docker compose` (Compose v2 plugin)

Проверки после установки:

- `docker version`
- `docker compose version`

### 2.3 Запустить Docker и настроить права

Внутри WSL:

- Запуск сервиса:
  - `sudo systemctl enable --now docker`

- (Рекомендуется) дать текущему пользователю право запускать docker без sudo:
  - `sudo usermod -aG docker $USER`
  - затем перелогиниться в WSL (закрыть окно терминала и открыть заново)

Проверка:

- `docker run --rm hello-world`

---

## 3) Вариант B (fallback): без systemd, запуск dockerd вручную

Если systemd включать не хотите/не можете:

1) Убедитесь, что docker установлен (как в варианте A).
2) Запускайте daemon вручную (в отдельной вкладке WSL):

- `sudo dockerd`

Проверка в другой вкладке:

- `docker ps`

Минусы fallback-пути:
- нет автозапуска
- нужно следить за логами и перезапуском `dockerd`

---

## 4) Поднять GEO Hub через Docker Compose

Перейдите в корень репозитория внутри WSL, например:

- `cd ~/projects/GEOv0-PROJECT`

### 4.1 Базовый production-like запуск (без hot-reload)

Base Compose задаёт `ENV=prod` и намеренно оставляет security-поля пустыми. До
запуска скопируйте `.env.example` в `.env` и заполните `JWT_SECRET`,
`ADMIN_TOKEN`, `SIMULATOR_SESSION_SECRET` и
`SIMULATOR_CSRF_ORIGIN_ALLOWLIST`. Иначе API корректно завершится с ошибкой
guardrail, не запустившись с небезопасным placeholder.

1) Поднять сервисы.

По умолчанию API слушает `http://localhost:8000`. Если этот порт уже занят другим сервисом (в Windows+WSL2 это встречается часто),
выберите другой host-port через переменную окружения (порт внутри контейнера остаётся `8000`):

- `GEO_API_PORT=18000 docker compose up -d --build`

Если конфликтов нет:

- `docker compose up -d --build`

Ожидаемые сервисы по `docker-compose.yml`:
- `db` (Postgres 16) порт 5432
- `redis` (Redis 7) порт 6379
- `app` (FastAPI) порт 8000

2) Применить миграции (важно):

По умолчанию миграции запускаются автоматически при старте контейнера `app` (см. `docker/docker-entrypoint.sh`).
Если нужно прогнать вручную:

- `docker compose exec app alembic -c migrations/alembic.ini upgrade head`

3) (Опционально) залить начальные данные:
- `docker compose exec app python scripts/seed_db.py`

### 4.2 Dev-режим с hot-reload (рекомендуется для разработки)

Запуск с override-файлом:

- `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`

Что меняется в dev override:
- приложение явно получает канонический `ENV=dev` и локальные dev-секреты
- `app` запускается `uvicorn ... --reload`
- код и тесты монтируются в контейнер для hot-reload
- логирование более подробное

### 4.3 Dev-tools (опционально): pgAdmin и redis-commander

Они описаны в `docker-compose.dev.yml` и включены через `profiles: tools`.

Запуск с профилем:

- `docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile tools up --build`

Порты:
- pgAdmin: http://localhost:5050
- redis-commander: http://localhost:8081

---

## 5) Проверка работоспособности

После запуска:

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs

Health endpoints (есть также алиасы `/api/v1/*`):
- `GET /health` и `GET /healthz` → `{ "status": "ok" }`
- `GET /health/db` → проверка подключения к БД (ok или HTTP 503)

---

## 6) Быстрая диагностика проблем

### 6.1 Статус контейнеров
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml ps`

### 6.2 Логи
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f app`
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f db`
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f redis`

### 6.3 Проверка healthcheck’ов
В `docker-compose.yml` есть healthcheck для `db` и `redis`.
Если `app` не стартует, часто причина: БД/Redis не healthy.

### 6.4 Конфликт портов
Если уже что-то заняло порты 8000/5432/6379/5050/8081, Compose не поднимется.
Решения:
- остановить конфликтующий сервис
- или поменять mapping портов в compose

### 6.5 Проблемы с hot-reload
Если `--reload` тормозит или не ловит изменения:
- перенесите репозиторий внутрь WSL (не `/mnt/c/...`)
- проверьте, что volumes из dev override реально смонтированы
- проверьте логи `app` на ошибки файловых watcher’ов

---

## 7) Тестирование

### 7.1 Базовый прогон тестов (как в README)
Тесты удобно гонять локально (на Windows или в WSL) через Python venv и зависимости:
- `requirements.txt`
- `requirements-dev.txt`

Запуск из PowerShell:
- `.\scripts\verify_local.ps1 -TaskSlug runbook_backend -BackendOnly`

### 7.2 OpenAPI contract test
- `.\scripts\verify_local.ps1 -TaskSlug runbook_openapi -BackendOnly -BackendSelector tests/contract/test_openapi_contract.py`

### 7.3 Postgres-backed тест конкурентности (важно)
Для проверки семантики блокировок/изоляции нужен Postgres (SQLite не подходит).

Паттерн:
1) Поднять контейнер БД:
- `docker compose up -d db`

2) Выбрать уникальное имя, проверить отсутствие БД и только затем создать её внутри контейнера:
- `docker exec geov0-db createdb -U geo geov0_test_runbook`

3) Указать переменные окружения тестов:
- `TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_runbook`
- `GEO_TEST_ALLOW_DB_RESET=1`

4) Запустить конкретный тест:
- `.\scripts\verify_local.ps1 -TaskSlug runbook_postgres -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py`

Внимание: reset opt-in допустим только после проверки точного URL и отсутствия уникальной
`geov0_test_*` БД. После прогона удаляйте только эту БД при нуле активных соединений.

---

## 8) Сводка «самый быстрый путь»

Если Docker уже установлен и работает в WSL:

1) Поднять всё:
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`

2) Миграции (обычно не нужно, потому что есть авто-миграции в entrypoint):
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app alembic -c migrations/alembic.ini upgrade head`

3) Проверка:
- http://localhost:8000/health
- http://localhost:8000/docs
