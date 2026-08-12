# Simulator frontend specs: статус каталога

Файлы здесь — design/acceptance evidence, а не source of truth текущего runtime.
Current behavior проверяется по `simulator-ui/v2`, backend, OpenAPI и behavioral
tests. Новое устойчивое правило переносится в implementation-oriented guide из
[`../../README.md`](../../README.md).

`archive/` хранит завершённые или заменённые документы и read-only по умолчанию.

## Cleanup batch 2026-08-10

Тот самый отдельный cleanup batch, который здесь был анонсирован, выполнен:

1. **Reference scan** — на active-копию `manual-operations-ui-improvements-spec-2026-02-26.md`
   ссылались только три архивных review/report документа.
2. **Единственная historical location** — выбрана
   [`archive/manual-operations-ui-improvements-spec-2026-02-26.md`](archive/manual-operations-ui-improvements-spec-2026-02-26.md);
   active-копия (побайтово идентичная, md5 `33c2611f689e52f1ef63a8f8289bde8d`) вынесена
   в карантин и удалена 2026-08-11 после проверки владельцем.
3. **Обновление ссылок** — 9 ссылок в `../archive/REVIEW-*-2026-02-26.md`,
   `../archive/REVIEW-*-2026-02-27.md`, `../archive/REPORT-manual-operations-ui-code-review-2026-02-27.md`
   переведены на `specs/archive/` (правка только пути; тела архивных документов не изменялись).
4. **Link check** — выполнен.

Заодно убраны три stub-указателя (`window-manager-acceptance-criteria.md`,
`window-controller-decomposition-spec.md`, `interact-windows-audit-2026-03-02.md`) — они
противоречили `documentation-rules.md` §4.2; полные копии лежат в `archive/`.
В `archive/` также переехали завершённые `legacy-removal-wm-only-plan-2026-03-03.md`
(Статус: IMPLEMENTED) и `interact-canvas-node-picking-fix-2026-03-03.md`.

Совпадение содержимого по-прежнему не делает ни одну копию runtime contract.
