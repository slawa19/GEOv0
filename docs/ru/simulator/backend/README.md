# Simulator Backend (RU)

```text
Статус: Stable
Область: simulator
Последнее обновление: 2026-08-11
```

Входная точка документации backend-части симулятора: REST/SSE control plane, runner, storage и интеграция с core payments/clearing.

## Авторитетность

- REST paths, request/response fields и wire schema: [`api/openapi.yaml`](../../../../api/openapi.yaml).
- Текущая реализация API: [`app/api/v1/simulator.py`](../../../../app/api/v1/simulator.py).
- Наблюдаемое поведение runner: код, runtime и сфокусированные backend/simulator tests.
- Документы ниже объясняют принятые решения или target design; они не заменяют проверку реализации.

## Implementation-oriented guides

Ссылки ниже проверены как текущие пути навигации; точность описанного поведения всё равно сверяется с backend-кодом, runtime и тестами.

- [Runner algorithm](runner-algorithm.md)
- [Payment and clearing integration](payment-integration.md)
- [Realtime event protocol](ws-protocol.md) — пояснение SSE/event payload; OpenAPI имеет приоритет для wire schema.
- [Scenario schema](scenario-schema.md)
- [Run storage](run-storage.md)
- [Observability](observability.md)
- [Real-mode runbook](real-mode-runbook.md)
- [API examples](api-examples.md)

## Design and acceptance documents

- [Behavior model spec](behavior-model-spec.md)
- [Adaptive clearing policy](adaptive-clearing-policy.md)
- [Backend-driven demo mode](backend-driven-demo-mode-spec.md)
- [Acceptance criteria](acceptance-criteria.md)
- [Test plan](test-plan.md)

Эта группа может содержать реализованные и target-разделы одновременно. Статус конкретного пункта подтверждается кодом и свежим результатом указанного test selector, а не отметкой внутри старого отчёта.

## Archive

- [Backend archive](archive/) — исторические и заменённые specs, включая ранний anonymous visitor design.

Архив используется только как контекст и не является current contract.
