export type ApiSuccess<T> = { success: true; data: T }
export type ApiError = {
  success: false
  error: {
    code: string
    message: string
    details?: unknown
  }
}
export type ApiEnvelope<T> = ApiSuccess<T> | ApiError

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

export function assertSuccess<T>(env: ApiEnvelope<T>, status = 200): T {
  if (env.success) return env.data
  throw new ApiException({
    status,
    code: env.error.code,
    message: env.error.message,
    details: env.error.details,
  })
}
