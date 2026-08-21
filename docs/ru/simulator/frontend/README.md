# Simulator Frontend (RU)

```text
Статус: Stable
Область: simulator
Последнее обновление: 2026-08-11
```

Входная точка документации текущего UI в [`simulator-ui/v2`](../../../../simulator-ui/v2/README.md). `simulator-ui/v1` и документы в `archive/` — исторические материалы.

## Implementation-oriented guides

Ссылки ниже проверены как текущие пути навигации; точность описанного поведения всё равно сверяется с UI-кодом, runtime и тестами.

- [API consumption and event shapes](docs/api.md) — пояснение для UI; при расхождении wire schema задаёт [`api/openapi.yaml`](../../../../api/openapi.yaml).
- [HUD user guide](docs/hud-user-guide.md)
- [Interact mode user guide](docs/interact-mode-user-guide.md)
- [Visual language](docs/visual-language.md)
- [FX playbook](docs/fx-playbook.md)
- [Graph rendering rules](docs/graph-rendering-rules.md)
- [Performance and quality policy](docs/performance-and-quality-policy.md)
- [Overlay/window development rules](docs/overlay-window-development-rules.md)

## Design and acceptance documents

- [Real-mode screens spec](docs/specs/simulator-real-mode-screens-spec.md) — целевое/приёмочное описание; статус конкретного поведения сверять с `simulator-ui/v2`, тестами и runtime.
- [Screen prototypes](screen-prototypes/) — визуальные референсы, не runtime contract.

## Archive

- [Frontend archive](docs/archive/) — заменённые UI-концепции и ранние спецификации.
- [Specs archive](docs/specs/archive/) — завершённые или заменённые датированные specs.

Совпадение текста архивного документа с текущим UI не повышает его статус.
