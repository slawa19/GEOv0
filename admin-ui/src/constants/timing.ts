export const DEBOUNCE_SEARCH_MS = 250
export const DEBOUNCE_FILTER_MS = 250

// Graph page-specific throttles (keep UI behavior stable).
export const THROTTLE_GRAPH_REBUILD_MS = 300
export const THROTTLE_LAYOUT_SPACING_MS = 250

// Polling / transient UI timings.
export const HEALTH_POLL_INTERVAL_MS = 15000

// A request that has not answered (headers AND body) by then ends with ApiException(TIMEOUT). The health
// probes answer from memory or one trivial query, so they get a shorter bound: the header status and the
// poll must not wait half a minute on a dead hub.
export const DEFAULT_REQUEST_TIMEOUT_MS = 30_000
export const HEALTH_REQUEST_TIMEOUT_MS = 5_000

export const GRAPH_SEARCH_HIT_FLASH_MS = 900

// Dev-only E2E helper timings.
export const DEV_GRAPH_DOUBLE_TAP_DELAY_MS = 50
