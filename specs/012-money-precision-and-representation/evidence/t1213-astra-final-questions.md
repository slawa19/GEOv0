**I would not accept the decision as justified or authorized.** The legacy-row repair path works, but the central argument conflates display precision with storage scale. I also found a reproducible test defect.

Reviewed `422ed50` read-only; working tree remained clean. Runtime evidence below is from **debug-only, in-memory probes**, not canonical gates or PostgreSQL execution.

**Q1. Keep 18 under the existing semantics; narrowing requires a separate product decision.**

The tree explicitly says these are **different quantities**:

- `app/utils/money.py:28` defines precision as the **minimum displayed fraction digits**.
- `app/utils/money.py:34` explicitly calls it a display parameter, and explains why accepting `0.05 HOUR` with precision 1 is intentional.
- `specs/012-money-precision-and-representation/spec.md:551` defers “precision is the minimum monetary quantum” to a separate, versioned decision with a data audit.
- `app/utils/validation.py:448` parses money independently of equivalent precision, then checks actual storage representability at line 461.

Consequently, declaring precision 12 does **not**, under the implemented contract, authorize twelve significant fractional digits in stored money. Padding an exact stored value with zeros also does not invent monetary value.

The new PostgreSQL measurement bypasses the money door: it creates a temporary numeric column at `tests/integration/test_p012_t1212_declared_precision_exceeds_storage_scale_postgres.py:192` and inserts raw SQL at line 204. That demonstrates PostgreSQL rounding, not a live payment path admitting unstorable money.

The protocol’s 0–8 declaration (`docs/ru/02-protocol-spec.md:155`) supports choosing that range as policy. It does not establish that narrowing fixes the demonstrated storage defect. Widening storage is likewise unsupported. My recommendation is **preserve 18 as display precision and retain the independent storage-bound door**, pending an authorized reconciliation.

**Q2. Out of surface; the provenance statement is candid but insufficient.**

Program 012 explicitly excludes `api/openapi.yaml` at `specs/012-money-precision-and-representation/spec.md:65`, repeated in Non-goals at line 344. This commit nevertheless changes its precision bound at `api/openapi.yaml:4093`. The permitted surfaces at spec lines 55–60 also do not authorize an equivalent-schema product redesign.

The same spec acknowledges at line 1353 that this fork belongs to unauthorized `F-015-14`; program 015 still marks `T1518` `[!]` at `specs/015-financial-core-verification/spec.md:333`.

Recording “S1 called this the owner’s decision; not reconfirmed” at 012 spec line 1351 honestly exposes uncertainty. It does not resolve it. **Insufficient evidence** establishes the underlying owner instruction or an authorized scope transfer. Meanwhile, `app/utils/validation.py:20` presents “owner’s decision” unqualified.

Previously established authorization would not require repeated confirmation. Here, the tree supplies an attributed assertion alongside contradictory scope rules. Implementation should have waited for verified authorization or an explicit reassignment; authorizing all of 015 was not the only possible remedy.

**Q3. Keep lexical 18. There is a stronger compatibility reason than the new commentary gives.**

Lexical scale counts spelling; storage scale constrains value. Changing `MONEY_MAX_LEXICAL_SCALE` to 8 would reject `"0.100000000"` even though its value is exactly storable. The checks are separate at `app/utils/validation.py:284` and line 461.

More importantly, **legacy precision-12 rows remain readable and still produce twelve-digit strings**. My probe confirmed:

```text
LEGACY RENDER ROUNDTRIP 0.100000000000 0.100000000000
```

Therefore 18 still protects real legacy renderer-to-door compatibility, not merely hypothetical formatter inputs. The statement at `app/utils/validation.py:182` that producers can now emit at most eight digits overlooks retained legacy rows.

Eighteen is a compatibility-preserving input cap, not a mathematically unique optimum. Moving it to eight would introduce another breaking change.

**Q4. The backend migration claim is safe for the specified row, with an operational qualification.**

For a canonical code such as `LEGACY12`:

