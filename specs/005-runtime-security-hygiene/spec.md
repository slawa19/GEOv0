# 005 — Runtime security and delivery hygiene

- **Date:** 2026-08-11
- **Status:** IN PROGRESS — T500-T507 authorized 2026-08-11; T508 not separately authorized
- **Status authority:** метка описательная; завершённость устанавливают success criteria и evidence.
- **Owner surface:** `app/api/deps.py`, `app/main.py`, `app/api/v1/health.py`, `app/api/v1/websocket.py`, `.dockerignore`, `docker/`, `docker-compose*.yml`
- **Почему одна программа:** все находки живут на периметре (auth, health, доставка образа), не пересекаются по файлам ни с 002/003/004, дёшевы и проверяемы поодиночке. Это лучший кандидат на первую волну.

## Problem

Периметр раздаёт наружу больше, чем должен: секреты попадают в логи, а диагностический эндпоинт
без аутентификации печатает строку исключения БД. Отдельно, **вне периметра безопасности**, —
гигиена сборки: dev-образ запекает внутрь рабочее дерево целиком. Ни одна из находок не требует
доменных решений — только аккуратности.

**Распределение severity:** 2 × P2, 5 × P3.

## Owner surface

Входит: аутентификация и авторизация на границе, health-эндпоинты, конфигурация сборки образа.

Не входит: доменная логика платежей/клиринга, схема REST (кроме удаления утечки из тела ответа),
политика rate limiting как продуктовое решение — здесь только неограниченный рост структуры.

## Findings

