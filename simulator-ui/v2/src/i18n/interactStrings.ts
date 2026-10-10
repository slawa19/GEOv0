/**
 * Every NEW user-facing string of the manual-payment flow (037 slice A2), in one place, English only.
 *
 * Why one module: the visitor-side dictionary (RU/EN with a switcher) belongs to the demo programme (034 S9); until it
 * lands these strings are the single list it will absorb - keys here, no literal of this kind in a template. Why English
 * only: no new Russian text is introduced by this slice; `utils/paymentRefusalText.ts` keeps its own RU branch.
 *
 * Placeholders are `{name}`; `interactText` fills them and leaves an unknown placeholder visible rather than guessing.
 */
export const interactStrings = {
  // The result screen of a committed payment.
  resultTitle: 'Payment sent',
  resultPaymentId: 'Payment ID',
  resultStatus: 'Status',
  resultAmount: 'Amount',
  resultParties: 'From → To',
  resultRoutes: 'Route {n} of {total}',
  resultRouteStep: '{from} → {to}: {amount} {unit}',
  resultNoRoutes: 'The server did not report the route of this payment.',
  resultAnother: 'Another payment',
  resultClose: 'Close',

  // An outcome that is not known, and what the user can do about it.
  unknownTitle: 'Result unknown',
  unknownNoAnswer: 'No usable answer arrived; the payment of {amount} {unit} ({from} → {to}) may have been made.',
  unknownEarlier: 'An earlier attempt of the payment of {amount} {unit} ({from} → {to}) has an unknown result; this answer does not settle it.',
  unknownRetry: 'Check / repeat',
  unknownRetryHint: 'Repeating sends the same payment under the same key: it is made once, and the stored payment is shown if it was already made.',

  // A success-shaped answer that is not a committed payment.
  notCommitted: 'The server did not confirm the payment (status {status}).',

  // Where the numbers on the panel come from.
  sourceServer: 'server',
  sourceSnapshot: 'snapshot, unconfirmed',
  sourceLoading: 'waiting…',
  sourceFailed: 'snapshot, no answer',

  // The server's estimate for the chosen recipient (`payment-targets`, `include_max_available`).
  estimateTitle: 'Estimated maximum',
  estimateShortest: '{n} step(s)',
  estimateShortestTitle: 'Shortest path: {n} step(s) (the estimated maximum is a separate computation)',
  estimateNotEstimated: 'not estimated',
  estimateLoading: 'waiting for the server',
  estimateFailed: 'not received',
  estimateExceeded: 'The amount is above the server\'s estimate of the maximum ({max} {unit}); the server decides.',
} as const

export type InteractStringKey = keyof typeof interactStrings

export function interactText(key: InteractStringKey, vars: Record<string, string | number> = {}): string {
  return interactStrings[key].replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole))
}
