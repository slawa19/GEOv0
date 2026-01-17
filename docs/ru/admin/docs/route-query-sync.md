# Admin UI — синхронизация фильтров с `route.query`

Эта заметка фиксирует «правило» для страниц Admin UI, где фильтры живут в URL (query) и в локальном состоянии (refs).

Цели:
- стабильная навигация без «мелькания»/двойных перезагрузок;
- строгая типизация и предсказуемость query;
- единая политика переносимых параметров между экранами.

---

## 1) Политика переносимых параметров

По умолчанию между страницами переносим **только** `scenario`.

- Используем `carryScenarioQuery(route.query)`.
- Не прокидываем «случайные» ключи из `route.query` дальше по приложению.

Причина: иначе фильтры одной страницы начинают влиять на другую (и это же часто становится триггером для лишних `router.replace()` и повторных загрузок).

---

## 2) Нормализация query

Не работаем напрямую с `route.query.*` как со строками.

- Для чтения: `readQueryString(route.query.someKey)`
- Для записи: `toLocationQueryRaw({ ... })`

Это устраняет проблемы со строгими типами Vue Router (`LocationQueryValue | LocationQueryValue[] | null`) и делает поведение одинаковым.

---

## 3) Двухсторонний sync: главный анти‑паттерн

Типовой баг (как было с «PID → Participants», когда страница «перегружается 2 раза»):

1) При навигации/маунте срабатывает watcher на `route.query` и **применяет query → refs**.
2) Эти присваивания триггерят watcher на refs, который считает изменения «пользовательскими» и делает:
   - лишний `router.replace()` и/или
   - второй `load()`.

Итог: визуальное «мелькание», двойные запросы, иногда скачок скролла.

---

## 4) Каноническое решение: `useRouteHydrationGuard`

Для страниц, где есть оба направления синхронизации:
- `route.query → refs` (гидратация)
- `refs → route.query` (пользовательские изменения)

используем `useRouteHydrationGuard`.

### 4.1 Шаблон

1) В начале страницы:

```ts
const route = useRoute()
const router = useRouter()

const { isApplying: applyingRouteQuery, isActive: isThisRoute, run: withRouteHydration } =
  useRouteHydrationGuard(route, '/participants')
```

2) При применении query → refs оборачиваем присваивания:

```ts
withRouteHydration(() => {
  q.value = readQueryString(route.query.q).trim()
  status.value = readQueryString(route.query.status).trim().toLowerCase()
})
```

3) В watcher’ах на refs (refs → query и/или reload) добавляем guard:

```ts
watch([q, status], () => {
  if (applyingRouteQuery.value) return
  // syncFiltersToRouteQuery()
  // debouncedReload()
})
```

### 4.2 Почему guard держится «до следующей microtask»

Vue может запланировать реакции watcher’ов не строго синхронно с присваиванием. Поэтому guard держится ещё одну microtask — чтобы изменения, вызванные «гидратацией маршрута», не перепутались с вводом пользователя.

---

## 5) Защита от `router.replace()` после ухода со страницы

Если страница делает `router.replace({ query: ... })`, обязательно проверяем, что мы всё ещё на этой странице:

- через `isActive` из `useRouteHydrationGuard`, либо
- через явную проверку пути.

Иначе при клике по ссылке на другую страницу старый watcher может успеть сделать `router.replace()` уже в новом маршруте.

---

## 6) Где смотреть примеры в коде

- `Participants` — query‑фильтры + reload: `admin-ui/src/pages/ParticipantsPage.vue`
- `Trustlines` — несколько фильтров + threshold (UI‑only): `admin-ui/src/pages/TrustlinesPage.vue`
- `Liquidity` — фильтры без reload: `admin-ui/src/pages/LiquidityPage.vue`
- `Graph` — фильтры без reload (но с rebuild графа): `admin-ui/src/pages/GraphPage.vue`
- общий guard: `admin-ui/src/composables/useRouteHydrationGuard.ts`
