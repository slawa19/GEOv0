import { z, type ZodType } from 'zod'

import { ApiException } from './envelope'

export const ADMIN_CONFIG_KEYS = [
  'LOG_LEVEL',
  'RATE_LIMIT_ENABLED',
  'ROUTING_MAX_HOPS',
  'ROUTING_MAX_PATHS',
  'INTEGRITY_CHECKPOINT_ENABLED',
  'INTEGRITY_CHECKPOINT_INTERVAL_SECONDS',
  'RECOVERY_ENABLED',
  'RECOVERY_INTERVAL_SECONDS',
  'PAYMENT_TX_STUCK_TIMEOUT_SECONDS',
  'FEATURE_FLAGS_MULTIPATH_ENABLED',
  'FEATURE_FLAGS_FULL_MULTIPATH_ENABLED',
  'CLEARING_ENABLED',
] as const

export const AdminConfigKeySchema = z.enum(ADMIN_CONFIG_KEYS)

export const AdminConfigSchema = z
  .object({
    LOG_LEVEL: z.string(),
    RATE_LIMIT_ENABLED: z.boolean(),
    ROUTING_MAX_HOPS: z.number().int(),
    ROUTING_MAX_PATHS: z.number().int(),
    INTEGRITY_CHECKPOINT_ENABLED: z.boolean(),
    INTEGRITY_CHECKPOINT_INTERVAL_SECONDS: z.number().int(),
    RECOVERY_ENABLED: z.boolean(),
    RECOVERY_INTERVAL_SECONDS: z.number().int(),
    PAYMENT_TX_STUCK_TIMEOUT_SECONDS: z.number().int(),
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

export const AdminFeatureFlagsSchema = z
  .object({
    multipath_enabled: z.boolean(),
    full_multipath_enabled: z.boolean(),
    clearing_enabled: z.boolean(),
  })
  .strict()

export const AdminParticipantActionResponseSchema = z
  .object({
    pid: z.string(),
    status: z.enum(['active', 'suspended']),
  })
  .strict()

export const AdminAbortTxResponseSchema = z
  .object({
    tx_id: z.string(),
    status: z.literal('aborted'),
  })
  .strict()

export const AdminEquivalentCodeSchema = z.string().regex(/^[A-Z0-9_]{1,16}$/)
export const AdminEquivalentPrecisionSchema = z.number().int().min(0).max(18)
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

const EquivalentIntegrityStatusSchema = z
  .object({
    status: IntegrityStatusValueSchema,
    checksum: z.string(),
    last_verified: DateTimeSchema.nullable().optional(),
    invariants: z.record(z.string(), InvariantResultSchema),
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

export const IntegrityRepairNetMutualDebtsResponseSchema = z
  .object({
    ok: z.literal(true),
    action: z.literal('net-mutual-debts'),
    netted_pairs: z.number().int().nonnegative(),
    updated: z.number().int().nonnegative(),
    deleted: z.number().int().nonnegative(),
  })
  .strict()

export const IntegrityRepairCapDebtsResponseSchema = z
  .object({
    ok: z.literal(true),
    action: z.literal('cap-debts-to-trust-limits'),
    scanned: z.number().int().nonnegative(),
    updated: z.number().int().nonnegative(),
    deleted: z.number().int().nonnegative(),
  })
  .strict()

export type AdminConfigResponse = z.infer<typeof AdminConfigResponseSchema>
export type AdminConfigPatchResponse = z.infer<typeof AdminConfigPatchResponseSchema>
export type AdminFeatureFlags = z.infer<typeof AdminFeatureFlagsSchema>
export type AdminParticipantActionResponse = z.infer<typeof AdminParticipantActionResponseSchema>
export type AdminAbortTxResponse = z.infer<typeof AdminAbortTxResponseSchema>
export type AdminEquivalentMutationResponse = z.infer<typeof AdminEquivalentMutationResponseSchema>
export type AdminEquivalentDeleteResponse = z.infer<typeof AdminEquivalentDeleteResponseSchema>
export type AdminEquivalentUsageResponse = z.infer<typeof AdminEquivalentUsageResponseSchema>
export type IntegrityStatusResponse = z.infer<typeof IntegrityStatusResponseSchema>
export type IntegrityVerifyResponse = z.infer<typeof IntegrityVerifyResponseSchema>
export type IntegrityRepairNetMutualDebtsResponse = z.infer<typeof IntegrityRepairNetMutualDebtsResponseSchema>
export type IntegrityRepairCapDebtsResponse = z.infer<typeof IntegrityRepairCapDebtsResponseSchema>

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
  const config: Record<string, unknown> = {}
  for (const item of response.items) config[item.key] = item.value
  return decodeAdminResponse(AdminConfigSchema, config, 'admin config facade')
}
