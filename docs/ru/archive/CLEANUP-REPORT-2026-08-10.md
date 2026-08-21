# Генеральная уборка планов, отчётов и документации — 2026-08-10

Область: `plans/`, `docs/ru/`, `specs/`. Ничего не удалено — только перенос в `archive/`
и в два карантинных каталога. Все спорные документы сверены с текущим кодом, а не только
со своими собственными пометками о статусе.

## Ответ на главный вопрос: да, основная документация русская

Это зафиксировано в `docs/README.md` и подтверждено измерением:

- **0 из 28** файлов EN/PL несут заголовок с датой синхронизации → по правилу самого
  `docs/README.md` весь EN/PL-корень формально **informative, not normative**.
- **~114 из 144** RU-документов (79%) вообще никогда не переводились — включая все
  «current front doors» (`ru/backend/README.md`, `ru/admin-ui/README.md`,
  `ru/simulator/README.md`, `ru/documentation-rules.md`) и весь `ru/simulator/` (81 файл).
- Максимальный дрейф — **215 дней**: `09-decisions-and-defaults` (RU 22 104 Б против EN 5 600 Б,
  то есть переводы содержат ~25% нормативного документа решений).
- Опаснее простого отставания: `05-deployment`, `10-testing-framework`, `config-reference` —
  **EN/PL длиннее RU**. RU переписали до проверяемого объёма в августе 2026, а переводы
  сохранили старый, уже неверный текст: они обещают то, чего RU больше не утверждает.

**Решение зафиксировано** в `docs/README.md` разделом «Translation freeze (RU is the working
tree) — 2026-08-10»: EN/PL заморожены и не актуализируются. Там же перечислены 8 файлов-сирот
EN/PL, чей RU-оригинал заархивирован или перемещён.

## Что сделано

### `plans/` — 39 → 6 активных документов

`plans/` целиком в `.gitignore`, git-истории у него нет, поэтому здесь только перенос.

- **27 документов → `plans/archive/`.** Разбор по каждому — в `plans/INDEX.md`.
- **7 файлов в карантин, удалены 2026-08-11**: 5 stub-указателей (нарушали
  `documentation-rules.md` §4.2) + пустой файл 0 Б + `_tmp_write_test.txt`.
- `plans/INDEX.md` переписан, `plans/archive/README.md` пополнен.

Что осталось активным: `simulator-analytics-dashboard-todo.md`,
`simulator-real-mode-implementation-plan-2026-01-28.md`,
`admin-ui-fixtures-first-prototyping-2026-01-12.md`, `ui-kit-design-system-agent-guide.md`,
`m20-nullish-coalescing-audit.*`, `INDEX.md`.

### Расхождения между пометками в документах и реальностью кода

| Документ | Было заявлено | Оказалось |
|---|---|---|
| `interact-panel-dropdown-migration-spec-2026-03-08.md` | «Draft for review before implementation» | Реализовано: `OverlaySelect.vue` используется в `ManualPaymentPanel.vue` и `TrustlineManagementPanel.vue`, native `<select>` не осталось |
| `simulator-analytics-dashboard-todo.md` | все чекбоксы пустые | BE-01/02 и FE-01/02 давно сделаны; открыты только FE-04…FE-13 |
| `simulator-window-management-audit-followups.md` | список доработок | Все Н-1…Н-6, K-1/K-4/K-5 закрыты; флага `__USE_WINDOW_MANAGER` больше нет — WM безусловен |
| `simulator-realistic-scenario-v2-2026-01-30.md` | «в работе», хардкод `min(limit, Decimal("3"))` | Хардкод убран, есть `SIMULATOR_REAL_AMOUNT_CAP` |
| `docs-ru-reorganization-proposal.md` | «частично применено» | Не применено; дерево `docs/ru/` ушло дальше самого предложения |

### `docs/ru/` — устаревшее в архив, дубликаты в карантин

- **8 документов → соответствующие `archive/`**: `legacy-removal-wm-only-plan-2026-03-03.md`
  (Статус: IMPLEMENTED), `interact-canvas-node-picking-fix-2026-03-03.md`,
  `concurrent-payments-fix-spec.md` (✅ Completed), `pre-implementation-readiness-report.md`,
  `real-mode-debug-harness-spec.md`, `backend-first-stats.md`,
  `render-visual-elements-audit.md`, `tmp/code-review-mvp-v0.1.md`.
