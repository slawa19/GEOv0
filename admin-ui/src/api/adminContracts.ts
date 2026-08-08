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

export type AdminConfigResponse = z.infer<typeof AdminConfigResponseSchema>
export type AdminConfigPatchResponse = z.infer<typeof AdminConfigPatchResponseSchema>
export type AdminFeatureFlags = z.infer<typeof AdminFeatureFlagsSchema>

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
