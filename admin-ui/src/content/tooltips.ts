export type TooltipKey =
  | 'nav.dashboard'
  | 'nav.integrity'
  | 'nav.incidents'
  | 'nav.trustlines'
  | 'nav.graph'
  | 'nav.participants'
  | 'nav.config'
  | 'nav.featureFlags'
  | 'nav.auditLog'
  | 'nav.equivalents'
  | 'dashboard.api'
  | 'dashboard.db'
  | 'dashboard.migrations'
  | 'dashboard.bottlenecks'
  | 'dashboard.incidentsOverSla'
  | 'dashboard.recentAudit'
  | 'participants.pid'
  | 'participants.displayName'
  | 'participants.type'
  | 'participants.status'
  | 'trustlines.eq'
  | 'trustlines.from'
  | 'trustlines.to'
  | 'trustlines.limit'
  | 'trustlines.used'
  | 'trustlines.available'
  | 'trustlines.status'
  | 'trustlines.createdAt'
  | 'incidents.txId'
  | 'incidents.state'
  | 'incidents.initiator'
  | 'incidents.eq'
  | 'incidents.age'
  | 'incidents.sla'
  | 'graph.eq'
  | 'graph.status'
  | 'graph.threshold'
  | 'graph.layout'
  | 'graph.type'
  | 'graph.minDegree'
  | 'graph.labels'
  | 'graph.incidents'
  | 'graph.hideIsolates'
  | 'graph.search'
  | 'graph.zoom'
  | 'graph.actions'
  | 'graph.spacing'
  | 'graph.legend'
  | 'audit.timestamp'
  | 'audit.actor'
  | 'audit.role'
  | 'audit.action'
  | 'audit.objectType'
  | 'audit.objectId'
  | 'audit.reason'

export type TooltipLink = {
  label: string
  to: { path: string; query?: Record<string, string> }
}

export type TooltipContent = {
  title: string
  body: string[]
  links?: TooltipLink[]
}

export type TooltipLocale = 'en' | 'ru'

