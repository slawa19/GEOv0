/** A plain JSON object: not null and not an array. One definition for the decoders of wire and fixture data. */
export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}
