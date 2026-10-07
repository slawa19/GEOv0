import { z } from 'zod'

import type { VizColorKey } from '../types/domain'

// The decoders of the Admin UI's READ endpoints (032 S6, E-13). One home for them: `realApi.ts` only calls the
// server, `types/domain.ts` holds the types the screens use, and `requestJson<T>` takes a `ZodType<T>`, so
// whatever a schema here decodes to is checked against the type the method returns at compile time.
// The mutation and integrity decoders live in `adminContracts.ts`.

/**
 * Money on the wire is a decimal STRING (`PlainDecimal` in `api/openapi.yaml`). A JSON number is refused
 * (032 S6, E-6): `100.25` has no exact binary form and `String(0.1 + 0.2)` is `0.30000000000000004`, so
 * accepting a number would let a float reach a money cell under a string's name. The old union accepted both
 * and normalized with `String(v)`.
 */
export const DecimalString = z.string()

// The closed set the server emits (`app/core/admin/viz_rules.py`): a type, a status, the debtor gradient bins.
const VIZ_COLOR_KEY = /^(?:person|business|debt|debt-[0-8]|suspended|left|deleted)$/
const VizColorKeySchema = z.custom<VizColorKey>((v) => typeof v === 'string' && VIZ_COLOR_KEY.test(v))

export const ParticipantSchema = z
  .object({
    pid: z.string(),
    display_name: z.string(),
    type: z.string(),
    status: z.string(),
    created_at: z.string().optional(),
    meta: z.record(z.string(), z.unknown()).optional(),

    net_balance_atoms: z.string().nullable().optional(),
    net_sign: z.union([z.literal(-1), z.literal(0), z.literal(1)]).nullable().optional(),
    viz_color_key: VizColorKeySchema.nullable().optional(),
    viz_size: z
      .object({
        w: z.number(),
        h: z.number(),
      })
      .nullable()
      .optional(),
  })
  .passthrough()

export const TrustlineSchema = z
  .object({
    equivalent: z.string(),
    from: z.string(),
    to: z.string(),
    from_display_name: z.string().nullable().optional(),
    to_display_name: z.string().nullable().optional(),
    limit: DecimalString,
    used: DecimalString,
    available: DecimalString,
    status: z.string(),
    created_at: z.string(),
    policy: z.record(z.string(), z.unknown()).nullable().optional(),
    close_requested_at: z.string().nullable().optional(),
  })
  .passthrough()

/**
 * `description` is nullable on the wire (`StoredEquivalent`). The UI type says `description: string`, and the
 * mutation response decoder (`adminContracts.ts`) already turns null into `''`; the reads do the same, so the
 * type is what the decoder produces (032 S6, E-5).
 */
export const EquivalentSchema = z
  .object({
    code: z.string(),
    precision: z.number(),
    description: z.string().nullable().optional(),
    is_active: z.boolean(),
  })
  .passthrough()
  .transform((e) => ({ ...e, description: e.description ?? '' }))

export const DebtSchema = z
  .object({
    equivalent: z.string(),
    debtor: z.string(),
    creditor: z.string(),
    amount: DecimalString,
  })
  .passthrough()

export const AuditLogEntrySchema = z
  .object({
    id: z.string(),
    timestamp: z.string(),
    actor_id: z.string().nullable().optional(),
    actor_role: z.string().nullable().optional(),
    action: z.string(),
    object_type: z.string().nullable().optional(),
    object_id: z.string().nullable().optional(),
    reason: z.string().nullable().optional(),
    before_state: z.unknown().optional(),
    after_state: z.unknown().optional(),
    request_id: z.string().nullable().optional(),
    ip_address: z.string().nullable().optional(),
    user_agent: z.string().nullable().optional(),
  })
  .passthrough()

