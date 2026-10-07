# Админка (прототип) — демо‑скрипт для ревью

Цель: быстро пройтись по всем экранам админки, проверить UX/структуру/состояния (loading/empty/error/403/401/slow) на **реальном засеянном backend-е**.

(2026-10-07, 032 S4: удалено — режим mock, `mockApi`, пакет фикстур `admin-fixtures/` и его копия в `admin-ui/public/`, сценарии `?scenario=...`, скрипты `sync:fixtures` / `validate:fixtures`, переключатель роли. Прежний раздел «fixture-driven prototyping» описывал именно их; в git он доступен по истории файла.)

---

## 1) Как запустить

Админка всегда ходит в реальный backend. Рекомендуемый способ на Windows — `scripts/run_local.ps1 start`: он поднимает backend, сидирует базу рецептом `riverside-town-50` и Admin UI, и пишет `admin-ui/.env.local`.

Без runner-а:

- backend с базой, засеянной `python scripts/seed_db.py --source recipe --community riverside-town-50`;
- из папки `admin-ui`: `VITE_API_BASE_URL=http://127.0.0.1:18000` (и `VITE_ADMIN_TOKEN`, если токен backend-а не дефолтный), затем `npm run dev`;
- открыть `http://localhost:5173/` (если порт занят — Vite выведет другой, например `5174`).

Примечание про Node.js:
- Проект проверяет версию Node на установке зависимостей (preinstall).
- Требование: `^20.19.0 || >=22.13.0`.

Подробнее: [README.md](README.md), ручной прогон — [manual-smoke-real-mode.md](manual-smoke-real-mode.md).

## Где открыть этот файл

В VS Code:

- откройте файл `docs/ru/admin-ui/admin-ui-prototype-demo-script.md`
- нажмите `Ctrl+Shift+V` (Preview)
- или команду: “Markdown: Open Preview to the Side”

## 2) Состояния ошибок и пустых данных

Сценариев `?scenario=...` больше нет. Вручную деградации воспроизводятся так: остановить backend (ошибка сети), задать неверный `VITE_ADMIN_TOKEN` (401/403), открыть экран на пустой базе. Автоматически 500/403/401, пустые списки, integrity warning/critical и медленный ответ создаёт Playwright через `page.route` (`admin-ui/e2e/states.spec.ts`); запуск — `scripts/verify_admin_e2e.ps1`.

## 3) Чеклист по экранам

### Dashboard
- Проверить: Health/DB/Migrations карточки отрисовываются, без падений.
- Проверить: блок “Trustline bottlenecks” показывает топ узких мест (подсветка по порогу).
- Проверить: блок “Incidents over SLA” показывает транзакции, которые просрочили SLA.
- Проверить: кнопки “View all” ведут на соответствующие экраны, сохраняя текущие query-параметры.

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
- Отдельной страницы нет: `/feature-flags` перенаправляет на `/config`, флаги правятся там как ключи конфигурации (2026-10-07, 032 S4).

### Equivalents
- Проверить: список активных, toggle “Include inactive”.
- Проверить: бейдж “Used by …” подгружается лениво при hover (и кэшируется).
- Проверить: для `inactive` equivalents доступна кнопка Delete:
	- prompt причины (reason) обязателен;
	- если equivalent используется (trustlines/incidents) — UI показывает conflict (409) и НЕ удаляет.

### Integrity
- Проверить: статус грузится.
- Проверить: Verify подтверждается диалогом; отмена не вызывает запрос.

## 4) Чеклист по состояниям (быстро)

- Пустая база: экраны списков показывают `ElEmpty` вместо таблиц.
- Backend остановлен или отвечает 500: на затронутых экранах виден «error» alert/сообщение.
- Неверный admin-токен (403/401): admin-экраны деградируют понятной ошибкой.
- Медленный ответ: видны skeleton/loading и нет «дерганий» при переключении.

Ролей в UI нет (переключатель `admin/operator/auditor` удалён 2026-10-07): доступ определяет admin-токен, его проверяет backend.

## 5) Где лежат данные

Данные приходят с backend-а. База наполняется рецептом сообщества (`seeds/communities/<id>/community.json` и `recipe.json`) командой `python scripts/seed_db.py --source recipe --community <id>`; подробнее — [../seeds/README.md](../seeds/README.md). Пакета фикстур Admin UI больше нет.
