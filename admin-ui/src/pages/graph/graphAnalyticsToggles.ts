export type AnalyticsToggles = {
  showRank: boolean
  showDistribution: boolean
  showConcentration: boolean
  showCapacity: boolean
  showBottlenecks: boolean
  showActivity: boolean
}

export const DEFAULT_ANALYTICS_TOGGLES: AnalyticsToggles = {
  showRank: true,
  showDistribution: true,
  showConcentration: true,
  showCapacity: true,
  showBottlenecks: true,
  showActivity: true,
}

export type AnalyticsToggleKey = keyof AnalyticsToggles

export type AnalyticsToggleItem = {
  key: AnalyticsToggleKey
  label: string
  tooltipText: string
  requires?: AnalyticsToggleKey
}

export const summaryToggleItems: AnalyticsToggleItem[] = [
  {
    key: 'showRank',
    label: 'Rank / percentile',
    tooltipText: 'Your position among all participants by net balance for the selected equivalent.',
  },
  {
    key: 'showConcentration',
    label: 'Concentration risk',
    tooltipText: 'How concentrated your debts/credits are across counterparties (top1/top5 shares + HHI).',
  },
  {
    key: 'showCapacity',
    label: 'Trustline capacity',
    tooltipText: 'Aggregate trustline capacity around the participant: used% = total_used / total_limit.',
  },
  {
    key: 'showActivity',
    label: 'Activity / churn',
    tooltipText: 'Recent changes around the participant in rolling windows (7/30/90 days).',
  },
]

export const balanceToggleItems: AnalyticsToggleItem[] = [
  {
    key: 'showRank',
    label: 'Rank / percentile',
    tooltipText: 'Rank/percentile of net balance across all participants (for selected equivalent).',
  },
  {
    key: 'showDistribution',
    label: 'Distribution histogram',
    tooltipText: 'Tiny histogram of net balance distribution across all participants (selected equivalent).',
  },
]

export const riskToggleItems: AnalyticsToggleItem[] = [
  {
    key: 'showConcentration',
    label: 'Concentration risk',
    tooltipText: 'Top1/top5 shares and HHI derived from counterparty debt shares.',
  },
  {
    key: 'showCapacity',
    label: 'Trustline capacity',
    tooltipText: 'Aggregate incoming/outgoing used vs limit and bottleneck detection.',
  },
  {
    key: 'showBottlenecks',
    label: 'Bottlenecks',
    tooltipText: 'Show bottleneck count/list inside Trustline capacity (threshold-based).',
    requires: 'showCapacity',
  },
  {
    key: 'showActivity',
    label: 'Activity / churn',
    tooltipText: '7/30/90-day counts from fixtures timestamps (trustlines/incidents/audit/transactions).',
  },
]
