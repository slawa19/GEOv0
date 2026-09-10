**Findings; not ready to close.** Reviewed `c80f600..95f5efe` read-only. I did not rerun the reported gates or existing mutations. New probes used the reviewed source in memory; sample checks used a small assertion harness and existing Vue/TypeScript dependencies, **not Vitest or canonical gates**. The working tree remains clean.

**Q1. The mechanisms have two incorrect trustline states.**

- **P2 — “No row” authorizes editing snapshot figures.** In [trustlinesSourceState.ts:74](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/simulator-ui/v2/src/composables/interact/trustlinesSourceState.ts:74), `no-row` permits every guarded action. Concrete state: REST answers `[]`; the snapshot still contains A→B with limit `100`; the editor is open for A→B. [SimulatorAppRoot.vue:655](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/simulator-ui/v2/src/components/SimulatorAppRoot.vue:655) returns that snapshot line, and [TrustlineManagementPanel.vue:91](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/simulator-ui/v2/src/components/TrustlineManagementPanel.vue:91) presents its figures without a warning. Update and Close are enabled.

  The in-memory probe returned `source={"kind":"no-row"}`, `canUpdateOrClose=true`, `notice=null`. The justification in `spec.md:532` establishes permission to **create** a missing line. It does not establish permission to update or close an existing line using contradicted snapshot figures. Those handlers call distinct update/close operations (`useInteractMode.ts:568,590`).

  **Target:** preserve authoritative absence, allow the creation flow, and stop presenting the snapshot line as an editable existing row.

- **P3 — Freezing unconfirmed data upgrades its provenance.** [SimulatorAppRoot.vue:694](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/simulator-ui/v2/src/components/SimulatorAppRoot.vue:694) assigns `frozen` whenever a frozen link exists, without retaining its source state. Concrete state: `never-asked`, snapshot fallback displayed, then “Send Payment” freezes the popup. The unavailable-source warning disappears although no answer arrived.

  The new test explicitly constructs this state and expects the warning to disappear (`SimulatorAppRoot.interact.test.ts:4000,4019`). This is a frozen **snapshot**, not necessarily a frozen authoritative answer. Buttons remain disabled through `wmEdgeDetailEffectiveBusy` (`SimulatorAppRoot.vue:514`), so this finding concerns misleading provenance rather than an executable mutation.

  **Target:** freeze the source information alongside the link.

`CountConfidence` also loses relevant distinctions, described in Q2. The producer’s `limit + 1` mechanism correctly distinguishes omitted, empty, complete and truncated collections on the paths inspected (`app/api/v1/admin.py:210`). Its schema enforcement has the Q5 gap.

**Q2. A third new false unknown exists.**

**P2 — An unattributable payment erases a known clearing zero.** [useGraphAnalytics.ts:801](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/admin-ui/src/composables/useGraphAnalytics.ts:801) attaches one `incomplete` flag to the entire transactions collection. Both payment and clearing cells consume it (`GraphAnalyticsDrawer.vue:1359,1367`).

Concrete input:

```text
included = ["transactions"]
truncated = []
selected participant = A
transactions = [
  COMMITTED PAYMENT, equivalent EUR, initiator B, from B, no to
]
```

The complete collection contains **no clearings**. That count is known independently of the payment’s missing recipient. Executing the current source produced:

```text
clearingCount = {"7":0,"30":0,"90":0}
confidence.incomplete = true
display = "— / — / —"
expected = "0 / 0 / 0"
```

The uncertainty is legitimate for payment attribution; propagating it to clearings creates ignorance the system does not have. The same flag also spans all three time windows.

**Target:** determine attribution completeness for the particular counter, including transaction type and relevant window, rather than only for its containing collection.

**Q3. Two plausible wrong implementations satisfy the named samples.**

**P3 — The samples do not cover independent audit truncation or a pending cache request.**

I wrote and evaluated these replacements **in memory**:

| Wrong implementation | Existing sample result | Concrete missed case |
|---|---:|---|
| Replace `collectionConfidence('audit_log', opts.included.value, opts.truncated.value)` with `collectionConfidence('audit_log', opts.included.value, [])` at `useGraphAnalytics.ts:575` | **21/21** confidence sample callbacks passed | An included, truncated audit log prints exact totals instead of `≥N`. |
| Delete `if (trustlinesLoadingRef.value) return { kind: 'loading' }` at `useInteractDataCache.ts:143` | **7/7** snapshot/cache sample callbacks passed | A pending first request reports `never-asked` instead of `loading`. |

The confidence sample truncates incidents while audit remains complete ([activityConfidence.test.ts:291](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/admin-ui/src/composables/useGraphAnalytics.activityConfidence.test.ts:291)); it never reverses those roles. The cache state samples await immediately settled responses ([snapshotTrustlines.test.ts:195](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/simulator-ui/v2/src/composables/interact/useInteractDataCache.snapshotTrustlines.test.ts:195)); they do not inspect a deferred request while pending.

These results establish weaknesses in those samples, not that the complete repository suite would accept both replacements.

**Q4. F-013-7 closes the wrong half again.**

