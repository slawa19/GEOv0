# GEO Hub: Wdrożenie

**Wersja:** 0.1  
**Data:** Listopad 2025

---

## Spis treści

1. [Wymagania](#1-wymagania)  
2. [Szybki start (Docker)](#2-szybki-start-docker)  
3. [Instalacja ręczna](#3-instalacja-ręczna)  
4. [Konfiguracja](#4-konfiguracja)  
5. [Wdrożenie produkcyjne](#5-wdrożenie-produkcyjne)  
6. [Monitoring](#6-monitoring)  
7. [Backup i odtwarzanie](#7-backup-i-odtwarzanie)  
8. [Aktualizacja](#8-aktualizacja)  
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Wymagania

### 1.1. Minimalne wymagania (development)

| Komponent | Wymaganie |
|-----------|-----------|
| CPU | 2 rdzenie |
| RAM | 2 GB |
| Dysk | 10 GB SSD |
| OS | Linux, macOS, Windows (WSL2) |

### 1.2. Zalecane (production, do 500 uczestników)

| Komponent | Wymaganie |
|-----------|-----------|
| CPU | 4 rdzenie |
| RAM | 8 GB |
| Dysk | 50 GB SSD |
| OS | Ubuntu 22.04 LTS / Debian 12 |

### 1.3. Wymagania programowe

- **Docker** 24.0+ oraz **Docker Compose** 2.20+  
- Lub (wariant ręczny):
  - Python 3.11+
  - PostgreSQL 15+
  - Redis 7+

Uwaga dla Windows: szczegółowa instrukcja dla Windows 11 + WSL2 **bez Docker Desktop** jest tutaj: `docs/pl/runbook-dev-wsl2-docker-no-desktop.md`.

---

## 2. Szybki start (Docker)

### 2.1. Klonowanie repozytorium

```bash
git clone https://github.com/slawa19/GEOv0.git
cd GEOv0-PROJECT
```

### 2.2. Konfiguracja środowiska

Repozytorium zawiera `.env.example` jako referencję (zob. też `app/config.py`).

Dla Docker Compose kopiowanie `.env` jest opcjonalne: domyślne wartości (URL DB/Redis, ustawienia JWT itd.)
są już ustawione w `docker-compose.yml`. `.env` potrzebujesz tylko, jeśli chcesz nadpisać sekrety/porty/flagi.

### 2.3. Uruchomienie

```bash
# Uruchom wszystkie serwisy (DB, Redis, API)
docker compose up -d --build

# Jeśli localhost:8000 jest zajęty przez inną usługę (częste w Windows+WSL2),
# wybierz inny port hosta (port w kontenerze pozostaje 8000):
# GEO_API_PORT=18000 docker compose up -d --build

# Sprawdź status
docker compose ps

# Podgląd logów
docker compose logs -f app
```

### 2.4. Inicjalizacja bazy danych

Migracje są wykonywane automatycznie przy starcie kontenera (zob. `docker/docker-entrypoint.sh`).

Jeśli chcesz uruchomić migracje ręcznie:

```bash
docker compose exec app alembic -c migrations/alembic.ini upgrade head
```

(Opcjonalnie) zasil dane demo z `seeds/`:

```bash
docker compose exec app python scripts/seed_db.py
```

### 2.5. Weryfikacja

```bash
# Sprawdź API
curl http://localhost:8000/health
curl http://localhost:8000/health/db

# Swagger UI:
# http://localhost:8000/docs
```

Jeśli uruchomiłeś z `GEO_API_PORT=18000`, zamień `8000` na `18000`.

### 2.6. Zatrzymanie

```bash
docker compose down

# Z usunięciem danych (uwaga!)
docker compose down -v
```

---

## 3. Instalacja ręczna

### 3.1. PostgreSQL

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql-15 postgresql-contrib-15

# Utwórz użytkownika i bazę
sudo -u postgres psql
CREATE USER geo_hub WITH PASSWORD 'your_password';
CREATE DATABASE geo_hub OWNER geo_hub;
\q
```

### 3.2. Redis

```bash
# Ubuntu/Debian
sudo apt install redis-server

# Uruchom
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

### 3.3. Środowisko Python

```bash
# Zainstaluj Python 3.11
sudo apt install python3.11 python3.11-venv

# Utwórz wirtualne środowisko
python3.11 -m venv venv
source venv/bin/activate

# Zainstaluj zależności
pip install -e ".[dev]"
```

### 3.4. Migracje

```bash
# Przykładowe zmienne środowiskowe
export DATABASE_URL="postgresql+asyncpg://geo:geo@localhost:5432/geov0"
export REDIS_URL="redis://localhost:6379/0"
export JWT_SECRET="change-me-in-production"

# Migracje
alembic -c migrations/alembic.ini upgrade head

# Opcjonalnie: seed
python scripts/seed_db.py
```

### 3.5. Uruchomienie aplikacji

```bash
# Development
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## 4. Konfiguracja

### 4.1. Zmienne środowiskowe

```bash
# === Wymagane (dla uruchomienia ręcznego) ===

DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-me-in-production

# === Opcjonalne ===

DEBUG=false
LOG_LEVEL=INFO

JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

REDIS_ENABLED=true

# Admin API (MVP)
ADMIN_TOKEN=dev-admin-token-change-me
```

### 4.2. Przykładowy plik .env

```bash
# .env (przykład)
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

### 4.3. Konfiguracja logowania

```python
# logging_config.py (lub w app/config.py)
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

## 5. Wdrożenie produkcyjne

### 5.1. Architektura

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

### 5.2. Konfiguracja Nginx

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

    # Nagłówki bezpieczeństwa
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
        
        # Timeouty
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

    # Health check (bez logowania)
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

### 5.3. Usługa systemd

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
# Aktywacja
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

## 6. Monitoring

### 6.1. Metryki Prometheus

Endpoint: `GET /metrics`

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'geo-hub'
    static_configs:
      - targets: ['geo-hub:8000']
    metrics_path: /metrics
```

### 6.2. Dashboard Grafana

JSON dashboardu nie jest dostarczany w tym repozytorium. Zbuduj dashboard na podstawie metryk z `GET /metrics`.

Sugerowane panele (przykłady PromQL):

- HTTP RPS: `sum(rate(geo_http_requests_total[5m]))`
- Udział 5xx: `sum(rate(geo_http_requests_total{status=~"5.."}[5m])) / sum(rate(geo_http_requests_total[5m]))`
- Latencja p95 dla payments: `histogram_quantile(0.95, sum by (le) (rate(geo_http_request_duration_seconds_bucket{path=~"/api/v1/payments.*"}[5m])))`
- Payment events: `sum(rate(geo_payment_events_total[5m])) by (event, result)`
- Clearing events: `sum(rate(geo_clearing_events_total[5m])) by (event, result)`

- Żądania HTTP na minutę  
- Latencja HTTP (p50, p95, p99)  
- Payment events (wg typu/wyniku)  
- Clearing events (wg typu/wyniku)  
- Routing failures (wg przyczyny)  
- Udział błędów (5xx)  

### 6.3. Reguły alertów

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

### 6.4. Endpoints health

```bash
# Liveness / readiness probes
curl http://localhost:8000/health
# {"status": "ok"}

# (alias) curl http://localhost:8000/healthz
# {"status": "ok"}

# Sprawdzenie dostępności bazy danych
curl http://localhost:8000/health/db
# {"status": "ok"}  (albo HTTP 503 z {"status": "error", ...})
```

---

## 7. Backup i odtwarzanie

### 7.1. Automatyczny backup PostgreSQL

```bash
#!/bin/bash
# /opt/scripts/backup-postgres.sh

BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
FILENAME="geo_hub_${DATE}.dump"

# Utwórz backup
pg_dump -Fc -h localhost -U geo_hub geo_hub > "${BACKUP_DIR}/${FILENAME}"

# Kompresja
gzip "${BACKUP_DIR}/${FILENAME}"

# Usuń stare (>7 dni)
find "${BACKUP_DIR}" -name "*.dump.gz" -mtime +7 -delete

# Wgraj do S3 (opcjonalnie)
aws s3 cp "${BACKUP_DIR}/${FILENAME}.gz" s3://backups/geo-hub/
```

```bash
# Cron: codziennie o 3:00
0 3 * * * /opt/scripts/backup-postgres.sh
```

### 7.2. Odtworzenie z backupu

```bash
# Zatrzymaj aplikację
sudo systemctl stop geo-hub

# Odtwórz bazę
gunzip -c /backups/postgres/geo_hub_20251129.dump.gz | \
    pg_restore -h localhost -U geo_hub -d geo_hub --clean

# Uruchom aplikację
sudo systemctl start geo-hub
```

### 7.3. Point-in-Time Recovery

```bash
# Włącz WAL archiving w postgresql.conf
archive_mode = on
archive_command = 'cp %p /var/lib/postgresql/wal_archive/%f'

# Odtworzenie na konkretny moment
pg_restore --target-time="2025-11-29 12:00:00" ...
```

---

## 8. Aktualizacja

### 8.1. Aktualizacja przez Docker

```bash
# Pobierz nową wersję
docker compose pull

# Zastosuj migracje (w osobnym kontenerze)
docker compose run --rm app alembic -c migrations/alembic.ini upgrade head

# Restart z zero downtime
docker compose up -d --no-deps --scale app=4 app
sleep 10
docker compose up -d --no-deps --scale app=3 app
```

### 8.2. Aktualizacja ręczna

```bash
# 1. Pobierz nowy kod
cd /opt/geo-hub
git pull origin main

# 2. Zaktualizuj zależności
source venv/bin/activate
pip install -e ".[dev]"

# 3. Zastosuj migracje
alembic -c migrations/alembic.ini upgrade head

# 4. Zrestartuj
sudo systemctl restart geo-hub
```

### 8.3. Rollback

```bash
# Cofnięcie migracji
alembic downgrade -1

# Powrót do poprzedniej wersji kodu
git checkout v0.1.0

# Restart
sudo systemctl restart geo-hub
```

---

## 9. Troubleshooting

### 9.1. Sprawdzenie stanu

```bash
# Status usług
sudo systemctl status geo-hub
sudo systemctl status postgresql
sudo systemctl status redis

# Logi aplikacji
journalctl -u geo-hub -f
tail -f /var/log/geo-hub/app.log

# Połączenie z bazą
psql -h localhost -U geo_hub -d geo_hub
```

### 9.2. Typowe problemy

#### Aplikacja nie startuje

```bash
# Sprawdź zmienne środowiskowe
cat /opt/geo-hub/.env

# Sprawdź dostępność bazy
psql -h localhost -U geo_hub -d geo_hub -c "SELECT 1"

# Sprawdź Redis
redis-cli ping
```

#### Wolne płatności

```bash
# Sprawdź indeksy
psql -d geo_hub -c "SELECT * FROM pg_stat_user_indexes WHERE idx_scan = 0;"

# Sprawdź blokady
psql -d geo_hub -c "SELECT * FROM pg_locks WHERE NOT granted;"

# Sprawdź pamięć Redis
redis-cli info memory
```

#### Wysokie obciążenie bazy

```bash
# Aktywne zapytania
psql -d geo_hub -c "SELECT * FROM pg_stat_activity WHERE state = 'active';"

# Długie zapytania
psql -d geo_hub -c "SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;"

# Vacuum
psql -d geo_hub -c "VACUUM ANALYZE;"
```

### 9.3. Przydatne komendy

```bash
# Wyczyść przeterminowane locks
python -m app.cli cleanup-locks

# Sprawdź integralność danych
python -m app.cli check-integrity

# Eksport stanu uczestnika
python -m app.cli export-participant --pid=xxx --output=participant.json

# Statystyki bazy
python -m app.cli db-stats
```

---

## Powiązane dokumenty

- [03-architecture.md](03-architecture.md) — Architektura systemu  
- [04-api-reference.md](04-api-reference.md) — Dokumentacja API  
- [06-contributing.md](06-contributing.md) — Jak wnosić wkład
