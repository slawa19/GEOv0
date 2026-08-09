# Simulator frontend specs: статус каталога

Файлы здесь — design/acceptance evidence, а не source of truth текущего runtime.
Current behavior проверяется по `simulator-ui/v2`, backend, OpenAPI и behavioral
tests. Новое устойчивое правило переносится в implementation-oriented guide из
[`../../README.md`](../../README.md).

`archive/` хранит завершённые или заменённые документы и read-only по умолчанию.
Одноимённые active/archive файлы сейчас не удаляются автоматически:

- часть пар различается и требует отдельного content-owner решения;
- точная копия `manual-operations-ui-improvements-spec-2026-02-26.md` остаётся
  временно, потому что исторические review-документы ссылаются на active path;
- совпадение содержимого не делает ни одну копию runtime contract.

Удаление/перемещение такой пары — отдельный cleanup batch: сначала reference scan,
выбор единственного historical location и обновление ссылок, затем link check.
