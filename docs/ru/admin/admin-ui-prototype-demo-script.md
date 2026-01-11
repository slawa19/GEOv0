# Админка (прототип) — демо‑скрипт для ревью

Цель: быстро пройтись по всем экранам админки‑прототипа, проверить UX/структуру/состояния (loading/empty/error/403/401/slow) и убедиться, что данные берутся из общего fixture‑pack.

## 1) Как запустить

Из папки `admin-ui`:

- `npm run dev`
- открыть `http://localhost:5173/` (если порт занят — Vite выведет другой, например `5174`)

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

### Integrity
- Проверить: статус грузится.
- Проверить: Verify подтверждается диалогом; отмена не вызывает запрос.

## 4) Чеклист по сценариям (быстро)

- `empty`: экраны списков показывают `ElEmpty` вместо таблиц.
- `error500`: показать “error” alert/сообщения на затронутых экранах.
- `admin_forbidden403`: все admin‑экраны должны деградировать понятной ошибкой.
- `integrity_unauthorized401`: только Integrity должен дать 401‑ошибку.
- `slow`: увидеть skeleton/loading и отсутствие “дерганий” при переключении.

## 5) Где лежат данные

- Канонический набор: `admin-fixtures/v1/...`
- То, что реально читает SPA: `admin-ui/public/admin-fixtures/v1/...`

Ожидаемая проверка: поменять что-то в `admin-fixtures/v1/datasets/*`, перезапустить dev — и увидеть изменение в UI.
