// The one error type of the Admin UI API client. The client returns the response body or throws
// this; there is no `{success, data}` envelope (removed 2026-10-07, 032 S4 - the backend never
// sent one, only the deleted mock client did).
export class ApiException extends Error {
  readonly status: number
  readonly code: string
  readonly details?: unknown

  constructor(opts: { status: number; code: string; message: string; details?: unknown }) {
    super(opts.message)
    this.name = 'ApiException'
    this.status = opts.status
    this.code = opts.code
    this.details = opts.details
  }
}
