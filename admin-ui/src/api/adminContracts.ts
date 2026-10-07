import { z, type ZodType } from 'zod'

import { ApiException } from './apiException'

export const ADMIN_CONFIG_KEYS = [
  'RATE_LIMIT_ENABLED',
  'ROUTING_MAX_HOPS',
  'ROUTING_MAX_PATHS',
  'FEATURE_FLAGS_MULTIPATH_ENABLED',
  'FEATURE_FLAGS_FULL_MULTIPATH_ENABLED',
  'CLEARING_ENABLED',
] as const

export const AdminConfigKeySchema = z.enum(ADMIN_CONFIG_KEYS)

export const AdminConfigSchema = z
  .object({
    RATE_LIMIT_ENABLED: z.boolean(),
    ROUTING_MAX_HOPS: z.number().int(),
    ROUTING_MAX_PATHS: z.number().int(),
    FEATURE_FLAGS_MULTIPATH_ENABLED: z.boolean(),
    FEATURE_FLAGS_FULL_MULTIPATH_ENABLED: z.boolean(),
    CLEARING_ENABLED: z.boolean(),
  })
  .strict()

export const AdminConfigPatchSchema = AdminConfigSchema.partial().strict()

export const AdminConfigResponseSchema = z
  .object({
    items: z.array(
      z
        .object({
          key: z.string(),
          value: z.unknown(),
          mutable: z.boolean(),
        })
        .passthrough(),
    ),
  })
  .passthrough()

export const AdminConfigPatchResponseSchema = z
  .object({
    updated: z.array(AdminConfigKeySchema),
  })
  .passthrough()

export const AdminParticipantActionResponseSchema = z
  .object({
    pid: z.string(),
    status: z.enum(['active', 'suspended']),
  })
  .strict()

export const AdminEquivalentCodeSchema = z.string().regex(/^[A-Z0-9_]{1,16}$/)
// 0..8, narrowed from 0..18 on 2026-08-25 (012 / S1): the ledger stores money in
// `Numeric(20, 8)` and protocol §3.2 declares `precision` as 0-8, so the API bound moved to
// match (`app/schemas/admin.py`, `api/openapi.yaml`).
//
// This schema guards MUTATION input and the mutation RESPONSE, and both are produced by the
// strict backend model that now emits at most 8. Reads are deliberately NOT bounded here:
// `EquivalentSchema` in `realApi.ts` keeps `z.number()`, mirroring the backend's
// `StoredEquivalent`, so a legacy row written when the door accepted 12 or 19 stays visible on
// the list and repairable through PATCH instead of failing to decode.
export const AdminEquivalentPrecisionSchema = z.number().int().min(0).max(8)
const DateTimeSchema = z.string().datetime({ offset: true })

export const AdminAuditLogEntrySchema = z
  .object({
    id: z.string().uuid(),
    timestamp: DateTimeSchema,
    actor_id: z.string().uuid().nullable().optional(),
    actor_role: z.string().nullable().optional(),
    action: z.string(),
    object_type: z.string().nullable().optional(),
    object_id: z.string().nullable().optional(),
    reason: z.string().nullable().optional(),
    before_state: z.record(z.string(), z.unknown()).nullable().optional(),
    after_state: z.record(z.string(), z.unknown()).nullable().optional(),
    request_id: z.string().nullable().optional(),
    ip_address: z.string().nullable().optional(),
    user_agent: z.string().nullable().optional(),
  })
  .strict()

export const AdminAuditLogSchema = z.array(AdminAuditLogEntrySchema)

export const AdminEquivalentWireResponseSchema = z
  .object({
    code: AdminEquivalentCodeSchema,
    symbol: z.string().nullable().optional(),
    precision: AdminEquivalentPrecisionSchema,
    description: z.string().nullable().optional(),
    metadata: z.record(z.string(), z.unknown()).nullable().optional(),
    is_active: z.boolean(),
    created_at: DateTimeSchema,
    updated_at: DateTimeSchema,
  })
  .passthrough()

export const AdminEquivalentMutationResponseSchema = AdminEquivalentWireResponseSchema
  .transform(({ code, precision, description, is_active }) => ({
    code,
    precision,
    description: description ?? '',
    is_active,
  }))

export const AdminEquivalentDeleteResponseSchema = z
  .object({
    deleted: z.string(),
  })
  .strict()

export const AdminEquivalentUsageResponseSchema = z
  .object({
    code: z.string(),
    trustlines: z.number().int().nonnegative(),
    debts: z.number().int().nonnegative(),
    integrity_checkpoints: z.number().int().nonnegative(),
  })
  .strict()

const IntegrityStatusValueSchema = z.enum(['healthy', 'warning', 'critical'])

const InvariantResultSchema = z
  .object({
    passed: z.boolean(),
    value: z.string().nullable().optional(),
    violations: z.number().int().nullable().optional(),
    details: z.record(z.string(), z.unknown()).nullable().optional(),
  })
  .strict()

