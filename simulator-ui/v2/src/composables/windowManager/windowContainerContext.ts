import { inject, provide, type InjectionKey, type Ref } from 'vue'

export type WindowContainerElRef = Ref<HTMLElement | null>

/**
 * Per-window container element ref (provided by WindowShell).
 * Consumers must listen on this element (not on global `window`).
 */
export const WINDOW_CONTAINER_EL_KEY: InjectionKey<WindowContainerElRef> = Symbol('geo.windowContainerEl')

export function provideWindowContainerEl(el: WindowContainerElRef): void {
  provide(WINDOW_CONTAINER_EL_KEY, el)
}

export function useWindowContainerEl(): WindowContainerElRef | null {
  return inject(WINDOW_CONTAINER_EL_KEY, null)
}

