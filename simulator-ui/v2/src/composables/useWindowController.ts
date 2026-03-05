
import { computed, onUnmounted, ref, watch, type ComputedRef, type Ref } from 'vue'

import type { Point } from '../types/layout'

import type { InteractPhase, InteractState } from './useInteractMode'
import { interactWindowOfPhase } from './windowManager/interactWindowOfPhase'
import type { FocusMode, WindowAnchor, WindowManagerApi } from './windowManager/types'

import { useWmEdgeDetail, type EdgeDetailCloseReason } from './useWmEdgeDetail'

// ARCH-4 (WM bridging): `watchEffect` запрещён.
// `wm.open()`/`wm.closeGroup()` читают реактивное состояние WM, и `watchEffect` может
// незаметно создать циклические зависимости (и множественные `wm.open()` в одном flush).
const watchEffect: never = null as never
void watchEffect

type InteractModeApi = {
	state: InteractState
	busy: Ref<boolean>

	cancel: () => void

	setPaymentToPid: (pid: string | null) => void
	setPaymentFromPid: (pid: string | null) => void

	setTrustlineToPid: (pid: string | null) => void
	setTrustlineFromPid: (pid: string | null) => void
}

type DesiredWindowState = {
	key: string
	type: 'interact-panel' | 'node-card' | 'edge-detail'
	shouldBeOpen: boolean
	data: any
	anchor: WindowAnchor | null
	focus: FocusMode
}

function anchorKey(a: WindowAnchor | null): string {
	if (!a) return ''
	return `${a.x}|${a.y}|${a.space}|${a.source}`
}

function interactDataKey(d: any): string {
	// IMPORTANT: do not include function identities (onBack/onClose) in comparisons.
	const panel = String(d?.panel ?? '')
	const phase = String(d?.phase ?? '')
	return `${panel}|${phase}`
}

function desiredSlotKey(s: DesiredWindowState): string {
	if (s.type === 'interact-panel') return `${s.type}@${anchorKey(s.anchor)}@${interactDataKey(s.data)}`
	if (s.type === 'node-card') return `${s.type}@${anchorKey(s.anchor)}@${String(s.data?.nodeId ?? '')}`
	if (s.type === 'edge-detail') {
		const fromPid = String(s.data?.fromPid ?? '')
		const toPid = String(s.data?.toPid ?? '')
		return `${s.type}@${anchorKey(s.anchor)}@${fromPid}→${toPid}`
	}
	const _exhaustive: never = s.type
	return _exhaustive
}

function applyDiff(prev: DesiredWindowState[], next: DesiredWindowState[], wm: WindowManagerApi): void {
	const prevByKey = new Map(prev.map((s) => [s.key, s] as const))
	const nextByKey = new Map(next.map((s) => [s.key, s] as const))

	const keys = new Set<string>()
	for (const k of prevByKey.keys()) keys.add(k)
	for (const k of nextByKey.keys()) keys.add(k)

	// Close first.
	for (const key of keys) {
		const p = prevByKey.get(key)
		const n = nextByKey.get(key)
		if (p?.shouldBeOpen && !(n?.shouldBeOpen ?? false)) {
			wm.closeByType(p.type, 'programmatic')
		}
	}

	// Open/update second.
	for (const key of keys) {
		const p = prevByKey.get(key)
		const n = nextByKey.get(key)
		if (!n?.shouldBeOpen) continue

		if (!p?.shouldBeOpen) {
			wm.open({
				type: n.type as any,
				anchor: n.anchor,
				data: n.data,
				focus: n.focus,
			})
			continue
		}

		// UPDATE (shallow-by-value)
		if (desiredSlotKey(p) !== desiredSlotKey(n)) {
			wm.open({
				type: n.type as any,
				anchor: n.anchor,
				data: n.data,
				focus: 'never',
			})
		}
	}
}

