import { ref, type Ref } from 'vue'

import { ApiError, type HttpConfig } from '../api/http'
import {
  actionClearingReal,
  actionPaymentReal,
  actionTrustlineClose,
  actionTrustlineCreate,
  actionTrustlineUpdate,
  getParticipantsList,
  getTrustlinesList,
} from '../api/simulatorApi'
import type {
  ParticipantInfo,
  SimulatorActionClearingRealResponse,
  SimulatorActionError,
  SimulatorActionPaymentRealResponse,
  SimulatorActionTrustlineCloseResponse,
  SimulatorActionTrustlineCreateResponse,
  SimulatorActionTrustlineUpdateResponse,
  TrustlineInfo,
} from '../api/simulatorTypes'

export type InteractActionError = {
  status: number
  code: string
  message: string
  details?: Record<string, unknown> | null

  /** Raw body text (best-effort) for diagnostics. */
  bodyText?: string
  /** Convenience flag for feature-flagged endpoints (SIMULATOR_ACTIONS_ENABLE=0). */
  actionsDisabled?: boolean
}

export function isInteractActionError(e: unknown): e is InteractActionError {
  return (
    !!e &&
    typeof e === 'object' &&
    'status' in e &&
    'code' in e &&
    'message' in e &&
    typeof (e as any).status === 'number' &&
    typeof (e as any).code === 'string' &&
    typeof (e as any).message === 'string'
  )
}

function randomIdFallback(): string {
  // Stable-enough for UI correlation (not a security token).
  return `act_${Date.now().toString(36)}_${Math.random().toString(16).slice(2)}`
}

function genClientActionId(): string {
  try {
    const c = (globalThis as any)?.crypto
    if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  } catch {
    // ignore
  }
  return randomIdFallback()
}

function parseActionErrorJson(bodyText: string | undefined): SimulatorActionError | null {
  if (!bodyText) return null
  const s = bodyText.trim()
  if (!s) return null
  try {
    const v = JSON.parse(s) as unknown
    const code = (v as any)?.code
    const message = (v as any)?.message
    if (typeof code !== 'string' || typeof message !== 'string') return null
    const details = (v as any)?.details
    return {
      code,
      message,
      details: details && typeof details === 'object' ? (details as Record<string, unknown>) : null,
    }
  } catch {
    return null
  }
}

function mapToInteractActionError(e: unknown): InteractActionError {
  // Avoid double-mapping (e.g. requireRunId() throws an already-mapped object).
  if (isInteractActionError(e)) return e

  if (e instanceof ApiError) {
    const parsed = parseActionErrorJson(e.bodyText)
    const code = parsed?.code ?? `HTTP_${e.status}`
    const message = parsed?.message ?? e.message
    const details = parsed?.details ?? null
    return {
      status: e.status,
      code,
      message,
      details,
      bodyText: e.bodyText,
      actionsDisabled: e.status === 403 && code === 'ACTIONS_DISABLED',
    }
  }

  return {
    status: 0,
    code: 'UNKNOWN',
    message: e instanceof Error ? e.message : String(e),
    details: null,
  }
}

