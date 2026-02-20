# Simulator (RU)

Входная точка для документации симулятора: **backend (runner + SSE + интеграция с core)** и **frontend (UI/визуализация)**.

## Быстрые ссылки (актуальные)

- Сценарии и движок запуска (обзор + техподробности): [scenarios-and-engine.md](scenarios-and-engine.md)
- Online-анализ проблем экономики сети (insights/уведомления): [network-economy-analyzer-spec.md](network-economy-analyzer-spec.md)
- Поведенческая модель экономики (real mode: behaviorProfiles/events): [backend/behavior-model-spec.md](backend/behavior-model-spec.md)

- Контракт API симулятора (snapshot + events + `viz_*`): [frontend/docs/api.md](frontend/docs/api.md)
- Инструкция пользователя по HUD (элементы + поведение): [frontend/docs/hud-user-guide.md](frontend/docs/hud-user-guide.md)
- Backend: интеграция с платежами/клирингом и каноничные контракты: [backend/payment-integration.md](backend/payment-integration.md)
- Backend: протокол realtime (SSE/WS payload): [backend/ws-protocol.md](backend/ws-protocol.md)
- Backend: алгоритм runner: [backend/runner-algorithm.md](backend/runner-algorithm.md)
- Backend: адаптивная политика клиринга (feedback-control): [backend/adaptive-clearing-policy.md](backend/adaptive-clearing-policy.md)
- Backend/UI: backend-driven demo mode (one pipeline) + Clearing Viz v2: [backend/backend-driven-demo-mode-spec.md](backend/backend-driven-demo-mode-spec.md)
- **Interact Mode** ✅ — интерактивный режим (`?ui=interact`): ручные платежи, управление trustlines, клиринг, SystemBalance. 7 backend action endpoints + frontend UI. Руководство пользователя: [frontend/docs/interact-mode-user-guide.md](frontend/docs/interact-mode-user-guide.md)
- **Anonymous visitors** ✅ — cookie-based сессии (`geo_sim_sid`, HMAC-SHA256) позволяют анонимным посетителям запускать свои simulator run'ы без регистрации. Per-owner изоляция, admin control plane, CSRF защита. **Полностью реализовано** (backend unit + frontend vitest — green). См. [спецификацию](backend/anonymous-visitors-cookie-runs-spec.md) и [acceptance criteria](backend/acceptance-criteria.md).
- Спеки UI/визуала:
  - [frontend/docs/specs/GEO-game-interface-spec.md](frontend/docs/specs/GEO-game-interface-spec.md)
  - [frontend/docs/specs/GEO-visual-demo-fast-mock.md](frontend/docs/specs/GEO-visual-demo-fast-mock.md)
- UI perf/quality policy (software-only / low FPS): [frontend/docs/performance-and-quality-policy.md](frontend/docs/performance-and-quality-policy.md)
- Референсы экранов: [frontend/screen-prototypes/](frontend/screen-prototypes/)

## Навигация

- Backend: [backend/](backend/)
- Frontend: [frontend/README.md](frontend/README.md)

