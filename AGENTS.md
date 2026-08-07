# AGENTS.md — GEOv0

Front-door и обязательный контракт для ИИ-агентов, работающих в этом репозитории. Здесь находятся правила принятия решений, маршрутизация по активным поверхностям и лестница проверок. Детали продукта живут в коде, OpenAPI и доменной документации; этот файл не должен превращаться в журнал проекта.

Текущая программа системного review/refactoring: `specs/001-codebase-renovation/spec.md`; последовательность волн — `plan.md`, исполнимый backlog — `tasks.md` в том же каталоге. Их статусы не заменяют проверку кода, git и свежих gate-результатов.

При работе в роли оркестратора дополнительно обязателен операционный протокол
`docs/codex-orchestrator-rule.md`. Он определяет, как удерживать контекст,
разделять owner surfaces, принимать результаты агентов и закрывать волну.

Если правило перестало соответствовать реальности, измените его в том же PR, который меняет реальность. Не обходите устаревшее правило молча.

---

## 1. Доказательства и расхождения

Код, git history, runtime-наблюдение, тесты и документация — **свидетельства**, а не безусловные источники истины по отдельности.

- Git показывает, что и когда было доставлено, но не доказывает корректность поведения.
- Код показывает реализованный механизм, но не доказывает наблюдаемый эффект и намерение владельца.
- Runtime и тесты показывают поведение только на фактически пройденных путях и в конкретном окружении.
- Документация и спеки показывают намерение и контекст, но могут отставать от реализации.
- Поле `Status`, старый отчёт, fixture или screenshot не доказывают текущее состояние без сверки с `git log` и owner surface.

**Запрещено автоматически подгонять код под документацию или документацию под код.** При расхождении сначала письменно разделите:

1. **Current behavior** — что делает текущий код/runtime, с `path:line`, selector и фактическим результатом.
2. **Intended behavior** — что обещают актуальные контракты и решения владельца, с датой и ссылкой.
3. **Optimal target** — какое поведение лучше сохраняет доменные инварианты, пользовательскую ценность, безопасность данных и сопровождаемость.

Только после этого выбирайте: исправить код, исправить документ, изменить оба согласованно или оставить расхождение как явно зарегистрированный долг. При конфликте намерений спрашивайте владельца.

Перед планированием проверяйте рабочее дерево и историю:

```powershell
$ownerPath = "app/core/payments"
$contractMarker = "tx.updated"
git status --short
git log --oneline --decorate -30
git log --oneline -- $ownerPath
git grep -n $contractMarker
```

Не храните в документах протухающие числа вроде текущего HEAD или количества проходящих тестов. Храните команду/selector; исторические SHA и датированные результаты допустимы.

---

## 2. Приоритет работы

Выбирайте high-leverage изменения: исправления инвариантов, устранение дублирующих источников состояния, ясные границы модулей, воспроизводимые проверки и упрощение частых пользовательских путей.

- Не охотьтесь за редкими edge cases, если нет реального сигнала, риска для денег/целостности/безопасности или дешёвого общего правила.
- Не overengineer: новая абстракция должна убирать подтверждённое дублирование или защищать контракт, а не предсказывать гипотетическое будущее.
- Не тюнингуйте алгоритм под один fixture, screenshot, строку или тест. Исправление должно выражать общее доменное правило.
- Не смешивайте независимые причины в один refactoring slice.
- Для локальной находки предпочитайте минимальную правку. Новый модуль/контракт или межмодульная миграция требуют спеки и последовательного плана до реализации.

---

## 3. Активные и защищённые поверхности

Активные product surfaces:

- backend: `app/`, `api/openapi.yaml`, `migrations/`, `tests/`;
- Admin UI: `admin-ui/`;
- Simulator UI: `simulator-ui/v2/`;
- canonical data generators and fixtures: `admin-fixtures/`, `seeds/`, релевантные `scripts/`;
- актуальная документация: прежде всего `README.md`, `docs/ru/00-overview.md`, `docs/ru/09-decisions-and-defaults.md` и доменные документы под `docs/ru/`.

Защищённые/read-only по умолчанию:

- `simulator-ui/v1/` — legacy prototype;
- любые `archive/`, архивные планы и исторические отчёты;
- уже применённые Alembic migrations;
- generated/public fixture copies и visual baselines, если задача не про их осознанную регенерацию.

