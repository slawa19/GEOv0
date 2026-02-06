<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  enabled: boolean
  isBusy: boolean
  placement?: 'corner' | 'inline'
  runTxOnce: () => Promise<void>
  runClearingOnce: () => Promise<void>
}>()

const localBusy = ref(false)
const errorText = ref('')

const busy = computed(() => props.isBusy || localBusy.value)
const placement = computed(() => props.placement ?? 'corner')

async function wrap(action: () => Promise<void>) {
  if (busy.value) return
  errorText.value = ''
  localBusy.value = true
  try {
    await action()
  } catch (e: any) {
    const msg = String(e?.message ?? e)
    const body = typeof e?.bodyText === 'string' && e.bodyText.trim() ? `\n${e.bodyText.trim()}` : ''
    errorText.value = `${msg}${body}`
  } finally {
    localBusy.value = false
  }
}

function runTxOnce() {
  return wrap(props.runTxOnce)
}

function runClearingOnce() {
  return wrap(props.runClearingOnce)
}
</script>

<template>
  <div
    v-if="enabled"
    class="fx-debug"
    :data-busy="busy ? '1' : '0'"
    :data-placement="placement"
  >
    <div class="fx-debug__title">FX Debug</div>
    <div class="fx-debug__row">
      <button class="fx-debug__btn" type="button" :disabled="busy" @click="runTxOnce">Single TX</button>
      <button class="fx-debug__btn" type="button" :disabled="busy" @click="runClearingOnce">Run Clearing</button>
    </div>

    <div v-if="errorText" class="fx-debug__error">
      <div class="fx-debug__error-title">Action failed</div>
      <div class="fx-debug__error-text mono">{{ errorText }}</div>
    </div>
  </div>
</template>

<style scoped>
.fx-debug {
  position: absolute;
  left: 12px;
  bottom: 12px;
  z-index: 40;
  padding: 10px 10px 8px;
  border-radius: 10px;
  background: rgba(10, 12, 16, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.12);
  backdrop-filter: blur(8px);
  color: rgba(255, 255, 255, 0.92);
  user-select: none;
}

.fx-debug[data-placement='inline'] {
  position: static;
  left: auto;
  bottom: auto;
  z-index: auto;
  margin-top: 8px;
}

.fx-debug__title {
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.8;
  margin-bottom: 6px;
}

.fx-debug__row {
  display: flex;
  gap: 8px;
}

.fx-debug__btn {
  appearance: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.08);
  color: inherit;
  border-radius: 9px;
  padding: 8px 10px;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
}

.fx-debug__btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.fx-debug__error {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.10);
  max-width: 420px;
}

.fx-debug__error-title {
  font-size: 11px;
  letter-spacing: 0.02em;
  opacity: 0.85;
  margin-bottom: 4px;
}

.fx-debug__error-text {
  font-size: 12px;
  line-height: 1.35;
  white-space: pre-wrap;
  opacity: 0.9;
}
</style>