The strongest evidence is [SimulatorAppRoot.interact.test.ts:3863](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts:3863): after an authoritative empty answer, the test requires snapshot limit `100` to remain visible and then requires **Update and Close** to be enabled. Its preceding justification discusses the operator’s right to **create** a line. The test protects the mistaken inference identified in Q1.

For the other original findings:

| Finding | Assessment from the inspected implementation and assertions |
|---|---|
| F-013-1 | Producer projection, include requests and decoding address the named defect; completeness remains too coarse under Q2. |
| F-013-2 | Both payment-target invalidation and active prefetch read `data_revision` (`useInteractDataCache.ts:382`, `useInteractMode.ts:273`). Tests exercise `tx.updated`, `clearing.done` and the `run_status` counterexample. |
| F-013-3 | Effective `sizeForNode` dimensions enter the key (`outlineCache.ts:43`); tests distinguish changed geometry, equivalent normalized sizes and unrelated entries (`fxRenderer.cache.test.ts:151,187,228`). |
| F-013-4 | The getter now requires all three responses and reads degradation (`health.ts:65`). No additional reachable defect established. |
| F-013-5 | Header uses server `total` (`IncidentsPage.vue:156`); row, drawer and dashboard no longer apply the redundant predicate (`IncidentsPage.vue:320,426`; `DashboardPage.vue:157`). |
| F-013-6 | Exact filter matching is implemented (`mockApi.ts:1222`). Full validation parity is **not** claimed honestly: the status test explicitly documents mock empty-success versus real 422 (`mockApi.trustlineFilterExactness.test.ts:163`). |
| F-013-7 | Not closed: authoritative absence is converted into permission to edit fallback figures. |

**Q5. Runtime projection is aligned; the new metadata schemas diverge.**

**P3 — The digest update accepts newly introduced enum drift.**

The canon restricts `included` and `truncated` elements to three names ([openapi.yaml:4540](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/api/openapi.yaml:4540)). Pydantic declares unrestricted `list[str]` ([graph.py:60](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/app/schemas/graph.py:60)); Zod likewise accepts arbitrary strings (`realApi.ts:229`).

A read-only schema probe confirmed:

```text
canonical items: string enum [incidents, audit_log, transactions]
generated items: string
model accepts: included=["not_a_collection"]
```

That invalid name is a concrete validator discrepancy, **not an observed response from the current producer**.

One correction to the question’s premise: the ratchet does compare canonical and generated schemas (`test_openapi_contract.py:1609`). However, it records their differences and checks the aggregate digest. Updating that digest accepts the difference; it does not prove alignment. The comment that “no entry got worse” (`test_openapi_contract.py:250`) is therefore unsupported for these new enum constraints.

For the five added fields:

- `included`/`truncated` are emitted by the shared helper and returned by both routes (`admin.py:210,1833,2205`).
- PAYMENT `from`/`to` are emitted conditionally as strings (`admin.py:329`), declared in the canon and accepted by Zod (`realApi.ts:207`).
- CLEARING `edges` is emitted conditionally with debtor/creditor strings (`admin.py:336`), likewise declared and accepted.
- I found **no added field that is universally declared but never emitted, or emitted but undeclared**. The divergence is validation/schema precision.

**Q6. Deferral is defensible; part of its evidence record is not reviewable.**

- **P3 — The include-cost decision lacks its claimed supporting record.** [spec.md:559](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/specs/013-frontend-data-honesty/spec.md:559) reports `+10.5%`, `+4.6%`, `+15.1%`, then says a separate task carries the measurement. I could not locate that task, an attached measurement, or a reproducing command in the reviewed tree. The percentages appear only in this narrative. **Insufficient evidence to assess the measurement**, without rerunning it.

  Deferring inclusion is nevertheless defensible: the client requests only transactions (`useGraphData.ts:36`), metrics supplies the activity counters, and truncated incident data requires deliberate presentation. The record should link the actual deferred task and evidence. The earlier “structurally zero” paragraph (`spec.md:502`) also needs an explicit superseded marker because the later paragraph corrects it.

- **Unused graph tabs:** defensible cleanup deferral. The import scan found no callers; the live page imports `GraphAnalyticsDrawer` (`GraphPage.vue:18`). `spec.md:506` openly describes the duplication and leaves its removal to 016. It does not disguise removal as completed work.

- **Payment attribution in metrics:** the handoff is honest. The implementation still uses payload counterparties (`metrics.py:693`), while [BACKLOG.md:551](C:/Users/admin/AppData/Local/Temp/geov0-013-review-1789070534/clone/specs/BACKLOG.md:551) records the exact defect, scope boundary, recipient and pending authorization. That is recorded unresolved work, not a claimed fix. However, `useGraphAnalytics.ts:589` should not describe these server counters as unconditionally exact while this limitation remains documented.

VERDICT-013: FINDINGS
VERDICT-MECHANISMS: BROKEN
VERDICT-NEW-DEFECT: FOUND
VERDICT-SAMPLE-BIAS: FOUND
VERDICT-CONTRACT: DIVERGENT
VERDICT-READY-TO-CLOSE: NO