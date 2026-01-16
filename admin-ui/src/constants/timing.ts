export const DEBOUNCE_SEARCH_MS = 250
export const DEBOUNCE_FILTER_MS = 250

// Used for zoom/viewport throttling where needed.
export const THROTTLE_ZOOM_MS = 100

// Graph page-specific throttles (keep UI behavior stable).
export const THROTTLE_GRAPH_REBUILD_MS = 300
export const THROTTLE_LAYOUT_SPACING_MS = 250

// Polling / transient UI timings.
export const HEALTH_POLL_INTERVAL_MS = 15000
export const TOAST_DEDUPE_MS = 2000
export const GRAPH_SEARCH_HIT_FLASH_MS = 900

// Dev-only E2E helper timings.
export const DEV_GRAPH_DOUBLE_TAP_DELAY_MS = 50
