# Внешнее независимое ревью — runbook

Status: current operational rule
Owner surface: процедура внешнего ревью, оба направления
Companion sources: `../AGENTS.md` §15 (когда обязательно, условия, приём находок), `codex-orchestrator-rule.md` (роль оркестратора)

Здесь только механика запуска. Триггеры обязательности, условия прогона и правила приёма находок живут в `AGENTS.md` §15 и не дублируются.

## 1. Кто кого ревьюит

**Принцип независимости: внешний ревьюер обязан быть другой системой и другой моделью, чем оркестратор.** Ревью самого себя не является evidence, каким бы свежим ни был контекст: одна модель воспроизводит собственные слепые пятна. Отсюда матрица — направление определяется тем, кто сейчас оркестратор:

| Оркестратор | Внешний ревьюер | Механика |
|---|---|---|
| Codex | Claude Code, `claude -p "/code-review high <BASE>..<HEAD>"` | §2 |
| Claude Code | Codex, `codex exec review --base <BRANCH>` или `codex exec` с промптом на stdin | §3 |

**Оркестратор запускает ревьюера сам** и сам принимает, трактует и перепроверяет результат — в обоих направлениях. Просьба к владельцу запустить ревьюера вручную не является частью протокола: это сигнал о нерешённой проблеме окружения, которую надо назвать прямо, а не обходить.

Внутренний adversarial review субагентом той же системы обязателен всегда (`AGENTS.md` §16.4) и **не засчитывается** как внешний. Он идёт до внешнего, а не вместо.

В evidence ledger по обоим направлениям фиксируется: имя и версия CLI, exact `<BASE>..<HEAD>` или SHA, точная команда, exit code, полнота вывода и модель. Claude Code отдаёт **resolved** model ID в `modelUsage` — его проверяют каждый раз. У Codex модель задаётся флагом `-m`, и записывается именно заданное значение; если флаг не передавали, так и фиксируйте — дефолт CLI не является проверенным выбором модели.

## 2. Claude Code как внешний ревьюер

Применяется, когда оркестратор — Codex.

**Preflight.** Не хардкодьте версию расширения или абсолютный пользовательский путь и не делайте вывод по одному PATH/WSL probe.

```powershell
$claudeCommand = Get-Command claude.exe -ErrorAction SilentlyContinue
if (-not $claudeCommand) {
    $claudeCommand = Get-Command claude -ErrorAction SilentlyContinue
}
$claudeExe = if ($claudeCommand) { $claudeCommand.Source } else { $null }
if (-not $claudeExe) {
    $extensionRoots = @(
        (Join-Path $env:USERPROFILE '.vscode\extensions'),
        (Join-Path $env:USERPROFILE '.vscode-insiders\extensions'),
        (Join-Path $env:USERPROFILE '.cursor\extensions')
    )
    $extensions = foreach ($root in $extensionRoots) {
        if (Test-Path -LiteralPath $root) {
            Get-ChildItem -LiteralPath $root -Directory `
                -Filter 'anthropic.claude-code-*-win32-*'
        }
    }
    $latest = $extensions | Sort-Object {
        [version](($_.Name -replace '^anthropic\.claude-code-', '') `
            -replace '-win32-.*$', '')
    } -Descending | Select-Object -First 1
    if ($latest) {
        $claudeExe = Join-Path $latest.FullName `
            'resources\native-binary\claude.exe'
    }
}
if (-not $claudeExe -or -not (Test-Path -LiteralPath $claudeExe -PathType Leaf)) {
    throw 'Claude Code binary not found'
}
& $claudeExe --version
if ($LASTEXITCODE -ne 0) { throw 'Claude Code --version failed' }
$reviewId = [guid]::NewGuid().ToString('N')
$reviewOutputRoot = Join-Path (git rev-parse --show-toplevel) ".local-run/codex-review/claude-$reviewId"
New-Item -ItemType Directory -Path $reviewOutputRoot -Force | Out-Null
$progressLog = Join-Path $reviewOutputRoot 'progress.log'
$resultJson = Join-Path $reviewOutputRoot 'review.json'
```

**Основной путь — model-controlled `/code-review`.**

```powershell
& $claudeExe -p --model opus --effort high `
  "/code-review high <BASE>..<HEAD>" `
  --permission-mode plan `
  --disallowedTools "Edit,Write,NotebookEdit" `
  --output-format json `
  1> $resultJson 2> $progressLog
```

