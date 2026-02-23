export function incCounter(map: Record<string, number>, key: string, by = 1): void {
  map[key] = (map[key] ?? 0) + by
}