Не исправляйте архив содержательно и не используйте его как текущую спецификацию. Если архивная ссылка нужна, пометьте её исторической. Удаление legacy/archive требует отдельного cleanup-slice, reference scan и сохранения истории в git/tag.

---

## 4. Стек и точки входа

- Backend: Python (target в tooling — 3.11), FastAPI, Pydantic v2, SQLAlchemy async, Alembic; SQLite для локальной разработки, Postgres для реальной семантики блокировок/изоляции.
- Admin UI: Vue 3, TypeScript, Vite, Pinia, Element Plus; Vitest и Playwright.
- Simulator UI v2: Vue 3, TypeScript, Vite, Vitest/happy-dom и Playwright.
- REST schema: `api/openapi.yaml`.
- Backend app: `app/main.py`.
- Локальный стек: `scripts/run_local.ps1` или `scripts/run_full_stack.ps1`.
- Simulator UI v2: `scripts/run_simulator_ui.ps1` либо npm scripts в `simulator-ui/v2/package.json`.
- Admin UI commands: `admin-ui/package.json`.
- Backend test discovery and markers: `pytest.ini` (не дублируйте настройки в новом месте).

Перед выводом «окружение сломано» проверьте фактические executable paths и версии. На Windows используйте PowerShell-команды репозитория; не вставляйте bash heredoc в PowerShell.

---

## 5. Canonical path и честный статус gates

Canonical path — задокументированный repo entrypoint или точный selector, который требуется задачей. Прямой вызов внутреннего helper — debug path и не заменяет canonical path. Всегда маркируйте debug-only результат.

В репозитории определён `.github/workflows/quality.yml`, а `scripts/verify_local.ps1` является canonical local entrypoint. До успешного выполнения workflow на опубликованной ветке это доказывает наличие конфигурации, но не статус «CI green». Mypy не настроен как gate; Ruff и Black пока выполняются только как явно non-blocking diagnostics, отделённые от `required-quality`, из-за известного repository-wide debt. Поэтому:

- не заявляйте «CI green» или «все gates green»;
- не превращайте текущие Ruff/Mypy findings в блокер несвязанной задачи без согласованного baseline;
- статические проверки запускайте и сообщайте как диагностические, пока отдельный foundational slice не сделает их воспроизводимыми gates;
- перечисляйте точные команды, exit code и непройденные surfaces.

### Cheap gates — после каждого безопасного micro-batch

Backend, с узким selector:

```powershell
.\scripts\verify_local.ps1 -TaskSlug agent_payments_review -BackendOnly -BackendSelector tests/unit/test_payments_2pc.py
.\scripts\verify_local.ps1 -TaskSlug agent_payments_review -BackendOnly -BackendSelector tests/contract/test_openapi_contract.py
```

Вторую команду запускайте только при изменении API/schema/serialization. Ruff можно запускать диагностически на изменённых Python-файлах, но результат нужно назвать диагностикой, а не существующим зелёным gate.

Admin UI:

```powershell
npm --prefix admin-ui run test
npm --prefix admin-ui run build
```

При изменениях lint-policy или широком TS/Vue refactor дополнительно:

```powershell
npm --prefix admin-ui run lint
```

Simulator UI v2:

```powershell
npm --prefix simulator-ui/v2 run typecheck
npm --prefix simulator-ui/v2 run test:unit
npm --prefix simulator-ui/v2 run build
```

Запускайте только затронутые surfaces; не используйте полный прогон как цикл отладки.

### Full local gate — milestone перед merge крупного slice

```powershell
.\scripts\verify_local.ps1 -TaskSlug premerge_payment_slice
```

Default full gate исключает `slow` и `e2e`; эти tiers запускаются отдельными milestone selectors. Команда не является утверждением, что текущий baseline уже зелёный. Каждый известный baseline failure фиксируйте дословно и отделяйте от регрессий текущего slice.

### Postgres gate

SQLite не доказывает advisory locks, concurrent writers, FK/isolation и SERIALIZABLE retry. Для таких изменений используйте отдельную disposable DB:

```powershell
$taskSlug = "agent_payments_review"
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_$taskSlug"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly -BackendSelector tests/integration/test_payment_engine_uow_retry_postgres.py
```

