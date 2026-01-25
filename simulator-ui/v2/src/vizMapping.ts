export type VizMapping = {
  node: {
    color: Record<string, { fill: string }>
  }
  link: {
    width_px: Record<string, number>
    alpha: Record<string, number>
    color: { default: string }
  }
  fx: {
    tx_spark: { core: string; trail: string }
    clearing_credit: string
    clearing_debt: string
    flash: { clearing: { from: string; to: string } }
  }
}

// Canonical mapping for demo-fast-mock v2.
// UI MUST NOT compute semantic colors/sizes; it only interprets keys.
export const VIZ_MAPPING: VizMapping = {
  node: {
    color: {
      business: { fill: '#10b981' },
      person: { fill: '#3b82f6' },
      suspended: { fill: '#94a3b8' },
      left: { fill: '#64748b' },
      deleted: { fill: '#475569' },

      'debt-0': { fill: '#84cc16' },
      'debt-1': { fill: '#a3e635' },
      'debt-2': { fill: '#d9f99d' },
      'debt-3': { fill: '#f59e0b' },
      'debt-4': { fill: '#f97316' },
      'debt-5': { fill: '#fb7185' },
      'debt-6': { fill: '#ef4444' },
      'debt-7': { fill: '#dc2626' },
      'debt-8': { fill: '#991b1b' },
    },
  },
  link: {
    width_px: {
      hairline: 0.6,
      thin: 0.9,
      mid: 1.1,
      thick: 1.25,
      highlight: 2.2,
    },
    alpha: {
      bg: 0.06,
      muted: 0.12,
      active: 0.20,
      hi: 0.32,
    },
    color: {
      default: '#64748b',
    },
  },
  fx: {
    tx_spark: {
      core: '#ffffff',
      trail: '#22d3ee',
    },
    clearing_credit: '#22d3ee',
    clearing_debt: '#f97316',
    flash: {
      clearing: {
        from: 'rgba(34,211,238,0.70)',
        to: 'rgba(34,211,238,0.00)',
      },
    },
  },
}
