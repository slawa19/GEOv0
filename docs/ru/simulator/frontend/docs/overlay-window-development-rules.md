# Overlay Window Development Rules

Status: ACTIVE
Scope: `simulator-ui/v2` overlay windows, interact panels, inspector popups, HUD toolbars, dropdown surfaces, `WindowShell`, `HudBar`, window-manager sizing/layout policy, and shared overlay shell geometry.

Этот документ фиксирует guardrails для разработки overlay/window/HUD компонентов, чтобы не повторять типовые ошибки: magic numbers, дублирование shell/content responsibilities, монолитные компоненты, локальные layout-патчи вместо системного контракта.

## 1. Source of truth hierarchy

Порядок ответственности всегда такой:

1. Window manager policy: positioning, z-index, sizing mode, clamping.
2. Design system: tokens, shared primitives, family-level layout contract.
3. Component-local CSS: только уникальные layout details конкретного окна.
4. Runtime-measured content: только там, где это разрешено sizing mode.

Если локальный scoped CSS или measured DOM size начинает подменять policy WM, архитектура считается нарушенной.

## 2. Mandatory design flow before coding

Перед изменением или созданием overlay/window компонента агент обязан определить:

1. window family: `interact-panel`, `inspector`, `popup`, `drawer`, либо явно новый family;
2. sizing mode: `fixed-width-auto-height`, `fixed-size`, `intrinsic-size`, либо другой явно названный режим;
3. кто владеет width и height: WM policy, component content, или смешанный разрешённый contract;
4. какие токены и primitives уже существуют и подходят;
5. какие тесты должны защитить выбранный contract.

Без этих пяти решений кодировать окно нельзя.

Если change затрагивает несколько runtime surfaces, до кодинга должна быть зафиксирована их общая matrix:

1. `surface -> family -> sizing mode -> positioning owner -> width owner -> height owner -> z-layer`;
2. matrix должна быть отражена в versioned spec/checklist, а не оставаться только в diff/обсуждении.

## 3. Responsibility boundaries

### 3.1 Window manager owns policy

WM обязан владеть:

- `left/top/z-index`;
- clamping и viewport safety;
- sizing mode;
- правилами применения measured size.

WM не должен получать свою width policy случайно из содержимого окна.

Правило:

- если окно объявлено как `fixed-width-auto-height`, measured width не применяется;
- measured height может применяться, если это часть режима.

Дополнительное правило lifecycle safety:

- WM/publisher code обязан безопасно игнорировать stale measured updates после unmount, rapid toggle или phase change;
- pending measurement publication не должна переживать unmount без explicit guard/cancel path.

### 3.2 WindowShell is transport, not business layout

`WindowShell` не должен становиться местом случайных визуальных или layout-костылей.

Запрещено использовать `WindowShell` как primary fix для:

- локальной проблемы `select` layout;
- family-specific spacing hacks;
- компенсации component-local hardcodes.

`WindowShell` допустимо менять только если меняется общий shell contract или sizing pipeline.

### 3.3 Content components own content, not shell policy

Контентные компоненты (`ManualPaymentPanel`, `TrustlineManagementPanel`, `ClearingPanel`, `NodeCardOverlay`, `EdgeDetailPopup`) не должны:

- самовольно решать policy ширины окна;
- дублировать surface/shell behavior, если оболочка уже это делает;
- лечить WM-проблему через `fit-content + max-width + local padding override`.

Если такое нужно, сначала проверяется поломка в WM contract или DS primitive.

### 3.4 Ownership boundaries are explicit

Границы владения должны быть жесткими:

- WM владеет positioning policy, sizing mode, clamp и measured-size application logic;
- overlay geometry publishers владеют только публикацией metrics, а не content-specific layout decisions;
- DS primitives владеют shared layout/visual contract;
- consumer components не пробивают WM/z-layer/global geometry policy внутрь local scoped CSS без documented exception.