Никогда не выставляйте `GEO_TEST_ALLOW_DB_RESET=1`, пока фактически не проверили, что URL указывает на отдельную тестовую DB. Не используйте developer/prod DB.

### E2E и дорогие проверки

```powershell
npm --prefix admin-ui run e2e
npm --prefix simulator-ui/v2 run test:e2e
.\scripts\verify_local.ps1 -TaskSlug agent_simulator_super_smoke -BackendOnly -BackendSelector tests/integration/test_simulator_super_smoke.py -IncludeExpensive
```

Playwright, Postgres, full-stack и super-smoke — milestone после unit/component gates, перед merge затрагивающего их контракта или по явному запросу. Обновление screenshots допустимо только после ручной проверки, что визуальное изменение намеренно.

---

## 6. Изоляция агентов и runtime

Параллельные агенты не имеют права делить изменяемую DB, basetemp, output directory или порт.

Для backend tests каждый агент выбирает уникальный task slug:

```powershell
$taskSlug = "agent_payments_review"
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly -BackendSelector tests/unit/test_payments_2pc.py
```

- Postgres DB name также уникален на agent/task.
- Для backend/Admin/Simulator/Playwright резервируйте уникальные порты и явно передавайте поддерживаемые env/CLI параметры.
- Если entrypoint жёстко фиксирует порт, такой прогон сериализуется; нельзя молча запускать два экземпляра.
- Runtime artifacts пишутся только в уникальную ignored директорию задачи.
- Перед full-stack/browser проверкой проверьте, какой процесс уже слушает порт. После проверки остановите только свой процесс.

Фиксированные shared `.pytest_geov0.db`, task-less basetemp, `test-results` и default dev ports не являются безопасными для параллельной оркестрации.

---

## 7. Защищённые доменные контракты

Эти поверхности меняются только намеренным согласованным набором: реализация + schema/config + behavioral tests + актуальная документация.

### REST, DB и serialization

- `api/openapi.yaml` — канон REST schema. Markdown может объяснять контракт, но не создавать параллельную схему.
- Применённые migrations не переписываются; создаётся новая migration с проверенным upgrade path.
- Денежные суммы и лимиты остаются decimal strings/`Decimal`; не вводите float в доменные вычисления.
- Pydantic models с alias сериализуются через `model_dump(mode="json", by_alias=True)` там, где требуется wire payload.

### Trustlines, payments и clearing

- Направление trustline: `from -> to` означает **creditor -> debtor** и лимит риска кредитора, не обратное направление.
- Payment сохраняет идемпотентность по `tx_id`, атомарность эффектов и допустимые переходы состояния; timeout/retry не должен удваивать эффект или оставлять частичный commit.
- Routing не может расходовать больше доступной capacity и обязан уважать ограничения участников/маршрута.
- Clearing не должен нарушать trust limits, долговые/балансовые инварианты, optimistic locking и транзакционную целостность; SQLite-прохождение не заменяет Postgres concurrency evidence.

### Simulator SSE

- Имена событий и wire shapes (`run_status`, `tx.updated`, `tx.failed`, `clearing.*`, topology/node/edge patches), replay/event IDs и порядок жизненного цикла — защищённый UI/backend контракт.
- Edge refs на wire используют aliases `from`/`to`, не Python-имя `from_`.
- Изменение SSE требует backend schema/serialization tests, OpenAPI/документации при релевантности, simulator smoke и затронутых UI normalization tests.

Нельзя ослаблять инвариант или тест только ради зелёного прогона. Сначала установите, неверна реализация, ожидание или обе стороны.

---

## 8. Fixtures и generated copies

- Канонические Admin fixtures находятся в `admin-fixtures/`.
- `admin-ui/public/admin-fixtures/` — синхронизированная public-копия; не редактировать вручную. Использовать `npm --prefix admin-ui run sync:fixtures`, затем `npm --prefix admin-ui run validate:fixtures`.
- Simulator demo fixtures в `simulator-ui/v2/public/simulator-fixtures/` создаются через `scripts/sync_demo_fixtures.ps1`/generator. Не исправлять generated JSON вручную, если источник можно исправить и перегенерировать.
- Регенерация обязана быть детерминированной: до/после проверить diff, metadata/counts и валидатор. Не включать случайные timestamps/order, если они не часть контракта.
- Runtime DB, logs, PID, NDJSON, Playwright output и test dumps не являются fixtures и не коммитятся.

