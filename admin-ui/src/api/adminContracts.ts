import { z, type ZodType } from 'zod'

import { ApiException } from './envelope'

export const AdminConfigSchema = z.record(z.string(), z.unknown())

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
    updated: z.array(z.string()),
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
    status: z.enum(['active', 'suspended', 'deleted']),
  })
  .strict()

export const AdminAbortTxResponseSchema = z
  .object({
    tx_id: z.string(),
    status: z.literal('aborted'),
  })
  .strict()

const EquivalentCodeSchema = z.string().regex(/^[A-Z0-9_]{1,16}$/)
const DateTimeSchema = z.string().datetime({ offset: true })

export const AdminEquivalentWireResponseSchema = z
  .object({
    code: EquivalentCodeSchema,
    symbol: z.string().nullable().optional(),
    precision: z.number().int().min(0).max(18),
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
