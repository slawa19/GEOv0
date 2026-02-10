# Archived simulator scenarios

Эта директория содержит устаревшие сценарии, оставленные для истории.

Важно:
- Runtime загружает fixture-сценарии только из поддиректорий `fixtures/simulator/*/scenario.json` первого уровня.
- Так как в `fixtures/simulator/_archive/` **нет** `scenario.json` на верхнем уровне, сценарии внутри `_archive/*/scenario.json` не подхватываются runtime.

Если нужно восстановить сценарий — переместите его обратно на верхний уровень `fixtures/simulator/<scenario_id>/scenario.json`.
