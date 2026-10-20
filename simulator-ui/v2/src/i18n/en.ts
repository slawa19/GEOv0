import { plural } from './messages'

/**
 * 046: the English dictionary of the interface - the reference one: its keys are the keys of every other language
 * (`ru.ts` is typed against it). Areas of the screen nest; a leaf is a whole sentence with `{name}` placeholders (never
 * a sentence glued from fragments), or a `plural(...)` message for a count. Names of participants and scenarios, codes
 * of equivalents and amounts are data and never live here.
 *
 * Wave 0 (T4601) brought the language switcher; wave 2 (T4603) the windows of the actions - payment, trust line,
 * clearing - with the texts they compose (the 037 A2 list, the refusal maps of `utils/paymentRefusalText.ts`, the
 * notices of `trustlinesSourceState.ts`, the toasts and history lines of `useInteractMode.ts`) and the analytics panel.
 * Wave 2 keeps every English text that existed byte for byte; a new key is new text. Wave 1 (T4602; narrowed, then
 * full after 048 S2) the first screen: the top bar and its "Details" rows, the action bar, the onboarding hint, the
 * bottom bar, the legend, "Find on graph", the line tooltip, the recent actions and the notifications - with the words
 * of spec 048 §G where §G names them (those English texts changed with it). Wave 3 (T4604) the participant card, the
 * line window and their shells (`card.*`, `edgeWindow.*`, `windowShell.*`), the trust line statuses and the busy-cancel
 * question; `interactStrings.ts` is gone. See spec 046.
 */
