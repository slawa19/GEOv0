export type DebouncedFn<TArgs extends unknown[]> = ((...args: TArgs) => void) & {
  cancel: () => void
}

export function debounce<TArgs extends unknown[]>(fn: (...args: TArgs) => void, waitMs: number): DebouncedFn<TArgs> {
  let timer: number | null = null

  const debounced = ((...args: TArgs) => {
    if (timer) window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      timer = null
      fn(...args)
    }, waitMs)
  }) as DebouncedFn<TArgs>

  debounced.cancel = () => {
    if (!timer) return
    window.clearTimeout(timer)
    timer = null
  }

  return debounced
}
