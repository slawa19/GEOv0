# GEO PWA Client — Detailed Specification (Blueprint)

**Version:** 0.3
**Status:** Blueprint for implementation without "guesswork"
**Stack (MVP):** Vue.js 3 (Vite), Pinia. UI layer: Tailwind CSS (optional).

Document aligned with current API contract: `api/openapi.yaml` + `docs/en/04-api-reference.md`.

---

## 1. Goals and Scope

### 1.1. Goals (MVP)
- Create/restore local wallet (Ed25519 keys) and connect to selected Hub.
- Show balances by equivalents, payment availability, and history.
- Manage trustlines (create/update/close) and contacts.
- Send payments via QR/Deeplink and show transaction details.

### 1.2. Non-Goals (in this version)
- Full offline mode for operations (offline read-only cache only).
- Multi-wallet management simultaneously.

---

## 2. Information Architecture (Sitemap)

Application — SPA with bottom navigation (Tab Bar).

### 2.1. Screen Map

1) **Auth / Onboarding (outside Tab Bar):**
- `Welcome` — choice: "Create Wallet" / "Restore".
- `KeyGeneration` — key creation.
- `SeedBackup` — phrase display/export + confirmation test.
- `WalletUnlock` — PIN/password entry to decrypt local secret.
- `HubConnect` — Hub Base URL entry + login (Challenge-Response).
- `SessionExpired` — expired JWT/refresh, re-login required.

2) **Main Tabs:**
- `Home` (Dashboard) — balances by equivalents + quick QR.
- `Trust` — list of contacts/trustlines.
- `Activity` — operation history (payments and related events).
- `Settings` — PID/Profile QR, Hub, export/backup, security.

3) **Auxiliary Screens (from tabs, but separate routes):**
- `ScanQR` — QR scanning/string paste.
- `PaymentCompose` — send payment (recipient, equivalent, amount, preflight).
- `PaymentConfirm` — signature confirmation and send.
- `PaymentResult` — success/error + CTA to details.
- `TransactionDetails` — transaction details by `tx_id`.
- `ContactDetails` — participant profile + trustlines (by equivalent).
- `TrustlineEdit` — create/modify/close trustline.
- `EquivalentPicker` — equivalent selection (list + search + manual entry).

---

## 3. User Flows (Scenarios)

### 3.1. Wallet Creation and Hub Login
1) `Welcome` → "Create Wallet".
2) `KeyGeneration`: Ed25519 keypair generated.
3) `SeedBackup`: user secures seed/export and passes test.
4) `WalletUnlock`: user sets PIN/password (to encrypt local secret).
5) `HubConnect`: enter `hub_base_url`.
6) Login to Hub via challenge-response (detailed in API mapping section).
7) Transition to `Home`.

### 3.2. Wallet Restoration
1) `Welcome` → "Restore".
2) Import seed/export.
3) `WalletUnlock`: set new PIN/password (re-encryption of local secret).
4) `HubConnect`: login via challenge-response.

### 3.3. Sending Payment via QR/Deeplink
1) User opens `ScanQR` (button on `Home`).
2) Scans QR.
3) `PaymentCompose` autofills (PID, equivalent/amount — if present).
4) On amount entry, preflight (`/payments/capacity`) runs, shows "Available".
5) `PaymentConfirm`: final confirmation → sign → `POST /payments`.
6) `PaymentResult` → `TransactionDetails`.

### 3.4. Create/Update Trustline
1) `Trust` → "Add Contact" (search/QR).
2) `ContactDetails` → "Set Limit".
3) `TrustlineEdit`: select equivalent, set limit, confirm signature.
4) `POST /trustlines` or `PATCH /trustlines/{id}`.

---

## 4. Data and Local Storage

### 4.1. Principles
- Only UX-necessary data stored locally (cache + encrypted secret).
- Private key and seed are never sent to network.

### 4.2. Storage
- **IndexedDB** (normative):
    - `wallet.v1` — encrypted secret + KDF parameters.
    - `cache.v1` — read-only cache (balances, trustlines, activity) bound to `hub_base_url`.
