# GEO PWA Client — Детальная спецификация (Blueprint)

**Версия:** 0.3  
**Статус:** Blueprint для реализации без «додумывания»  
**Стек (MVP):** Vue.js 3 (Vite), Pinia. UI-слой: Tailwind CSS (опционально).  

Документ привязан к текущему контракту API: `api/openapi.yaml` + `docs/ru/04-api-reference.md`.

---

## 1. Цели и границы (Scope)

### 1.1. Цели (MVP)
- Создать/восстановить локальный кошелёк (Ed25519 ключи) и подключиться к выбранному Hub.
- Показать балансы по эквивалентам, доступность платежа и историю.
- Управлять линиями доверия (создать/обновить/закрыть) и контактами.
- Отправлять платежи по QR/Deeplink и показывать детали транзакции.

### 1.2. Не-цели (в этой версии)
- Полноценный офлайн-режим для операций (в офлайне только read-only кэш).
- Управление несколькими кошельками одновременно.

---

## 2. Информационная архитектура (Sitemap)

Приложение — SPA с нижней навигацией (Tab Bar).

### 2.1. Карта экранов

1) **Auth / Onboarding (вне Tab Bar):**
- `Welcome` — выбор: «Создать кошелёк» / «Восстановить».
- `KeyGeneration` — создание ключей.
- `SeedBackup` — показ фразы/экспорта + тест-подтверждение.
- `WalletUnlock` — ввод PIN/пароля для расшифровки локального секрета.
- `HubConnect` — ввод Base URL Hub + вход (Challenge-Response).
- `SessionExpired` — истёкший JWT/refresh, требуется повторный вход.

2) **Main Tabs:**
- `Home` (Dashboard) — балансы по эквивалентам + быстрый QR.
- `Trust` — список контактов/линий доверия.
- `Activity` — история операций (платежи и связанные события).
- `Settings` — PID/QR профиля, Hub, экспорт/бэкап, безопасность.

3) **Вспомогательные экраны (из табов, но отдельными роутами):**
- `ScanQR` — сканирование QR/вставка строки.
- `PaymentCompose` — отправка платежа (получатель, эквивалент, сумма, preflight).
- `PaymentConfirm` — подтверждение подписи и отправка.
- `PaymentResult` — успех/ошибка + CTA в детали.
- `TransactionDetails` — детали транзакции по `tx_id`.
- `ContactDetails` — профиль участника + trustlines (в разрезе эквивалентов).
- `TrustlineEdit` — создание/изменение/закрытие trustline.
- `EquivalentPicker` — выбор эквивалента (список + поиск + ручной ввод).

---

## 3. User Flows (Сценарии)

### 3.1. Создание кошелька и вход в Hub
1) `Welcome` → «Создать кошелёк».
2) `KeyGeneration`: генерируется Ed25519 ключевая пара.
3) `SeedBackup`: пользователь фиксирует seed/экспорт и проходит тест.
4) `WalletUnlock`: пользователь задаёт PIN/пароль (для шифрования локального секрета).
5) `HubConnect`: ввод `hub_base_url`.
6) Вход в Hub через challenge-response (подробно в разделе API mapping).
7) Переход в `Home`.

### 3.2. Восстановление кошелька
1) `Welcome` → «Восстановить».
2) Импорт seed/экспорт.
3) `WalletUnlock`: установка нового PIN/пароля (перешифрование локального секрета).
4) `HubConnect`: вход через challenge-response.

### 3.3. Отправка платежа через QR/Deeplink
1) Пользователь открывает `ScanQR` (кнопка на `Home`).
2) Сканирует QR.
3) `PaymentCompose` автозаполняется (PID, эквивалент/сумма — если есть).
4) На вводе суммы выполняется preflight (`/payments/capacity`), показывается «Доступно».
5) `PaymentConfirm`: финальное подтверждение → подпись → `POST /payments`.
6) `PaymentResult` → `TransactionDetails`.

### 3.4. Создание/обновление линии доверия
1) `Trust` → «Добавить контакт» (поиск/QR).
2) `ContactDetails` → «Установить лимит».
3) `TrustlineEdit`: выбрать эквивалент, задать лимит, подтвердить подпись.
4) `POST /trustlines` или `PATCH /trustlines/{id}`.

---

## 4. Данные и локальное хранение

### 4.1. Принципы
- Локально хранится только то, что нужно для UX (кэш + зашифрованный секрет).
- Приватный ключ и seed никогда не отправляются в сеть.