// An invariant the server does NOT evaluate. Added with T1402 of programme 014, which withdrew
// zero-sum from publication: it could not fail on corruption, so `passed: true` was a claim the
// backend could not support.
//
// This schema had to change in the same slice, and that is worth naming: `InvariantResultSchema`
// is `.strict()`, so the new `{status, reason}` entry would have been REJECTED by the decoder and
// the whole Integrity page would have failed to load against a healthy server. That is exactly
// the unowned P3 programme 013 recorded in `specs/BACKLOG.md` - a strict decoder turning an
// additive server change into a page failure - meeting a real additive change for the first time.
const InvariantWithdrawnSchema = z
  .object({
    status: z.literal('not_verified'),
    reason: z.literal('check_withdrawn'),
  })
  .strict()

// 026 `T2601`: `trust_limits` lists debt above a lowered limit as allowed and says growth is not
// verified by a snapshot (OpenAPI `TrustLimitsResult`).
const OverLimitKeys = ['debtor_id', 'creditor_id', 'equivalent_id', 'debt_amount', 'trust_limit', 'excess']
const TrustLimitsResultSchema = z
  .object({
    passed: z.boolean(),
    violations: z.number().int().nonnegative(),
    details: z.record(z.string(), z.unknown()).nullable(),
    over_limit_allowed: z.array(z.object(Object.fromEntries(OverLimitKeys.map((k) => [k, z.string()]))).strict()),
    growth: z.object({ status: z.literal('not_verified'), reason: z.literal('requires_operation_prestate') }).strict(),
  })
  .strict()

const InvariantOutcomeSchema = z.union([InvariantWithdrawnSchema, TrustLimitsResultSchema, InvariantResultSchema])

const EquivalentIntegrityStatusSchema = z
  .object({
    status: IntegrityStatusValueSchema,
    checksum: z.string(),
    last_verified: DateTimeSchema.nullable().optional(),
    invariants: z.record(z.string(), InvariantOutcomeSchema),
    // Names in `invariants` carrying no verdict. Optional on the client so an older server, or a
    // replayed fixture, still decodes.
    unverified: z.array(z.string()).optional(),
  })
  .strict()

export const IntegrityStatusResponseSchema = z
  .object({
    status: IntegrityStatusValueSchema,
    last_check: DateTimeSchema,
    equivalents: z.record(z.string(), EquivalentIntegrityStatusSchema),
    alerts: z.array(z.string()),
  })
  .strict()

export const IntegrityVerifyResponseSchema = z
  .object({
    status: IntegrityStatusValueSchema,
    checked_at: DateTimeSchema,
    equivalents: z.record(z.string(), EquivalentIntegrityStatusSchema),
    alerts: z.array(z.string()),
  })
  .strict()

// 032 S5 (F-4): the per-equivalent summary the Integrity screen reads for the holds (`GET /integrity/summary`,
// `api/openapi.yaml` IntegritySummaryResponse). `hold: true` - money in this equivalent is held until an admin
// clears it (`POST /admin/equivalents/{code}/integrity-hold/clear`).
export const IntegritySummaryResponseSchema = z
  .object({
    equivalents: z.array(
      z
        .object({
          equivalent: z.string(),
          status: IntegrityStatusValueSchema,
          checked_at: DateTimeSchema.nullable(),
          hold: z.boolean(),
        })
        .strict(),
    ),
  })
  .strict()

export type AdminConfigResponse = z.infer<typeof AdminConfigResponseSchema>
export type AdminConfigPatchResponse = z.infer<typeof AdminConfigPatchResponseSchema>
export type AdminParticipantActionResponse = z.infer<typeof AdminParticipantActionResponseSchema>
export type AdminEquivalentMutationResponse = z.infer<typeof AdminEquivalentMutationResponseSchema>
export type AdminEquivalentDeleteResponse = z.infer<typeof AdminEquivalentDeleteResponseSchema>
export type AdminEquivalentUsageResponse = z.infer<typeof AdminEquivalentUsageResponseSchema>
export type IntegrityStatusResponse = z.infer<typeof IntegrityStatusResponseSchema>
export type IntegrityVerifyResponse = z.infer<typeof IntegrityVerifyResponseSchema>
export type IntegritySummaryResponse = z.infer<typeof IntegritySummaryResponseSchema>

export function decodeAdminResponse<T>(schema: ZodType<T>, value: unknown, operation: string): T {
  const validated = schema.safeParse(value)
  if (!validated.success) {
    throw new ApiException({
      status: 200,
      code: 'INVALID_RESPONSE',
      message: `${operation} -> 200: Response JSON does not match expected schema`,
      details: {
        operation,
        issues: validated.error.issues,
      },
    })
  }

  return validated.data
}

export function flattenAdminConfig(response: AdminConfigResponse): Record<string, unknown> {
  // 029 F-029-4: only what the backend lets an admin change; a key read once at start is not offered for editing.
  const config: Record<string, unknown> = {}
  for (const item of response.items) if (item.mutable) config[item.key] = item.value
  return decodeAdminResponse(AdminConfigSchema, config, 'admin config facade')
}
