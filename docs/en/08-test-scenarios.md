# GEOv0-PROJECT — MVP Test Scenarios (v0.1)

**Goal:** provide a reproducible set of scenarios for end-to-end MVP verification and future automation (e2e/integration tests).

Scenario format:

- **ID**
- **Name**
- **Preconditions**
- **Steps**
- **Expected Result**
- **Invariant Checks / Notes**

---

## 0. Common Notation

Participants:

- Alice = `PID_ALICE`
- Bob = `PID_BOB`
- Carol = `PID_CAROL`
- Dave = `PID_DAVE`

Equivalent:

- `UAH` (precision 2)

By "balance" we mean values available via the balance API and payment/transaction history.

---

## 1. Onboarding and Identity

### TS-01. Register New Participant (with Ed25519 Keys)

**Preconditions:** none

**Steps:**
1. Client generates Ed25519 keypair.
2. Client submits registration (with signature per protocol).
3. Receives `pid` and profile.

**Expected Result:**
- Participant created with status `active`.
- Public key saved.
- Re-submitting the same request with the same `tx_id` — idempotency (no duplicate).

**Checks:** signatures, time drift, idempotency.

### TS-02. Login (Get JWT) and Access Protected Endpoints

**Preconditions:** `PID_ALICE` is registered

**Steps:**
1. Alice completes challenge-response.
2. Receives JWT.
3. Requests `/balance`.

**Expected Result:**
- JWT is valid.
- `/balance` returns correct structure.

### TS-03. JWT Expiry and Refresh

**Preconditions:** `PID_ALICE` is authorized

**Steps:**
1. Wait for access token expiry.
2. Retry request.
3. Use refresh.

**Expected Result:**
- Service returns 401 for expired access token.
- Refresh issues new access token.

---

## 2. Participants and Profiles

### TS-04. Search Participant by Partial Name / Filters

**Preconditions:** ≥ 3 participants created

**Steps:**
1. Execute search by query.
2. Apply `type` filter.

**Expected Result:**
- Results limited by `limit`.
- Filter is correct.

---

## 3. TrustLines

### TS-05. Create Trustline Alice → Bob

**Preconditions:** Alice and Bob are registered

**Steps:**
1. Alice creates trustline to Bob with `limit=1000.00 UAH` and signature.

**Expected Result:**
- Trustline created.
- `used=0.00`.
- Re-submitting with same `tx_id` does not create duplicate.

### TS-06. Update Trustline Alice → Bob (Increase Limit)

**Preconditions:** trustline Alice → Bob exists

**Steps:**
1. Alice updates limit to 1500.00.

**Expected Result:**
- New limit applied.
- Invariants preserved.

### TS-07. Attempt to Reduce Limit Below Current `used`

**Preconditions:** debt exists on line Alice→Bob (used>0)

**Steps:**
1. Alice attempts to set `limit < used`.

**Expected Result:**
- Operation rejected (domain/HTTP error returned correctly).

### TS-08. Close Trustline When `used=0`

**Preconditions:** trustline Alice → Bob exists, `used=0`

**Steps:**
1. Alice closes trustline.

**Expected Result:**
- Trustline closed/removed per rules.
- Event recorded in history.

### TS-09. Close Trustline When `used>0`

**Preconditions:** trustline Alice → Bob exists, `used>0`

**Steps:**
1. Alice closes trustline.

**Expected Result:**
- Operation rejected.

---

## 4. Payments: Capacity, Max-flow, Single-path

### TS-10. Check Capacity for Specific Amount

**Preconditions:** path Alice → Bob exists with available capacity ≥ 100.00

**Steps:**
1. Alice calls `GET /payments/capacity?to=PID_BOB&equivalent=UAH&amount=100.00`.

**Expected Result:**
- `can_pay=true`.
- `max_amount` ≥ 100.00.
- `routes_count` and `estimated_hops` are reasonable.

### TS-11. Calculate Maximum Payment (Max-flow)

**Preconditions:** trustlines graph created

**Steps:**
1. Alice calls `GET /payments/max-flow?to=PID_BOB&equivalent=UAH`.

**Expected Result:**
- `max_amount` matches algorithm estimate.
- `paths` and `bottlenecks` present and consistent.

### TS-12. Create Single-path Payment (Success)

**Preconditions:** route capacity ≥ amount

