# Локальный PostgreSQL без Docker и без прав администратора

Статус: Stable
Область: backend
Последнее обновление: 2026-08-21

Как за несколько минут поднять PostgreSQL, пригодный для `-BackendMarker postgres`, на машине без
Docker и без прав администратора. Написано после фактического подъёма 2026-08-20; все ловушки ниже —
не гипотетические, каждая встречена в реальном прогоне.

**Когда это нужно.** Дефолтный tier тестов идёт на SQLite, и его достаточно для большинства правок.
Postgres обязателен там, где SQLite ничего не доказывает: блокировки, изоляция, конкурентность,
поведение `NUMERIC`, миграции. См. `AGENTS.md` §4 и §8.

**Когда это НЕ нужно.** Если на машине есть Docker — используйте штатный путь из
[`README.md`](../../../README.md), раздел «Postgres-backed backend tests»: `docker compose up -d db`.
Этот документ — альтернатива для машин без Docker, а не замена ему.

## 0. Ловушки, из-за которых теряют время

Прочитайте до начала — каждая стоила отдельного разбирательства.

1. **Инсталлятор требует UAC.** GUI-диалог в агентской сессии не будет закрыт никем и повесит прогон
   до таймаута. Поэтому берутся **портативные бинарники**, а не installer.
2. **`pg_ctl start` держит консоль.** Запущенный напрямую, он не возвращает управление, а сервер
   становится дочерним процессом этой оболочки. Когда оболочку убивают (таймаут, teardown агента,
   конец сессии) — **сервер умирает вместе с ней**. Запускать только отвязанным процессом (§3).
3. **Версия должна совпадать с CI.** `.github/workflows/quality.yml` пинит `postgres:16`. Ставить
   «последнее» значит получить локальные гейты, не представляющие CI.
4. **Сброс схемы требует `GEO_TEST_ALLOW_DB_RESET=1`.** Это предохранитель, а не препятствие: без
   него `verify_local.ps1` откажется трогать Postgres. CI выставляет тот же флаг.
5. **Дефолтному unit-tier нельзя подавать `TEST_DATABASE_URL`.** Он рассчитан на SQLite; Postgres в
   этой переменной даст ложную красноту на тестах, которые к нему отношения не имеют.

## 1. Скачать бинарники

Версию берите из `quality.yml` (сейчас 16). Проверьте доступность конкретной сборки — состав
зеркала меняется:

```bash
for v in 16.9-1 16.8-1 16.6-1 16.4-1; do
  URL="https://get.enterprisedb.com/postgresql/postgresql-$v-windows-x64-binaries.zip"
  echo "$v -> $(curl -s -o /dev/null -w '%{http_code}' -L --max-time 25 -I "$URL")"
done
```

Первая, ответившая `200`, годится. Скачать и распаковать (~300 МБ, распаковка занимает минуты):

```powershell
$tools = "$env:USERPROFILE\tools"
New-Item -ItemType Directory -Force $tools | Out-Null
curl.exe -sSL -o "$tools\pg16.zip" "https://get.enterprisedb.com/postgresql/postgresql-16.9-1-windows-x64-binaries.zip"
Expand-Archive -LiteralPath "$tools\pg16.zip" -DestinationPath "$tools\pg16" -Force
& "$tools\pg16\pgsql\bin\postgres.exe" --version   # ожидается 16.x
```

Контрольную сумму зеркало не публикует рядом с файлом; если нужна проверка целостности — сверяйте
с подписями на `postgresql.org`. Для одноразовой тестовой БД риск принят осознанно.

## 2. Инициализировать кластер

Роль и база берутся **из CI**, чтобы DSN совпадал байт-в-байт: пользователь `geo`, пароль `geo`,
база `geov0_test_ci`.

```powershell
$bin  = "$env:USERPROFILE\tools\pg16\pgsql\bin"
$data = "$env:USERPROFILE\tools\pgdata"
$pw   = "$env:USERPROFILE\tools\.pgpw"
Set-Content -Path $pw -Value "geo" -NoNewline -Encoding ascii
& "$bin\initdb.exe" -D $data -U geo -A scram-sha-256 --pwfile=$pw -E UTF8 --locale=C
Remove-Item $pw -Force
```