Если generated copy предлагается untrack/delete, сначала докажите, что clean checkout может восстановить её без скрытого локального состояния и что offline/dev fallback не сломан.

---

## 9. Качество тестов

- Проверяйте наблюдаемое поведение: пользовательский success/failure path, state transition, DB effect, API/event payload, DOM/a11y или visual contract.
- Не добавляйте placeholder tests, `pass`, тесты без наблюдаемой проверки или no-op fixtures.
- Не используйте реальный `sleep`, если можно управлять clock/event/barrier.
- Test, зависящий от Postgres, внешнего сервиса, времени, E2E или долгого сценария, обязан иметь marker/явный selector и documented prerequisites.
- Не называйте DB/API test unit-тестом. Различайте pure unit, component/service integration, DB integration, Postgres concurrency и E2E.
- Тесты точного текста source/CSS/import wiring допустимы только как явно именованные policy/architecture guards. По возможности переносите policy в ESLint/static checker, а продуктовый контракт проверяйте через mount/emits/DOM/Playwright.
- Гард формы не доказывает истинность документа или корректность UX. Его сообщение должно объяснять границы проверки и способ исправления.
- При удалении теста покажите, что он пуст, дублируется более сильным тестом либо проверяет удалённый контракт. «Старый» или «wiring-focused» без такого evidence недостаточно.

Перед словом «готово» найдите все call-sites изменённого API, проверьте success/failure/retry/restart/cache paths в пределах заявления и назовите непройденные пути.

---

## 10. Документация и спеки

- `README.md` — onboarding/run entrypoint; подробные доменные решения живут в stable `docs/ru/`.
- `api/openapi.yaml` владеет REST schema; `docs/ru/09-decisions-and-defaults.md` и релевантный domain doc владеют принятыми решениями/семантикой.
- EN/PL документы считаются переводами, а не отдельными нормативными контрактами, пока их синхронность с актуальным RU/кодом не доказана.
- `plans/`, review reports и датированные TODO не являются source of truth. Новый устойчивый контракт нельзя оставлять только там.
- Не создавайте одинаковые active/archive копии и stubs на архив вместо обновления ссылок.
- Ссылки в Markdown должны разрешаться от расположения файла; после переносов запускайте link scan/checker.

Для межмодульной миграции или нового контракта до кода зафиксируйте Problem, Current/Intended/Optimal behavior, Non-goals, owner surface, зависимости, anti-regression и verification plan. Узкий очевидный fix не требует церемонии. Не реконструируйте план задним числом; завершённую работу фиксируйте датированным changelog/evidence.

Документация обновляется в том же slice после выбора target behavior. Не переписывайте исторический документ так, будто новое решение существовало всегда.

---

## 11. Git и cleanup

- Сохраняйте пользовательские изменения; до работы смотрите `git status --short` и diff затронутых файлов.
- Не используйте destructive reset/checkout и не смешивайте несвязанные изменения.
- Один cleanup-класс — один небольшой revertable commit: runtime artifacts, placeholders, generated copies, legacy surface, docs links и т.д. не сваливаются вместе.
- Не удаляйте файл только потому, что `git grep` не нашёл ссылку. Проверьте package scripts, dynamic imports, docs, generators, runtime entrypoints и историю через `git log -- $candidatePath`.
- Для каждого кандидата явно назначьте: **safe delete**, **verify first** или **keep**, с причиной и командой проверки.
- Перед массовым удалением сохраните baseline/tag или отдельную ветку; rollback — `git revert` конкретного batch, не переписывание истории.
- После cleanup: `git diff --check`, targeted gates, reference scan и `git status --short`.

Корень репозитория не используется для `.tmp*`, DB, logs, PID, caches, reports и editor-history metadata. Ignore-rule не превращает мусор в допустимую архитектуру; генератор должен писать в выделенный ignored output root с понятной политикой очистки.

---

## 12. Оркестрация и независимое ревью

Главная сессия держит цель, инварианты, dependency graph, decisions и evidence ledger. Делегируйте **крупные непересекающиеся owner slices**, а не десятки микрозадач.

Для каждого агента зафиксируйте:

