// The one error type of the Admin UI API client. The client returns the response body or throws
// this; there is no `{success, data}` envelope (removed 2026-10-07, 032 S4 - the backend never
// sent one, only the deleted mock client did).
export class ApiException extends Error {
  readonly status: number
  readonly code: string
  readonly details?: unknown
  /**
   * The server's correlation id of the failed request: `error.request_id` of the error body, else the
   * `X-Request-ID` response header, else `null` (the request never reached the server, or it sent none).
   * `describeError` shows it to the operator so a message on screen can be found in the server log.
   */
  readonly requestId: string | null

  constructor(opts: { status: number; code: string; message: string; details?: unknown; requestId?: string | null }) {
    super(opts.message)
    this.name = 'ApiException'
    this.status = opts.status
    this.code = opts.code
    this.details = opts.details
    this.requestId = opts.requestId ? opts.requestId : null
  }
}