function createPeriodicTrailingThrottle<T>(ms: number, fn: (arg: T) => void): {
	request: (arg: T) => void
	cancel: () => void
} {
	let timer: ReturnType<typeof setTimeout> | null = null
	let pending = false
	let lastArg: T | null = null

	const tick = () => {
		timer = null

		if (!pending || lastArg == null) return
		pending = false
		const arg = lastArg

		fn(arg)

		// Keep the cadence while requests keep coming in.
		// If no more requests happen, the next tick will see `pending=false` and stop.
		timer = setTimeout(tick, ms)
	}

	const request = (arg: T) => {
		lastArg = arg
		pending = true
		if (timer != null) return
		timer = setTimeout(tick, ms)
	}

	const cancel = () => {
		if (timer != null) clearTimeout(timer)
		timer = null
		pending = false
		lastArg = null
	}

	return { request, cancel }
}

function isFormLikeTarget(t: EventTarget | null): boolean {
	const el = t as any
	const tag = String(el?.tagName ?? '').toLowerCase()
	if (tag === 'input' || tag === 'textarea' || tag === 'select') return true

	// contenteditable
	try {
		if (typeof el?.isContentEditable === 'boolean' && el.isContentEditable) return true
	} catch {
		// ignore
	}

	return false
}

function dispatchTopmostWindowEsc(wm: WindowManagerApi): boolean {
	try {
		const top = wm.windows.value.length ? wm.windows.value[wm.windows.value.length - 1] : null
		if (!top) return true

		const container = document.querySelector(`[data-win-id="${top.id}"]`) as HTMLElement | null
		if (!container) return true

		// Not bubbling to avoid re-entering onGlobalKeydown.
		const escEv = new KeyboardEvent('keydown', {
			key: 'Escape',
			code: 'Escape',
			cancelable: true,
			bubbles: false,
		})
		return container.dispatchEvent(escEv)
	} catch {
		return true
	}
}

function confirmCancelInteractBusy(): boolean {
	// P0-2 policy B: ESC / outside-click while busy must ask for confirmation.
	// Use a synchronous confirm for now (minimal UX) to keep the gate atomic.
	try {
		const c = (window as any)?.confirm
		if (typeof c !== 'function') return true
		return !!c('Отменить операцию?')
	} catch {
		// Best-effort: if confirm cannot be shown, default to allowing cancel.
		return true
	}
}

