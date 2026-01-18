# GEO Hub: Развёртывание

**Версия:** 0.1  
**Дата:** Ноябрь 2025

---

## Содержание

1. [Требования](#1-требования)
2. [Быстрый старт (Docker)](#2-быстрый-старт-docker)
3. [Ручная установка](#3-ручная-установка)
4. [Конфигурация](#4-конфигурация)
5. [Production развёртывание](#5-production-развёртывание)
6. [Мониторинг](#6-мониторинг)
7. [Backup и восстановление](#7-backup-и-восстановление)
8. [Обновление](#8-обновление)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Требования

### 1.1. Минимальные требования (development)

| Компонент | Требование |
|-----------|------------|
| CPU | 2 cores |
| RAM | 2 GB |
| Disk | 10 GB SSD |
| OS | Linux, macOS, Windows (WSL2) |

### 1.2. Рекомендуемые (production, до 500 участников)

| Компонент | Требование |
|-----------|------------|
| CPU | 4 cores |
| RAM | 8 GB |
| Disk | 50 GB SSD |
| OS | Ubuntu 22.04 LTS / Debian 12 |

### 1.3. Программные требования

- **Docker** 24.0+ и **Docker Compose** 2.20+
- Или:
  - Python 3.11+
  - PostgreSQL 16+
  - Redis 7+
  - Node.js 20+ (только если вы запускаете Admin UI из `admin-ui/`)

---

## 2. Быстрый старт (Docker)

### 2.1. Клонирование репозитория

```bash
git clone https://github.com/slawa19/GEOv0.git
cd GEOv0-PROJECT
```

### 2.2. Настройка окружения

` .env.example` в репозитории дан как справочник (см. также `app/config.py`).

Для Docker Compose копировать `.env` не обязательно: дефолтные значения (DB/Redis URL, JWT настройки и т.п.)
уже заданы в `docker-compose.yml`. `.env` нужен только если вы хотите переопределить секреты/порты/флаги.

### 2.3. Запуск

```bash
# Запустить все сервисы (DB, Redis, API)
docker compose up -d --build

# Если localhost:8000 занят другим сервисом (часто в Windows+WSL2),
# выберите другой host-port (порт внутри контейнера остаётся 8000):
# GEO_API_PORT=18000 docker compose up -d --build

# Проверить статус
docker compose ps

# Просмотреть логи
docker compose logs -f app
```

### 2.4. Admin UI (опционально)

Admin UI находится в `admin-ui/` и работает как отдельное приложение (Vue 3 + TypeScript + Vite).

Режимы:

- `real` — обращается к backend Admin API (`/api/v1/admin/*`)
- `mock` — работает на фикстурах (для детерминированных демо-наборов)

Запуск (real-mode):

```bash
npm --prefix admin-ui install

# В PowerShell:
#   $env:VITE_API_MODE = 'real'
#   $env:VITE_API_BASE_URL = 'http://localhost:8000'   # Docker default
#   # Если вы меняли GEO_API_PORT (например 18000), используйте его здесь.
#   # Если запускаете локально через scripts/run_local.ps1, дефолт: http://127.0.0.1:18000

npm --prefix admin-ui run dev
```

### 2.5. Инициализация базы данных

Миграции выполняются автоматически при старте контейнера (см. `docker/docker-entrypoint.sh`).

Если нужно запустить миграции вручную:

```bash
docker compose exec app alembic -c migrations/alembic.ini upgrade head
```

(Опционально) загрузить демо-данные из `seeds/`:

```bash
docker compose exec app python scripts/seed_db.py
```

### 2.6. Проверка

```bash
# Проверить API
curl http://localhost:8000/health
curl http://localhost:8000/health/db

# Swagger UI
# http://localhost:8000/docs
```

Если запускали с `GEO_API_PORT=18000`, замените `8000` на `18000`.

### 2.7. Остановка

```bash
docker compose down

# С удалением данных (осторожно!)
docker compose down -v
```

---

## 3. Ручная установка

### 3.1. PostgreSQL

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql-16 postgresql-contrib-16

# Создать пользователя и базу
sudo -u postgres psql
CREATE USER geo_hub WITH PASSWORD 'your_password';
CREATE DATABASE geo_hub OWNER geo_hub;
\q
```

### 3.2. Redis

```bash
# Ubuntu/Debian
sudo apt install redis-server

# Запустить
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

### 3.3. Python окружение

```bash
# Установить Python 3.11
sudo apt install python3.11 python3.11-venv

# Создать виртуальное окружение
python3.11 -m venv venv
source venv/bin/activate

# Установить зависимости
python -m pip install -r requirements.txt -r requirements-dev.txt
```

### 3.4. Применить миграции

```bash
# Пример переменных окружения
export DATABASE_URL="postgresql+asyncpg://geo:geo@localhost:5432/geov0"
export REDIS_URL="redis://localhost:6379/0"
export JWT_SECRET="change-me-in-production"

# Миграции
alembic -c migrations/alembic.ini upgrade head

# Опционально: сиды
python scripts/seed_db.py
```

### 3.5. Запуск приложения

```bash
# Development (manual run; локальный runner обычно использует :18000)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## 4. Конфигурация

### 4.0. Схема конфигурации (env + YAML)

В проекте используется два уровня конфигурации:

1) **Переменные окружения (.env)** — инфраструктура и секреты (URL БД/Redis, секреты JWT, режим debug, базовые лимиты запросов и т.п.).
2) **YAML конфиг хаба** — параметры протокола и поведения: `routing.*`, `clearing.*`, `limits.*`, `feature_flags.*`, `observability.*` (см. пример `geo-hub-config.yaml` в [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1)).

Полный список параметров, их дефолты, диапазоны и пометки **runtime (через админку)** vs **restart/migration** находятся в [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1).

Важно для MVP: параметры, которые ожидаемо придётся подкручивать в пилоте **без рестарта** (через админку/оператора), включают как минимум:
- `routing.max_paths_per_payment` (перф‑проверки multipath/full‑multipath);
- `clearing.trigger_cycles_max_length` (перф‑проверки клиринга).

### 4.1. Переменные окружения

```bash
# === Обязательные (для manual run) ===

DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-me-in-production

# === Опциональные ===

DEBUG=false
LOG_LEVEL=INFO

JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

REDIS_ENABLED=true

# Admin API (MVP)
ADMIN_TOKEN=dev-admin-token-change-me
```

### 4.2. Пример .env файла

```bash
# .env (пример)
DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:5432/geov0
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-me-in-production

DEBUG=false
LOG_LEVEL=INFO

JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

REDIS_ENABLED=true
ADMIN_TOKEN=dev-admin-token-change-me
```

### 4.3. Конфигурация логирования

```python
# logging_config.py (или в app/config.py)
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        },
        "json": {
            "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default"
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "/var/log/geo-hub/app.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "formatter": "json"
        }
    },
    "loggers": {
        "app": {"level": "INFO", "handlers": ["console", "file"]},
        "uvicorn": {"level": "INFO", "handlers": ["console"]}
    }
}
```

---

## 5. Production развёртывание

### 5.1. Архитектура

```
                    ┌─────────────────┐
                    │   Cloudflare    │
                    │   (CDN + WAF)   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │     Nginx       │
                    │  (reverse proxy)│
                    └────────┬────────┘
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
    ┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐
    │   App 1     │   │   App 2     │   │   App 3     │
    │  (uvicorn)  │   │  (uvicorn)  │   │  (uvicorn)  │
    └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
           │                 │                 │
           └─────────────────┼─────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐┌──────▼──────┐┌──────▼──────┐
       │ PostgreSQL  ││    Redis    ││   Loki      │
       │  (primary)  ││             ││  (logs)     │
       └─────────────┘└─────────────┘└─────────────┘
```

### 5.2. Nginx конфигурация

```nginx
# /etc/nginx/sites-available/geo-hub
upstream geo_hub {
    least_conn;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
}

server {
    listen 443 ssl http2;
    server_name hub.example.com;

    ssl_certificate /etc/letsencrypt/live/hub.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hub.example.com/privkey.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # API
    location /api/ {
        proxy_pass http://geo_hub;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Таймауты
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # WebSocket
    location /api/v1/ws {
        proxy_pass http://geo_hub;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    # Health check (без логирования)
    location /health {
        proxy_pass http://geo_hub;
        access_log off;
    }
}

server {
    listen 80;
    server_name hub.example.com;
    return 301 https://$server_name$request_uri;
}
```

### 5.3. Systemd сервис

```ini
# /etc/systemd/system/geo-hub.service
[Unit]
Description=GEO Hub API Server
After=network.target postgresql.service redis.service

[Service]
Type=exec
User=geo
Group=geo
WorkingDirectory=/opt/geo-hub
Environment="PATH=/opt/geo-hub/venv/bin"
EnvironmentFile=/opt/geo-hub/.env
ExecStart=/opt/geo-hub/venv/bin/gunicorn app.main:app \
    -w 4 \
    -k uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:8001 \
    --access-logfile /var/log/geo-hub/access.log \
    --error-logfile /var/log/geo-hub/error.log
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
# Активация
sudo systemctl daemon-reload
sudo systemctl enable geo-hub
sudo systemctl start geo-hub
```

### 5.4. Docker Compose (production)

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  app:
    image: ghcr.io/geo-protocol/geo-hub:latest
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '1'
          memory: 1G
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - JWT_SECRET=${JWT_SECRET}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    depends_on:
      - postgres
      - redis

  postgres:
    image: postgres:15
    volumes:
      - postgres_data:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=${DB_USER}
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      - POSTGRES_DB=${DB_NAME}
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/letsencrypt:ro
    depends_on:
      - app

volumes:
  postgres_data:
  redis_data:
```

---

## 6. Мониторинг

### 6.1. Prometheus метрики

Endpoint: `GET /metrics`

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'geo-hub'
    static_configs:
      - targets: ['geo-hub:8000']
    metrics_path: /metrics
```

### 6.2. Grafana дашборд

Готовый JSON дашборда в этом репозитории не поставляется. Соберите дашборд по метрикам, доступным на `GET /metrics`.

Рекомендуемые панели (примеры PromQL):

- HTTP RPS: `sum(rate(geo_http_requests_total[5m]))`
- Доля 5xx: `sum(rate(geo_http_requests_total{status=~"5.."}[5m])) / sum(rate(geo_http_requests_total[5m]))`
- Latency p95 по payments: `histogram_quantile(0.95, sum by (le) (rate(geo_http_request_duration_seconds_bucket{path=~"/api/v1/payments.*"}[5m])))`
- Payment events: `sum(rate(geo_payment_events_total[5m])) by (event, result)`
- Clearing events: `sum(rate(geo_clearing_events_total[5m])) by (event, result)`

- HTTP запросы в минуту
- HTTP latency (p50, p95, p99)
- Payment events (по типу/результату)
- Clearing events (по типу/результату)
- Routing failures (по причине)
- Доля ошибок (5xx)

### 6.3. Alerting правила

```yaml
# alerting_rules.yml
groups:
  - name: geo-hub
    rules:
      - alert: HighErrorRate
        expr: sum(rate(geo_http_requests_total{status=~"5.."}[5m])) / sum(rate(geo_http_requests_total[5m])) > 0.01
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High error rate detected"

      - alert: PaymentLatencyHigh
        expr: histogram_quantile(0.99, sum by (le) (rate(geo_http_request_duration_seconds_bucket{path=~"/api/v1/payments.*"}[5m]))) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Payment latency p99 > 5s"

      - alert: DatabaseConnectionsHigh
        expr: pg_stat_activity_count > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Database connections > 80%"
```

### 6.4. Health endpoints

```bash
# Liveness / readiness probes
curl http://localhost:8000/healthz
# {"status": "ok"}

curl http://localhost:8000/health
# {"status": "ok"}

# Проверка доступности БД
curl http://localhost:8000/health/db
# {"status": "ok"}  (или HTTP 503 с {"status": "error", ...})
```

---

## 7. Backup и восстановление

### 7.1. Автоматический backup PostgreSQL

```bash
#!/bin/bash
# /opt/scripts/backup-postgres.sh

BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
FILENAME="geo_hub_${DATE}.dump"

# Создать backup
pg_dump -Fc -h localhost -U geo_hub geo_hub > "${BACKUP_DIR}/${FILENAME}"

# Сжать
gzip "${BACKUP_DIR}/${FILENAME}"

# Удалить старые (>7 дней)
find "${BACKUP_DIR}" -name "*.dump.gz" -mtime +7 -delete

# Загрузить в S3 (опционально)
aws s3 cp "${BACKUP_DIR}/${FILENAME}.gz" s3://backups/geo-hub/
```

```bash
# Cron: каждый день в 3:00
0 3 * * * /opt/scripts/backup-postgres.sh
```

### 7.2. Восстановление из backup

```bash
# Остановить приложение
sudo systemctl stop geo-hub

# Восстановить базу
gunzip -c /backups/postgres/geo_hub_20251129.dump.gz | \
    pg_restore -h localhost -U geo_hub -d geo_hub --clean

# Запустить приложение
sudo systemctl start geo-hub
```

### 7.3. Point-in-Time Recovery

```bash
# Включить WAL archiving в postgresql.conf
archive_mode = on
archive_command = 'cp %p /var/lib/postgresql/wal_archive/%f'

# Восстановление на конкретный момент
pg_restore --target-time="2025-11-29 12:00:00" ...
```

---

## 8. Обновление

### 8.1. Обновление через Docker

```bash
# Получить новую версию
docker compose pull

# Применить миграции (в отдельном контейнере)
docker compose run --rm app alembic -c migrations/alembic.ini upgrade head

# Перезапустить с zero downtime
docker compose up -d --no-deps --scale app=4 app
sleep 10
docker compose up -d --no-deps --scale app=3 app
```

### 8.2. Обновление вручную

```bash
# 1. Получить новый код
cd /opt/geo-hub
git pull origin main

# 2. Обновить зависимости
source venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt

# 3. Применить миграции
alembic -c migrations/alembic.ini upgrade head

# 4. Перезапустить
sudo systemctl restart geo-hub
```

### 8.3. Откат

```bash
# Откатить миграции
alembic downgrade -1

# Вернуть код
git checkout v0.1.0

# Перезапустить
sudo systemctl restart geo-hub
```

---

## 9. Troubleshooting

### 9.1. Проверка состояния

```bash
# Статус сервисов
sudo systemctl status geo-hub
sudo systemctl status postgresql
sudo systemctl status redis

# Логи приложения
journalctl -u geo-hub -f
tail -f /var/log/geo-hub/app.log

# Подключение к БД
psql -h localhost -U geo_hub -d geo_hub
```

### 9.2. Типичные проблемы

#### Приложение не запускается

```bash
# Проверить переменные окружения
cat /opt/geo-hub/.env

# Проверить доступность БД
psql -h localhost -U geo_hub -d geo_hub -c "SELECT 1"

# Проверить Redis
redis-cli ping
```

#### Медленные платежи

```bash
# Проверить индексы
psql -d geo_hub -c "SELECT * FROM pg_stat_user_indexes WHERE idx_scan = 0;"

# Проверить блокировки
psql -d geo_hub -c "SELECT * FROM pg_locks WHERE NOT granted;"

# Проверить Redis memory
redis-cli info memory
```

#### Высокая нагрузка на БД

```bash
# Активные запросы
psql -d geo_hub -c "SELECT * FROM pg_stat_activity WHERE state = 'active';"

# Долгие запросы
psql -d geo_hub -c "SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;"

# Vacuum
psql -d geo_hub -c "VACUUM ANALYZE;"
```

### 9.3. Полезные команды

```bash
# Очистить просроченные locks
python -m app.cli cleanup-locks

# Проверить целостность данных
python -m app.cli check-integrity

# Экспорт состояния участника
python -m app.cli export-participant --pid=xxx --output=participant.json

# Статистика БД
python -m app.cli db-stats
```

---

## Связанные документы

- [03-architecture.md](03-architecture.md) — Архитектура системы
- [04-api-reference.md](04-api-reference.md) — Справочник API
- [06-contributing.md](06-contributing.md) — Как вносить вклад