Записывайте CLI version, exact SHAs, exit code, полноту JSON и фактический resolved model ID из `modelUsage`. Калибровка 2026-08-07 разрешила alias `opus` в `claude-opus-5`, но resolved model проверяется заново после каждого запуска. `/code-review` — forked subagent и наследует модель сессии, поэтому модель задаётся на верхнем `claude -p` через `--model`, а reasoning — через `--effort`. Микроправки и fix-delta проверяются этим путём, не `ultrareview`. Если появится кастомный `.claude/agents/*.md`, его `model:` разрешается после `CLAUDE_CODE_SUBAGENT_MODEL` и модели конкретного вызова, но до модели основной сессии.

**Опциональный `ultrareview`** — model-uncontrolled cloud pipeline, допустим только для завершённого product batch меньше `500` файлов и `8000` changed lines. Target обязан исключать governance/spec documents, generated files и deleted-artifact cleanup; смешанную пачку проверяйте локальным `/code-review`. Без target он сравнивает текущую ветку с default branch, включая uncommitted changes; в frozen clone используйте документированный PR number или именованную base branch, а не bare SHA:

```powershell
git branch review-base <BASE>
& $claudeExe ultrareview review-base --timeout 30 --json `
  1> $resultJson 2> $progressLog
```

Stdout хранит result/JSON, stderr — progress и tracking URL; сохраняйте оба вне репозитория. Требуется авторизация через claude.ai account; pipeline недоступен на Bedrock, Vertex, Foundry и для ZDR-организаций. На Team/Enterprise проверьте `/usage-credits`/billing: отсутствие credits блокирует запуск при валидном auth. Логические коммиты без близкой base branch лимит diff не уменьшают.

**Семантика завершения.** Для локального `/code-review`: exit `1`/`130`, timeout, malformed/truncated/empty JSON или отсутствие подтверждённого model ID означают `UNVERIFIED`, а не «findings нет». Для `ultrareview` model ID не ожидается; обязательны exit `0` и полный result/JSON — `0` означает завершённый review (с findings или без), `1` — ошибка/не стартовал/timeout, `130` — прервано.

## 3. Codex как внешний ревьюер

Применяется, когда оркестратор — Claude Code.

**Preflight.** Codex вызывается **из `PATH`** (`codex`) либо явно как `$env:APPDATA\npm\codex.cmd` — это стабильная npm-сборка. Каталог `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\` принадлежит настольному приложению: его сборки автообновляются, в том числе до альфа-версий, и подкаталог меняется прямо между запусками. **Отсюда ревьюер не берётся.** Инцидент 2026-10-07 (программа 032): прежний сниппет выбирал «самую новую» сборку из этого каталога и взял `0.162.0-alpha.2`, у которой не поднималась Windows-песочница (`helper_unknown_error: setup refresh had errors`; `codex doctor` — `sandbox provisioning failed`; причина — Windows error 32 при обновлении прав на занятый `node_repl.exe`). Два ревью подряд вернулись `UNVERIFIED`: ревьюер не прочитал ни одного файла. Владелец поставил стабильную `0.160.1` (`codex doctor` — 0 отказов, права администратора не понадобились).

```powershell
$codex = (Get-Command codex -ErrorAction SilentlyContinue).Source
if (-not $codex) { $codex = Join-Path $env:APPDATA 'npm\codex.cmd' }
if (-not (Test-Path $codex)) { throw 'Codex CLI not found in PATH or %APPDATA%\npm' }
$codexVersion = (& $codex --version 2>&1 | Select-Object -First 1)
if ($codexVersion -match 'alpha') { throw "Codex $codexVersion is an alpha build; install the stable npm release" }
$codexVersion   # в evidence ledger вместе с $codex
& $codex doctor --summary   # строка sandbox должна быть без отказа
```

**Если локальная песочница всё же не стартует** (ответ «не смог запустить shell» с маркерами — это `UNVERIFIED`, а не «находок нет»): допустим обход, проверенный 2026-10-07 на срезах S1 и S4 программы 032, — ревьюер читает код через GitHub на **точном SHA**. Смена механизма чтения объявляется первой строкой промпта, с ссылками `https://github.com/<owner>/<repo>/tree/<sha>`, `.../compare/<base>...<sha>` и `https://raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>`; остальной текст промпта не меняется. Ветка для этого должна быть запушена.

В ledger идут и путь, и строка версии: утверждения ниже про флаги проверены на `0.147.x`–`0.160.x` и не переносятся на другую сборку автоматически.