| ID | Sev | Находка | Evidence |
|---|---|---|---|
| **F-005-1 (N-C1)** | **P2** | Bearer JWT передаётся в query string WebSocket; реализация websockets в uvicorn логирует полный path с query через `uvicorn.error`, поэтому `--no-access-log` не помогает. CWE-532 (secret in log) | `app/api/v1/websocket.py:19`; `requirements.txt:2`; `docker/Dockerfile:35`; URL задокументирован в `docs/ru/pwa/specs/pwa-client-ui-spec.md:273` |
| **F-005-2 (C4-final)** | **P2** | Неаутентифицированный `/health/db` возвращает `"details": str(exc)` — это раскрывает пользователя БД, хост, имя базы и драйвер. Оба алиаса эндпоинта | `app/main.py:566` (нет auth-зависимости), `:584`; `app/api/v1/health.py:74-81`. Прямо противоречит `001/tasks.md:556` («without leaking exception text») |
| **F-005-3 (C-1)** | P3 | **Гигиена сборки, не security.** `.dockerignore` не обновили после переезда на `.local-run` → **dev**-образ с `COPY --chown=app:app . .` (`Dockerfile:41`) запекает `.local-run/` (~320 МБ: dev-SQLite БД и `admin-ui.err.*.log`), `.venv/` (~99 МБ) и `node_modules/`. Секретов среди этого нет: `.env` исключён (`.dockerignore:6`), `.pem`/`*.key` в корне отсутствуют, отслеживается только `.env.example` | `.dockerignore` содержит 9 записей (`.git __pycache__ venv tests docs .env .pytest_cache *.pyc *.pyo`); `Dockerfile:41`; `docker-compose.dev.yml:6-8`. Проверено: `.local-run/` и `.venv/` присутствуют в дереве и не исключены ни одним правилом |
| **F-005-4 (C-Risk#2)** | P3 | Самый привилегированный admin-токен сравнивается через `!=`, а не `compare_digest` — при том что HMAC сессии симулятора `compare_digest` использует | `app/api/deps.py:136,169,257` |
| **F-005-5 (C-Risk#3)** | P3 | In-memory rate-limiter вытесняет только предыдущий бакет того же хоста → структура растёт неограниченно | `app/api/deps.py:29,80-86` |
| **F-005-6 (C2-final)** | P3 | `/health` отдаёт 200 при `"status":"degraded"`; в поставляемой топологии нет потребителя HEALTHCHECK | `app/main.py:551-558` |
| **F-005-7 (C4-final env)** | P3 | `/api/v1/health` читает окружение только через `os.getenv`, минуя Settings и `.env` → в проде показывает `"dev"` | `app/api/v1/health.py:35-37` |

### Почему F-005-3 — не security (переклассификация 2026-08-11)

Находка изначально несла ярлык «P2-условно / утечка через образ». Проверка на точном HEAD этот
ярлык не подтвердила; ниже — основания понижения до P3.

- **Образ никуда не публикуется.** В `.github/workflows/*.yml` нет ни `docker push`, ни
  `docker/build-push-action`, ни упоминания registry. Артефакт не покидает машину сборки.
- **Затронут только dev-образ.** Шапка корневого `Dockerfile` прямо говорит: это development-образ,
  используемый только `docker-compose.dev.yml`, а канонический base/production-образ —
  `docker/Dockerfile`. Базовый `docker-compose.yml:31-33` собирает `docker/Dockerfile`;
  `docker-compose.dev.yml:6-8` переопределяет сборку на корневой.
- **Канонический production-образ не затронут по конструкции.** `docker/Dockerfile` копирует по
  явному allowlist (`COPY app/`, `COPY migrations/`, `COPY scripts/`, `COPY fixtures/`,
  `COPY docker/docker-entrypoint.sh`) и нигде не делает `COPY . .`. Рабочее дерево целиком
  запекает только dev-`Dockerfile:41`.
- **`.env` исключён** правилом `.dockerignore:6`. Файлов `.pem`, ключей и прочих секретов в корне
  нет; из env-файлов отслеживается только `.env.example`.

Реальная цена находки — не утечка учётных данных, а: раздувание образа (плюс ~420 МБ мусора),
запечённая устаревшая dev-БД, которая может перекрыть смонтированный том, и невоспроизводимость
сборки (содержимое образа зависит от состояния рабочего дерева разработчика).

## Current / Intended / Optimal

**Current.** Секрет ходит в URL; диагностика печатает внутренности БД анониму; dev-образ собирается
из рабочего дерева как есть.

**Intended.** Токен не появляется в URL и логах. Health отдаёт факт доступности без деталей
исключения. Dev-образ содержит только исходники приложения и воспроизводим.

**Optimal.** Токен WebSocket — через подпротокол или заголовок при рукопожатии, с осознанным
решением по обратной совместимости для документированного URL. Health — двухуровневый: публичный
liveness без деталей и аутентифицированный diagnostic с деталями. `.dockerignore` — allowlist-подход
либо явное покрытие всех ignored output roots, с проверкой размера/содержимого образа в CI.

## Non-goals

- Переработка модели аутентификации.
- Введение внешнего rate-limiting backend (Redis и т.п.) — здесь только ограничение роста памяти.
- Изменение продуктового смысла статуса `degraded`.

## Verification plan

1. Тест: запрос WebSocket с токеном не оставляет токен ни в одной строке лога uvicorn при штатной конфигурации.
2. Тест: `/health/db` при недоступной БД не содержит имени пользователя, хоста, базы и драйвера в теле ответа.
3. Проверка образа (гигиена, не security): сборка dev-образа не содержит `.local-run`, `*.db`,
   `.venv`, `node_modules` (проверяется командой по содержимому образа, а не глазами).
4. Тест: сравнение admin-токена устойчиво по времени.
5. Тест: rate-limiter не растёт неограниченно при потоке уникальных хостов.
6. Решение по `/health` 200-при-degraded зафиксировано в `docs/ru/09-decisions-and-defaults.md`
   независимо от того, меняется код или нет.

## Tasks

| ID | Задача | Статус |
|---|---|---|
| T500 | Перенести токен WebSocket из query string; решение по совместимости задокументированного URL | `[x]` |
| T501 | Убрать `str(exc)` из публичного health; разделить liveness и diagnostic | `[x]` |
| T502 | Переписать `.dockerignore` (гигиена сборки dev-образа, не security); добавить в CI проверку содержимого образа | `[!]` |
| T503 | `compare_digest` для admin-токена | `[!]` |
| T504 | Ограничить рост структуры rate-limiter | `[!]` |
| T505 | Решение и реализация по `/health` degraded + HEALTHCHECK-потребителю | `[!]` |
| T506 | `/api/v1/health` читает окружение через Settings | `[!]` |
| T507 | Синхронизация `docs/ru/05-deployment.md`, `config-reference.md`, `09-decisions-and-defaults.md` | `[!]` |
| T508 | Независимое внешнее ревью и evidence на точном HEAD (триггер AGENTS.md §15: периметр безопасности) | `[!]` |

## Changelog

### 2026-08-11 — T500

- Implementation commit: `fcfd4f5191742fd03f4f8a63d45a73274df89485`.
- До: `app/api/v1/websocket.py:19` принимал bearer JWT из `?token=`; документированный URL был
  `docs/ru/pwa/specs/pwa-client-ui-spec.md:273`.
- После: `app/api/v1/websocket.py:18-23,28,40` принимает пару WebSocket-подпротоколов
  `["bearer", access_token]`, выбирает только `bearer` и отклоняет legacy query-only соединение;
  решение об осознанном разрыве совместимости записано в
  `docs/ru/09-decisions-and-defaults.md:237-248`, клиентский контракт — в
  `docs/ru/pwa/specs/pwa-client-ui-spec.md:273-276`.
- Первый canonical запуск
  `$env:DEBUG='false'; .\scripts\verify_local.ps1 -TaskSlug wave1_t500_red -BackendOnly -BackendSelector tests/unit/test_websocket_payment_received_event.py`
  подтвердил репродьюсер: exit `1`, `3 failed` (subprotocol path закрыт `1008`/HTTP 403, query path
  не отклонён). Предшествующая попытка без локального `DEBUG=false` не собрала тесты из-за внешнего
  `DEBUG=release` (`ValidationError`, wrapper exit `1`, pytest exit `4`) и не считается evidence.
- Та же canonical проверка после исправления с `-TaskSlug wave1_t500`: exit `0`, `3 passed`;
  live-uvicorn проверка подтверждает, что токен отсутствует во всех собранных строках
  `uvicorn.error`/`uvicorn.access`.

### 2026-08-11 — T501

- Implementation commit: `2813c34712e7937009ffe6807baa14932b3b665b`.
- До: оба публичных обработчика возвращали `details: str(exc)` — `app/main.py:578-585` в прежней
  нумерации (`details` на `:584`) и `app/api/v1/health.py:74-81`; versioned alias дополнительно
  раскрывал dialect.
- После: public root `app/main.py:566-586` и versioned alias `app/api/v1/health.py:57-81`
  возвращают одинаковый санитизированный 503; authenticated diagnostic вынесен в
  `app/api/v1/health.py:84-122` (`GET /api/v1/admin/health/db`) и описан в
  `api/openapi.yaml:99`.
- Red canonical selector
  `$env:DEBUG='false'; .\scripts\verify_local.ps1 -TaskSlug wave1_t501_red2 -BackendOnly -BackendSelector tests/integration/test_health_and_equivalents.py`:
  exit `1`, `2 failed, 2 passed`; actual public body содержал sentinel URL, а анонимный diagnostic
  alias отвечал `503` вместо ожидаемого auth failure.
- Target correction: попытка превратить versioned alias в diagnostic нарушила явную alias-классификацию
  OpenAPI (`Transport header drift expected=59; actual=60`). После выделения admin path поведенческий
  selector с `-TaskSlug wave1_t501_fix` прошёл: exit `0`, `4 passed`.
- OpenAPI ratchet честно зафиксировал изменение public 503: промежуточно
  `Error response drift expected=84; actual=85`, затем при совпавшем count — новый digest. T610 не
  выполнялся: новый operation приведён к generated-схеме, а существующий count остался `84`;
  датированная причина записана рядом с константой.
- Финальный canonical contract selector
  `$env:DEBUG='false'; .\scripts\verify_local.ps1 -TaskSlug wave1_t501_contract5 -BackendOnly -BackendSelector tests/contract/test_openapi_contract.py`:
  exit `0`, `23 passed`.
