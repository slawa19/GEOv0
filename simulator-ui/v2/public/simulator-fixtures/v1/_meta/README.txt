This folder contains meta docs for simulator fixtures.
Fixtures live under: public/simulator-fixtures/v1/<EQ>/...

For demo-fast-mock v2, the active EQ is UAH.

Event playlists (<EQ>/events/demo-tx.json, demo-clearing.json) are played by the offline effects workbench (042 T4205).
Amounts (2026-10-18, spec 039 Changelog): the recorded patches are financially inconsistent (inherited), so the
amounts were derived by one rule, held by src/composables/usePlaylistWorkbench.test.ts:
- tx.updated: amount = the receiver's net balance increase in its node_patch (= the sender's decrease), replayed
  from the snapshot in playlist order; from/to = the route's ends; amount_flyout = true (as the live server sends).
  The edge_patch `used` deltas are NOT the amount: they differ leg to leg and from the node patches.
- clearing.done: cleared_amount = sum over the cycles of cycle_edges of the reduction of `used` common to every
  line of the cycle (its minimum); cleared_cycles = the number of cycles.
