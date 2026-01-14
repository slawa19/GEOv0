export type CycleDebtEdge = {
  debtor: string
  creditor: string
  equivalent: string
}

export type TrustlineDirection = {
  from: string
  to: string
  equivalent: string
}

function normEq(v: unknown): string {
  return String(v || '').trim().toUpperCase()
}

// Cycle edges are debt edges (debtor -> creditor).
// TrustLine direction in the graph is creditor -> debtor.
export function cycleDebtEdgeToTrustlineDirection(edge: CycleDebtEdge): TrustlineDirection | null {
  const debtor = String(edge?.debtor || '').trim()
  const creditor = String(edge?.creditor || '').trim()
  const equivalent = normEq(edge?.equivalent)

  if (!debtor || !creditor || !equivalent) return null

  return { from: creditor, to: debtor, equivalent }
}