`--locale=C` — чтобы сортировка не зависела от локали машины; `-U geo` делает суперпользователем
сразу нужную роль, так что отдельный `CREATE ROLE` не потребуется.

## 3. Запустить сервер — отвязанным процессом

**Это тот шаг, где ошибаются.** Запуск должен вернуть управление, а сервер — пережить оболочку:

```powershell
$bin  = "$env:USERPROFILE\tools\pg16\pgsql\bin"
$data = "$env:USERPROFILE\tools\pgdata"
$log  = "$env:USERPROFILE\tools\pg16.log"
Start-Process -FilePath "$bin\pg_ctl.exe" `
  -ArgumentList @('-D', $data, '-l', $log, '-o', '"-p 5432 -h 127.0.0.1"', 'start') `
  -WindowStyle Hidden
```

Проверка, что сервер жив (а не «команда не упала»):

```powershell
$env:PGPASSWORD = "geo"
& "$bin\psql.exe" -h 127.0.0.1 -p 5432 -U geo -d postgres -tAc "SELECT version();"
```

Остановка: `& "$bin\pg_ctl.exe" -D $data stop -m fast`.

Если после перезагрузки машины сервер не поднят — повторите только этот шаг, кластер сохраняется.

## 4. Создать базу и накатить схему

Схему накатывайте **каноническим entrypoint**, а не голым `alembic upgrade`: CI использует именно
его, и расхождение «локально прошло, на CI упало» рождается на этой развилке.

```bash
export PGPASSWORD=geo
BIN="$USERPROFILE/tools/pg16/pgsql/bin"
"$BIN/createdb.exe" -h 127.0.0.1 -p 5432 -U geo geov0_test_ci

cd /path/to/GEOv0
export DATABASE_URL="postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_ci"
export ENV=test
python scripts/check_alembic_heads.py      # голова обязана быть единственной
bash docker/docker-entrypoint.sh true      # применяет миграции
```

## 5. Прогнать тесты

```powershell
$env:TEST_DATABASE_URL       = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_ci"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
.\scripts\verify_local.ps1 -TaskSlug <ваш_слаг> -BackendOnly -BackendMarker postgres -BackendSelector tests/integration
```

Гейт печатает `Test database guard passed (backend=postgresql, database='geov0_test_ci')` — это и
есть подтверждение, что прогон шёл против Postgres, а не против SQLite. **Смотрите на эту строку, а
не на «passed»:** зелёный SQLite-прогон легко принять за Postgres-evidence.

Перед прогоном дефолтного tier переменную снимите:

```powershell
Remove-Item Env:TEST_DATABASE_URL, Env:GEO_TEST_ALLOW_DB_RESET -ErrorAction SilentlyContinue
```

## 6. Параллельные агенты

`AGENTS.md` §7 запрещает делить изменяемую БД. Каждому агенту — своя база, имя от его слага:

```bash
"$BIN/createdb.exe" -h 127.0.0.1 -p 5432 -U geo "geov0_test_$TASK_SLUG"
```

и соответствующий `TEST_DATABASE_URL`. Кластер при этом общий — это допустимо, разделены базы.
После работы: `"$BIN/dropdb.exe" -h 127.0.0.1 -p 5432 -U geo "geov0_test_$TASK_SLUG"`.

## 7. Перенос на другую машину

Переносится только процедура, не данные. На новой машине: §1 → §2 → §3 → §4, порядка десяти минут,
из них большая часть — скачивание и распаковка. Каталог `pgdata` копировать между машинами не
нужно и не рекомендуется: база одноразовая, а несовпадение версии или локали даст отказ запуска.

## Что этот документ не покрывает

Продовое развёртывание (см. [`05-deployment.md`](../05-deployment.md)), настройку под нагрузку,
резервное копирование, TLS и доступ по сети: кластер слушает только `127.0.0.1` и предназначен
исключительно для одноразовых тестовых баз.
