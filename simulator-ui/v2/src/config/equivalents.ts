/**
 * Equivalent codes of the EQ selector (BottomBar).
 *
 * 028 F-028-47 (C1, owner В-3): the list comes from the API, not from a constant. The constant left is what
 * the FIXTURES ship (`public/simulator-fixtures/v1/<EQ>/`) - the only source fixtures mode has.
 */
export const FIXTURE_EQUIVALENT_CODES = ['UAH', 'HOUR', 'EUR'] as const

/** The grammar of `Equivalent.code` (`api/openapi.yaml`): what a deep link may name before any list is read. */
export const EQUIVALENT_CODE_RE = /^[A-Z0-9_]{1,16}$/

/**
 * Real mode: the selected scenario's equivalents (a run has no others), else the equivalents catalogue, else
 * only the current one. The current code is always kept, so the selector never shows a value it does not list.
 */
export function equivalentOptions(input: {
  apiMode: 'fixtures' | 'real'
  scenario: readonly string[] | null | undefined
  catalogue: readonly string[]
  current: string
}): string[] {
  const norm = (xs: readonly string[] | null | undefined) =>
    (xs ?? []).map((x) => String(x ?? '').trim().toUpperCase()).filter((x) => EQUIVALENT_CODE_RE.test(x))
  const scenario = norm(input.scenario)
  const fromApi = input.apiMode === 'fixtures' ? [...FIXTURE_EQUIVALENT_CODES] : scenario.length ? scenario : norm(input.catalogue)
  return Array.from(new Set([...fromApi, ...norm([input.current])]))
}