- **Memory** (runtime):
    - decrypted private key (for session duration)
    - `access_token`

---

## 5. QR/Deeplink Format (v1, Normative)

### 5.1. General Rules
- QR payload — UTF-8 string.
- Any URL parameters must be percent-encoded.
- Client must validate format before actions.

### 5.2. URI Scheme

Minimum two types supported:

1) **Participant Profile**
```
geo://v1/participant?pid={PID}&hub={HUB_BASE_URL?}
```

2) **Payment Request**
```
geo://v1/pay?to={PID}&equivalent={CODE?}&amount={AMOUNT?}&memo={TEXT?}&hub={HUB_BASE_URL?}
```

Notes:
- `hub` — optional. If specified and different from current, UI must show warning and offer to switch Hub.
- `amount` — amount string (`"100.00"`), or absent.
- `memo` — optional, display only (not necessarily sent to server).

### 5.3. Format Errors
- If payload does not start with `geo://v1/` → show "Unsupported QR".
- If `pid`/`to` fails PID validation → show "Invalid PID".

---

## 6. Security Model (Normative)

### 6.1. Keys and Identifiers
- Ed25519 keypair generated on client.
- PID calculated as `base58(sha256(pubkey))`.
- Server authentication: challenge-response (signed by private key).

### 6.2. What is "seed" in UX Context
- If seed-phrase/export is used, it is private material sufficient to restore private key.
- UX must call this "Recovery Secret" and explicitly warn about risks.

### 6.3. Secret Encryption on Device
- Secret (seed/privkey) stored **encrypted only**.
- KDF: PBKDF2-HMAC-SHA256 (WebCrypto), parameters:
    - `salt`: 16+ bytes (random)
    - `iterations`: minimum 200_000 (may be higher on desktop)
- Cipher: AES-GCM:
    - `iv`: 12 bytes (random)
    - `ciphertext`: bytes

Store object in IndexedDB (logical example):
```json
{
    "version": 1,
    "kdf": {"name": "PBKDF2", "hash": "SHA-256", "salt": "base64...", "iterations": 200000},
    "cipher": {"name": "AES-GCM", "iv": "base64..."},
    "ciphertext": "base64..."
}
```

### 6.4. Tokens
- `access_token` store in memory.
- `refresh_token` store in IndexedDB (preferably encrypted with same key as secret).
- On `401` client makes one `POST /auth/refresh` attempt, then either retries or transitions to `SessionExpired`.

---

## 7. API Mapping (Screen → Endpoint → Fields → Cache)

Note: v0.1 endpoints return successful responses as plain JSON (no `{success,data}` envelope).
Errors use a common envelope shape: `{ "error": {"code": "...", "message": "...", "details": {...}} }`.

### 7.1. Auth / Hub

**HubConnect (login):**
- `POST /auth/challenge` → `{challenge, expires_at}`
- `POST /auth/login` (signed challenge) → `{access_token, refresh_token, expires_in, participant}`

**Participant Registration (first run):**
- `POST /participants` → `{pid, display_name, status, created_at}`

**Token Refresh:**
- `POST /auth/refresh` → `{access_token, refresh_token, expires_in}`

Cache:
- `hub_base_url` (setting)
- `participant` (in cache.v1)

### 7.2. Home (Dashboard)

Data:
- `GET /balance` → `BalanceSummary.equivalents[]`:
    - `code`
    - `net_balance`
    - `available_to_spend`, `available_to_receive`

Cache:
- `balance_summary` on `hub_base_url`, TTL 60s (update pull-to-refresh when online).

### 7.3. Activity (History)

List:
- `GET /payments?direction=all&equivalent={code?}&status={...}&page={...}` → `PaymentResult[]`

Details:
- `GET /payments/{tx_id}` → `PaymentResult`

Cache:
- activity pages cache (read-only), TTL 5–10 minutes.

### 7.4. ScanQR / PaymentCompose / PaymentConfirm

Preflight (capability check):
- `GET /payments/capacity?to={pid}&equivalent={code}&amount={amount}` →
    - `can_pay`, `max_amount`, `routes_count`, `estimated_hops`