### 4.2. Хранилища
- **IndexedDB** (нормативно):
    - `wallet.v1` — зашифрованный секрет + параметры KDF.
    - `cache.v1` — read-only кэш (balances, trustlines, activity) с привязкой к `hub_base_url`.
- **Memory** (runtime):
    - расшифрованный приватный ключ (на время сессии)
    - `access_token`

---

## 5. QR/Deeplink формат (v1, нормативно)

### 5.1. Общие правила
- QR payload — UTF-8 строка.
- Любые параметры URL должны быть percent-encoded.
- Клиент обязан валидировать формат до действий.

### 5.2. Схема URI

Поддерживаются минимум два типа:

1) **Профиль участника**
```
geo://v1/participant?pid={PID}&hub={HUB_BASE_URL?}
```

2) **Запрос платежа**
```
geo://v1/pay?to={PID}&equivalent={CODE?}&amount={AMOUNT?}&memo={TEXT?}&hub={HUB_BASE_URL?}
```

Пояснения:
- `hub` — опционально. Если указан и отличается от текущего, UI должен показать предупреждение и предложить переключить Hub.
- `amount` — строка суммы (`"100.00"`), либо отсутствует.
- `memo` — опционально, только для отображения (не обязательно отправлять на сервер).

### 5.3. Ошибки формата
- Если payload не начинается с `geo://v1/` → показать «Неподдерживаемый QR».
- Если `pid`/`to` не проходит валидацию PID → показать «Некорректный PID».

---

## 6. Security Model (нормативно)

### 6.1. Ключи и идентификаторы
- Ed25519 keypair генерируется на клиенте.
- PID вычисляется как `base58(sha256(pubkey))`.
- Серверная аутентификация: challenge-response (подписывается приватным ключом).

### 6.2. Что такое «seed» в контексте UX
- Если используется seed-фраза/экспорт, это приватный материал, достаточный для восстановления приватного ключа.
- UX должен называть это «Секрет восстановления» и явно предупреждать о рисках.

### 6.3. Шифрование секрета на устройстве
- Секрет (seed/privkey) хранится **только зашифрованным**.
- KDF: PBKDF2-HMAC-SHA256 (WebCrypto), параметры:
    - `salt`: 16+ bytes (random)
    - `iterations`: минимум 200_000 (может быть выше на десктопе)
- Шифр: AES-GCM:
    - `iv`: 12 bytes (random)
    - `ciphertext`: bytes

В IndexedDB хранить объект (пример логический):
```json
{
    "version": 1,
    "kdf": {"name": "PBKDF2", "hash": "SHA-256", "salt": "base64...", "iterations": 200000},
    "cipher": {"name": "AES-GCM", "iv": "base64..."},
    "ciphertext": "base64..."
}
```

### 6.4. Токены
- `access_token` хранить в памяти.
- `refresh_token` хранить в IndexedDB (желательно шифровать тем же ключом, что и секрет).
- При `401` клиент делает одну попытку `POST /auth/refresh`, затем либо повторяет запрос, либо переводит в `SessionExpired`.

---

## 7. API Mapping (экран → endpoint → поля → кэш)

Примечание: по OpenAPI действует envelope `{success,data}`. В клиенте должен быть единый `unwrap()` для извлечения `data`.

### 7.1. Auth / Hub

**HubConnect (вход):**
- `POST /auth/challenge` → `{challenge, expires_at}`
- `POST /auth/login` (подпись challenge) → `{access_token, refresh_token, expires_in, participant}`

**Регистрация участника (первый запуск):**
- `POST /participants` → `{pid, display_name, status, created_at}`

**Обновление токена:**
- `POST /auth/refresh` → `{access_token, refresh_token, expires_in}`

Кэш:
- `hub_base_url` (настройка)
- `participant` (в cache.v1)

### 7.2. Home (Dashboard)

Данные:
- `GET /balance` → `BalanceSummary.equivalents[]`:
    - `code` (код)
    - `net_balance`
    - `available_to_spend`, `available_to_receive`

Кэш:
- `balance_summary` на `hub_base_url`, TTL 60s (при онлайне обновлять pull-to-refresh).

### 7.3. Activity (История)

Список:
- `GET /payments?direction=all&equivalent={code?}&status={...}&page={...}` → `PaymentResult[]`

Детали:
- `GET /payments/{tx_id}` → `PaymentResult`

Кэш:
- страницы activity кэшировать (read-only), TTL 5–10 минут.

### 7.4. ScanQR / PaymentCompose / PaymentConfirm

Preflight (проверка возможности):
- `GET /payments/capacity?to={pid}&equivalent={code}&amount={amount}` →
    - `can_pay`, `max_amount`, `routes_count`, `estimated_hops`