export const en = {
  locale: {
    switcherLabel: 'Language',
    switcherAria: 'Interface language',
    /** 046 closing (2026-10-20): the browser tab's title, written by `locale.ts` on load and on a switch. */
    documentTitle: 'GEO — mutual credit network demo',
  },

  /** Shared by the three windows of the actions. */
  common: {
    cancel: 'Cancel',
    close: 'Close',
    escHint: 'Esc: one step back, then close',
    pickPlaceholder: 'Pick on the canvas or choose',
    invalidAmount: "Invalid amount format. Use digits and '.' for decimals.",
    /** 046 decision 4 (advisor, round 1, 2026-10-18): a comma in an amount is refused, with the amount as it should be typed. */
    commaRefused: 'Use a dot: {example}',
    /** 028 F-028-48: the client's hint of the equivalent's step; the server decides. */
    stepHint: '{eq} allows at most {precision} decimal places; the server will refuse this amount.',
    /** 026 `T2602`: a negative `available` is the debt's excess over a lowered limit. */
    overLimit: 'over limit by {amount}',
  },

  /** Participant statuses (`chk_participant_status`, plus the scenario's `frozen`); an unknown one is shown as it came. */
  participantStatus: {
    active: 'active',
    frozen: 'frozen',
    suspended: 'suspended',
    left: 'left',
    deleted: 'deleted',
  },

  /** 046 wave 3: trust line statuses (`chk_trust_line_status`) the line window shows; an unknown one is shown as it came. */
  trustlineStatus: {
    active: 'active',
    closed: 'closed',
  },

  /**
   * Payment statuses (`chk_transaction_payment_terminal`); an unknown one is shown as it came. Words, as in Russian
   * (owner's review of the final frames, 2026-10-20: the result said «Status COMMITTED», the server's code).
   */
  paymentStatus: {
    COMMITTED: 'completed',
    ABORTED: 'rejected',
  },

  payment: {
    panelAria: 'Manual payment panel',
    title: 'Manual payment',
    titlePair: 'Manual payment: {from} → {to}',
    fromLabel: 'From',
    toLabel: 'To',
    amountLabel: 'Amount',
    fromTrigger: 'From participant',
    toTrigger: 'To participant',
    toSurface: 'To participant options',
    updating: '(updating…)',
    confirm: 'Confirm',
    sending: 'Sending…',

    // The progression of the form and why Confirm is disabled.
    nextChoiceRecipient: 'Choose the recipient.',
    needsParties: 'Choose the sender and the recipient first.',
    needsRecipient: 'Choose the recipient first.',
    enterPositive: 'Enter a positive amount.',
    selfPayment: 'You cannot send a payment to yourself.',
    selfPaymentReselect: 'You cannot send a payment to yourself. Please select a different recipient.',
    recipientGone: 'Selected recipient is no longer available. Please re-select.',
    noRoutesBetween: 'Backend reports no payment routes between selected participants.',
    noRoutesFromSender: 'Backend reports no payment routes from selected sender (max hops: {hops}).',
    senderNotActive: 'Selected sender is not active (status: {status}); a participant that is not active takes no part in payments.',
    recipientNotActive: 'Selected recipient is not active (status: {status}); a participant that is not active takes no part in payments.',
    routesUpdating: 'Routes are updating.',
    routesUpdatingWait: 'Routes are updating; a recipient can be chosen once the server answers.',
    routesUpdatingLastAnswer: 'Routes are updating; the list is the last answer of the server and may include unreachable recipients.',
    routesFailedUnreachable: 'Routes update failed; showing fallback data. Some recipients may be unreachable.',
    routesFailedMissing: 'Routes update failed; showing fallback data. Some recipients may be missing.',
    exceedsDirect: 'Amount may exceed direct trustline capacity ({capacity} {unit}). Multi-hop may still succeed; backend will validate.',
    noSelection: 'Select From and To first',

    // The result screen of a committed payment.
    resultTitle: 'Payment sent',
    resultStatus: 'Status',
    resultAmount: 'Amount',
    resultParties: 'From → To',
    resultRoute: 'Route',
    resultRoutes: 'Route {n} of {total}',
    resultRouteStep: '{from} → {to}: {amount} {unit}',
    resultNoRoutes: 'The server did not report the route of this payment.',
    resultAnother: 'Another payment',
    resultClose: 'Close',

    // An outcome that is not known, and what the user can do about it.
    unknownTitle: 'Result unknown',
    unknownNoAnswer: 'No usable answer arrived; the payment of {amount} {unit} ({from} → {to}) may have been made.',
    unknownFrozen: 'Unresolved payment: {amount} {unit}, {from} → {to}.',
    unknownOtherRun: 'This payment belongs to another run, so it cannot be checked from here. Go back to that run, or discard it.',
    unknownBlocksNew: 'A payment with an unknown result has to be checked or discarded before another payment is sent.',
    noValidKey: 'The payment has no valid key, so nothing was sent.',
    storageRefused: 'The browser would not save a record of this payment, so it was not sent: after a reload it could not be checked safely. Free the storage or allow it, then repeat.',
    storageUnreadable: 'The browser storage could not be read, so it is not known whether an earlier payment is unresolved; no payment was sent. Allow the storage, then repeat.',
    unknownDiscard: 'Discard and start over',
    unknownDiscardWarning: 'The first payment may have been made; a new one could be a second. Discard only after checking.',
    unknownDiscardConfirm: 'Discard it',
    unknownDiscardKeep: 'Keep checking',
    unknownRetry: 'Check / repeat',
    unknownRetryHint: 'A repeat uses the same key: the payment is made once.',
    /** A success-shaped answer that is not a committed payment. */
    notCommitted: 'The server did not confirm the payment (status {status}).',

    // A payment started from a line goes against the line's arrow (F-037-4).
    lineDirectionTitle: 'Why is the direction reversed?',
    lineDirectionNote: 'This line runs from creditor to debtor; Pay starts a payment from debtor to creditor. For the part sent directly, any reverse debt is offset first, and only the remainder increases the debtor’s debt under this line’s limit; it does not repay that debt. The server may route some or all of the payment through other participants.',

    // The information line of the confirm step (045 S2).
    directTitle: 'Direct',
    directCapacityHelp: '1-hop hint; recipients are server-routed (max hops: {hops}).',
    sourceServer: 'server',
    sourceSnapshot: 'snapshot, unconfirmed',
    sourceLoading: 'waiting…',
    sourceFailed: 'snapshot, no answer',
    estimateShort: 'Max',
    estimateSource: 'server estimate',
    shortestTitle: 'Shortest path',
    shortestSteps: plural({ one: '{count} step', other: '{count} steps' }),
    routeNote: 'The route is chosen when the payment is executed.',
    shortestNote: '{title}. {note}',
    estimateNotEstimated: 'not estimated',
    estimateLoading: 'waiting for the server',
    estimateFailed: 'not received',
    estimateExceeded: 'The amount is above the server\'s estimate of the maximum ({max} {unit}); the server decides.',
  },

  trustline: {
    panelAria: 'Trustline management panel',
    title: 'Trustline',
    titlePair: 'Trustline: {from} → {to}',
    fromLabel: 'From',
    toLabel: 'To',
    fromTrigger: 'Trustline from participant',
    toTrigger: 'Trustline to participant',
    toSurface: 'Trustline to participant options',
    toNeedsFrom: "Select 'From' participant first",
    existingLabel: 'Existing',
    existingTitle: 'Available only in edit/create mode',
    existingTrigger: 'Pick existing trustline',
    exists: '{label} (exists)',
    limitLabel: 'Limit',
    newLimitLabel: 'New limit',
    statsAria: 'Trustline stats',
    // Advisor, round 1 (2026-10-18): one sentence, the unit after EACH figure (was "Used 12 / Limit 100 UAH · avail 88 UAH");
    // a no-break space keeps a figure with its unit when the line wraps (hud).
    stats: 'Used {used}\u00a0{unit} · Limit {limit}\u00a0{unit} · Available {available}\u00a0{unit}',
    statsOverLimit: 'Used {used}\u00a0{unit} · Limit {limit}\u00a0{unit} · Over limit by {over}\u00a0{unit}',
    needsParties: 'Choose From and To first.',
    needsLimit: 'Enter a limit.',
    needsNewLimit: 'Enter a new limit.',
    closeAlreadyRequested: 'Close already requested.',
    closeRequested: 'Close requested: limit stays 0; the line closes when its debt (used: {used} {unit}) is repaid.',
    closeRequestNote: 'Closing with debt (used: {used} {unit}) is a request: limit becomes 0, the line closes when the debt is repaid.',
    cancelClose: 'Cancel close',
    confirmClose: 'Confirm close',
    // Advisor, round 1: a line without debt closes; a line with debt only gets a close request (026, `closeIsRequest`).
    close: 'Close line',
    requestClose: 'Request close',
    update: 'Update',
    updating: 'Updating…',
    create: 'Create',
    creating: 'Creating…',
    noSelection: 'Select From and To first',
    noTrustlineSelected: 'Select trustline first',
  },

  /**
   * `F-013-7` (`trustlinesSourceState.ts`): what stands behind the figures of one pair. Facts about the DATA, not a
   * policy about buttons.
   */
  trustlineSource: {
    neverAsked: 'Trustline data for this pair has not been requested from the backend; the figures on the graph come from the snapshot and nothing has confirmed them for this pair.',
    loading: 'Trustline data is still loading from the backend; the figures for this pair are not available yet.',
    loadingFrozen: 'Trustline data for this pair was still being requested when this window was frozen; that request will not update these figures, and nothing has confirmed them for this pair.',
    failed: 'Trustline data could not be loaded: {message}. The figures for this pair are not available, and what the graph shows may be out of date.',
    frozenAnswer: 'These figures are a frozen copy of an earlier backend answer, kept as context for the action in progress; they are not being refreshed and may describe a different pair than the one now selected.',
    frozenOther: 'The figures in this window are a frozen copy kept as context for the action in progress; they are not being refreshed and may describe a different pair than the one now selected.',
    noRow: 'The backend answered for this pair: there is no trustline. The figures on the graph come from the snapshot and do not describe an existing line — there is nothing to update or close here; create the line instead.',
    trustlinesFailed: 'Trustlines refresh failed',
    paymentTargetsFailed: 'Payment targets refresh failed',
  },

  clearing: {
    panelAria: 'Clearing panel',
    runTitle: 'Run clearing',
    confirmHelp: 'This will run a clearing cycle in backend.',
    confirm: 'Confirm',
    runningShort: 'Running…',
    // The result of a clearing (the edges of the answer run creditor -> debtor: "{debtor}'s debt to {creditor}").
    resultTitle: 'Clearing result',
    running: 'Running clearing…',
    cycles: 'Cycles cleared',
    total: 'Total over cycles',
    totalNote: 'Each debt in a cycle was reduced by the amount of that cycle; this total adds the cycle amounts, not the debts.',
    cycle: 'Cycle {n} of {total}: {amount} {unit}',
    // Advisor, 2026-10-18: the arrow form, names undeclined, and ONE arrow meaning across the interface - creditor -> debtor,
    // the protocol's from -> to, as the line window's title and the graph's edge (was "{debtor}’s debt to {creditor} ...";
    // round 1 had it debtor-first, corrected the same day).
    edgeLine: '{creditor} → {debtor}: debt reduced by {amount} {unit}',
    // Codex §15 round 1: a clearing refused because of the equivalent's state (`CONFLICT` + `details.reason`).
    equivalentInactive: 'The equivalent is stopped — clearing is unavailable.',
    equivalentIntegrityHold: 'The equivalent is under an integrity check — operations are on hold.',
    nothing: 'Nothing was cleared: no cycle of debts was found.',
  },

  /** 028 F-028-51 (owner В-6): the client's text of a payment or clearing refusal, by `details.reason` / code. */
  refusal: {
    noRoute: 'No payment route between these participants.',
    noRouteMax: 'No payment route between these participants (available now: {max} {eq}).',
    insufficientCapacity: 'Not enough capacity on the route.',
    insufficientCapacityMax: 'Payment declined: at most {max} {eq} is available now. Reduce the amount.',
    policy: 'The route breaks a trust line policy.',
    participantSuspended: 'A participant of this payment is suspended.',
    amountPrecisionExceeded: '{eq} allows at most {precision} decimal places.',
    equivalentIntegrityHold: 'Payments in {eq} are on hold after an integrity check.',
    equivalentInactive: '{eq} is not active.',
    busy: 'The system is busy.',
    busyKeyed: 'The system is busy; send the same payment again.',
    timeout: 'The payment timed out.',
    txIdReused: 'This payment id was already used.',
    unverifiableLegacyIdentity: 'The earlier payment with this id cannot be verified.',
    invalidSignature: 'The payment signature is invalid.',
    recipientNotFound: 'The recipient was not found.',
    amountNotPositive: 'The amount must be positive.',
    selfPayment: 'You cannot pay yourself.',
    equivalentNotFound: 'Equivalent {eq} was not found.',
    other: 'The payment was refused.',
    generic: 'The payment was not accepted.',
    clearingStep: 'Clearing refused: the database holds debts finer than the accounting step of {eq}. Reseed the database.',
    clearingStepNoEq: 'Clearing refused: the database holds debts finer than the accounting step of the equivalent. Reseed the database.',
    outcomeUnknown: 'No answer; the result is unknown - the action may have been carried out. Check the current state before repeating it.',
    outcomeUnknownSeconds: 'No answer within {seconds} s; the result is unknown - the action may have been carried out. Check the current state before repeating it.',
  },

  /**
   * The error codes the simulator action endpoints answer with (`app/api/v1/simulator.py`), and the transport codes of
   * `api/http.ts`. An English text equals the server's own message where that message is fixed, so English reads as
   * before. A code not listed: `unknown` with the code (046 decision 3).
   */
  actionError: {
    RUN_NOT_FOUND: 'Run not found',
    RUN_TERMINAL: 'Run is in terminal state',
    ACCESS_DENIED: 'Access denied',
    ACTIONS_DISABLED: 'Simulator actions are disabled',
    RUN_PERIMETER_UNAVAILABLE: 'Run perimeter could not be established',
    PARTICIPANT_NOT_FOUND: 'Participant not found',
    EQUIVALENT_NOT_FOUND: 'Equivalent not found',
    INVALID_AMOUNT: 'Invalid amount',
    INVALID_REQUEST: 'Invalid request',
    TRUSTLINE_USED_UNAVAILABLE: 'Temporary error while reading current used amount',
    USED_EXCEEDS_NEW_LIMIT: 'Limit is below current used amount',
    TRUSTLINE_EXISTS: 'Active trustline already exists',
    TRUSTLINE_NOT_FOUND: 'Trustline not found',
    TRUSTLINE_CLOSE_REQUESTED: 'Trustline close is requested; its limit stays 0 until it closes',
    TRUSTLINE_CLOSED: 'Trustline is already closed',
    SEEDING_FAILED: 'Failed to seed scenario into database. Please retry.',
    SIMULATOR_PID_TAKEN: 'The scenario names a participant that exists and was not created by the simulator.',
    SCENARIO_TRUSTLINE_REFUSED: 'A trust line of the scenario breaks the trust line rules.',
    NO_ROUTE: 'No payment route between these participants.',
    INSUFFICIENT_CAPACITY: 'Not enough capacity on the route.',
    CONFLICT: 'The system is busy; try again.',
    CLEARING_FAILED: 'Clearing failed',
    CLEARING_INTERRUPTED: 'Clearing pass interrupted before the plan was exhausted',
    CLEARING_REFUSED: 'Clearing refused',
    PAYMENT_REJECTED: 'The payment was not accepted.',
    ENGINE_TIMEOUT: 'The payment timed out.',
    TIMEOUT: 'No answer from the server in time.',
    INVALID_JSON: 'The server answer could not be read.',
    /** 046 fix-delta F2: `fetch` rejected (a `TypeError`) - no answer reached the client. */
    NETWORK: 'No connection to the server — try again.',
    noRun: 'No active run_id',
    unknown: 'The action failed.',
    /** 048 S3: an answer that named no code of its own (the client's `HTTP_<status>`): the status is not printed. */
    httpFailed: 'The server did not carry out the action — try again later.',
  },

  /**
   * Codex §15 round 1: the equivalent's state named by `details.reason` of a coded answer - told before the code's own
   * text (a `CONFLICT` with these reasons is not "the system is busy").
   */
  actionReason: {
    equivalent_inactive: 'The equivalent is stopped — operations are unavailable.',
    equivalent_integrity_hold: 'The equivalent is under an integrity check — operations are on hold.',
    /** 046 wave 1 (044 stage 2, PR #299): a trust line create refused because a party is suspended (`details.participants`). */
    participant_suspended: 'The participant is suspended — operations with this participant are unavailable.',
  },

  /** History lines of the actions (`useInteractMode.ts`; 049 S2: the success toasts are gone). Amounts arrive as decimal strings. */
  history: {
    payment: 'Payment {amount} {unit}: {from} → {to}',
    trustlineCreated: 'Trustline created: {from} → {to} ({limit} {unit})',
    trustlineUpdated: 'Trustline updated: {from} → {to} → limit {limit} {unit}',
    trustlineClosed: 'Trustline closed: {from} → {to}',
    closeRequested: 'Close requested: {from} → {to} (closes when the debt is repaid)',
    clearing: plural({ one: 'Clearing: {count} cycle, −{amount} {unit}', other: 'Clearing: {count} cycles, −{amount} {unit}' }),
    clearingNothing: 'Clearing: no cycles found',
  },

  /**
   * The participant card (`NodeCardOverlay.vue`). 045 S3: the reasons and the line description; 046 wave 3: the rest of the
   * card (English byte for byte as it was, except the statistics and the group headings: the advisor's words of
   * 2026-10-18, «Trusts others / Trusted by / Trusts (N) / Trusted by (N)»). The role of the participant is said in the
   * legend's words (`legend.entry.*`) - one set of words for the legend and the card; so is its type
   * (`statusText.ts` `participantTypeText`); its status is `participantStatus.*`.
   */
  card: {
    flowOpenReason: 'Cancel current action first (press ESC).',
    busyReason: 'Operation in progress… please wait.',
    lineAvailable: 'available {amount} {unit}',
    /** 046 wave 3: a negative `available` of a row - the debt's excess over a lowered limit (026 `T2602`). */
    lineOverLimit: 'over limit by {amount} {unit}',
    pin: 'Pin',
    unpin: 'Unpin',
    /** 049 S1 (decision 3): the head of a card whose balance did not arrive - one message, never a zero. */
    balanceUnavailable: 'Balance unavailable.',
    // The sums of the limits of the participant's lines: as the creditor (`from`) and as the debtor (`to`).
    trustsOthers: 'Trusts others',
    trustedBy: 'Trusted by',
    trustsOthersHint: 'The sum of the limits this participant has given others, as the creditor.',
    trustedByHint: 'The sum of the limits others have given this participant, as the debtor.',
    pay: 'Pay',
    newLine: 'New line',
    trustsGroup: 'Trusts ({count})',
    trustedByGroup: 'Trusted by ({count})',
    edit: 'Edit',
    editLine: 'Edit trustline',
    editLineFrom: 'Edit trustline (set by {name})',
    loading: 'Loading trustlines…',
    none: 'No trustlines',
  },

  /**
   * 046 wave 3: the line window (`EdgeDetailPopup.vue`). English byte for byte as it was (the fallback of "Pay" without a
   * name is "Pay": "Pay sender" named the wrong party - the payment goes TO the line's creditor), plus the one explaining
   * sentence of 048 item 13 (б). The line runs creditor -> debtor (`from -> to`); the window's title is that arrow.
   */
  edgeWindow: {
    aria: 'Edge detail popup',
    titleFallback: 'Edge',
    used: 'Used',
    limit: 'Limit',
    available: 'avail',
    utilization: 'Utilization',
    utilizationBar: 'Utilization bar',
    utilizationUnknown: 'unknown',
    /** Said after a notice of `trustlineSource.*` that the figures are not confirmed (its own sentence). */
    closeDisabledUntil: 'Closing the line is disabled until then.',
    closeRequested: 'Close requested: limit stays 0; the line closes when its debt is repaid.',
    closeRequestNote: 'Closing with debt (used: {used} {unit}) is a request: limit becomes 0 until repaid.',
    pay: 'Pay {name}',
    payNoName: 'Pay',
    changeLimit: 'Change limit',
    changeLimitTitle: 'Change the limit of this line',
    closeLine: 'Close line',
    closeLineTitle: 'Close this line',
    /** 048 item 13 (б): what a trust line is, in the names of this line. */
    trustExplanation: '{from} trusts {to} up to the limit — that is how much {to} may owe {from}.',
  },

  metrics: {
    panelAria: 'Run analytics',
    title: 'Analytics',
    measurements: 'Measurements',
    noSeries: 'No series in this window',
    trendAria: '{title} trend',
    malformed: 'Series value outside the wire contract',
    gaps: plural({ one: '{gaps} of {count} points have no measurement', other: '{gaps} of {count} points have no measurement' }),
    errorDetail: 'The analytics request did not complete.',
    /** Codex §15 round 1: the failure is shown as its status or code and request id (`errorReference`), apart from the prose. */
    errorReference: 'Reference: {ref}',
    series: {
      success_rate: 'Success rate',
      avg_route_length: 'Avg route length',
      total_debt: 'Total debt',
      clearing_volume: 'Clearing volume',
      bottlenecks_score: 'Bottlenecks score',
      active_participants: 'Active participants',
      active_trustlines: 'Active trustlines',
    },
    // One whole sentence per section and reason (no noun glued into a shared sentence).
    measurementsIdle: 'Analytics updates while a run is running.',
    measurementsLoading: 'Loading measurements…',
    measurementsUnavailableTitle: 'No measurements recorded',
    measurementsErrorTitle: 'Analytics request failed',
    measurementsStorageOff: 'Metric storage is off for this run, so no measurements were recorded.',
    measurementsReadFailed: 'The metric store could not be read, so there are no measurements to show.',
    measurementsNone: 'This run has no recorded measurements yet. Nothing is being substituted for them.',
    bottlenecksIdle: 'Bottlenecks update while a run is running.',
    bottlenecksLoading: 'Loading bottlenecks…',
    bottlenecksUnavailableTitle: 'No bottlenecks recorded',
    bottlenecksErrorTitle: 'Bottlenecks request failed',
    bottlenecksStorageOff: 'Metric storage is off for this run, so no bottlenecks were recorded.',
    bottlenecksReadFailed: 'The metric store could not be read, so there are no bottlenecks to show.',
    bottlenecksNone: 'This run has no recorded bottlenecks yet. Nothing is being substituted for them.',
  },

  /**
   * The bottlenecks list. `reason_code` is a closed set (`simulatorContracts.ts` BOTTLENECK_REASON_CODES); the server's
   * English `label` / `suggested_action` are no longer shown - these are (their English is the server's where it has
   * one). A code not listed: `unknownReason` with the code.
   */
  bottlenecks: {
    title: 'Bottlenecks',
    empty: 'No bottlenecks reported',
    focus: 'Focus',
    focusAria: 'Focus {target}',
    unknownReason: 'Bottleneck ({code})',
    reason: {
      LOW_AVAILABLE: 'Low available capacity',
      HIGH_USED: 'High utilization',
      FREQUENT_ABORTS: 'Frequent failures',
      TOO_MANY_TIMEOUTS: 'Too many timeouts',
      ROUTING_TOO_DEEP: 'Routes too long',
      CLEARING_PRESSURE: 'Debts waiting for clearing',
    },
    action: {
      LOW_AVAILABLE: 'Raise trust limits or add alternative routes',
      HIGH_USED: 'Consider clearing or adding alternative routes',
      FREQUENT_ABORTS: 'Increase trust limits, add alternative routes, or clear',
      TOO_MANY_TIMEOUTS: 'Reduce load or increase routing timeouts',
      ROUTING_TOO_DEEP: 'Add shorter links between participants',
      CLEARING_PRESSURE: 'Run clearing',
    },
  },
  // ------------------------------------------------------------------------------------------------------------------
  // 046 wave 1 (T4602): narrowed by the owner's word 2026-10-18 - the run's stop reason, the start the limit of runs
  // refused and its retry, the scenario lock under a live run (BACKLOG 043-3), the accessible names of the window shell
  // (wave 2 finding 9); full after 048 S2 (2026-10-19) - the rest of the first screen, below.
  // ------------------------------------------------------------------------------------------------------------------

  /** The top bar (`TopBar.vue`): its first row, the rows behind "Details", the error line and the stall alert. */
  topBar: {
    /** BACKLOG 043-3: the scenario cannot be changed under a live run; the select is disabled and says why. */
    scenarioLocked: 'Stop the run to choose another scenario.',
    /** The line of a start the global limit of runs refused (409 `conflict_kind: global_active_limit`): a new attempt. */
    retry: 'Try again',
    errorAria: 'Error',
    stopReasonAria: 'Stop reason',
    stopLabel: 'Stop',
    /**
     * 048 S1 (decision 5, §G): the state of the network while a living run has lost its event stream (SSE
     * reconnecting or closed) - the visitor's form of the SSE badge, which is `?debug=1`'s.
     */
    noConnection: 'no connection to the server',

    // ----------------------------------------------------------------------------------------------------------------
    // 046 wave 1, full (after 048 S2): the rest of the top bar. The words are 048 §G where §G names them; elsewhere the
    // English text is the one that was there.
    // ----------------------------------------------------------------------------------------------------------------
    /** The network on screen when the source is the offline snapshot (no scenario of a server). */
    offlineDemo: 'offline demo',
    /** The state of the network, as words (043 decision 1); a run state outside the set shows `pending` or `noRun`. */
    state: {
      offline: 'snapshot without a server',
      running: 'live',
      paused: 'paused',
      created: 'starting',
      stopping: 'stopping',
      stopped: 'stopped',
      error: 'error',
      pending: '…',
      noRun: 'no run',
    },
    /** "Поток" (the owner's word, 043): pause and resume the automatic payments. */
    flow: 'Flow',
    flowPause: 'Pause automatic payments',
    flowResume: 'Resume automatic payments',
    details: 'Details',
    /** §G "Network <name>": the label of the full name behind "Details". */
    network: 'Network',
    detailsRowAria: 'Top bar',
    modeAria: 'Mode',
    themeGroupAria: 'Theme',
    themeLabel: 'Theme',
    themeAria: 'UI theme',
    themeSaas: 'Calm',
    themeHud: 'Contrast (HUD)',
    runControlsAria: 'Run controls',
    scenarioLabel: 'Scenario',
    scenarioAria: 'Scenario',
    scenarioPlaceholder: '— select —',
    start: 'Start',
    stop: 'Stop',
    tempoGroupAria: 'Auto-payment tempo',
    tempoLabel: 'Auto-payment tempo, %',
    tempoAria: 'Auto-payment tempo, percent',
    /** 049 S2 (decision 10): the visitor's tempo - three steps by words; the percent each step sets is in the runbook. */
    tempoStepsAria: 'Payment tempo',
    tempoStepsLabel: 'Tempo',
    tempoSlow: 'slow',
    tempoNormal: 'normal',
    tempoFast: 'fast',
    /** 049 S2 (decision 5): the one switch of the network's events in the history. */
    feedToggle: 'Show events',
    apply: 'Apply',
    noScenarios: 'No scenarios could be loaded — try again later',
    statusAria: 'Status',
    paymentsAria: 'Payments',
    payments: 'Payments: {ok} of {total} went through',
    paymentsNone: 'No payments yet',
    stallAria: 'Payments stalled',
    /** 048 decision 2: the one diagnosis a visitor gets; it claims no more than the mechanism (`consec_all_rejected_ticks`). */
    stall: "Recent payments are not going through. Try clearing or increase the relevant trustline's limit.",
  },

  /**
   * 046 wave 1, full: the three actions of the protocol in the first row (`ActionBar.vue`). The idle hint of a button says
   * what the action RESULTS in, not its name again (048 item 13 (a)). Why a button is disabled: `card.busyReason` and
   * `card.flowOpenReason` are the same sentences and are read from there.
   */
  actions: {
    aria: 'Interact actions',
    payment: 'Send Payment',
    trustline: 'Manage Trustline',
    clearing: 'Run Clearing',
    paymentHint: 'The amount reaches the recipient along a chain of trust.',
    trustlineHint: 'Sets how much one participant may owe another.',
    clearingHint: 'Debts that run around a cycle cancel out — each participant in the cycle owes less.',
    cancellingTitle: 'Cancelling… please wait for the operation to finish.',
    cancellingHint: 'Cancelling… please wait.',
    disabled: 'Actions are disabled by backend feature flags.',
    /**
     * 048 S2 hand-over (P2-2): a visitor has no Start since S2, so the reason no longer sends them to one. A reload makes
     * the opening's one attempt again (`useInteractAutoBootstrapRun.ts`: an ended run is replaced by a new one).
     */
    noRun: 'The actions need a live run. If the run has ended, reload the page to start a new one.',
    busyHintAria: 'Action Bar busy hint',
    lockedHintAria: 'Action Bar locked hint',
  },

  /** 043 S1 / 048 item 13 (a): the one onboarding hint - the three actions in the words of the protocol. */
  onboarding: {
    protocol: 'A payment travels along a chain of trust; a trust line is the limit up to which one trusts another; clearing cancels debts around a cycle.',
    explore: 'Choose a participant and hover over one of its lines: who trusts whom and who owes whom.',
    close: 'Close hint',
  },

  /** The bottom bar (`BottomBar.vue`): the view settings behind "Details" and "Fit network" of the main row. */
  bottomBar: {
    rowAria: 'Bottom bar',
    viewAria: 'View settings',
    equivalent: 'Equivalent',
    layout: 'Layout',
    layoutOption: {
      'admin-force': 'cloud',
      'community-clusters': 'groups',
      'balance-split': 'by balance',
      'type-split': 'by type',
      'status-split': 'by status',
    },
    toolsAria: 'Tools',
    fitNetwork: 'Fit network',
  },

  /** The total debt of the bottom row and the network figures behind "Details" (`SystemBalanceBar.vue`). */
  systemBalance: {
    balanceAria: 'System balance',
    metricsAria: 'System metrics',
    totalDebt: 'Total debt',
    available: 'Free on lines',
    trustlines: 'Trust lines',
    participants: 'Participants',
    utilization: 'Limits in use',
    /** 049 S2 (decision 8): the short definitions of the figures - the `title` of their plaques, not text on the screen. */
    availableHint: 'The sum of the free limits of all trustlines; it does not promise that every payment gets through.',
    trustlinesHint: 'How many trustlines are active in the network.',
    participantsHint: 'How many participants are active in the network.',
    utilizationHint: 'The share of the trust limits taken by debts.',
    // 039 slice D: the total debt just before and just after the last clearing, shown above the current total.
    clearingChange: 'clearing: was {before} → became {after} {unit}',
  },

  /**
   * The legend of the graph (`Legend.vue`). 046 wave 3: the participant card says the role, the freeze and the hub in
   * these same words (`NodeCardOverlay.vue`); `vizMapping.ts` ROLE_TEXT, the card's own English words, is gone.
   */
  legend: {
    title: 'Node colour — balance',
    titleWithEquivalent: 'Node colour — balance · {eq}',
    help: 'Net position: what others owe minus what this participant owes.',
    entry: {
      debtor: 'owes more',
      creditor: 'is owed more',
      neutral: 'even',
      unknown: 'unknown',
      frozen: 'frozen',
      hub: 'hub',
      person: 'person',
      organisation: 'organisation',
    },
  },

  /** "Find on graph" behind "Details" (`GraphNavigator.vue`): the keyboard path to participants and lines. */
  navigator: {
    aria: 'Graph navigator',
    summary: 'Find on graph',
    participant: 'Participant',
    line: 'Line',
    openParticipant: 'Open participant',
    openLine: 'Open line',
    participantOpened: 'Participant opened: {name}',
    lineOpened: 'Line opened: {name}',
    lineDisabled: 'A line opens on the live network (server source) when no action is in progress.',
  },

  /** The tooltip of a hovered line (`EdgeTooltip.vue`): a trust line runs creditor -> debtor (`from -> to`). */
  edgeTooltip: {
    aria: 'Edge tooltip',
    trust: '{from} trusts {to} up to {limit}',
    debt: '{to} owes {from} {used} · {available}',
    available: 'available {amount}',
    availableUnknown: 'available —',
    overLimit: 'over limit by {amount}',
    /** A hovered line without any of the three figures: the bare amounts (internal adversarial pass, 2026-10-19). */
    amounts: 'from: {from} / to: {to} {unit}',
  },

  /** The short history of the person's actions on the first view (`InteractHistoryLog.vue`). */
  recentActions: {
    aria: 'Action history',
    title: 'Recent actions',
    /** 049 S2 (decision 5): the title while the network's events are shown. */
    feedTitle: 'Network events',
    feedAria: 'Network events',
  },

  /**
   * 049 S2 (decision 5): the lines of the network's events in the history (`InteractHistoryLog.vue`), shown while
   * «Show events» is on. Built from accepted SSE events only (`realEventPipeline.ts` `feedItemOf`); a missing field writes
   * no line. A failure has no amount (`tx.failed` carries none) and an unknown reason gets no diagnosis. A series within
   * 2 s is one line: its lead (`paymentSeries`, `failureSeries`) and the latest event's line after it.
   * The reasons are short (owner's review of the final frames, 2026-10-20): a line is one row of a 448 px column, where only
   * the names give way (decision 9) - the longest reason with two one-letter names must fit in the hud's monospace.
   */
  feed: {
    payment: '{from} → {to}: {amount}\u00a0{unit}',
    failed: '{from} → {to}: payment did not go through',
    failedNoCapacity: '{from} → {to}: failed — route lacks capacity',
    failedNoRoute: '{from} → {to}: failed — no route',
    failedLimit: '{from} → {to}: failed — would exceed a line limit',
    failedLineInactive: '{from} → {to}: failed — a trustline is not active',
    failedTimeout: '{from} → {to}: failed — timed out',
    paymentSeries: plural({ one: 'In 2 s: {count} payment; latest — ', other: 'In 2 s: {count} payments; latest — ' }),
    failureSeries: plural({ one: 'In 2 s: {count} failure; latest — ', other: 'In 2 s: {count} failures; latest — ' }),
    clearing: plural({
      one: 'Clearing: total debt down by {amount}\u00a0{unit}; {count} cycle',
      other: 'Clearing: total debt down by {amount}\u00a0{unit}; {count} cycles',
    }),
    clearingCycles: plural({ one: 'Clearing: {count} cycle', other: 'Clearing: {count} cycles' }),
    clearingDebt: 'Clearing: total debt down by {amount}\u00a0{unit}',
    trustGrowth: 'Trust limits grew after the clearing',
    /** The mark of a line of the person's own action while the network's events are shown beside it. */
    you: 'you',
  },

  /** The scene's loading overlay of the root (048 S3: its error is a sentence of `failure.*`, without an «Error» title). */
  scene: {
    loading: 'Loading…',
  },

  /**
   * 048 S3 (decision 3, «одна форма ошибки»): a request that failed, told by the KIND of the failure - one sentence «what
   * happened → what to do» (`failureRunErrorLine`). The HTTP status, the server's message and a class name are the
   * technical detail (the `title`, the console); the request id is a line of its own.
   */
  failure: {
    noAnswer: 'The server did not answer in time. Wait a moment and reload the page.',
    noConnection: 'No connection to the server. Check the connection and reload the page.',
    forbidden: 'The server refused access. Reload the page.',
    notFound: 'The server did not find what was asked for. Reload the page.',
    serverFailed: 'The server could not complete the request. Try again later or reload the page.',
    viewFailed: 'The picture of the network could not be updated. Reload the page.',
    streamFailed: 'Network updates stopped; the displayed data may be outdated. Reload the page.',
  },

  /**
   * Why a run stopped (`RunStatus.stop_reason`), a closed set: the reasons the clients of this repository send
   * (`user_stop` - Stop of the top bar; `admin_stop` - Stop of the operator's list; `admin_stop_all` - the default of the
   * operator's stop-all, `app/api/v1/simulator.py`) and the server's idle stop (`idle_no_subscribers`, 044 stage 2,
   * PR #299). Any other value (a free text of another client) is `unknown` with the value itself - for correlation.
   */
  runStop: {
    user_stop: 'stopped with the Stop button',
    admin_stop: 'stopped by the operator',
    admin_stop_all: 'stopped by the operator (all runs)',
    idle_no_subscribers: 'stopped by the server: nobody watched it',
    unknown: 'stopped',
  },

  /** A start that did not begin a run (`startRunErrorText`, `noScenarioText`). The retry button is `topBar.retry`. */
  runStart: {
    limitBusy: 'Someone is already watching the demo on this server; try again later.',
    limitOne: 'The only place of the demo is taken right now: 1 person is watching it. Try again in a minute.',
    limitAll: plural({
      one: 'All {count} places of the demo are taken right now: {count} people are watching it. Try again in a minute.',
      other: 'All {count} places of the demo are taken right now: {count} people are watching it. Try again in a minute.',
    }),
    noScenario: 'The server offers no scenario: there is nothing to show.',
  },

  /**
   * Lines of the run that 044 stage 2 (PR #299) wrote as literals, moved here on its merge (046 wave 1): the error line of
   * a run the idle stop ended, and the refusal of a start or an apply at tempo 0 (BACKLOG 043-19).
   */
  runLine: {
    idleStopped: 'The server stopped the run: nobody watched it for a long time. To start again, reload the page.',
    zeroIntensity: 'A tempo of 0 makes no payments: enter a tempo from 1 to 100. To stop the payments, pause.',
    /**
     * 046 wave 1, full (048 S2 hand-over P2-2): a run stopped by anything but Stop of this screen (`user_stop`) and the idle
     * stop - the operator's stop-all (`admin_stop_all`), a stop through the API with any reason or none (the runbook's
     * one-run stop sends none) - and a run the server ended with an error. A visitor has no Start since 048 S2: the way
     * back is the reload, as for the idle stop. Who stopped it is the stop reason behind "Details", not this line (a stop
     * without a reason names nobody). The error is named by its code only (046 decision 3: the server's English prose is
     * not shown; the code correlates).
     */
    stopped: 'The run was stopped. To start again, reload the page.',
    errorStopped: 'The demo stopped because of an error. Reload the page to start a new run.',
    /** 048 S3: a user-facing code of a run that goes on - the code correlates; the server's `CODE: message` is the title. */
    runningError: 'Something failed during the run; the run goes on.',
  },

  /**
   * The accessible names of the window shell (`overlaySurfaceCatalog.ts` -> `WindowShell.vue`). An action window's
   * shell is named by its panel's `*.panelAria`; these are the rest.
   */
  windowShell: {
    window: 'Window',
    closeWindow: 'Close window',
    clearingTitle: 'Clearing',
    /** 046 wave 1, full: the name of the layer the windows live in (the root's `TransitionGroup`). */
    layer: 'Window layer',
    /** 046 wave 3: the shells of the two inspector windows (`resolveWindowSurfaceDescriptor`). */
    lineDetails: 'Trustline details',
    lineDetailsPair: 'Trustline details: {from} to {to}',
    nodeDetails: 'Node details',
    nodeDetailsNamed: 'Node details: {name}',
  },
} as const
