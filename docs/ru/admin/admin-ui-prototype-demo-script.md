# Админка (прототип) — демо‑скрипт для ревью

Цель: быстро пройтись по всем экранам админки‑прототипа, проверить UX/структуру/состояния (loading/empty/error/403/401/slow) и убедиться, что данные берутся из общего fixture‑pack.

---

## Как устроена разработка админки сейчас (fixture-driven prototyping)

В проекте админка разрабатывается в подходе **fixture-driven prototyping** (иногда его же можно назвать *fixture-first UI*):

- UI строится поверх **каноничного набора JSON-фикстур** (fixtures), которые имитируют ответы API.
- Поведение API (ошибки/пустые списки/задержки/403/401) задаётся **сценариями** (scenario-based mocking).

Это позволяет быстро и воспроизводимо развивать UI **без поднятого backend**, но так, чтобы переключение на реальный API в будущем было минимальным.

### Где лежат данные и что является source of truth

1) **Канонические фикстуры** (source of truth):
- `admin-fixtures/v1/datasets/*.json`
- `admin-fixtures/v1/scenarios/*.json`

2) **Публичная копия для SPA** (то, что реально читает браузер в runtime):
- `admin-ui/public/admin-fixtures/v1/...`

Эта копия **перезаписывается** скриптом синхронизации (см. ниже).

### Как это подключено в админке (Mock API)

- UI-страницы (`admin-ui/src/pages/*.vue`) обращаются к клиенту `mockApi`:
  - `admin-ui/src/api/mockApi.ts`
- `mockApi` грузит JSON из `public/admin-fixtures/v1/...` через `fetch`, применяет выбранный сценарий и возвращает ответы в envelope-формате:

Дополнительно:
- Для части экранов, которые читают фикстуры напрямую (например, `/graph`), используется загрузчик:
  - `admin-ui/src/api/fixtures.ts`

```json
{ "success": true, "data": { "...": "..." } }
```

или

```json
{ "success": false, "error": { "code": "FORBIDDEN", "message": "..." } }
```

Сценарий задаётся в URL как `?scenario=...` (и переключается в Header), после чего `mockApi`:
- может добавлять искусственную latency;
- может возвращать ошибки для отдельных endpoints через overrides;
- может возвращать пустые списки для list-эндпоинтов.

Важно: при навигации мы сохраняем `scenario` в query (`...route.query`), чтобы вся админка оставалась в одном режиме.

### Синхронизация и валидация фикстур (dev/build)

Скрипты и команды (выполнять из папки `admin-ui`):

- `npm run sync:fixtures` — копирует `../admin-fixtures/v1` → `public/admin-fixtures/v1`
- `npm run validate:fixtures` — проверяет, что фикстуры корректны/парсятся и соответствуют ожидаемой структуре

Автоматизация:
- `npm run dev` запускает `predev` → *sync + validate*.
- `npm run build` запускает `prebuild` → *clean + sync + validate*.

### Генерация фикстур (Python)

Фикстуры можно:
- править вручную в `admin-fixtures/v1/...` (быстро для мелких изменений), или
- генерировать детерминированно Python-скриптами в `admin-fixtures/tools/` (рекомендуемо для больших наборов).

Полезная точка входа (единый генератор):

- `admin-fixtures/tools/generate_fixtures.py` — единый детерминированный генератор каноничных community seeds:
	- `--seed greenfield-village-100` (100 участников)
	- `--seed riverside-town-50` (50 участников)

Общее:
- `admin-fixtures/tools/seedlib.py` — общие утилиты и производные датасеты (debts/cycles/meta) для seed-генераторов.

См. также:
- Спека fixture-pack: `docs/ru/admin/specs/admin-ui-prototype-fixtures-spec.md`
- Подход к seed-документам и генерации: `docs/ru/seeds/README.md`

#### Guardrails валидатора (важно для демо)

Скрипт `admin-ui/scripts/validate-fixtures.mjs` проверяет ряд ожидаемых ограничений. В частности:
- equivalents в «каноничном» режиме ожидаются ровно `UAH/EUR/HOUR`;
- `participants.length` обычно должен быть 50 или 100 (если не задан `EXPECTED_PARTICIPANTS`).

Поэтому seed-генераторы дают валидируемый набор.

#### Примеры команд для генерации

Рекомендуемые команды:
- `python admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100`
- `python admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50`