Диагностика (по запросу пользователя, не по умолчанию):
- `GET /payments/max-flow?to={pid}&equivalent={code}` → `max_amount` + `paths[]` + `bottlenecks[]`

Создание:
- `POST /payments` → `PaymentResult` (в т.ч. `status`, `routes[]` при успехе, либо `error` при ABORTED)

### 7.5. Trust / ContactDetails / TrustlineEdit

Контакты/участники:
- `GET /participants/search?q={...}&page={...}&per_page={...}`
- `GET /participants/{pid}`

Trustlines:
- `GET /trustlines?direction={...}&equivalent={...?}&status={...?}&page={...}`
- `POST /trustlines`
- `PATCH /trustlines/{id}`
- `DELETE /trustlines/{id}`

### 7.6. Settings

Профиль:
- `GET /participants/me`
- `PATCH /participants/me`

### 7.7. WebSocket (реальное время)

Подключение:
- `wss://{hub_base_url}/api/v1/ws?token={access_token}`

Подписка на события (минимальный набор):
- `payment.received` — входящий платёж
- `trustline.updated` — изменение линии доверия
- `clearing.completed` — выполнен клиринг

Heartbeat:
- Клиент отправляет `ping` каждые 30 секунд при отсутствии исходящего трафика.
- Если 90 секунд нет входящих сообщений — считать соединение разорванным.

Reconnect стратегия (нормативно, согласовано с `04-api-reference.md` §7.6):
- Переподключаться с exponential backoff: 1s, 2s, 5s, 10s, 30s.
- После переподключения: заново `subscribe`, сверить состояние через REST.
- Готовность к дубликатам и пропускам событий (at-most-once доставка).

---

## 8. Equivalent Picker (источник данных)

### 8.1. Источник (приоритет)
1) `GET /equivalents` (если реализовано на Hub) — нормальный справочник.
2) Иначе: объединение кодов из `GET /balance`, `GET /trustlines` (локально известные эквиваленты).
3) Иначе: ручной ввод (пользователь вводит произвольный код).

### 8.2. Валидация кода эквивалента
- `CODE` должен быть строкой длиной 2..16.
- Допустимые символы: латиница/цифры/`_`/`-`.
- Рекомендация: показывать предупреждение, если код не из справочника.

---

## 9. UI States Matrix (нормативно)

### 9.1. Общие состояния
- **Loading:** skeleton/loader + запрет действий, которые требуют данных.
- **Empty:** объясняющий текст + CTA.
- **Error:** человекочитаемое сообщение + «Повторить».
- **Offline:** баннер + блокировка операций записи.

### 9.2. По экранам (минимальный набор)

**Home:**
- Loading: «Загружаем баланс…»
- Empty: «Пока нет эквивалентов. Создайте trustline или получите платёж.» → CTA: «Добавить доверие»
- Error: «Не удалось загрузить баланс» → CTA: «Повторить»

**Trust:**
- Empty: «Нет линий доверия» → CTA: «Добавить контакт»

**Activity:**
- Empty: «Нет операций»

**PaymentCompose:**
- Если `can_pay=false`: показать «Недостаточно доступной ёмкости» + кнопку «Показать максимум» (вызывает `/payments/max-flow`).

**SessionExpired:**
- Текст: «Сессия истекла. Войдите снова.»
- CTA: «Разблокировать кошелёк» → `WalletUnlock` → `HubConnect`.

---

## 10. Инструкции для ИИ (Prompts)

Принцип: генерируем компоненты, не задавая «новых цветов/тем», используем базовую палитру и существующие дизайн-токены проекта.

### 10.1. Dashboard
> "Создай Vue 3 компонент Dashboard. Сверху — список карточек по эквивалентам (code, net_balance, available_to_spend). Ниже — кнопка 'Сканировать QR' и список последних операций (до 5). Добавь состояния loading/empty/error и обработку offline (read-only)."

### 10.2. Payment compose
> "Создай компонент PaymentCompose. Поля: получатель PID (readonly), эквивалент (через EquivalentPicker), сумма. На вводе суммы делай запрос /payments/capacity и показывай can_pay/max_amount/routes_count/estimated_hops. Кнопка 'Отправить' ведёт на PaymentConfirm."

### 10.3. Key / wallet storage
> "Напиши сервис на TypeScript для хранения секрета кошелька в IndexedDB в зашифрованном виде: PBKDF2(SHA-256) + AES-GCM. Функции: setupPin(pin, secret), unlock(pin) -> secret, lock(), isUnlocked(), rotatePin(oldPin,newPin)."

