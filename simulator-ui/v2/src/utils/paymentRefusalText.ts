import { extractErrorMessage } from './errorMessage'
export const PAYMENT_REFUSAL_REASONS: readonly string[] = []
export function paymentRefusalText(e: unknown, _eq: string, _l?: string): string { return extractErrorMessage(e) }
