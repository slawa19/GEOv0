import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, request, type APIRequestContext, type APIResponse } from '@playwright/test'

/**
 * The real backend every Admin e2e runs against (032 S4, 2026-10-07).
 *
 * `ADMIN_E2E_BACKEND_ORIGIN` - e.g. `http://127.0.0.1:18141`; `ADMIN_E2E_TOKEN` - the backend's
 * `ADMIN_TOKEN`. Both are required: without them the suite refuses to start, because an Admin UI
 * with no backend shows empty screens, and an empty screen must never pass for a working one.
 */
export function e2eBackend(): { origin: string; token: string } {
  const origin = (process.env.ADMIN_E2E_BACKEND_ORIGIN ?? '').trim().replace(/\/$/, '')
  const token = (process.env.ADMIN_E2E_TOKEN ?? '').trim()
  if (!origin || !token) {
    throw new Error(
      'Admin e2e needs a real backend: set ADMIN_E2E_BACKEND_ORIGIN and ADMIN_E2E_TOKEN ' +
        '(locally: scripts/verify_admin_e2e.ps1 brings up a seeded backend and sets both).',
    )
  }
  return { origin, token }
}

export async function adminApi(): Promise<APIRequestContext> {
  const { origin, token } = e2eBackend()
  return request.newContext({ baseURL: origin, extraHTTPHeaders: { 'X-Admin-Token': token } })
}

export async function okJson<T>(response: APIResponse, operation: string): Promise<T> {
  if (!response.ok()) throw new Error(`${operation} failed with HTTP ${response.status()}: ${await response.text()}`)
  return (await response.json()) as T
}

/** The community the backend is seeded with (`scripts/seed_db.py --source recipe --community ...`). */
export const SEEDED_COMMUNITY = 'riverside-town-50'

/**
 * Display names of the seeded community, read from the description the seed executes. PIDs are
 * generated at seed time, names are not - so a name is what proves a row came from the seed.
 */
export function seededParticipantNames(): Set<string> {
  const path = fileURLToPath(new URL(`../../seeds/communities/${SEEDED_COMMUNITY}/community.json`, import.meta.url))
  const description = JSON.parse(readFileSync(path, 'utf-8')) as { participants: Array<{ name: string }> }
  const names = new Set(description.participants.map((p) => p.name))
  expect(names.size, 'the seeded community names no participant').toBeGreaterThan(0)
  return names
}
