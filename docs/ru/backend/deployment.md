# Deployment Guide (RU)

Этот документ является тонкой обёрткой (индексом) для канонической документации по развёртыванию.

## Основной документ

Руководство по развёртыванию находится здесь:
- [`docs/ru/05-deployment.md`](../05-deployment.md)

## Docker

`docker/Dockerfile` — канонический base/production build. Корневой `Dockerfile`
— dev-образ для bind mounts и hot reload; его выбирает
`docker-compose.dev.yml`. Оба образа используют общий
`docker/docker-entrypoint.sh`: он выполняет migration preflight и Alembic, затем
без изменений запускает переданный `CMD`/Compose `command`.

Docker-файлы находятся в директории `docker/`:
- [`docker/Dockerfile`](../../../docker/Dockerfile)
- [`docker/docker-entrypoint.sh`](../../../docker/docker-entrypoint.sh)

Docker Compose файлы в корне:
- [`docker-compose.yml`](../../../docker-compose.yml)
- [`docker-compose.dev.yml`](../../../docker-compose.dev.yml)
