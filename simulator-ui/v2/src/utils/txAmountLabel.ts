export type NormalizedTxAmountLabelInput = {
  nodeId: string
  raw: string
  sign: '-' | '+'
  /** Raw amount text with leading '+' stripped (may still include a leading '-'). */
  amountText: string
}

export function normalizeTxAmountLabelInput(
  nodeId: string,
  signedAmount: string,
): NormalizedTxAmountLabelInput | null {
  const id = nodeId.trim()
  if (!id) return null

  const raw = signedAmount.trim()
  if (!raw) return null

  const sign: '-' | '+' = raw.startsWith('-') ? '-' : '+'
  const amountText = raw.replace(/^\+/, '')

  return { nodeId: id, raw, sign, amountText }
}
