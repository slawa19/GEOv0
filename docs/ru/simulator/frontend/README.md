# Simulator Frontend (RU)

Документы в этой папке описывают игровой интерфейс симулятора (Vue 3).

## SoT (Single Source of Truth)

- Основные спецификации (актуальные):
  - [docs/specs/GEO-game-interface-spec.md](docs/specs/GEO-game-interface-spec.md)
  - [docs/specs/GEO-visual-demo-fast-mock.md](docs/specs/GEO-visual-demo-fast-mock.md)
- Контракт API (snapshot/events + `viz_*`):
  - [docs/api.md](docs/api.md)

Историческая (архивная) версия Phase 1 tech spec:
- [docs/archive/geo-simulator-phase1-tech-spec.md](docs/archive/geo-simulator-phase1-tech-spec.md)

Если есть расхождения между документами — руководствоваться SoT.

## Актуальные референсы/контекст (не архивировать)

- [Игровой интерфейс симулятора GEO.md](%D0%98%D0%B3%D1%80%D0%BE%D0%B2%D0%BE%D0%B9%20%D0%B8%D0%BD%D1%82%D0%B5%D1%80%D1%84%D0%B5%D0%B9%D1%81%20%D1%81%D0%B8%D0%BC%D1%83%D0%BB%D1%8F%D1%82%D0%BE%D1%80%D0%B0%20GEO.md)
- [Игровой интерфейс (Стек Vue 3).md](%D0%98%D0%B3%D1%80%D0%BE%D0%B2%D0%BE%D0%B9%20%D0%B8%D0%BD%D1%82%D0%B5%D1%80%D1%84%D0%B5%D0%B9%D1%81%20(%D0%A1%D1%82%D0%B5%D0%BA%20Vue%203).md) — заметки по реализации

## “Короткие” доки для разработки (конвенции)

- Визуальный язык (цвета/ключи/приоритеты): [docs/visual-language.md](docs/visual-language.md)
- FX playbook (эффекты, правила композиции): [docs/fx-playbook.md](docs/fx-playbook.md)
- Правила рендера графа (узлы/рёбра/LOD): [docs/graph-rendering-rules.md](docs/graph-rendering-rules.md)

## Архив (устаревшие версии)

- Исторические документы/черновики сложены в:
  - [archive/](archive/)
  - [archive/README.md](archive/README.md)

Остальные файлы могут содержать дубликаты/черновики (исторические версии).
