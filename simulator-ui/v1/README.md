# GEO Simulator UI v1 — legacy prototype

**Статус:** историческая read-only surface. Она исключена из обычных build/test
gates и не является поддерживаемым entrypoint. Код сохранён для git-history и
сравнения раннего прототипа; product changes здесь требуют отдельного
cleanup/migration slice.

Активный клиент находится в [`../v2/`](../v2/README.md), а актуальная RU-навигация
по Simulator — в
[`../../docs/ru/simulator/frontend/README.md`](../../docs/ru/simulator/frontend/README.md).

Старые команды запуска и ссылки удалены из этого README, потому что они указывали
на неверные каталоги и создавали конкурирующий путь запуска. Используйте root
scripts или npm scripts из `simulator-ui/v2/package.json`.
