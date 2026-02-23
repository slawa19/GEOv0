export type LruOpts = {
  max: number
}

/**
 * Small, allocation-light LRU cache based on Map insertion order.
 *
 * - `get()` touches recency
 * - `set()` upserts and evicts oldest entries when over capacity
 */
export class LruCache<K, V> {
  private map = new Map<K, V>()

  constructor(private opts: LruOpts) {}

  get(key: K): V | undefined {
    const v = this.map.get(key)
    if (v === undefined) return undefined
    // Refresh insertion order (poor-man LRU).
    this.map.delete(key)
    this.map.set(key, v)
    return v
  }

  has(key: K): boolean {
    return this.map.has(key)
  }

  set(key: K, v: V): void {
    if (this.map.has(key)) this.map.delete(key)
    this.map.set(key, v)
    this.evictIfNeeded()
  }

  delete(key: K): boolean {
    return this.map.delete(key)
  }

  clear(): void {
    this.map.clear()
  }

  size(): number {
    return this.map.size
  }

  keys(): K[] {
    return Array.from(this.map.keys())
  }

  setMax(max: number): void {
    this.opts.max = max
    this.evictIfNeeded()
  }

  private evictIfNeeded(): void {
    const max = Math.max(0, Math.floor(this.opts.max))
    while (this.map.size > max) {
      const oldestKey = this.map.keys().next().value as K | undefined
      if (oldestKey === undefined) break
      this.map.delete(oldestKey)
    }
  }
}