Если exception действительно нужен, он должен быть записан в spec/checklist и иметь ограниченный scope.

## 4. Layout system rules

### 4.1 First fix existing primitives

Перед вводом нового primitive нужно сначала проверить, можно ли исправить существующий.

Текущее правило для compact forms:

1. сначала исправляется `ds-controls__row`;
2. только потом допускается `ds-controls__row--compact` или новый compact modifier;
3. новый full replacement primitive создаётся только если базовый primitive не покрывает use cases без новых локальных патчей.

### 4.2 No grid blowout contracts

Запрещены grid/flex схемы, которые позволяют содержимому неявно раздувать окно.

Guardrails:

- для form rows использовать `minmax(0, 1fr)` вместо голого `1fr`, если есть риск overflow-driven expansion;
- label rail должен быть tokenized, а не захардкожен в произвольных локальных стилях;
- long metadata не должна вечно жить только внутри `option label`, если из-за этого страдает layout contract.

### 4.3 Scoped CSS is for local layout only

В `scoped` стилях компонента допустимы:

- локальный grid/flex для уникального внутреннего блока;
- позиционирование уникальных элементов;
- spacing для truly local sections.

В `scoped` стилях компонента недопустимы:

- hardcoded colors/shadows/radii;
- дублирование button/input/panel primitives;
- family-wide layout contracts, которые должны жить в DS;
- локальные fixes, скрывающие проблему WM sizing policy.

## 5. Tokens and numeric discipline

### 5.1 No magic numbers for shared semantics

Все shared значения для overlay/windows должны идти через токены, если они задают:

- width/min-width/max-width;
- rail widths;
- shared spacing;
- shell/body paddings;
- shared action-row geometry;
- breakpoints или viewport clamps;
- overlay family geometry.

### 5.2 Do not over-tokenize

Не нужно токенизировать каждое число подряд.

Необязательно выносить в токены:

- одноразовую opacity-константу;
- локальное значение, не формирующее shared contract;
- внутреннюю настройку уникального эффекта, если она не влияет на system drift.

Критерий простой:

- если значение повторяется или задаёт системную геометрию, это token;
- если значение уникально и не влияет на общий контракт, оно может остаться локальным.

### 5.3 JS/CSS geometry sync

Если geometry одновременно нужна в CSS и TypeScript:

1. сначала выбирать CSS token как source of truth;
2. JS читает это значение через approved helper;
3. синхронные пары помечаются как `@token-sync`;
4. для них нужен unit test/regression test.

Нельзя держать silent drift между `overlayGeometry.ts` и CSS tokens.

Если app-level geometry или z-layer contract уже опубликован в runtime source of truth, нельзя скрытно создавать параллельную alias-map в другом файле без отдельного migration decision.

Для текущего Simulator UI это означает:

- app-level z-layer source of truth должен проверяться в `App.css`;
- `designSystem.overlays.css` и компоненты выступают consumers этого контракта, а не вторым независимым источником.

### 5.4 Missing or invalid geometry must degrade safely

Missing CSS var, invalid measured size, stale geometry publish или broken clamp не должны приводить к crash.

Минимальный contract:

- invalid geometry input пропускается или заменяется safe fallback;
- dev-only diagnostics допускаются и желательны для broken geometry contract;
- overlap/invalid clamp не должен silently masquerade as valid layout state.

## 6. Anti-monolith rules

### 6.1 Component size and responsibility

Overlay-компонент нельзя превращать в монолит, который одновременно держит:

- shell behavior;
- domain orchestration;
- layout system;
- local persistence;
- keyboard/outside-click policy;
- data formatting helpers.

Если компонент превышает разумный уровень сложности, нужно декомпозировать:

1. composable для state/policy;
2. DS primitive или shared layout helper для повторяемой верстки;
3. utility для format/normalization logic.

### 6.2 No mixed layers in one fix

Один фикс не должен одновременно без необходимости:

