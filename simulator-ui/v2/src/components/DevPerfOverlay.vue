<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

const props = defineProps<{
  enabled: boolean
  perf: {
    lastFps: number | null
    fxBudgetScale: number | null
    maxParticles: number | null
    renderQuality: string | null
    dprClamp: number | null

    canvasCssW: number | null
    canvasCssH: number | null
    canvasPxW: number | null
    canvasPxH: number | null
    canvasDpr: number | null
  }
}>()

type GpuInfo = { vendor: string | null; renderer: string | null }

const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1

const gpu = ref<GpuInfo>({ vendor: null, renderer: null })

function tryGetGpuInfo(): GpuInfo {
  try {
    const canvas = document.createElement('canvas')
    const gl = (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')) as WebGLRenderingContext | null
    if (!gl) return { vendor: null, renderer: null }

    const dbg = gl.getExtension('WEBGL_debug_renderer_info') as any
    if (!dbg) return { vendor: null, renderer: null }

    const vendor = String(gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) ?? '')
    const renderer = String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) ?? '')
    return { vendor: vendor || null, renderer: renderer || null }
  } catch {
    return { vendor: null, renderer: null }
  }
}

const text = computed(() => {
  const p = props.perf
  const fmt = (v: any) => (v === null || v === undefined || v === '' ? '—' : String(v))

  const lines = [
    `UA: ${ua}`,
    `devicePixelRatio: ${fmt(dpr)}`,
    `WEBGL vendor: ${fmt(gpu.value.vendor)}`,
    `WEBGL renderer: ${fmt(gpu.value.renderer)}`,
    '',
    `FPS(last): ${fmt(p.lastFps)}`,
    `renderQuality(effective): ${fmt(p.renderQuality)}`,
    `dprClamp(effective): ${fmt(p.dprClamp)}`,
    `fxBudgetScale: ${fmt(p.fxBudgetScale)}`,
    `maxParticles: ${fmt(p.maxParticles)}`,
    '',
    `canvas css: ${fmt(p.canvasCssW)} x ${fmt(p.canvasCssH)}`,
    `canvas px:  ${fmt(p.canvasPxW)} x ${fmt(p.canvasPxH)}`,
    `canvasDpr:  ${fmt(p.canvasDpr)}`,
  ]

  return lines.join('\n')
})

const copied = ref(false)
let copiedTimer: number | null = null

function legacyCopyText(s: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = s
    ta.setAttribute('readonly', 'true')
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    ta.style.top = '-9999px'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

async function copy() {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text.value)
    } else {
      const ok = legacyCopyText(text.value)
      if (!ok) throw new Error('legacy copy failed')
    }
    copied.value = true
    if (copiedTimer !== null) window.clearTimeout(copiedTimer)
    copiedTimer = window.setTimeout(() => {
      copied.value = false
      copiedTimer = null
    }, 900)
  } catch {
    // As a last resort, try legacy copy (some embedded browsers block navigator.clipboard).
    const ok = legacyCopyText(text.value)
    if (ok) {
      copied.value = true
      if (copiedTimer !== null) window.clearTimeout(copiedTimer)
      copiedTimer = window.setTimeout(() => {
        copied.value = false
        copiedTimer = null
      }, 900)
    }
  }
}

onMounted(() => {
  if (!props.enabled) return
  gpu.value = tryGetGpuInfo()
})

onUnmounted(() => {
  if (copiedTimer !== null) window.clearTimeout(copiedTimer)
})
</script>

<template>
  <div v-if="enabled" class="perf ds-ov-item ds-ov-surface ds-ov-dev-perf" role="region" aria-label="Performance diagnostics">
    <div class="row">
      <div class="title">Perf probe</div>
      <button class="ds-btn ds-btn--secondary" style="height: 28px; padding: 0 10px" type="button" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
    </div>

    <div
      v-if="String(gpu.renderer ?? '').includes('Microsoft Basic Render Driver')"
      class="warn"
      role="note"
    >
      Hardware acceleration appears disabled (Microsoft Basic Render Driver). Expect severe slowness in Chrome.
      Check <span class="mono">chrome://gpu</span> and enable hardware acceleration.
    </div>

    <pre class="pre">{{ text }}</pre>
  </div>
</template>

<style scoped>
.perf {
  /* visuals + placement live in DS overlays layer */
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px 6px 12px;
}

.title {
  font-size: 12px;
  font-weight: 600;
  color: var(--ds-text-1);
}


.pre {
  margin: 0;
  padding: 0 12px 12px 12px;
  font-size: 11px;
  line-height: 1.35;
  color: var(--ds-text-2);
  white-space: pre-wrap;
}

.warn {
  margin: 0 12px 8px 12px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid color-mix(in srgb, var(--ds-warn) 35%, transparent);
  background: color-mix(in srgb, var(--ds-warn) 18%, transparent);
  color: color-mix(in srgb, var(--ds-warn) 12%, var(--ds-text-1));
  font-size: 12px;
  line-height: 1.35;
}

.mono {
  font-family: var(--ds-font-mono);
}
</style>
