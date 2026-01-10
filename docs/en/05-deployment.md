# GEO Hub: Deployment

**Version:** 0.1  
**Date:** November 2025

---

## Table of Contents

1. [Requirements](#1-requirements)
2. [Quick Start (Docker)](#2-quick-start-docker)
3. [Manual Installation](#3-manual-installation)
4. [Configuration](#4-configuration)
5. [Production Deployment](#5-production-deployment)
6. [Monitoring](#6-monitoring)
7. [Backup and Recovery](#7-backup-and-recovery)
8. [Updates](#8-updates)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Requirements

### 1.1. Minimum requirements (development)

| Component | Requirement |
|-----------|-------------|
| CPU | 2 cores |
| RAM | 2 GB |
| Disk | 10 GB SSD |
| OS | Linux, macOS, Windows (WSL2) |

### 1.2. Recommended (production, up to 500 participants)

| Component | Requirement |
|-----------|-------------|
| CPU | 4 cores |
| RAM | 8 GB |
| Disk | 50 GB SSD |
| OS | Ubuntu 22.04 LTS / Debian 12 |

### 1.3. Software requirements

- **Docker** 24.0+ and **Docker Compose** 2.20+
- Or:
  - Python 3.11+
  - PostgreSQL 15+
  - Redis 7+

Windows note: for a detailed Windows 11 + WSL2 setup **without Docker Desktop**, see: `docs/en/runbook-dev-wsl2-docker-no-desktop.md`.

---

## 2. Quick Start (Docker)

### 2.1. Repository cloning

```bash
git clone https://github.com/slawa19/GEOv0.git
cd GEOv0-PROJECT
```

### 2.2. Environment setup

This repository provides `.env.example` for reference (see also `app/config.py`).

For Docker Compose, most defaults are already set in `docker-compose.yml` (including DB/Redis URLs),
so copying `.env` is optional unless you want to override secrets or ports.

### 2.3. Launch

```bash
# Start all services (DB, Redis, API)
docker compose up -d --build

# If localhost:8000 is already used by another service on your machine,
# pick a different host port (container port stays 8000):
# GEO_API_PORT=18000 docker compose up -d --build

# Check status
docker compose ps

# View logs
docker compose logs -f app
```

### 2.4. Database initialization

Migrations are executed automatically on container start (see `docker/docker-entrypoint.sh`).

If you want to run them manually:

```bash
docker compose exec app alembic -c migrations/alembic.ini upgrade head
```

(Optional) Seed demo data from `seeds/`:

```bash
docker compose exec app python scripts/seed_db.py
```

### 2.5. Verification

```bash
# Check API
curl http://localhost:8000/health
curl http://localhost:8000/health/db

# Open documentation
# Swagger UI:
# http://localhost:8000/docs
```

If you started with `GEO_API_PORT=18000`, replace `8000` with `18000`.

### 2.6. Stop

```bash
docker compose down

# With data removal (careful!)
docker compose down -v
```

---

## 3. Manual Installation

### 3.1. PostgreSQL

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql-15 postgresql-contrib-15

# Create user and database
sudo -u postgres psql
CREATE USER geo_hub WITH PASSWORD 'your_password';
CREATE DATABASE geo_hub OWNER geo_hub;
\q
```

### 3.2. Redis

```bash
# Ubuntu/Debian
sudo apt install redis-server

# Start
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

### 3.3. Python environment

```bash
# Install Python 3.11
sudo apt install python3.11 python3.11-venv

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

### 3.4. Apply migrations

```bash
# Set environment variables (examples)
export DATABASE_URL="postgresql+asyncpg://geo:geo@localhost:5432/geov0"
export REDIS_URL="redis://localhost:6379/0"
export JWT_SECRET="change-me-in-production"

# Migrations
alembic -c migrations/alembic.ini upgrade head

# Optional seed
python scripts/seed_db.py
```

### 3.5. Launch application

```bash
# Development
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## 4. Configuration

### 4.1. Environment variables

```bash
# === Required (for local/manual run) ===

# PostgreSQL database
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database

# Redis
REDIS_URL=redis://localhost:6379/0

# JWT secret
JWT_SECRET=change-me-in-production

# === Optional ===

DEBUG=false
LOG_LEVEL=INFO

# JWT settings
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# Feature flags / runtime toggles
REDIS_ENABLED=true

# Admin API (MVP)
ADMIN_TOKEN=dev-admin-token-change-me

# Limits
MAX_PAYMENT_HOPS=6
MAX_CLEARING_CYCLE_LENGTH=6
PAYMENT_TIMEOUT_SECONDS=30

# Rate limiting
RATE_LIMIT_PER_MINUTE=100

# Background tasks
CLEARING_INTERVAL_SECONDS=300
CLEANUP_EXPIRED_LOCKS_SECONDS=60
```

### 4.2. Example .env file

```bash
# .env
DATABASE_URL=postgresql+asyncpg://geo_hub:password@localhost:5432/geo_hub
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=change-this-to-a-very-long-random-string-at-least-32-chars

DEBUG=false
LOG_LEVEL=INFO

ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

MAX_PAYMENT_HOPS=6
CLEARING_INTERVAL_SECONDS=300
```

### 4.3. Logging configuration

```python
# logging_config.py (or in app/config.py)
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

## 5. Production Deployment

### 5.1. Architecture

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

### 5.2. Nginx configuration

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
        
        # Timeouts
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

    # Health check (no logging)
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

### 5.3. Systemd service

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
# Activation
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

### 6.1. Prometheus metrics

Endpoint: `GET /metrics`

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'geo-hub'
    static_configs:
      - targets: ['geo-hub:8000']
    metrics_path: /metrics
```

### 6.2. Grafana dashboard

Dashboard JSON is not shipped in this repository. Create a dashboard using the metrics exposed at `GET /metrics`.

Suggested panels (PromQL examples):

- HTTP requests per second: `sum(rate(geo_http_requests_total[5m]))`
- 5xx error rate: `sum(rate(geo_http_requests_total{status=~"5.."}[5m])) / sum(rate(geo_http_requests_total[5m]))`
- Payment endpoints latency p95: `histogram_quantile(0.95, sum by (le) (rate(geo_http_request_duration_seconds_bucket{path=~"/api/v1/payments.*"}[5m])))`
- Payment events: `sum(rate(geo_payment_events_total[5m])) by (event, result)`
- Clearing events: `sum(rate(geo_clearing_events_total[5m])) by (event, result)`

- HTTP requests per minute
- HTTP latency (p50, p95, p99)
- Payment events (by type/result)
- Clearing events (by type/result)
- Routing failures (by reason)
- Error rate (5xx ratio)

### 6.3. Alerting rules

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

# DB connectivity check
curl http://localhost:8000/health/db
# {"status": "ok"}  (or HTTP 503 with {"status": "error", ...})
```

---

## 7. Backup and Recovery

### 7.1. Automatic PostgreSQL backup

```bash
#!/bin/bash
# /opt/scripts/backup-postgres.sh

BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
FILENAME="geo_hub_${DATE}.dump"

# Create backup
pg_dump -Fc -h localhost -U geo_hub geo_hub > "${BACKUP_DIR}/${FILENAME}"

# Compress
gzip "${BACKUP_DIR}/${FILENAME}"

# Remove old backups (>7 days)
find "${BACKUP_DIR}" -name "*.dump.gz" -mtime +7 -delete

# Upload to S3 (optional)
aws s3 cp "${BACKUP_DIR}/${FILENAME}.gz" s3://backups/geo-hub/
```

```bash
# Cron: every day at 3:00
0 3 * * * /opt/scripts/backup-postgres.sh
```

### 7.2. Restore from backup

```bash
# Stop application
sudo systemctl stop geo-hub

# Restore database
gunzip -c /backups/postgres/geo_hub_20251129.dump.gz | \
    pg_restore -h localhost -U geo_hub -d geo_hub --clean

# Start application
sudo systemctl start geo-hub
```

### 7.3. Point-in-Time Recovery

```bash
# Enable WAL archiving in postgresql.conf
archive_mode = on
archive_command = 'cp %p /var/lib/postgresql/wal_archive/%f'

# Restore to specific time
pg_restore --target-time="2025-11-29 12:00:00" ...
```

---

## 8. Updates

### 8.1. Update via Docker

```bash
# Get new version
docker compose pull

# Apply migrations (in separate container)
docker compose run --rm app alembic -c migrations/alembic.ini upgrade head

# Restart with zero downtime
docker compose up -d --no-deps --scale app=4 app
sleep 10
docker compose up -d --no-deps --scale app=3 app
```

### 8.2. Manual update

```bash
# 1. Get new code
cd /opt/geo-hub
git pull origin main

# 2. Update dependencies
source venv/bin/activate
pip install -e ".[dev]"

# 3. Apply migrations
alembic -c migrations/alembic.ini upgrade head

# 4. Restart
sudo systemctl restart geo-hub
```

### 8.3. Rollback

```bash
# Rollback migrations
alembic downgrade -1

# Restore code
git checkout v0.1.0

# Restart
sudo systemctl restart geo-hub
```

---

## 9. Troubleshooting

### 9.1. Status check

```bash
# Service status
sudo systemctl status geo-hub
sudo systemctl status postgresql
sudo systemctl status redis

# Application logs
journalctl -u geo-hub -f
tail -f /var/log/geo-hub/app.log

# Database connection
psql -h localhost -U geo_hub -d geo_hub
```

### 9.2. Common issues

#### Application won't start

```bash
# Check environment variables
cat /opt/geo-hub/.env

# Check database availability
psql -h localhost -U geo_hub -d geo_hub -c "SELECT 1"

# Check Redis
redis-cli ping
```

#### Slow payments

```bash
# Check indexes
psql -d geo_hub -c "SELECT * FROM pg_stat_user_indexes WHERE idx_scan = 0;"

# Check locks
psql -d geo_hub -c "SELECT * FROM pg_locks WHERE NOT granted;"

# Check Redis memory
redis-cli info memory
```

#### High database load

```bash
# Active queries
psql -d geo_hub -c "SELECT * FROM pg_stat_activity WHERE state = 'active';"

# Slow queries
psql -d geo_hub -c "SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;"

# Vacuum
psql -d geo_hub -c "VACUUM ANALYZE;"
```

### 9.3. Useful commands

```bash
# Clean expired locks
python -m app.cli cleanup-locks

# Check data integrity
python -m app.cli check-integrity

# Export participant state
python -m app.cli export-participant --pid=xxx --output=participant.json

# Database statistics
python -m app.cli db-stats
```

---

## Related Documents

- [03-architecture.md](03-architecture.md) — System architecture
- [04-api-reference.md](04-api-reference.md) — API reference
- [06-contributing.md](06-contributing.md) — How to contribute