// Keep content short and review-friendly.
export const TOOLTIPS_EN: Record<TooltipKey, TooltipContent> = {
  // Navigation section tooltips
  'nav.dashboard': {
    title: 'Dashboard',
    body: ['System health overview: API, DB, migrations status.', 'Shows trustline bottlenecks and incidents over SLA.'],
  },
  'nav.integrity': {
    title: 'Integrity',
    body: ['Data integrity checks: zero-sum balances, limits consistency.', 'Automated validation of network invariants.'],
  },
  'nav.incidents': {
    title: 'Incidents',
    body: ['Stuck transactions requiring manual intervention.', 'Sorted by age; admin can force-abort.'],
  },
  'nav.trustlines': {
    title: 'Trustlines',
    body: ['Credit lines between network participants.', 'Filter by equivalent, creditor, debtor, status.'],
  },
  'nav.graph': {
    title: 'Network Graph',
    body: ['Visual representation of the trust network.', 'Interactive graph with filtering and zoom.'],
  },
  'nav.participants': {
    title: 'Participants',
    body: ['Manage network participants.', 'View details, freeze/unfreeze accounts.'],
  },
  'nav.config': {
    title: 'Config',
    body: ['System configuration: limits, routing, policies.', 'Some keys require service restart.'],
  },
  'nav.featureFlags': {
    title: 'Feature Flags',
    body: ['Runtime feature toggles.', 'Enable/disable experimental features.'],
  },
  'nav.auditLog': {
    title: 'Audit Log',
    body: ['Administrative actions log.', 'Who, what, when, and why for all changes.'],
  },
  'nav.equivalents': {
    title: 'Equivalents',
    body: ['Currency/unit catalog (UAH, USD, POINT).', 'Precision and description for each.'],
  },

  'dashboard.api': {
    title: 'API',
    body: ['Basic service health from /api/v1/health.', 'Used to validate that the backend is reachable.'],
  },
  'dashboard.db': {
    title: 'DB',
    body: ['Database reachability/latency from /api/v1/health/db.', 'Helps detect DB connectivity issues.'],
  },
  'dashboard.migrations': {
    title: 'Migrations',
    body: ['Schema migration status from /api/v1/admin/migrations.', 'Should be “up to date” on healthy deployments.'],
  },
  'dashboard.bottlenecks': {
    title: 'Trustline bottlenecks',
    body: ['Trustlines where available / limit is below the threshold.', 'Threshold is a UI control and uses decimal-safe math (no floats).'],
    links: [{ label: 'Open Trustlines', to: { path: '/trustlines' } }],
  },
  'dashboard.incidentsOverSla': {
    title: 'Incidents over SLA',
    body: ['Transactions stuck beyond their SLA.', 'age_seconds > sla_seconds → over-SLA.'],
    links: [{ label: 'Open Incidents', to: { path: '/incidents' } }],
  },
  'dashboard.recentAudit': {
    title: 'Recent audit log',
    body: ['Latest 10 administrative actions.', 'Shows who did what and when.'],
    links: [{ label: 'Open Audit Log', to: { path: '/audit-log' } }],
  },

  'participants.pid': {
    title: 'PID',
    body: ['Participant identifier in the GEO network.'],
  },
  'participants.displayName': {
    title: 'Name',
    body: ['Human-friendly display name (if set).'],
  },
  'participants.type': {
    title: 'Type',
    body: ['Participant category (e.g., user/org/admin depending on backend).'],
  },
  'participants.status': {
    title: 'Status',
    body: ['Account status used for access/routing decisions.', 'Typical values: active, suspended, left, deleted.'],
  },

  'trustlines.eq': {
    title: 'Equivalent',
    body: ['Equivalent (currency/unit) code, e.g. UAH, USD.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'trustlines.from': {
    title: 'from',
    body: ['Creditor PID (trustline source).'],
  },
  'trustlines.to': {
    title: 'to',
    body: ['Debtor PID (trustline destination).'],
  },
  'trustlines.limit': {
    title: 'limit',
    body: ['Trustline credit limit (decimal string).', 'Edge case: limit = 0 is allowed.'],
    links: [
      {
        label: 'Default limit (Config)',
        to: { path: '/config', query: { key: 'limits.default_trustline_limit' } },
      },
    ],
  },
  'trustlines.used': {
    title: 'used',
    body: ['Currently reserved/used amount on the trustline (decimal string).'],
  },
  'trustlines.available': {
    title: 'available',
    body: ['Remaining available amount (decimal string).', 'Shown in red when below the threshold.'],
  },
  'trustlines.status': {
    title: 'status',
    body: ['Trustline lifecycle state.', 'Typical values: active, frozen, closed.'],
  },
  'trustlines.createdAt': {
    title: 'created_at',
    body: ['Creation timestamp (ISO 8601).'],
  },

  'incidents.txId': {
    title: 'tx_id',
    body: ['Transaction identifier.'],
  },
  'incidents.state': {
    title: 'state',
    body: ['Current state of the stuck transaction.'],
  },
  'incidents.initiator': {
    title: 'initiator',
    body: ['PID of the transaction initiator.'],
  },
  'incidents.eq': {
    title: 'Equivalent',
    body: ['Equivalent involved in the transaction.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'incidents.age': {
    title: 'age',
    body: ['How long the transaction has been stuck (seconds).'],
  },
  'incidents.sla': {
    title: 'sla',
    body: ['Allowed time budget (seconds).', 'age > sla → over-SLA.'],
  },

  'graph.eq': {
    title: 'Equivalent filter',
    body: ['Filters edges by equivalent code.', 'ALL shows all equivalents present in fixtures.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'graph.status': {
    title: 'Status filter',
    body: ['Filters edges by trustline status.'],
    links: [{ label: 'Open Trustlines', to: { path: '/trustlines' } }],
  },
  'graph.threshold': {
    title: 'Bottleneck threshold',
    body: ['A trustline is a bottleneck when available / limit is below this value.', 'Uses decimal-safe math (no floats).'],
  },
  'graph.layout': {
    title: 'Layout',
    body: ['Controls how the graph is arranged.', 'fcose is good for clustered networks; grid/circle are deterministic.'],
  },
  'graph.type': {
    title: 'Participant type',
    body: [
      'Filters nodes by participant type (e.g., person vs business).',
      'Edges are shown only between same-type participants (cross-type edges are hidden).',
    ],
  },
  'graph.minDegree': {
    title: 'Minimum degree',
    body: ['Hides low-connected nodes to reduce noise.', 'Degree is computed after current filters are applied.'],
  },
  'graph.labels': {
    title: 'Labels',
    body: ['Shows display name + PID on nodes.', 'Disable for performance on large graphs.'],
  },
  'graph.incidents': {
    title: 'Incidents',
    body: ['Highlights nodes/edges related to incident initiators.', 'Dashed edges indicate the initiator side.'],
    links: [{ label: 'Open Incidents', to: { path: '/incidents' } }],
  },
  'graph.hideIsolates': {
    title: 'Hide isolates',
    body: ['When enabled, shows only participants that appear in trustlines after filtering.'],
  },
  'graph.search': {
    title: 'Search',
    body: [
      'Search by PID or participant name (partial match).',
      'Pick a suggestion to set the focus node used by Find and Fit component.',
      'If multiple matches exist, Find fits and highlights a subset; refine the query to narrow it down.',
    ],
  },
  'graph.zoom': {
    title: 'Zoom helpers',
    body: [
      'Use the slider or mouse wheel to zoom in/out.',
      'Edge thickness and label size are adjusted with zoom for readability.',
    ],
  },

  'graph.actions': {
    title: 'Graph actions',
    body: [
      'Find: centers on the focused participant (picked from search) or the last clicked node.',
      'Fit: fits the whole graph into the viewport.',
      'Re-layout: runs the selected layout algorithm again (use after changing filters/spacing).',
      'Zoom: use the slider to zoom in/out (also works with mouse wheel).',
    ],
  },

  'graph.spacing': {
    title: 'Layout spacing',
    body: ['Controls how spread-out the force layout is.', 'Higher values reduce clutter but take longer to settle.'],
  },
  'graph.legend': {
    title: 'Legend',
    body: ['Explains node/edge colors and styles used in this visualization.'],
  },

  'audit.timestamp': {
    title: 'timestamp',
    body: ['When the admin/audit event happened (ISO 8601).'],
  },
  'audit.actor': {
    title: 'actor',
    body: ['Who performed the action (actor_id).', 'May be empty for system/admin-token actions (MVP auth).'],
  },
  'audit.role': {
    title: 'role',
    body: ['Role under which the action was performed.'],
  },
  'audit.action': {
    title: 'action',
    body: ['Action name (e.g., create/update/verify).'],
  },
  'audit.objectType': {
    title: 'object',
    body: ['Domain object type affected by the action.'],
  },
  'audit.objectId': {
    title: 'object_id',
    body: ['Identifier of the affected object.'],
  },
  'audit.reason': {
    title: 'reason',
    body: ['Optional operator-provided reason for the action.'],
  },
}

export const TOOLTIPS_RU: Record<TooltipKey, TooltipContent> = {
  'nav.dashboard': {
    title: 'Дашборд',
    body: ['Обзор состояния системы: API, DB, статус миграций.', 'Показывает узкие места trustline и инциденты сверх SLA.'],
  },
  'nav.integrity': {
    title: 'Целостность',
    body: ['Проверки целостности данных: нулевой баланс, согласованность лимитов.', 'Автоматическая валидация инвариантов сети.'],
  },
  'nav.incidents': {
    title: 'Инциденты',
    body: ['Зависшие транзакции, требующие ручного вмешательства.', 'Отсортированы по возрасту; админ может принудительно отменить.'],
  },
  'nav.trustlines': {
    title: 'Трастлайны',
    body: ['Кредитные линии между участниками сети.', 'Фильтры по эквиваленту, кредитору, должнику, статусу.'],
  },
  'nav.graph': {
    title: 'Граф сети',
    body: ['Визуальное представление сети доверия.', 'Интерактивный граф с фильтрами и масштабированием.'],
  },
  'nav.participants': {
    title: 'Участники',
    body: ['Управление участниками сети.', 'Детали, заморозка/разморозка аккаунтов.'],
  },
  'nav.config': {
    title: 'Конфиг',
    body: ['Настройки системы: лимиты, маршрутизация, политики.', 'Некоторые ключи требуют перезапуска сервиса.'],
  },
  'nav.featureFlags': {
    title: 'Фиче-флаги',
    body: ['Переключатели функций в рантайме.', 'Включение/выключение экспериментальных возможностей.'],
  },
  'nav.auditLog': {
    title: 'Аудит-лог',
    body: ['Журнал административных действий.', 'Кто, что, когда и почему — по всем изменениям.'],
  },
  'nav.equivalents': {
    title: 'Эквиваленты',
    body: ['Каталог валют/единиц (UAH, USD, POINT).', 'Точность и описание для каждой.'],
  },

  'dashboard.api': {
    title: 'API',
    body: ['Базовое состояние сервиса из /api/v1/health.', 'Используется для проверки доступности бэкенда.'],
  },
  'dashboard.db': {
    title: 'DB',
    body: ['Доступность/латентность БД из /api/v1/health/db.', 'Помогает выявлять проблемы подключения к БД.'],
  },
  'dashboard.migrations': {
    title: 'Миграции',
    body: ['Статус миграций схемы из /api/v1/admin/migrations.', 'В норме должно быть “актуально” на здоровом деплое.'],
  },
  'dashboard.bottlenecks': {
    title: 'Узкие места trustline',
    body: ['Трастлайны, где available / limit ниже порога.', 'Порог — настройка UI; расчёт безопасен для decimal (без float).'],
    links: [{ label: 'Открыть трастлайны', to: { path: '/trustlines' } }],
  },
  'dashboard.incidentsOverSla': {
    title: 'Инциденты сверх SLA',
    body: ['Транзакции, застрявшие дольше SLA.', 'age_seconds > sla_seconds → сверх SLA.'],
    links: [{ label: 'Открыть инциденты', to: { path: '/incidents' } }],
  },
  'dashboard.recentAudit': {
    title: 'Последние действия',
    body: ['Последние 10 административных действий.', 'Показывает кто что сделал и когда.'],
    links: [{ label: 'Открыть аудит-лог', to: { path: '/audit-log' } }],
  },

  'participants.pid': {
    title: 'PID',
    body: ['Идентификатор участника в сети GEO.'],
  },
  'participants.displayName': {
    title: 'Имя',
    body: ['Человекочитаемое имя (если задано).'],
  },
  'participants.type': {
    title: 'Тип',
    body: ['Категория участника (напр., user/org/admin — зависит от бэкенда).'],
  },
  'participants.status': {
    title: 'Статус',
    body: ['Статус аккаунта для доступа/маршрутизации.', 'Типичные значения: active, suspended, left, deleted.'],
  },

  'trustlines.eq': {
    title: 'Эквивалент',
    body: ['Код эквивалента (валюта/единица), напр. UAH, USD.'],
    links: [{ label: 'Открыть эквиваленты', to: { path: '/equivalents' } }],
  },
  'trustlines.from': {
    title: 'from',
    body: ['PID кредитора (источник trustline).'],
  },
  'trustlines.to': {
    title: 'to',
    body: ['PID должника (получатель trustline).'],
  },
  'trustlines.limit': {
    title: 'limit',
    body: ['Кредитный лимит трастлайна (decimal-строка).', 'Крайний случай: limit = 0 допустим.'],
    links: [
      {
        label: 'Лимит по умолчанию (Конфиг)',
        to: { path: '/config', query: { key: 'limits.default_trustline_limit' } },
      },
    ],
  },
  'trustlines.used': {
    title: 'used',
    body: ['Текущая зарезервированная/использованная сумма (decimal-строка).'],
  },
  'trustlines.available': {
    title: 'available',
    body: ['Оставшаяся доступная сумма (decimal-строка).', 'Подсвечивается красным при значении ниже порога.'],
  },
  'trustlines.status': {
    title: 'status',
    body: ['Состояние жизненного цикла трастлайна.', 'Типичные значения: active, frozen, closed.'],
  },
  'trustlines.createdAt': {
    title: 'created_at',
    body: ['Время создания (ISO 8601).'],
  },

  'incidents.txId': {
    title: 'tx_id',
    body: ['Идентификатор транзакции.'],
  },
  'incidents.state': {
    title: 'state',
    body: ['Текущее состояние зависшей транзакции.'],
  },
  'incidents.initiator': {
    title: 'initiator',
    body: ['PID инициатора транзакции.'],
  },
  'incidents.eq': {
    title: 'Эквивалент',
    body: ['Эквивалент, задействованный в транзакции.'],
    links: [{ label: 'Открыть эквиваленты', to: { path: '/equivalents' } }],
  },
  'incidents.age': {
    title: 'age',
    body: ['Сколько времени транзакция зависла (секунды).'],
  },
  'incidents.sla': {
    title: 'sla',
    body: ['Допустимый бюджет времени (секунды).', 'age > sla → сверх SLA.'],
  },

  'graph.eq': {
    title: 'Фильтр эквивалента',
    body: ['Фильтрует рёбра по коду эквивалента.', 'ALL показывает все эквиваленты из фикстур.'],
    links: [{ label: 'Открыть эквиваленты', to: { path: '/equivalents' } }],
  },
  'graph.status': {
    title: 'Фильтр статуса',
    body: ['Фильтрует рёбра по статусу трастлайна.'],
    links: [{ label: 'Открыть трастлайны', to: { path: '/trustlines' } }],
  },
  'graph.threshold': {
    title: 'Порог узких мест',
    body: ['Трастлайн — узкое место, если available / limit ниже этого значения.', 'Расчёт безопасен для decimal (без float).'],
  },
  'graph.layout': {
    title: 'Раскладка',
    body: ['Управляет расположением графа.', 'fcose хорош для кластеров; grid/circle детерминированы.'],
  },
  'graph.type': {
    title: 'Тип участника',
    body: [
      'Фильтрует узлы по типу участника (например, человек vs бизнес).',
      'Рёбра показываются только между участниками одного типа (межтиповые скрываются).',
    ],
  },
  'graph.minDegree': {
    title: 'Минимальная степень',
    body: ['Скрывает слабо-связанные узлы, чтобы уменьшить шум.', 'Степень считается после применения текущих фильтров.'],
  },
  'graph.labels': {
    title: 'Подписи',
    body: ['Показывает display name + PID на узлах.', 'Отключите для производительности на больших графах.'],
  },
  'graph.incidents': {
    title: 'Инциденты',
    body: ['Подсвечивает узлы/рёбра, связанные с инициаторами инцидентов.', 'Пунктирные рёбра показывают сторону инициатора.'],
    links: [{ label: 'Открыть инциденты', to: { path: '/incidents' } }],
  },
  'graph.hideIsolates': {
    title: 'Скрыть изолятов',
    body: ['Если включено — показывает только участников, которые встречаются в трастлайнах после фильтрации.'],
  },
  'graph.search': {
    title: 'Поиск',
    body: [
      'Поиск по PID или имени участника (частичное совпадение).',
      'Выберите подсказку, чтобы задать фокус-узел для Find и Fit.',
      'Если совпадений несколько, Find центрирует и подсветит часть; уточните запрос.',
    ],
  },
  'graph.zoom': {
    title: 'Масштабирование',
    body: ['Используйте слайдер или колёсико мыши для zoom in/out.', 'Толщина рёбер и размер текста адаптируются для читабельности.'],
  },
  'graph.actions': {
    title: 'Действия графа',
    body: [
      'Find: центрирует на фокусном участнике (из поиска) или на последнем кликнутом узле.',
      'Fit: вписывает весь граф в область просмотра.',
      'Re-layout: запускает выбранный алгоритм раскладки ещё раз (после фильтров/spacing).',
      'Zoom: слайдером масштабируйте (также работает колёсико).',
    ],
  },
  'graph.spacing': {
    title: 'Плотность раскладки',
    body: ['Задаёт “разброс” силовой раскладки.', 'Большие значения уменьшают скученность, но дольше стабилизируются.'],
  },
  'graph.legend': {
    title: 'Легенда',
    body: ['Объясняет цвета и стили узлов/рёбер в этой визуализации.'],
  },

  'audit.timestamp': {
    title: 'timestamp',
    body: ['Когда произошло событие (ISO 8601).'],
  },
  'audit.actor': {
    title: 'actor',
    body: ['Кто выполнил действие (actor_id).', 'Может быть пустым для system/admin-token (MVP auth).'],
  },
  'audit.role': {
    title: 'role',
    body: ['Роль, от имени которой выполнено действие.'],
  },
  'audit.action': {
    title: 'action',
    body: ['Название действия (например, create/update/verify).'],
  },
  'audit.objectType': {
    title: 'object',
    body: ['Тип доменного объекта, который затронут действием.'],
  },
  'audit.objectId': {
    title: 'object_id',
    body: ['Идентификатор затронутого объекта.'],
  },
  'audit.reason': {
    title: 'reason',
    body: ['Необязательная причина, заданная оператором.'],
  },
}

export const TOOLTIPS = TOOLTIPS_EN

export function getTooltips(locale: TooltipLocale): Record<TooltipKey, TooltipContent> {
  return locale === 'ru' ? TOOLTIPS_RU : TOOLTIPS_EN
}

export function getTooltipContent(key: TooltipKey, locale: TooltipLocale): string {
  const entry = getTooltips(locale)[key]
  if (!entry) return ''
  const title = (entry.title || '').trim()
  const body = (entry.body || []).join(' ').trim()
  return title ? `${title}. ${body}`.trim() : body
}

export default TOOLTIPS_EN