**Прогон.** Оркестратор запускает Codex сам — так же, как Codex сам запускает Claude Code. Промпт подаётся на stdin, рабочий корень задаётся флагом, финальный ответ пишется **в отдельный файл**, прогресс — в лог. Каталог ревью живёт **внутри проекта**, в игнорируемом `.local-run/codex-review/<id>/` (решение владельца 2026-09-21: каталогов вне папки проекта не создаём); он не коммитится, а замороженный клон удаляется сразу после прогона.

```powershell
$reviewId = [guid]::NewGuid().ToString('N')
$reviewRoot = Join-Path (git rev-parse --show-toplevel) ".local-run/codex-review/$reviewId"
New-Item -ItemType Directory -Path $reviewRoot -Force | Out-Null

Get-Content "$reviewRoot\prompt_sliceA.txt" -Raw |
  & $codex exec --sandbox read-only -C $repoRoot `
      -o "$reviewRoot\final_sliceA.md" - `
      > "$reviewRoot\progress_sliceA.log" 2>&1
```

Срезы независимы и запускаются параллельно, пока у каждого свои `-o` и лог.

**Для commit-пачки используйте встроенный режим review** — прямой аналог `/code-review`: он сам строит diff-таргет, поэтому таргет закреплён механически, а не обещанием в промпте. Запускать его нужно **в замороженном credential-free clone на exact HEAD**, а не в рабочем checkout:

```powershell
& $codex exec --sandbox read-only -C $frozenClone `
    review --base review-base -m <MODEL> `
    -o "$reviewRoot\final_range.md" `
    > "$reviewRoot\progress_range.log" 2>&1
```

Таргет задаётся одним из `--base <BRANCH>`, `--commit <SHA>`, `--uncommitted`. `--uncommitted` берёт всё рабочее дерево целиком: на грязном дереве это смешанная пачка, запрещённая `AGENTS.md` §15. Для замороженного среза — `--base` (именованная ветка, как `review-base` в §2) или `--commit`.

Generic `codex exec` с промптом на stdin остаётся для осознанных path-срезов и аудитов, которые встроенный diff-таргет не выражает, — например для ревью набора документов.

**Что фиксируется как evidence:** путь и строка `--version` выбранного бинарника, точная команда с таргетом, exit code, непустой `-o`-файл и финальный маркер или валидный по схеме JSON. Модель задаётся явно через `-m <MODEL>`; каталог доступных моделей — `codex debug models --bundled`. Записывается **requested** model: локальная справка не подтверждает per-run resolved model ID, поэтому выдавать заданное значение за подтверждённое нельзя.

**Три отличия от §2, которые меняют дисциплину приёмки:**

1. **Структурированный вывод возможен, но не бесплатен.** `--output-schema <FILE>` принимает JSON Schema для финального ответа, `--json` печатает события JSONL. Если схему не задали — вывод свободный, и тогда промпт **обязан** требовать машинно-проверяемый маркер вердикта, а приёмка — грепать его:

   ```powershell
   Select-String -Path "$reviewRoot\final_slice*.md" -Pattern 'VERDICT-' | Select-Object -Last 6
   ```

   Отсутствие маркера при свободном выводе — `UNVERIFIED`, а не «findings нет».

2. **Разделяйте финальный ответ и прогресс.** `-o` даёт ответ отдельным файлом; без него всё смешивается в stdout, и оборванный прогон выглядит как законченный отчёт. Судите по наличию `-o`-файла с финальным маркером, а не по размеру лога.

3. **Read-only sandbox — не замена credential-free окружению.** Ревьюер не должен видеть credential в достижимой файловой системе. Если ревью идёт на другой машине через перенос исходников, переносимый набор обязан быть credential-free, а его точный SHA — записан; иначе нельзя утверждать, какой именно код проверялся.

Файлы ревью не коммитятся (`AGENTS.md` §12).

## 4. Приём результата

Общее для обоих направлений — `AGENTS.md` §15: оркестратор вручную воспроизводит каждый P1/P2, подтверждённое исправляет владелец slice, ложное отклоняется с path/test evidence, P3 волну не расширяет, допускается один review только по fix-delta. Волна с непройденным обязательным внешним ревью не закрывается без датированного принятия риска владельцем.

## 5. Gemini Argon через OpenRouter — второй ревьюер, выборочно

Применяется **только** по одному из трёх триггеров `AGENTS.md` §15 («Второй ревьюер — Gemini Argon, выборочно»), при любом оркестраторе. Codex (§3) остаётся основным ревьюером; Argon его не заменяет и из CI не вызывается. Перед решением «звать» прочитайте раздел «Текущая оценка» в [`reviewer-usefulness-ledger.md`](reviewer-usefulness-ledger.md) и запишите триггер и причину **до** вызова. Программа и обоснование — `specs/040-second-reviewer-argon/spec.md`.

**Модель.** `google/gemini-4-argon` (константа `ARGON_MODEL` в `scripts/argon_review.py`). Владелец назвал её `openrouter/google/gemini-4-argon`; в API OpenRouter идентификатор идёт без префикса `openrouter/`. Бесплатный каталог `GET https://openrouter.ai/api/v1/models` 2026-10-16 (040 `T4001`): канонический id `google/gemini-4-argon-20261013`, контекст 1 048 576 токенов, ответ до 65 536. Каталожная цена ($2 за 1M prompt-токенов, $10 за 1M completion) — каталог, а не расход: расход пишется только из `usage.cost` ответа.