Diagnostics (on user request, not default):
- `GET /payments/max-flow?to={pid}&equivalent={code}` → `max_amount` + `paths[]` + `bottlenecks[]`

Create:
- `POST /payments` → `PaymentResult` (incl. `status`, `routes[]` on success, or `error` if ABORTED)

### 7.5. Trust / ContactDetails / TrustlineEdit

Contacts/Participants:
- `GET /participants/search?q={...}&type={...}&page={...}&per_page={...}` (pagination)
- `GET /participants/search?q={...}&type={...}&limit={...}` (simple limit)
- `GET /participants/{pid}`

Trustlines:
- `GET /trustlines?direction={...}&equivalent={...?}&status={...?}&page={...}`
- `POST /trustlines`
- `PATCH /trustlines/{id}`
- `DELETE /trustlines/{id}`

### 7.6. Settings

Profile:
- `GET /participants/me`
- `PATCH /participants/me`

### 7.7. WebSocket (Real-time)

Connect:
- `wss://{hub_base_url}/api/v1/ws?token={access_token}`

Subscribe to events (minimum set):
- `payment.received` — incoming payment
- `trustline.updated` — trustline change
- `clearing.completed` — clearing executed

Heartbeat:
- Client sends `ping` every 30 seconds if no outgoing traffic.
- If no incoming messages for 90 seconds — consider connection broken.

Reconnect Strategy (Normative, aligned with `04-api-reference.md` §7.6):
- Reconnect with exponential backoff: 1s, 2s, 5s, 10s, 30s.
- After reconnect: re-`subscribe`, verify state via REST.
- Readiness for duplicates and event skips (at-most-once delivery).

---

## 8. Equivalent Picker (Data Source)

### 8.1. Source (Priority)
1) `GET /equivalents` (if implemented on Hub) — normal dictionary.
2) Else: union of codes from `GET /balance`, `GET /trustlines` (locally known equivalents).
3) Else: manual entry (user types arbitrary code).

### 8.2. Equivalent Code Validation
- `CODE` must be string length 2..16.
- Allowed chars: latin/digits/`_`/`-`.
- Recommendation: show warning if code not in dictionary.

---

## 9. UI States Matrix (Normative)

### 9.1. General States
- **Loading:** skeleton/loader + disable actions requiring data.
- **Empty:** explanatory text + CTA.
- **Error:** human-readable message + "Retry".
- **Offline:** banner + block write operations.

### 9.2. By Screen (Minimal Set)

**Home:**
- Loading: "Loading balance..."
- Empty: "No equivalents yet. Create a trustline or receive a payment." → CTA: "Add Trust"
- Error: "Failed to load balance" → CTA: "Retry"

**Trust:**
- Empty: "No trustlines" → CTA: "Add Contact"

**Activity:**
- Empty: "No operations"

**PaymentCompose:**
- If `can_pay=false`: show "Insufficient capacity" + button "Show Max" (calls `/payments/max-flow`).

**SessionExpired:**
- Text: "Session expired. Sign in again."
- CTA: "Unlock Wallet" → `WalletUnlock` → `HubConnect`.

---

## 10. AI Instructions (Prompts)

Principle: generate components without defining "new colors/themes", use base palette and existing project design tokens.

### 10.1. Dashboard
> "Create Vue 3 Dashboard component. Top — list of equivalent cards (code, net_balance, available_to_spend). Below — button 'Scan QR' and list of recent operations (up to 5). Add loading/empty/error states and offline handling (read-only)."

### 10.2. Payment compose
> "Create PaymentCompose component. Fields: recipient PID (readonly), equivalent (via EquivalentPicker), amount. On amount input perform /payments/capacity request and show can_pay/max_amount/routes_count/estimated_hops. Button 'Send' leads to PaymentConfirm."

### 10.3. Key / wallet storage
> "Write TypeScript service for wallet secret storage in IndexedDB encrypted: PBKDF2(SHA-256) + AES-GCM. Functions: setupPin(pin, secret), unlock(pin) -> secret, lock(), isUnlocked(), rotatePin(oldPin,newPin)."