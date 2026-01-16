import { t } from '../i18n'

const FIXTURES_BASE = '/admin-fixtures/v1'

const cache = new Map<string, unknown>()

export async function loadFixtureJson<T>(relPath: string): Promise<T> {
  const key = relPath
  if (cache.has(key)) return cache.get(key) as T

  const res = await fetch(`${FIXTURES_BASE}/${relPath}`)
  if (!res.ok) throw new Error(t('fixtures.loadFailedOne', { path: relPath, status: res.status }))

  const data = (await res.json()) as T
  cache.set(key, data)
  return data
}
