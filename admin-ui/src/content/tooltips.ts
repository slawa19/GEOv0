export type TooltipKey =
  | 'nav.dashboard'
  | 'nav.integrity'
  | 'nav.trustlines'
  | 'nav.graph'
  | 'nav.participants'
  | 'nav.config'
  | 'nav.auditLog'
  | 'nav.equivalents'
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
  | 'graph.eq'
  | 'graph.status'
  | 'graph.threshold'
  | 'graph.layout'
  | 'graph.type'
  | 'graph.minDegree'
  | 'graph.labels'
  | 'graph.hideIsolates'
  | 'graph.search'
  | 'graph.zoom'
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
    body: ['Quick overview:', 'participants, one row per equivalent (its lines and sums),', 'equivalents on an integrity hold, recent audit entries.'],
  },
  'nav.integrity': {
    title: 'Integrity',
    body: [
      'Integrity checks that network data is internally consistent.',
      'Examples: balances add up (zero-sum); limits and derived values match.',
      'If this page shows issues, analytics may be misleading',
      'and operations can be risky.',
      'Equivalents on an integrity hold are listed here and cleared with a reason.',
    ],
  },
  'nav.trustlines': {
    title: 'Trustlines',
    body: ['Credit limits between participants (who can owe whom, and how much).', 'Filter by equivalent, creditor, debtor, status.'],
  },
  'nav.graph': {
    title: 'Network Graph',
    body: ['Interactive map of the trust network.', 'Use it to explore clusters, isolate problems, and drill into a participant or edge.'],
  },
  'nav.participants': {
    title: 'Participants',
    body: ['Participant directory and management actions.', 'Open details, copy IDs, freeze/unfreeze when needed.'],
  },
  'nav.config': {
    title: 'Config',
    body: ['System settings: limits, policies, operational toggles.', 'Some changes may require a restart to take effect.'],
  },
  'nav.auditLog': {
    title: 'Audit Log',
    body: ['History of administrative actions.', 'Who did what, when, and (optionally) why.'],
  },
  'nav.equivalents': {
    title: 'Equivalents',
    body: ['Catalog of units/currencies used in the network (e.g., UAH, HOUR).', 'Each has precision and description.'],
  },

  'dashboard.recentAudit': {
    title: 'Recent audit log',
    body: ['Latest administrative actions.', 'Useful for understanding recent changes and troubleshooting.'],
    links: [{ label: 'Open Audit Log', to: { path: '/audit-log' } }],
  },

  'participants.pid': {
    title: 'PID',
    body: ['Unique participant ID used across the network.'],
  },
  'participants.displayName': {
    title: 'Name',
    body: ['Human-friendly name shown in the UI (if provided).'],
  },
  'participants.type': {
    title: 'Type',
    body: ['Participant category (e.g., person vs business).'],
  },
  'participants.status': {
    title: 'Status',
    body: ['Operational status of the participant.', 'Inactive or suspended participants', 'may be blocked from transactions.'],
  },

  'trustlines.eq': {
    title: 'Equivalent',
    body: ['Equivalent (currency/unit) code, e.g. UAH, USD.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'trustlines.from': {
    title: 'from',
    body: ['Creditor (who is taking the risk / providing the credit).'],
  },
  'trustlines.to': {
    title: 'to',
    body: ['Debtor (who can owe up to the limit on this edge).'],
  },
  'trustlines.limit': {
    title: 'limit',
    body: ['Maximum amount the debtor can owe the creditor on this trustline.'],
    links: [
      {
        label: 'Default limit (Config)',
        to: { path: '/config', query: { key: 'limits.default_trustline_limit' } },
      },
    ],
  },
  'trustlines.used': {
    title: 'used',
    body: ['Amount currently used/reserved on this trustline.'],
  },
  'trustlines.available': {
    title: 'available',
    body: [
      'Unused trust: limit − used. Low values indicate a likely bottleneck.',
      'Negative when the creditor lowered the limit below the debt: the debt exceeds trust by that amount; it cannot grow, only be repaid. It is not a payable amount and not the payment capacity.',
    ],
  },
  'trustlines.status': {
    title: 'status',
    body: [
      'Trustline lifecycle state (active or closed).',
      '"close requested": the creditor asked to close; the limit is 0 and the line stays active until the debt it supports is repaid, then it is closed.',
    ],
  },
  'trustlines.createdAt': {
    title: 'created_at',
    body: ['When the trustline was created.'],
  },

  'graph.eq': {
    title: 'Equivalent filter',
    body: ['Shows trustlines for a specific unit/currency.', 'Use ALL to see the whole network.'],
    links: [{ label: 'Open Equivalents', to: { path: '/equivalents' } }],
  },
  'graph.status': {
    title: 'Status filter',
    body: ['Shows only trustlines with selected statuses.'],
    links: [{ label: 'Open Trustlines', to: { path: '/trustlines' } }],
  },
  'graph.threshold': {
    title: 'Bottleneck threshold',
    body: ['An edge is a bottleneck when remaining capacity is low', 'relative to its limit.', 'Higher threshold marks more edges as “near limit”.'],
  },
  'graph.layout': {
    title: 'Layout',
    body: ['Controls how the network is arranged on screen.', 'Use fcose for clusters; grid/circle for stable layouts.'],
  },
  'graph.type': {
    title: 'Participant type',
    body: [
      'Filters nodes by participant type (e.g., person vs business).',
      'Use it to reduce noise and focus analysis on one segment.',
    ],
  },
  'graph.minDegree': {
    title: 'Minimum degree',
    body: ['Hides low-connected nodes to reduce noise.', 'Useful when the graph is too dense.'],
  },
  'graph.labels': {
    title: 'Labels',
    body: ['Shows names/IDs on nodes.', 'Disable if labels make the view too noisy.'],
  },
  'graph.hideIsolates': {
    title: 'Hide isolates',
    body: ['Hides participants with no visible trustlines in the current view.'],
  },
  'graph.search': {
    title: 'Search',
    body: [
      'Search by participant ID or name (partial match works).',
      'Pick a suggestion to set an exact focus participant.',
      'Use Find to center on the focus participant and keep it highlighted.',
    ],
  },
  'graph.zoom': {
    title: 'Zoom helpers',
    body: ['Use the slider or mouse wheel to zoom.', 'Zoom helps inspect local neighborhoods and crowded areas.'],
  },


  'graph.spacing': {
    title: 'Layout spacing',
    body: ['Controls how spread-out the layout is.', 'More spacing reduces clutter but may take longer to settle.'],
  },
  'graph.legend': {
    title: 'Legend',
    body: ['Explains colors and styles used in the graph.'],
  },

  'audit.timestamp': {
    title: 'timestamp',
    body: ['When the admin/audit event happened (ISO 8601).'],
  },
  'audit.actor': {
    title: 'actor',
    body: ['Who performed the action.', 'Can be empty for system-initiated actions.'],
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
    body: ['Быстрый обзор:', 'участники, строка на эквивалент (его линии и суммы),', 'эквиваленты на удержании, последние записи аудита.'],
  },
  'nav.integrity': {
    title: 'Целостность',
    body: [
      'Целостность — проверка, что данные сети согласованы',
      'и им можно доверять.',
      'Если здесь есть ошибки, аналитика может быть неверной',
      'и операции — рискованными.',
      'Здесь же отмечены эквиваленты на удержании и снимается удержание (с причиной).',
    ],
  },
  'nav.trustlines': {
    title: 'Трастлайны',
    body: ['Кредитные лимиты между участниками (кто кому и сколько может быть должен).', 'Фильтры по эквиваленту, кредитору, должнику, статусу.'],
  },
  'nav.graph': {
    title: 'Граф сети',
    body: ['Интерактивная карта сети доверия.', 'Полезно для поиска кластеров, изолятов и расследования конкретных узлов/рёбер.'],
  },
  'nav.participants': {
    title: 'Участники',
    body: ['Справочник участников и операционные действия.', 'Детали, копирование ID, заморозка/разморозка при необходимости.'],
  },
  'nav.config': {
    title: 'Конфиг',
    body: ['Настройки системы: лимиты, политики, переключатели.', 'Некоторые изменения начинают действовать только после перезапуска.'],
  },
  'nav.auditLog': {
    title: 'Аудит-лог',
    body: ['История административных действий.', 'Кто что сделал, когда и (при наличии) почему.'],
  },
  'nav.equivalents': {
    title: 'Эквиваленты',
    body: ['Каталог единиц/валют сети (например, UAH, HOUR).', 'У каждой есть точность и описание.'],
  },

  'dashboard.recentAudit': {
    title: 'Последние действия',
    body: ['Последние административные действия.', 'Полезно для понимания недавних изменений и расследований.'],
    links: [{ label: 'Открыть аудит-лог', to: { path: '/audit-log' } }],
  },

  'participants.pid': {
    title: 'PID',
    body: ['Уникальный ID участника в сети.'],
  },
  'participants.displayName': {
    title: 'Имя',
    body: ['Человекочитаемое имя (если задано).'],
  },
  'participants.type': {
    title: 'Тип',
    body: ['Категория участника (например, человек или бизнес).'],
  },
  'participants.status': {
    title: 'Статус',
    body: ['Операционный статус участника.', 'Неактивные или замороженные участники', 'могут быть ограничены в операциях.'],
  },

  'trustlines.eq': {
    title: 'Эквивалент',
    body: ['Код эквивалента (валюта/единица), напр. UAH, USD.'],
    links: [{ label: 'Открыть эквиваленты', to: { path: '/equivalents' } }],
  },
  'trustlines.from': {
    title: 'from',
    body: ['Кредитор (кто принимает риск и даёт кредитный лимит).'],
  },
  'trustlines.to': {
    title: 'to',
    body: ['Должник (кто может быть должен по этому лимиту).'],
  },
  'trustlines.limit': {
    title: 'limit',
    body: ['Максимальная сумма, которую должник может быть должен кредитору по этому трастлайну.'],
    links: [
      {
        label: 'Лимит по умолчанию (Конфиг)',
        to: { path: '/config', query: { key: 'limits.default_trustline_limit' } },
      },
    ],
  },
  'trustlines.used': {
    title: 'used',
    body: ['Сколько лимита уже использовано/зарезервировано.'],
  },
  'trustlines.available': {
    title: 'available',
    body: [
      'Неиспользованное доверие: limit − used. Низкие значения — признак узкого места.',
      'Отрицательное, если кредитор снизил лимит ниже долга: долг превышает доверие на эту величину; расти он не может, только гаситься. Это не сумма к оплате и не ёмкость платежа.',
    ],
  },
  'trustlines.status': {
    title: 'status',
    body: [
      'Состояние трастлайна (active или closed).',
      '«закрытие запрошено»: кредитор попросил закрыть линию; лимит 0, линия остаётся активной, пока поддерживаемый ею долг не погашен, затем закрывается.',
    ],
  },
  'trustlines.createdAt': {
    title: 'created_at',
    body: ['Когда трастлайн был создан.'],
  },

  'graph.eq': {
    title: 'Фильтр эквивалента',
    body: ['Показывает трастлайны только для выбранной единицы/валюты.', 'ALL — весь граф целиком.'],
    links: [{ label: 'Открыть эквиваленты', to: { path: '/equivalents' } }],
  },
  'graph.status': {
    title: 'Фильтр статуса',
    body: ['Показывает только трастлайны выбранных статусов.'],
    links: [{ label: 'Открыть трастлайны', to: { path: '/trustlines' } }],
  },
  'graph.threshold': {
    title: 'Порог узких мест',
    body: ['Ребро считается узким местом, если остаётся мало лимита', 'относительно общего лимита.', 'Чем выше порог — тем больше рёбер помечается', 'как “почти исчерпано”.'],
  },
  'graph.layout': {
    title: 'Раскладка',
    body: ['Управляет тем, как граф раскладывается на экране.', 'fcose удобен для кластеров; grid/circle дают стабильный вид.'],
  },
  'graph.type': {
    title: 'Тип участника',
    body: [
      'Фильтрует узлы по типу участника (например, человек vs бизнес).',
      'Полезно, чтобы убрать шум и сфокусироваться на одном сегменте.',
    ],
  },
  'graph.minDegree': {
    title: 'Минимальная степень',
    body: ['Скрывает слабо-связанные узлы, чтобы уменьшить шум.', 'Полезно, когда граф слишком плотный.'],
  },
  'graph.labels': {
    title: 'Подписи',
    body: ['Показывает имена/ID на узлах.', 'Отключите, если подписи мешают чтению.'],
  },
  'graph.hideIsolates': {
    title: 'Скрыть изолятов',
    body: ['Скрывает участников без видимых трастлайнов в текущем представлении.'],
  },
  'graph.search': {
    title: 'Поиск',
    body: [
      'Поиск по PID или имени (частичное совпадение работает).',
      'Выберите подсказку, чтобы точно задать фокусного участника.',
      'Нажмите Find, чтобы центрировать и оставить участника подсвеченным.',
    ],
  },
  'graph.zoom': {
    title: 'Масштабирование',
    body: ['Используйте слайдер или колёсико мыши.', 'Масштаб помогает переключаться между обзором и деталями.'],
  },
  'graph.spacing': {
    title: 'Плотность раскладки',
    body: ['Задаёт “разброс” раскладки.', 'Больше — меньше скученность, но дольше перестраивается.'],
  },
  'graph.legend': {
    title: 'Легенда',
    body: ['Объясняет цвета и стили узлов/рёбер на графе.'],
  },

  'audit.timestamp': {
    title: 'timestamp',
    body: ['Когда произошло событие (ISO 8601).'],
  },
  'audit.actor': {
    title: 'actor',
    body: ['Кто выполнил действие.', 'Может быть пустым для системных действий.'],
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
  return (entry.body || [])
    .map((s) => String(s || '').trim())
    .filter(Boolean)
    .join('\n')
}

export default TOOLTIPS_EN