- менять WM policy;
- вводить новые DS primitives;
- переписывать компонентный layout;
- добавлять случайную токенизацию по всей кодовой базе.

Приоритет:

1. root cause;
2. smallest system-level fix;
3. only then local cleanup.

## 7. Overlay-specific anti-patterns

Ниже перечислены анти-паттерны, которые больше нельзя повторять:

1. content-measured width управляет width policy interact-window;
2. `fit-content + local max-width + local padding override` вместо системного family contract;
3. native select overflow styling рассматривается как primary fix для window sizing;
4. новый primitive создаётся раньше, чем проверен существующий `ds-controls__row`;
5. компонент дублирует shell surface/close affordance поверх уже существующего shell;
6. token layer разрастается из-за микротокенизации, не решая structural drift;
7. inspector abstraction вводится без второго consumer и без подтверждённого shared contract.
8. `HudBar` существует как shared primitive, но `TopBar`/`BottomBar` заново определяют его section behavior через local `:deep()`;
9. toolbar/HUD stack может расти по DOM content, но WM и overlay CSS продолжают верить статическому `--ds-hud-stack-height`.
10. pending async measurement/update публикуется после unmount или stale phase transition;
11. app-level z-layer contract дублируется второй alias-map без явной migration decision;
12. content component берёт на себя WM/global geometry policy через local override вместо documented exception.

## 8. Required tests for window work

Каждое изменение overlay/window компонента должно проверить подходящий набор регрессий:

1. width stability на phase transitions;
2. отсутствие second-frame resize jump;
3. long labels/async content не ломают geometry contract;
4. viewport clamp сохраняется;
5. ESC/focus/topmost behavior не регрессирует, если менялись shell/WM rules;
6. token-sync tests обновлены, если менялись JS/CSS geometry пары.
7. toolbar/HUD stack не конфликтует с WM anchoring и overlay insets, если менялся HUD shell contract.
8. stale measurement / rapid mount-unmount / rapid toggle не публикуют неверную геометрию, если менялся measurement pipeline;
9. z-layer order и runtime fallbacks не ломают stacking contract, если менялся layer/geometry source of truth.

Если changed code затрагивает WM sizing policy, тест на width stability обязателен.
Если changed code затрагивает `HudBar`, top/bottom stack height или dropdown shell contract, нужен regression check для toolbar layout.
Если changed code затрагивает measurement lifecycle или geometry publishing, нужен regression check на stale-state safety.

## 9. Review checklist for AI agent

Перед завершением работы агент обязан проверить:

- выбран ли family и sizing mode до кодинга;
- зафиксирована ли matrix для затронутых runtime surfaces, если меняется общий contract;
- не лечится ли root issue локальным CSS вместо WM/DS contract;
- нет ли новых magic numbers для shared geometry;
- не появился ли новый primitive без доказанной необходимости;
- не размазалась ли ответственность между WM, shell и content;
- не размазалась ли ответственность между HUD shell, `HudBar` и consumer-local CSS;
- не появилась ли stale async/measurement path без cleanup или guard;
- не создан ли второй source of truth для z-layer/geometry contract без explicit migration decision;
- добавлены ли regression tests на сломанный ранее сценарий;
- обновлена ли документация, если введён новый contract.

## 10. Where to record decisions

- Стабильные правила разработки: в `docs/ru/*`.
- Project-wide standards: `docs/ru/development-standards.md`.
- DS/tokens/primitives rules: `simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md`.
- Versioned implementation spec/checklist: `plans/*`.

Если изменён именно overlay/window contract, изменение должно быть отражено здесь или в документе того же уровня, а не оставлено только в plan/review заметке.

Если изменён runtime geometry/z-layer source of truth, нужно обновить не только код, но и:

- DS guide / glossary;
- token-sync notes для CSS↔JS geometry pairs;
- runtime surface catalog / versioned checklist, если изменилась classification или ownership.