export function useInteractActions(opts: {
  httpConfig: Ref<HttpConfig>
  runId: Ref<string>
}): {
  /** True when backend rejects actions due to feature flag (HTTP 403 ACTIONS_DISABLED). */
  actionsDisabled: Ref<boolean>
  sendPayment: (from: string, to: string, amount: string, eq: string, opts?: { clientActionId?: string }) => Promise<SimulatorActionPaymentRealResponse>
  createTrustline: (
    from: string,
    to: string,
    limit: string,
    eq: string,
    opts?: { clientActionId?: string },
  ) => Promise<SimulatorActionTrustlineCreateResponse>
  updateTrustline: (
    from: string,
    to: string,
    newLimit: string,
    eq: string,
    opts?: { clientActionId?: string },
  ) => Promise<SimulatorActionTrustlineUpdateResponse>
  closeTrustline: (from: string, to: string, eq: string, opts?: { clientActionId?: string }) => Promise<SimulatorActionTrustlineCloseResponse>
  runClearing: (eq: string, maxDepth?: number, opts?: { clientActionId?: string }) => Promise<SimulatorActionClearingRealResponse>
  fetchParticipants: () => Promise<ParticipantInfo[]>
  fetchTrustlines: (eq: string, pid?: string) => Promise<TrustlineInfo[]>
} {
  const actionsDisabled = ref(false)

  function requireRunId(): string {
    const id = opts.runId.value.trim()
    if (!id) throw mapToInteractActionError(new Error('No active run_id'))
    return id
  }

  async function wrap<T>(fn: () => Promise<T>): Promise<T> {
    try {
      // Do NOT clear `actionsDisabled` optimistically before awaiting.
      // If backend keeps rejecting with 403 ACTIONS_DISABLED, the UI should not flicker.
      return await fn()
    } catch (e) {
      const mapped = mapToInteractActionError(e)
      if (mapped.actionsDisabled) actionsDisabled.value = true

      // If the run no longer exists, stop issuing run-scoped calls.
      // This can happen if a previously stored runId becomes stale (backend restart, TTL, cleanup).
      if (mapped.status === 404) {
        opts.runId.value = ''
      }

      throw mapped
    }
  }

  async function wrapAction<T>(fn: () => Promise<T>): Promise<T> {
    const res = await wrap(fn)
    // Clear only after a successful *action* call (feature flag may have been re-enabled).
    actionsDisabled.value = false
    return res
  }

  return {
    actionsDisabled,
    sendPayment: (from, to, amount, eq, o) =>
      wrapAction(() =>
        actionPaymentReal(opts.httpConfig.value, requireRunId(), {
          from_pid: from,
          to_pid: to,
          equivalent: eq,
          amount,
          client_action_id: o?.clientActionId ?? genClientActionId(),
        }),
      ),

    createTrustline: (from, to, limit, eq, o) =>
      wrapAction(() =>
        actionTrustlineCreate(opts.httpConfig.value, requireRunId(), {
          from_pid: from,
          to_pid: to,
          equivalent: eq,
          limit,
          client_action_id: o?.clientActionId ?? genClientActionId(),
        }),
      ),

    updateTrustline: (from, to, newLimit, eq, o) =>
      wrapAction(() =>
        actionTrustlineUpdate(opts.httpConfig.value, requireRunId(), {
          from_pid: from,
          to_pid: to,
          equivalent: eq,
          new_limit: newLimit,
          client_action_id: o?.clientActionId ?? genClientActionId(),
        }),
      ),

    closeTrustline: (from, to, eq, o) =>
      wrapAction(() =>
        actionTrustlineClose(opts.httpConfig.value, requireRunId(), {
          from_pid: from,
          to_pid: to,
          equivalent: eq,
          client_action_id: o?.clientActionId ?? genClientActionId(),
        }),
      ),

    runClearing: (eq, maxDepth, o) =>
      wrapAction(() =>
        actionClearingReal(opts.httpConfig.value, requireRunId(), {
          equivalent: eq,
          ...(maxDepth != null ? { max_depth: maxDepth } : {}),
          client_action_id: o?.clientActionId ?? genClientActionId(),
        }),
      ),

    fetchParticipants: async () => {
      try {
        const res = await wrap(() => getParticipantsList(opts.httpConfig.value, requireRunId()))
        return res.items
      } catch (e) {
        if (isInteractActionError(e) && e.status === 404) return []
        throw e
      }
    },

    fetchTrustlines: async (eq, pid) => {
      try {
        const res = await wrap(() => getTrustlinesList(opts.httpConfig.value, requireRunId(), eq, pid))
        return res.items
      } catch (e) {
        if (isInteractActionError(e) && e.status === 404) return []
        throw e
      }
    },
  }
}