// F-013-1 / T1302. The shape below is AdminGraphTransactionItem (api/openapi.yaml) and what the
// projection `_graph_fetch_transactions` actually emits - nothing more.
//
// It used to require `payload`, which neither the canon declares nor the producer sends. That was
// invisible only because the client never asked for `include=transactions`, so the array was always
// empty and no row was ever validated. The first response that carried a transaction would have been
// rejected whole as INVALID_RESPONSE and blanked the graph page - which is why the include, the
// schema and the consumer had to land as one change and not as a series with a broken middle.
//
// Required here is exactly what the canon marks required. `equivalent` and `error` are nullable and
// absence-tolerant because the canon says so: `equivalent` is payload.get("equivalent") and is null
// on a row whose payload has no such key, `error` is null on every row that did not abort.
// `.passthrough()` keeps the tail open while the schema stays strict about every key it does declare.
export const TransactionSchema = z
  .object({
    tx_id: z.string(),
    type: z.string(),
    state: z.string(),
    initiator_pid: z.string().nullable(), // null on a CLEARING (028 F-028-45)
    created_at: z.string(),
    updated_at: z.string(),
    equivalent: z.string().nullable().optional(),
    error: z.record(z.string(), z.unknown()).nullable().optional(),
    // ATTRIBUTION FIELDS, declared 2026-09-10 after the internal adversarial review pointed out
    // that the canon gained them and this schema did not - so they reached the consumer only
    // through `.passthrough()`, and the consumer had to launder every row through a cast. The rule
    // written above this schema ("strict about every key it does declare") had been applied to the
    // key removed and not to the three added.
    //
    // Present per type and never both: `from`/`to` on a PAYMENT, `edges` on a CLEARING, and absent
    // when the internal payload does not carry them - which is why none of them is required.
    from: z.string().optional(),
    to: z.string().optional(),
    edges: z
      .array(z.object({ debtor: z.string(), creditor: z.string() }).passthrough())
      .optional(),
  })
  .passthrough()

export const GraphSnapshotSchema = z
  .object({
    participants: z.array(ParticipantSchema),
    trustlines: z.array(TrustlineSchema),
    equivalents: z.array(EquivalentSchema),
    debts: z.array(DebtSchema),
    audit_log: z.array(AuditLogEntrySchema),
    transactions: z.array(TransactionSchema),
    // F-013-1 / T1302. Which optional collections the body actually carries, and which of them hit
    // the include limit. Optional here on purpose: the canon does not list them under `required`,
    // and demanding a field the canon does not declare is the exact defect this task removes from
    // TransactionSchema above. Absent means "this server says nothing about them", which the
    // consumer must treat as "not asked" - never as "asked, and there are none".
    // The names are a CLOSED set, and the canon says so (`api/openapi.yaml`,
    // `AdminGraphSnapshotResponse.included`). Declared as an enum after external review found the
    // canon narrower than both implementations: `z.string()` here and `list[str]` on the server
    // would have accepted a fourth name silently, on a field whose entire purpose is to be trusted
    // when a consumer decides whether it may draw a conclusion.
    // 032 S5 (A-4): `incidents` left the set with the incidents surface (always empty since 019 stage 4).
    included: z.array(z.enum(['audit_log', 'transactions'])).optional(),
    truncated: z.array(z.enum(['audit_log', 'transactions'])).optional(),
  })
  .passthrough()

export const BalanceRowSchema = z
  .object({
    equivalent: z.string(),
    outgoing_limit: DecimalString,
    outgoing_used: DecimalString,
    incoming_limit: DecimalString,
    incoming_used: DecimalString,
    total_debt: DecimalString,
    total_credit: DecimalString,
    net: DecimalString,
  })
  .passthrough()

export const ParticipantMetricsSchema = z
  .object({
    pid: z.string(),
    equivalent: z.string().nullable(),
    // 032 S5 (F-1): the balance rows are the whole answer; the participant analytics were removed.
    balance_rows: z.array(BalanceRowSchema),
  })
  .passthrough()

export const ParticipantsStatsSchema = z
  .object({
    participants_by_status: z.record(z.string(), z.number()),
    participants_by_type: z.record(z.string(), z.number()),
    total_participants: z.number(),
  })
  .passthrough()

// 032 S5 (F-2, F-3): the Dashboard's row of one equivalent - its active lines and their money.
export const LiquiditySummarySchema = z
  .object({
    equivalent: z.string().nullable(),
    updated_at: z.string(),
    active_trustlines: z.number(),
    // 028 F-028-37: без эквивалента сервер не суммирует деньги — `null`, а не сумма разных единиц.
    total_limit: DecimalString.nullable(),
    total_used: DecimalString.nullable(),
    total_available: DecimalString.nullable(),
  })
  .passthrough()

function paginatedSchema<T extends z.ZodType>(itemSchema: T) {
  return z
    .object({
      items: z.array(itemSchema),
      page: z.number().int().min(1),
      per_page: z.number().int().min(1).max(200),
      total: z.number().int().min(0),
    })
    .passthrough()
}

export const ParticipantsListSchema = paginatedSchema(ParticipantSchema)
export const TrustlinesListSchema = paginatedSchema(TrustlineSchema)
export const AuditLogListSchema = paginatedSchema(AuditLogEntrySchema)
export const EquivalentsListSchema = z.object({ items: z.array(EquivalentSchema) }).passthrough()
