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
$reviewOutputRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    "geov0-claude-review-$reviewId"
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

**Preflight.** В `bin` лежит несколько самостоятельных бинарников разных версий — верхний `bin\codex.exe` не обёртка, а отдельная старая сборка (проверено 2026-08-11: `0.130.0-alpha.5` против `0.147.0-alpha.6.5` в версионированном подкаталоге). Разница версий меняет доступные флаги, поэтому **выбор делается по фактическому `--version` каждого кандидата, а не по времени файла**. Не хардкодьте hash подкаталога и номер версии.

```powershell
$candidates = Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin" -Recurse -Filter codex.exe
$resolved = foreach ($c in $candidates) {
    $v = (& $c.FullName --version 2>&1 | Select-Object -First 1)
    if ($LASTEXITCODE -eq 0 -and $v -match '(\d+)\.(\d+)\.(\d+)') {
        [pscustomobject]@{ Path = $c.FullName; Raw = $v
                           Sort = [version]("{0}.{1}.{2}" -f $Matches[1],$Matches[2],$Matches[3]) }
    }
}
$codexInfo = $resolved | Sort-Object Sort -Descending | Select-Object -First 1
if (-not $codexInfo) { throw 'Codex binary not found' }
$codex = $codexInfo.Path
$codexInfo.Raw   # в evidence ledger вместе с $codex
```

В ledger идут и путь, и строка версии: утверждения ниже про флаги верны для `0.147.x` и не переносятся на более старую сборку автоматически.

**Прогон.** Оркестратор запускает Codex сам — так же, как Codex сам запускает Claude Code. Промпт подаётся на stdin, рабочий корень задаётся флагом, финальный ответ пишется **в отдельный файл**, прогресс — в лог. Каталог ревью живёт вне репозитория.

```powershell
$reviewId = [guid]::NewGuid().ToString('N')
$reviewRoot = Join-Path ([System.IO.Path]::GetTempPath()) "geov0-codex-review-$reviewId"
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