**Steps:**
1. Alice makes `POST /payments` for 100.00.

**Expected Result:**
- `status=COMMITTED`.
- `routes` contains 1 path with amount=100.00.
- Balances/used recalculated correctly.

### TS-13. Create Single-path Payment (Insufficient Capacity)

**Preconditions:** capacity < amount

**Steps:**
1. Alice makes `POST /payments` for 100.00 when only 50.00 available.

**Expected Result:**
- `status=ABORTED`.
- Error `Insufficient capacity`, `requested` and `available` correct.

---

## 5. Payments: Limited Multipath (2–3 Routes)

### TS-14. Multipath Payment: Amount Split Across 2 Routes

**Preconditions:** 2 independent routes exist with combined capacity ≥ amount

**Steps:**
1. Alice makes `POST /payments` for 120.00.

**Expected Result:**
- `status=COMMITTED`.
- `routes` contains 2 routes.
- Sum of routes = 120.00.
- Invariants preserved.

### TS-15. Multipath Payment: 3 Routes (max_paths_per_payment Limit)

**Preconditions:** limited multipath enabled; 3 routes available

**Steps:**
1. Alice makes payment for amount requiring 3 routes.

**Expected Result:**
- `routes` ≤ `max_paths_per_payment`.
- If >3 required, payment should be rejected or partially impossible per policy (MVP — rejection).

---

## 6. Experiment: Full Multipath (Feature Flag)

### TS-16. Enable Full Multipath and Repeat Max-flow

**Preconditions:** admin access, enable `feature_flags.full_multipath_enabled`

**Steps:**
1. Enable flag in admin console.
2. Alice calls `GET /payments/max-flow`.

**Expected Result:**
- `algorithm` reflects enabled mode (e.g., `full_multipath`).
- `max_amount` may increase (not required), but contract preserved.

---

## 7. Clearing

### TS-17. Automatic Clearing of Length-3 Cycle

**Preconditions:** debt cycle of length 3 formed

**Steps:**
1. Create debts forming cycle A→B→C→A.
2. Wait for triggered clearing.

**Expected Result:**
- Debts reduced/zeroed per clearing.
- Event recorded in history.

### TS-18. Effect of trigger_cycles_max_length Parameter

**Preconditions:** cycle of length 5 exists

**Steps:**
1. Set `clearing.trigger_cycles_max_length=4`.
2. Verify cycle of 5 is not cleared by trigger.
3. Enable periodic search for length 5.
4. Verify cycle of 5 is cleared periodically.

**Expected Result:**
- Behavior matches configuration.

---

## 8. WebSocket

### TS-19. Subscribe to Events and Receive Payment Notification

**Preconditions:** WS available, Alice subscribed

**Steps:**
1. Alice subscribes to `payment.received`.
2. Bob makes payment to Alice.

**Expected Result:**
- Alice receives WS event.
- Event matches schema.

---

## 9. Operator/Admin Console (Minimum)

### TS-20. Freeze Participant and Block Operations

**Preconditions:** admin access, Bob is active

**Steps:**
1. Admin freezes Bob.
2. Bob attempts to create payment or trustline.

**Expected Result:**
- Operation rejected.
- Audit log contains freeze record and operation attempt.

### TS-21. Change Parameters in Admin Console: max_paths_per_payment

**Preconditions:** admin access

**Steps:**
1. Change `routing.max_paths_per_payment` to 2.
2. Execute scenario TS-15.

**Expected Result:**
- Now `routes` ≤ 2.
- Audit log records the change.

---

## 10. Reliability and Idempotency

### TS-22. Retry POST /payments with Same tx_id

**Preconditions:** network/client simulates retry

**Steps:**
1. Send payment.
2. Repeat same request with same `tx_id`.

**Expected Result:**
- No duplicates.
- Response is consistent.

### TS-23. Concurrent Payments on Same Bottleneck

**Preconditions:** limited capacity

**Steps:**
1. Launch 2 payments in parallel competing for same trustline.

**Expected Result:**
- One may commit, other should receive correct error/abort.
- Invariants preserved.

---

## 11. Top-5 Scenarios for Minimal e2e Suite

- TS-01 Registration
- TS-05 Create trustline
- TS-12 Single-path payment success
- TS-14 Multipath payment 2 routes
- TS-17 Automatic clearing of length-3 cycle
