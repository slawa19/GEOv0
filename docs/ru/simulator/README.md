# Simulator (RU)

Текущая входная точка документации симулятора. Реализация разделена на backend (runner, REST/SSE, интеграция с core) и frontend (`simulator-ui/v2`).

## Авторитетность

- REST paths и wire schema: [`api/openapi.yaml`](../../../api/openapi.yaml).
- Фактическое поведение: [`app/api/v1/simulator.py`](../../../app/api/v1/simulator.py), код runner/UI, runtime-наблюдение и сфокусированные тесты.
- Принятые объяснения и решения: текущие документы из индексов ниже.
- `archive/`, `concept/` и датированные design/spec-файлы не доказывают текущую реализацию без сверки с кодом и тестами.

## Текущие входные точки

- [Backend index](backend/README.md) — API/runner/SSE, сценарии, storage, observability и runbook.
- [Frontend index](frontend/README.md) — current UI v2, API consumption, UX guides и активные правила разработки.
- [Scenarios and engine](scenarios-and-engine.md) — обзор сценариев и движка.
- [Realistic scenarios](realistic-scenarios.md) — формат и проверка realistic-v2 сценариев.
- [Network economy analyzer](network-economy-analyzer-spec.md) — design contract анализатора; реализацию проверять отдельно.

Исторические документы доступны из `archive/` внутри соответствующего домена и не являются current entrypoints.