**Что уходит наружу — это публикация.** Провайдер может хранить запрос. Скрипт отправляет ровно одно: замороженный промпт, список коммитов `<BASE>..<HEAD>`, `git diff <BASE>..<HEAD>` (без внешних diff-драйверов и textconv) и полные тексты затронутых файлов **как они закоммичены на `<HEAD>`**. Рабочее дерево, неотслеживаемые файлы, `.git/config` и любые другие файлы диска в нагрузку не попадают по построению: она собирается только из git-объектов диапазона, поэтому отдельный credential-free clone здесь не нужен — модель файловой системы не видит вовсе. Репозиторий публичный, так что закоммиченный диапазон и так лежит на GitHub. Бинарные и не-UTF-8 файлы не встраиваются и перечисляются поимённо в `meta.json`.

**Отказ до отправки** (exit `3`, ничего не отправлено, каталог прогона не создан):

- нет `OPENROUTER_API_KEY` (в живом режиме);
- SHA не разрешается в коммит;
- пустой диапазон, в том числе после `--paths`;
- затронутый путь похож на секрет: `.env*`, `*.pem`, `*.key`, `*secret*` в любом компоненте пути, включая удалённые пути и обе стороны переименования. **Единственное исключение** — последний компонент вида `.env*.example`, то есть те же версионируемые шаблоны, которые пропускает `.gitignore` (`.env.example`, `admin-ui/.env.local.example`, `simulator-ui/v2/.env.example`). Настоящий `.env`, `.env.local` и `.example` под каталогом вида `secrets/` по-прежнему отвергаются;
- нагрузка содержит ключ;
- нагрузка длиннее `MAX_PAYLOAD_CHARS` (1 500 000 символов) — **никакого усечения**: сужайте диапазон или `--paths`;
- `--out` существует и не пуст (старый `final.md` читался бы как ответ этого прогона).

**Ключ** берётся только из переменной окружения `OPENROUTER_API_KEY` текущей сессии: не из аргумента, не из файла репозитория, не из промпта. Скрипт не пишет его ни в один файл прогона и не печатает; если ключ оказался в тексте ошибки транспорта, он заменяется на `<redacted>`.

**Сначала — сухой прогон** (ключ не нужен, сети нет): проверьте размер нагрузки и список файлов.

```powershell
$base = '<BASE SHA>'; $head = '<HEAD SHA>'
$runRoot = Join-Path (git rev-parse --show-toplevel) ".local-run/argon-review"
$runId = "$(Get-Date -Format yyyyMMddTHHmmss)-$($head.Substring(0,7))"
.\.venv\Scripts\python.exe scripts/argon_review.py --base $base --head $head `
    --prompt "$runRoot\$runId-prompt.txt" --out "$runRoot\$runId-dry" --dry-run
Get-Content "$runRoot\$runId-dry\meta.json"   # payload_chars, files_included, skipped_binaries
```

**Прогон** — один платный вызов. Повторов нет намеренно: повтор — это второй платный вызов и новая строка журнала.

```powershell
.\.venv\Scripts\python.exe scripts/argon_review.py --base $base --head $head `
    --prompt "$runRoot\$runId-prompt.txt" --out "$runRoot\$runId"
"exit $LASTEXITCODE"
```

Флаги: `--model` (по умолчанию `ARGON_MODEL`), `--paths` (сузить дифф и набор файлов), `--timeout` (секунды на каждую сокетную операцию, а не на весь вызов; по умолчанию 900), `--verify-response <file>` (проверить сохранённый ответ без вызова).

