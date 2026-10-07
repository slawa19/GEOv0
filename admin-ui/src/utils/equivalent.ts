/**
 * The canonical form of an equivalent code: trimmed and upper-case (`UAH`). Codes are compared and used as map
 * keys in this form, so a value from a URL, an input or the wire (`uah`, ` UAH `) finds the same entry.
 *
 * The one implementation (032 S6, E-12): copies of this expression lived in the graph composables, the precision
 * source and the API client. A code the server accepts is `^[A-Z0-9_]{1,16}$` (`api/openapi.yaml`), so upper-casing
 * never changes which equivalent is meant.
 */
export function normalizeEquivalentCode(value: unknown): string {
  return String(value ?? '').trim().toUpperCase()
}
