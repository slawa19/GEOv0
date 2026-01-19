import { mockApi } from './mockApi'
import { realApi } from './realApi'

import {
  type ApiMode,
  apiModeFromEnv,
  effectiveApiMode,
  resolveApiMode,
} from './apiMode'

export type { ApiMode }
export { resolveApiMode, apiModeFromEnv, effectiveApiMode }

export const api = effectiveApiMode() === 'real' ? realApi : mockApi