| Operation | Result and evidence |
|---|---|
| Admin list | Returns precision 12 through `StoredEquivalent`; `app/api/v1/admin.py:1143`. Confirmed in memory. |
| Public list | Returns precision 12 when active; `app/api/v1/equivalents.py:27`. Confirmed in memory. |
| Detail read | **No equivalent-detail GET exists.** The per-equivalent GET is `/usage`, returning counts without precision; `app/api/v1/admin.py:1296`, line 1311. Router inspection confirmed this. |
| PATCH without precision | Raises **409**, “Legacy equivalent precision must be repaired by this PATCH”; `app/api/v1/admin.py:1219`. Confirmed before mutation. |
| PATCH precision to 8 | Assigns at line 1241, validates the strict response at line 1257, commits at line 1268. Confirmed response precision 8 and persisted precision 8. |

There is no precision-induced 500, response-validation failure, or unrepairable canonical row on these paths. ORM hydration bypasses assignment validation; the read projection leaves precision unbounded at `app/schemas/equivalents.py:15`.

However, **“no migration required” does not mean “no operational change.”** Description-only edits and activation/deactivation now require precision repair first. The UI’s activation action supplies no precision (`admin-ui/src/pages/EquivalentsPage.vue:163`). Existing operators therefore encounter a new 409 until repair.

**Q5. Found omissions and one reproducible guard failure.**

- **The new fixture guard fails on a clean checkout.** It requires generated `admin-ui/dist/admin-fixtures/v1/datasets/equivalents.json` at `tests/integration/test_p012_t1212_declared_precision_exceeds_storage_scale_postgres.py:92`, asserts existence at line 260, and counts that copy at line 265. Direct execution produced:

  ```text
  admin-ui/dist/admin-fixtures/v1/datasets/equivalents.json is gone; re-derive the list before trusting the count
  ```

  `dist` is ignored (`.gitignore:152`). The PostgreSQL job runs this tier without building Admin UI (`.github/workflows/quality.yml:219`). This is a reproducibility defect, not evidence that supplied precision values are invalid.

- **The actual tracked datasets fit 0–8.** I enumerated all five tracked `equivalents.json` files. Seeds contain precision 1 and 2 (`seeds/equivalents.json:10`); the three canonical Admin fixture datasets contain only 2, including HOUR (`admin-fixtures/v1/datasets/equivalents.json:16` and the corresponding pack files). The HOUR discrepancy between seeds and Admin fixtures predates this change; narrowing does not reconcile it.

- **Simulator catalogue reads correctly remain tolerant.** `simulator-ui/v2/src/config/equivalentPrecision.ts:52` uses `normalizePrecision`, which has no upper clamp (`simulator-ui/v2/src/utils/money.ts:99`). That supports legacy rows. Its test’s precision-9 example has an empty code and is discarded (`simulator-ui/v2/src/config/equivalentPrecision.test.ts:52`); it proves nothing about retaining a usable legacy precision-12 row. Add that coverage rather than narrowing the reader.

- **The mock mishandles newly legacy rows.** A description-only PATCH preserves precision 12 and immediately decodes it with the now-strict mutation schema (`admin-ui/src/api/mockApi.ts:1313`, line 1317). This throws `INVALID_RESPONSE` reporting status 200 (`admin-ui/src/api/adminContracts.ts:223`), whereas the backend deliberately returns repair-required 409. Activation has the same mismatch at `mockApi.ts:1344`. Explicit PATCH-to-8 remains repairable.

- **Dropping producer coverage above 8 overlooks reachable legacy inputs.** The justification that nothing was lost appears at `specs/012-money-precision-and-representation/spec.md:1384`. Yet producers still read persisted precision directly—for example `app/core/balance/service.py:131`. Retain at least one production-path test with a legacy row inserted through the table, as the repair test already does.

VERDICT-DECISION: KEEP-18
VERDICT-AUTHORITY: OUT-OF-SURFACE
VERDICT-LEXICAL-18: KEEP
VERDICT-MIGRATION: SAFE
VERDICT-MISSED: FOUND