- owner surface и конкретный результат;
- разрешённые и запрещённые файлы;
- зависимости/контракты соседних slices;
- unique DB/basetemp/ports/output root;
- cheap и milestone gates;
- формат evidence: `path:line`, команды, exit codes, риски и unverified paths.

Параллельные агенты не редактируют одни файлы и не регенерируют общие fixtures одновременно. Сначала разделяйте работу по owner boundaries; пересекающиеся интеграционные изменения выполняются отдельной последовательной волной.

Проверяйте несущие факты субагента сами до интеграции. После крупной пачки назначайте независимый **adversarial review** другому агенту/модели: искать пропущенные call-sites, sibling bugs, нарушения инвариантов, shared state, тесты, проходящие вхолостую, и расхождения docs/runtime. Adversarial reviewer сначала работает read-only и не исправляет собственные находки, пока они не подтверждены оркестратором.

Не объявляйте волну завершённой, пока не сведены результаты slices, не разрешены contract conflicts, не выполнены заявленные milestone gates и не перечислено сознательно непроверенное.

### Внешний review через Claude Code

Для законченной высокорисковой пачки коммитов выполняйте дополнительный read-only
review через локально установленный Claude Code. Это внешний, cloud-visible
источник evidence, а не доверенный gate или автоматический автор исправлений.

- Заморозьте один связный owner slice как точный `<BASE>..<HEAD>`. Число
  коммитов само по себе не ограничивает diff; последующие коммиты не входят в уже
  запущенный review.
- Никогда не запускайте внешний reviewer из основного checkout, если его
  `.git/config`, environment или untracked files содержат credential. Используйте
  отдельный standalone local clone (не worktree) с credential-free filesystem
  `origin`, чистым деревом и проверенным exact HEAD. Stdout/stderr храните только
  во внешнем task output, не в репозитории.
- Model-controlled команда для глубокого review:

```powershell
claude.exe -p --model opus --effort high `
  "/code-review high <BASE>..<HEAD>" `
  --permission-mode plan `
  --disallowedTools "Edit,Write,NotebookEdit" `
  --output-format json
```

  Записывайте CLI version, exact SHAs, exit code, полноту JSON и фактический
  resolved model ID из `modelUsage`; alias нельзя выдавать за конкретную модель
  без этого evidence. Успешная калибровка 2026-08-07 разрешила `opus` в
  `claude-opus-5`; policy этого репозитория требует `--effort high`, но resolved
  model всё равно проверяется заново после каждого запуска.
- `ultrareview` допустим как дополнительный model-uncontrolled cloud pipeline.
  Он ограничивает branch diff (на момент калибровки: 500 files / 8000 changed
  lines); используйте именованную base branch, а не недокументированный bare SHA.
  Логические коммиты без близкой base branch лимит не уменьшают.
- Exit `1`/`130`, timeout, malformed/truncated/empty JSON или отсутствие
  подтверждённого model ID означают `UNVERIFIED`, а не «findings нет». Разрешён
  один явно записанный fallback; после его сбоя slice остаётся непроверенным.
- Запускайте reviewer параллельно только с непересекающейся работой. Frozen clone
  и range не меняются до результата.
- Оркестратор вручную воспроизводит каждый P1/P2. Подтверждённое замечание
  исправляет агент owning slice; ложное отклоняется с path/test evidence. P3 не
  расширяет волну. После remediation допускается один review только fix-delta и
  нерешённых findings; оставшийся P1 блокирует/rescope batch вместо бесконечного
  цикла.

---

## 13. Запрещено

- Выдавать debug path, частичный/оборванный вывод или старый artifact за canonical evidence.
- Говорить «всё зелёное», когда не проверены все заявленные surfaces или отсутствует CI.
- Смешивать current behavior, intended behavior и target architecture.
- Исправлять редкий пример набором литералов или ослаблять инвариант ради теста.
- Менять OpenAPI, migrations, wire aliases, SSE shapes, fixture schema или денежную семантику попутно.
- Запускать destructive DB reset по непроверенному URL.
- Править `v1`/archive или generated copies без явного scope.
- Коммитить runtime artifacts, секреты, токены и абсолютные локальные пути.
- Объявлять работу полной без свежих `path:line`, точных команд и честного списка непроверенного.
