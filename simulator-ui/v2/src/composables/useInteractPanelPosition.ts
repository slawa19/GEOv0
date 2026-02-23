import { readonly, ref, watch } from 'vue'
import type { Ref } from 'vue'
import type { Point } from '../types/layout'
export type { Point } from '../types/layout'
import type { InteractPhase } from './useInteractMode'

/**
 * Источник открытия панели.
 * Используется только для читаемости кода (документация намерений),
 * не влияет на логику — anchor-значение всегда передаётся явным snapshot.
 */
export type PanelOpenSource =
  | 'edge-click'    // клик по ребру → state.edgeAnchor
  | 'node-card'     // ✏️ из NodeCard → nodeCardStyle position
  | 'action-bar'    // ActionBar кнопка → null (CSS default)
  | 'change-limit'  // "Change Limit" в EdgeDetailPopup → state.edgeAnchor

/**
 * Composable для централизованного управления anchor-позицией Interact-панелей.
 *
 * Решает проблему: ранее `trustlinePanelAnchor` управлялся вручную в 4+ обработчиках
 * (onEdgeDetailChangeLimit, onInteractEditTrustline, onActionStart*),
 * что было хрупко — легко забыть сбросить/установить anchor.
 *
 * ## Использование в SimulatorAppRoot:
 * ```ts
 * const { panelAnchor, openFrom } = useInteractPanelPosition(interactPhase)
 *
 * // При открытии из EdgeDetailPopup "Change Limit":
 * openFrom('change-limit', interact.mode.state.edgeAnchor ?? null)
 *
 * // При открытии из NodeCard ✏️ (nextTick нужен — watcher phase сбрасывает anchor):
 * void nextTick(() => openFrom('node-card', parseNodeCardAnchor(nodeCardStyle.value)))
 *
 * // При открытии из ActionBar (CSS default — anchor = null):
 * openFrom('action-bar')
 * ```
 *
 * ## Передача в панели:
 * ```html
 * <TrustlineManagementPanel :anchor="panelAnchor" :host-el="hostEl" />
 * <ManualPaymentPanel       :anchor="panelAnchor" :host-el="hostEl" />
 * <ClearingPanel            :anchor="panelAnchor" :host-el="hostEl" />
 * ```
 *
 * ## Таблица позиционирования:
 * | Сценарий                         | source          | anchor              | Позиция                    |
 * |----------------------------------|-----------------|---------------------|----------------------------|
 * | "Change Limit" в EdgeDetailPopup | 'change-limit'  | state.edgeAnchor    | рядом с ребром             |
 * | ✏️ из NodeCard                   | 'node-card'     | nodeCardStyle pos   | рядом с нодой              |
 * | ActionBar → Manage Trustline     | 'action-bar'    | null                | CSS default (right/top)    |
 * | ActionBar → Send Payment         | 'action-bar'    | null                | CSS default (right/top)    |
 * | ActionBar → Run Clearing         | 'action-bar'    | null                | CSS default (right/top)    |
 */
export function useInteractPanelPosition(phase: Ref<InteractPhase>) {
  const _anchor = ref<Point | null>(null)

  /**
   * Автоматически сбрасывает anchor при любой смене фазы.
   *
   * Обработчики устанавливают якорь ПОСЛЕ смены фазы (или в nextTick —
   * см. onInteractEditTrustline), поэтому этот watcher всегда видит "старый"
   * anchor и очищает его первым, а затем обработчик задаёт новый.
   */
  watch(phase, () => {
    _anchor.value = null
  }, { flush: 'sync' })

  /**
   * Установить anchor для следующей открываемой панели.
   *
   * @param source   — читаемое имя источника (для понимания кода и дебага)
   * @param snapshot — координаты { x, y } или null/undefined (→ CSS default)
   */
  function openFrom(_source: PanelOpenSource, snapshot?: Point | null): void {
    _anchor.value = snapshot ?? null
  }

  return {
    /** Текущий anchor. Передаётся в панели как `:anchor` prop. */
    panelAnchor: readonly(_anchor),
    openFrom,
  }
}
