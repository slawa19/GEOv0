import { readonly, ref, watch } from 'vue'
import type { Ref } from 'vue'
import type { Point } from '../types/layout'
export type { Point } from '../types/layout'
import type { InteractPhase } from './useInteractMode'
import { toLower } from '../utils/stringHelpers'

/**
 * Derives the panel group from an interact phase string.
 * Same convention as `useActivePanelState`: phase name contains 'payment', 'trustline', or 'clearing'.
 * Returns null for 'idle' and any unrecognized phase.
 */
export function panelGroupOf(phase: string): 'payment' | 'trustline' | 'clearing' | null {
  const p = toLower(phase)
  if (p.includes('payment')) return 'payment'
  if (p.includes('trustline')) return 'trustline'
  if (p.includes('clearing')) return 'clearing'
  return null
}

/**
 * Источник открытия панели.
 * Используется только для читаемости кода (документация намерений),
 * не влияет на логику — anchor-значение всегда передаётся явным snapshot.
 */
export type PanelOpenSource =
  | 'edge-click'    // клик по ребру → state.edgeAnchor
  | 'node-card'     // ✏️ из NodeCard → selectedNodeScreenCenter
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
 * openFrom('change-limit', interact.mode.state.edgeAnchor)
 *
 * // При открытии из NodeCard ✏️ (safe to set synchronously — flush:'sync' watcher
 * // has already cleared anchor during the phase transition in opts.start()):
 * openFrom('node-card', selectedNodeScreenCenter.value)
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
 * | ✏️ из NodeCard                   | 'node-card'     | node screen center  | рядом с нодой              |
 * | ActionBar → Manage Trustline     | 'action-bar'    | getActionBarAnchor()| top-right, под кнопками    |
 * | ActionBar → Send Payment         | 'action-bar'    | getActionBarAnchor()| top-right, под кнопками    |
 * | ActionBar → Run Clearing         | 'action-bar'    | getActionBarAnchor()| top-right, под кнопками    |
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
  watch(phase, (cur, prev) => {
    if (panelGroupOf(cur) !== panelGroupOf(prev ?? '')) {
      console.warn('[PANEL-DEBUG] watcher: group changed', panelGroupOf(prev ?? ''), '->', panelGroupOf(cur), '=> clearing anchor')
      _anchor.value = null
    }
  }, { flush: 'sync' })

  /**
   * Установить anchor для следующей открываемой панели.
   *
   * @param source   — читаемое имя источника (для понимания кода и дебага)
   * @param snapshot — координаты { x, y } или null/undefined (→ CSS default)
   */
  function openFrom(_source: PanelOpenSource, snapshot: Point | null = null): void {
    console.warn('[PANEL-DEBUG] openFrom:', _source, '=>', JSON.stringify(snapshot))
    _anchor.value = snapshot
  }

  return {
    /** Текущий anchor. Передаётся в панели как `:anchor` prop. */
    panelAnchor: readonly(_anchor),
    openFrom,
  }
}
