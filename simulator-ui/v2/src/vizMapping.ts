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
      suspended: { fill: '#e6a23c' },  // aligned with admin-ui (warning orange)
      left: { fill: '#909399' },
      deleted: { fill: '#606266' },

      // Debt bins: debt-0 uses base type color (person/business), so not listed here.
      // Gradient: light yellow -> red (aligned with admin-ui)
      'debt-1': { fill: '#eadca8' },
      'debt-2': { fill: '#e2cf8d' },
      'debt-3': { fill: '#d8c073' },
      'debt-4': { fill: '#cfae62' },
      'debt-5': { fill: '#c79459' },
      'debt-6': { fill: '#be7a52' },
      'debt-7': { fill: '#b05f4b' },
      'debt-8': { fill: '#9a4444' },
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