- **9 файлов в карантин, удалены 2026-08-11**: 5 stub-указателей + 4 дубликата
  (в т.ч. побайтовый дубликат на 139 884 Б и вложенный каталог `concept/ru/` внутри уже
  русского дерева).
- Исправлены битые ссылки в активных документах: `fx-playbook.md`,
  `graph-rendering-rules.md`, `visual-language.md` (лишний уровень `../`,
  несуществующий `useAppFxAndRender.ts` → `useAppFxOverlays.ts`).
- Закрыт cleanup batch, анонсированный в `simulator/frontend/docs/specs/README.md`:
  reference scan → выбор единственной historical location → 9 ссылок обновлено → link check.

**Проверка ссылок после уборки: 113 активных `.md`, 0 битых ссылок.**

### `specs/` — создан индекс

Новый `specs/README.md` фиксирует границу `specs/` (под git, формальные программы) против
`plans/` (не под git, приватные рабочие документы) и статусы:

- **001-codebase-renovation — закрыта.** 18/19 DONE, 1 ACCEPTED-NOT-DOING, phase 7 closure
  COMPLETE, 12/12 MUST = PASS. Каталог **оставлен на месте, не заархивирован**: на его
  closeout ссылается программа 002.
- **002-payment-integrity-follow-up — открыта.** Phase 0 закрыта (21/21), Phase 1 и 2 —
  16 задач в статусе `[!]`, блокирует отсутствие owner authorization. Два подтверждённых P2
  в скоупе + ambiguous durable clearing commit как кандидат на отдельную программу `specs/003-*`.

### Побочная находка: скрытая потеря данных в `.gitignore`

Правило `plans/` было **не якорное** и совпадало на любой глубине — оно молча исключало
из git `docs/ru/simulator/frontend/docs/plans/` (137 КБ документации симулятора, существовавшей
только на локальной машине). Исправлено на `/plans/` и `/docs/plans/`; четыре файла теперь
видны git как untracked и ждут коммита.

## Что осталось на ваше решение

1. ~~Удалить содержимое двух карантинных каталогов~~ — **выполнено 2026-08-11**: оба каталога
   удалены после подтверждения владельцем, вместе с `plans/ui-kit-design-system-agent-guide.md`.
2. **`docs/ru/pwa/specs/pwa-client-ui-spec.md`** — домена `pwa` нет в каноне
   `documentation-rules.md` §2.2, входящих ссылок нет. Оставлен на месте: непонятно,
   это мёртвый документ или отложенная работа. Нужно ваше решение.
3. **8 файлов-сирот EN/PL** (перечислены в `docs/README.md`) — кандидаты на удаление
   или перенос в `en|pl/archive/`.
4. **`ui-kit-design-system-agent-guide.md`** дублируется с более свежим
   `simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md` — стоит слить в один.
5. **`docs/ru/backend/api-reference.md`** — тонкая обёртка, ссылающаяся обратно на
   `docs/ru/04-api-reference.md`. Такая же инверсия у `backend/config-reference.md`.
6. **Пустые каталоги** `docs/ru/tmp/`, `docs/ru/concept/ru/`,
   `docs/ru/simulator/frontend/specs/` удалить не удалось (отказ в правах на монтированной
   ФС). Git их не отслеживает, но на диске они остались.
7. **`.git/index.lock`** от 15:46 висит уже несколько часов, 0 байт — похоже, аварийно
   завершившийся git-процесс. Из-за него `git mv` не работал (использован обычный `mv`,
   git распознает переименования при `git add`). Файл не тронут: удалять его без вашего
   ведома небезопасно.

## Открытая инженерная работа, найденная в ходе уборки

Единственная реальная незакрытая задача во всём `plans/` — **панель аналитики Simulator UI**:
backend (`GET /simulator/runs/{run_id}/metrics` и `/bottlenecks`) и API-клиент
(`getMetrics`/`getBottlenecks`) готовы, но **у них ноль call-site'ов** — UI-потребителей нет.
Живая спецификация — `docs/ru/simulator/ui-analytics-dashboard-spec.md`.
Плюс пункт M20 (`?? ''` / `?? null`) из архивированного `simulator-ui-code-review.md`.