**Вывод** — `.local-run/argon-review/<id>/`, не коммитится: `prompt.txt` (замороженная копия промпта), `payload.json` (что отправлено, без ключа), `response.json` (сырой ответ или ошибка транспорта), `final.md` (текст ответа), `meta.json` (модель запрошенная и из ответа, `generation_id`, `finish_reason`, токены, `cost` — число из `usage.cost` либо слово `unknown`, время стены, BASE/HEAD, размер нагрузки, файлы), `progress.log`.

**Исходы.** `exit 0` (`VERIFIED`) — только HTTP 200, модель ответа равна запрошенной или её датированной форме (`google/gemini-4-argon-<суффикс>`), `finish_reason == "stop"`, непустой текст и все три маркера отдельными строками: `VERDICT-<TAG>: …`, `CLASS-1-COUNT: N`, `REVIEW-COMPLETE: YES`. Всё остальное — `exit 2` (`UNVERIFIED`) с причиной в `meta.json`: ошибка HTTP (в том числе 402 — лимит оплаты, 429 — лимит частоты), таймаут и обрыв соединения, не-JSON или оборванный JSON, другая или отсутствующая модель, `finish_reason` ≠ `stop` (в том числе `length`), пустой текст, нет маркера. `UNVERIFIED` — не «находок нет» (`AGENTS.md` §15). Маркеры доказывают законченность ответа, а не его внимательность.

**Приём.** После `exit 0` скрипт печатает заготовку строки журнала с числами из `meta.json`; знаки `?` заполняет оркестратор после проверки находок. Каждый P1/P2 оркестратор воспроизводит сам (§4); при двух и более ревью одной пачки составляется таблица консолидации по доказательствам (`AGENTS.md` §15). Строка журнала пишется в том же PR, где использован результат; владельцу кратко сообщается разница оценок, добавленная польза и итоговое решение.

**Ограничения evidence** (пишутся в ledger): Argon получает только текст — не исполняет тестов, не читает файлов вне нагрузки и не видит истории за пределами диапазона; гейты им не покрыты. Ответ модели не исполняется и не применяется автоматически.

### 5.1 Шаблон промпта

Промпт пишется в файл до запуска и после запуска не меняется; новая редакция — новый прогон и новая строка журнала. Список коммитов, дифф и файлы скрипт добавляет сам.

```text
Ты — независимый внешний ревьюер проекта GEOv0 (открытый протокол сети взаимного кредита;
правила проекта — AGENTS.md). Ниже — замороженный диапазон <BASE>..<HEAD>: список коммитов,
git diff и полные тексты затронутых файлов на <HEAD>. Больше у тебя ничего нет.

Границы. Ты только читаешь: ничего не правишь и не принимаешь решений. Тесты ты не запускаешь,
поэтому любое утверждение о поведении опирай на строки, которые видишь; если вывод требует того,
чего в нагрузке нет, скажи прямо, чего не хватает.

Триггер вызова: <1 | 2 | 3> — <одна фраза причины>.
Что изменено и зачем: <две-три фразы>.
Инварианты, которые обязаны выжить: <перечень>.

Что уже измерено — оспорьте вывод, не повторяйте замер:
- <измерение: команда, результат, вывод, который из него сделан>
- <...>
Атакуйте рассуждение от результата к выводу: где вывод не следует из измерения, где стенд
не способен увидеть исход, ради которого построен.

Разногласие (только для триггера 1): Codex утверждает <...>; внутренний adversarial утверждает <...>.
Какое чтение верно и какое наблюдение решило бы спор?

Ищи перечисленные формы дефекта, а не «качество вообще»: <пропущенные call-sites, путь в обход
защиты, ложное зелёное, ...>.

Формат каждой находки:
- [P1|P2|P3] <заголовок>
  - где: path:line на <HEAD>
  - что не так и почему (механизм, а не впечатление)
  - repro: тест, команда или последовательность шагов, которая это покажет
  - класс по AGENTS.md §19.5: 1 (потеря на денежном пути, воспроизводимая) или 2 (всё остальное)

В конце ответа, каждый маркер отдельной строкой:
VERDICT-<TAG>: SOUND | WEAK | BROKEN
CLASS-1-COUNT: <число находок класса 1>
REVIEW-COMPLETE: YES
```

`<TAG>` — короткая метка среза или вопроса (например `VERDICT-040-A`); при нескольких вопросах — по маркеру на каждый. Без маркеров ответ — `UNVERIFIED`.
