// The Admin UI's one API client, the real one (032 S4, 2026-10-07: the mock client and its mode
// switch were deleted; `singleClient.guard.test.ts` keeps it that way).
import { realApi } from './realApi'

export const api = realApi