export function useWindowController(opts: {
	apiMode: Ref<string>
	isInteractUi: Ref<boolean>
	interactPhase: ComputedRef<InteractPhase>
	isFullEditor: Ref<boolean>
	interactState: InteractState
	interactMode: InteractModeApi

	wm: WindowManagerApi
	wmEdgeDetail: ReturnType<typeof useWmEdgeDetail>

	getNodeScreenCenter: (nodeId: string) => Point | null
}): {
	desiredWindows: ComputedRef<DesiredWindowState[]>

	wmEdgePopupAnchor: Ref<Point | null>
	wmPanelOpenAnchor: Ref<Point | null>

	wmNodeCardId: Ref<number | null>

	uiCloseEdgeDetailWindow: (reason: EdgeDetailCloseReason) => void
	uiCloseTopmostInspectorWindow: () => 'edge-detail' | 'node-card' | null

	uiOpenOrUpdateEdgeDetail: (o: { fromPid: string; toPid: string; anchor: Point }) => void
	uiOpenOrUpdateNodeCard: (o: { nodeId: string; anchor: Point | null }) => void

	onGlobalKeydown: (ev: KeyboardEvent) => void
} {
	const wmNodeCardId = ref<number | null>(null)

	const wmEdgePopupAnchor = ref<Point | null>(null)
	const wmPanelOpenAnchor = ref<Point | null>(null)

	function toWmAnchor(p: Point | null, source: string): WindowAnchor | null {
		if (!p) return null
		return { x: p.x, y: p.y, space: 'host', source }
	}

	function makeInteractPanelWindowData(panel: 'payment' | 'trustline' | 'clearing', phase: string) {
		const onBack = (): boolean => {
			// Step-back inside Interact FSM (only when it has a meaningful previous step).
			// MUST: do not perform Flow-cancel here; fallback to UI-close when no step-back exists.
			const p = String(opts.interactPhase.value) as InteractPhase

			// Payment: confirm → picking-to → (picking-from only if FROM was not pre-filled)
			if (p === 'confirm-payment') {
				opts.interactMode.setPaymentToPid(null)
				return true
			}
			if (p === 'picking-payment-to') {
				// If the flow was initiated with pre-filled FROM (NodeCard/EdgeDetail/etc),
				// there is no meaningful "previous step" — close the window instead.
				if (opts.interactMode.state.initiatedWithPrefilledFrom) return false
				opts.interactMode.setPaymentFromPid(null)
				return true
			}

			// Trustline: confirm/edit → picking-to → (picking-from only if FROM was not pre-filled)
			if (p === 'editing-trustline' || p === 'confirm-trustline-create') {
				// Always step back to picking-to by clearing TO.
				opts.interactMode.setTrustlineToPid(null)
				return true
			}
			if (p === 'picking-trustline-to') {
				// If the flow was initiated with pre-filled FROM (NodeCard/EdgeDetail/etc),
				// there is no meaningful "previous step" — close the window instead.
				if (opts.interactMode.state.initiatedWithPrefilledFrom) return false
				opts.interactMode.setTrustlineFromPid(null)
				return true
			}

			return false
		}

		return { panel, phase, onBack, onClose: () => opts.interactMode.cancel() }
	}

	const wmInteractAnchor = computed<WindowAnchor | null>(() => {
		// Edge popup anchor has priority (normative requirement).
		if (wmEdgePopupAnchor.value) return toWmAnchor(wmEdgePopupAnchor.value, 'edge-popup')

		// NodeCard / ActionBar initiated anchors should apply to the first WM open.
		if (wmPanelOpenAnchor.value) return toWmAnchor(wmPanelOpenAnchor.value, 'panel')

		return null
	})

	const wmInteractAnchorKey = computed(() => {
		const a = wmInteractAnchor.value
		if (!a) return ''
		return `${a.x}|${a.y}|${a.space}|${a.source}`
	})

	const desiredWindows = computed<DesiredWindowState[]>(() => {
		const apiMode = String(opts.apiMode.value)
		const isInteractUi = Boolean(opts.isInteractUi.value)
		const phase = String(opts.interactPhase.value) as InteractPhase
		const isFullEditor = Boolean(opts.isFullEditor.value)

		if (apiMode !== 'real' || !isInteractUi) return []

		const m = interactWindowOfPhase(phase, isFullEditor)
		if (m?.type !== 'interact-panel') return []

		return [
			{
				key: 'interact-panel',
				type: 'interact-panel',
				shouldBeOpen: true,
				data: makeInteractPanelWindowData(m.panel, phase),
				anchor: wmInteractAnchor.value,
				focus: 'never',
			},
		]
	})

	const edgeDetailAutoReq = computed(() => {
		const curApiMode = String(opts.apiMode.value)
		const curIsInteractUi = Boolean(opts.isInteractUi.value)
		const phase = String(opts.interactPhase.value)
		const isFullEditor = Boolean(opts.isFullEditor.value)

		const fromPid = String(opts.interactState.fromPid ?? '')
		const toPid = String(opts.interactState.toPid ?? '')
		const a = (opts.interactState as any).edgeAnchor as Point | null

		const eligible = curApiMode === 'real' && curIsInteractUi && phase === 'editing-trustline' && !isFullEditor
		if (!eligible) return null
		if (!a || !fromPid || !toPid) return null

		return {
			fromPid,
			toPid,
			anchor: toWmAnchor(a ?? null, 'interact-state'),
			focus: 'never' as const,
			source: 'auto' as const,
		}
	})

	let prevDesired: DesiredWindowState[] = []

	watch(
		[
			() => desiredWindows.value,
			() => edgeDetailAutoReq.value,
			() => String(opts.apiMode.value),
			() => Boolean(opts.isInteractUi.value),
			() => wmInteractAnchorKey.value,
		],
		([nextDesired, nextEdgeReq, curApiMode, curIsInteractUi]) => {
			// Interact panel bridging (desiredWindows diff).
			if (curApiMode !== 'real' || !curIsInteractUi) {
				opts.wm.closeGroup('interact', 'programmatic')
				// Match legacy: leaving interact UI clears only panel-open anchor.
				wmPanelOpenAnchor.value = null
			} else {
				applyDiff(prevDesired, nextDesired ?? [], opts.wm)

				// If interact window is not desired (idle/inspector-only), clear stale anchors.
				if ((nextDesired ?? []).length === 0) {
					wmEdgePopupAnchor.value = null
					wmPanelOpenAnchor.value = null
				}
			}

			prevDesired = nextDesired ?? []

			// EdgeDetail auto-sync (delegated state machine).
			opts.wmEdgeDetail.syncAuto(nextEdgeReq ?? null)
			opts.wmEdgeDetail.applyToWindowManager(opts.wm, { closeReason: 'programmatic' })
		},
		{ immediate: true },
	)

	// ---------------------------------------------------------------------------
	// UX-9: NodeCard → WindowManager anchor follow (throttled)
	// ---------------------------------------------------------------------------

	const nodeCardScreenCenterKey = computed(() => {
		const win = opts.wm.windows.value.find((w) => w.type === 'node-card' && w.state !== 'closing')
		if (!win) return ''
		const nodeId = String((win.data as any)?.nodeId ?? '')
		if (!nodeId) return ''
		const p = opts.getNodeScreenCenter(nodeId)
		if (!p) return ''
		return `${nodeId}|${p.x}|${p.y}`
	})

	const nodeCardAnchorUpdater = createPeriodicTrailingThrottle(100, (o: { nodeId: string; anchor: Point | null }) => {
		wmNodeCardId.value = opts.wm.open({
			type: 'node-card',
			// Use the same anchor source as the initial open to avoid a spurious
			// `anchorChanged` (WM compares source too).
			anchor: toWmAnchor(o.anchor, 'node'),
			data: {
				nodeId: o.nodeId,
				onClose: (reason: 'esc' | 'action' | 'programmatic') => {
					if (reason === 'esc' || reason === 'action') {
						if (wmNodeCardId.value != null) wmNodeCardId.value = null
					}
				},
			},
			// Watcher-driven (camera/layout changes): do not steal focus.
			focus: 'never',
		})
	})

	onUnmounted(() => {
		nodeCardAnchorUpdater.cancel()
	})

	watch(
		[
			() => opts.wm.windows.value.some((w) => w.type === 'node-card' && w.state !== 'closing'),
			() => nodeCardScreenCenterKey.value,
		],
		([isOpen]) => {
			if (!isOpen) return

			const win = opts.wm.windows.value.find((w) => w.type === 'node-card' && w.state !== 'closing')
			if (!win) return

			const nodeId = String((win.data as any)?.nodeId ?? '')
			if (!nodeId) return

			// Avoid redundant `wm.open()` right after the initial open.
			// (That would cause extra WM churn and can trigger a visible jump while unmeasured.)
			const p = opts.getNodeScreenCenter(nodeId)
			if (!p) return
			const cur = win.anchor
			if (cur && cur.x === p.x && cur.y === p.y && cur.space === 'host' && cur.source === 'node') {
				return
			}

			nodeCardAnchorUpdater.request({
				nodeId,
				anchor: p,
			})
		},
	)

	function uiCloseEdgeDetailWindow(reason: EdgeDetailCloseReason) {
		opts.wmEdgeDetail.close({ reason, suppress: true })
		opts.wmEdgeDetail.applyToWindowManager(opts.wm, { closeReason: reason })
	}

	function uiCloseTopmostInspectorWindow(): 'edge-detail' | 'node-card' | null {
		const top = opts.wm.getTopmostInGroup('inspector')
		const topType: 'edge-detail' | 'node-card' | null =
			top && top.policy.closeOnOutsideClick && (top.type === 'edge-detail' || top.type === 'node-card')
				? top.type
				: null

		// Clear keepAlive so frozen inspectors can't persist after an outside-click.
		opts.wmEdgeDetail.releaseKeepAlive()
		opts.wmEdgeDetail.applyToWindowManager(opts.wm, { closeReason: 'programmatic' })

		if (!top || !topType) return null

		// Outside-click is a UI-close. For edge-detail we must suppress auto-open
		// until selection changes (otherwise the watcher will immediately reopen it).
		if (topType === 'edge-detail') {
			opts.wmEdgeDetail.close({ reason: 'programmatic', suppress: true })
			opts.wmEdgeDetail.applyToWindowManager(opts.wm, { closeReason: 'programmatic' })
			return 'edge-detail'
		}

		// node-card
		opts.wm.close(top.id, 'programmatic')
		if (wmNodeCardId.value === top.id) wmNodeCardId.value = null
		return 'node-card'
	}

	function uiOpenOrUpdateEdgeDetail(o: { fromPid: string; toPid: string; anchor: Point }) {
		opts.wmEdgeDetail.open({
			fromPid: o.fromPid,
			toPid: o.toPid,
			anchor: toWmAnchor(o.anchor, 'edge-click'),
			focus: 'always',
		})
		opts.wmEdgeDetail.applyToWindowManager(opts.wm)
	}

	function uiOpenOrUpdateNodeCard(o: { nodeId: string; anchor: Point | null }) {
		const reqNodeId = String(o.nodeId ?? '').trim()
		if (!reqNodeId) return

		// UX-5: repeated dblclick on the SAME node while its NodeCard is already the
		// WM topmost window must be a no-op to avoid a visible flicker / extra relayout.
		const top = opts.wm.windows.value.length ? opts.wm.windows.value[opts.wm.windows.value.length - 1] : null
		if (top && top.type === 'node-card') {
			const topNodeId = String((top.data as any)?.nodeId ?? '').trim()
			if (topNodeId && topNodeId === reqNodeId) return
		}

		const id = opts.wm.open({
			type: 'node-card',
			anchor: toWmAnchor(o.anchor, 'node'),
			data: {
				nodeId: reqNodeId,
				onClose: (reason: 'esc' | 'action' | 'programmatic') => {
					if (reason === 'esc' || reason === 'action') {
						if (wmNodeCardId.value === id) wmNodeCardId.value = null
					}
				},
			},
			// User-initiated (node dblclick): always bring to front.
			focus: 'always',
		})
		wmNodeCardId.value = id
	}

	function hardDismissInteractBusyIfNeeded(): boolean {
		if (!opts.isInteractUi.value) return false
		if (!opts.interactMode.busy.value) return false

		const ok = confirmCancelInteractBusy()
		if (!ok) {
			// User declined: consume ESC (no WM close, no flow cancel).
			return true
		}

		// User confirmed: cancel flow (epoch bump) and close all related windows.
		opts.interactMode.cancel()
		opts.wm.closeGroup('interact', 'programmatic')

		// Match Step0 hard dismiss behavior: close inspector windows too.
		uiCloseTopmostInspectorWindow()
		uiCloseTopmostInspectorWindow()
		return true
	}

	function onGlobalKeydown(ev: KeyboardEvent) {
		// WM-only: delegate ESC handling to WindowManager.
		if (ev.key === 'Escape' || ev.key === 'Esc') {
			// Preserve existing rule: do not treat ESC in inputs as a global dismiss.
			if (isFormLikeTarget(ev.target)) return

			// P0-2 policy B: while Interact is busy, ESC is gated by confirmation.
			if (hardDismissInteractBusyIfNeeded()) return

			// If nested content already consumed ESC via a container listener, do not run WM close logic.
			if (ev.defaultPrevented) return

			opts.wm.handleEsc(ev, {
				isFormLikeTarget,
				dispatchWindowEsc: () => dispatchTopmostWindowEsc(opts.wm),
			})
		}
	}

	return {
		desiredWindows,

		wmEdgePopupAnchor,
		wmPanelOpenAnchor,

		wmNodeCardId,

		uiCloseEdgeDetailWindow,
		uiCloseTopmostInspectorWindow,

		uiOpenOrUpdateEdgeDetail,
		uiOpenOrUpdateNodeCard,

		onGlobalKeydown,
	}
}