После любой генерации (или ручной правки канонических JSON):
- `cd admin-ui; npm run sync:fixtures; npm run validate:fixtures`

## 1) Как запустить

Из папки `admin-ui`:

- `npm run dev`
- открыть `http://localhost:5173/` (если порт занят — Vite выведет другой, например `5174`)

Примечание про Node.js:
- Проект проверяет версию Node на установке зависимостей (preinstall).
- Требование: `^20.19.0 || >=22.12.0`.

Фикстуры автоматически синхронизируются в `admin-ui/public/admin-fixtures/v1` на `predev`.

## Где открыть этот файл

В VS Code:

- откройте файл `docs/ru/admin/admin-ui-prototype-demo-script.md`
- нажмите `Ctrl+Shift+V` (Preview)
- или команду: “Markdown: Open Preview to the Side”

## 2) Управление сценариями

Сценарий выбирается в верхней панели (Scenario) и сохраняется в URL как `?scenario=...`.

Рекомендуемый порядок:

1. `happy` — базовая функциональность
2. `empty` — пустые списки
3. `error500` — ошибки API на ряде эндпоинтов
4. `admin_forbidden403` — запрет на `/api/v1/admin/*`
5. `integrity_unauthorized401` — 401 на `/api/v1/integrity/*`
6. `slow` — повышенная задержка

## 3) Чеклист по экранам (happy)

### Dashboard
- Проверить: Health/DB/Migrations карточки отрисовываются, без падений.
- Проверить: блок “Trustline bottlenecks” показывает топ узких мест (подсветка по порогу).
- Проверить: блок “Incidents over SLA” показывает транзакции, которые просрочили SLA.
- Проверить: кнопки “View all” ведут на соответствующие экраны, сохраняя `?scenario=`.

### Trustlines
- Проверить: фильтры (eq / creditor / debtor / status).
- Проверить: подсветка “узких” trustlines по порогу (decimal‑string без float).
- Проверить: drawer (детали) по клику на строку.

### Network Graph
- Открыть `Network Graph` в sidebar и убедиться, что граф отображается.
- Проверить: фильтры `Equivalent`, `Status`, `Threshold`.
- Проверить: `Search` (PID или имя) + `Find` центрирует узел и кратко подсвечивает.
- Проверить: одиночный клик по node выделяет node и подставляет PID/имя в поиск (drawer не открывает).
- Проверить: двойной клик по node центрирует/зуумит и открывает drawer участника.
- Проверить: клик по edge открывает drawer trustline.

### Incidents
- Проверить: пагинация.
- Проверить: подсветка просрочки SLA.
- Проверить: “Force abort” просит reason и показывает success/error.

### Participants
- Проверить: поиск по PID/имени, фильтр по status, пагинация.

### Audit log
- Проверить: пагинация, drawer деталей (before/after).

### Config
- Проверить: редактирование ключей, “dirty” счетчик.
- Проверить: Save отправляет patch и обновляет данные.

### Feature Flags
- Проверить: toggle подтверждается confirm‑диалогом; отмена возвращает значение.

### Equivalents
- Проверить: список активных, toggle “Include inactive”.
- Проверить: бейдж “Used by …” подгружается лениво при hover (и кэшируется).
- Проверить: для `inactive` equivalents доступна кнопка Delete:
	- prompt причины (reason) обязателен;
	- если equivalent используется (trustlines/incidents) — UI показывает conflict (409) и НЕ удаляет.

### Integrity
- Проверить: статус грузится.
- Проверить: Verify подтверждается диалогом; отмена не вызывает запрос.

## 4) Чеклист по сценариям (быстро)

- `empty`: экраны списков показывают `ElEmpty` вместо таблиц.
- `error500`: показать “error” alert/сообщения на затронутых экранах.
- `admin_forbidden403`: все admin‑экраны должны деградировать понятной ошибкой.
- `integrity_unauthorized401`: только Integrity должен дать 401‑ошибку.
- `slow`: увидеть skeleton/loading и отсутствие “дерганий” при переключении.

Быстрая проверка ролей:
- Переключить роль на `auditor (read-only)` и убедиться, что destructive actions скрыты/заблокированы.

## 5) Где лежат данные

- Канонический набор: `admin-fixtures/v1/...`
- То, что реально читает SPA: `admin-ui/public/admin-fixtures/v1/...`

Ожидаемая проверка: поменять что-то в `admin-fixtures/v1/datasets/*`, перезапустить dev — и увидеть изменение в UI.
