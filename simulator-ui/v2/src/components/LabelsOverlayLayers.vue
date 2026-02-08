<script setup lang="ts">

type LabelNode = {
  id: string
  x: number
  y: number
  text: string
  color: string
}

type FloatingLabel = {
  id: number
  x: number
  y: number
  text: string
  color: string
  glow: number
  cssClass?: string
}

type Props = {
  labelNodes: LabelNode[]
  floatingLabels: FloatingLabel[]
  worldToCssTranslateNoScale: (x: number, y: number) => string
}

defineProps<Props>()
</script>

<template>
  <!-- Floating Labels (isolated layer for perf) -->
  <div v-if="labelNodes.length" class="labels-layer">
    <div
      v-for="n in labelNodes"
      :key="n.id"
      class="node-label"
      :style="{ transform: worldToCssTranslateNoScale(n.x, n.y) }"
    >
      <div class="node-label-inner" :style="{ borderColor: n.color, color: n.color }">{{ n.text }}</div>
    </div>
  </div>

  <div class="floating-layer">
    <div
      v-for="fl in floatingLabels"
      :key="fl.id"
      class="floating-label"
      :style="{ transform: worldToCssTranslateNoScale(fl.x, fl.y) }"
    >
      <div
        class="floating-label-inner"
        :class="[{ 'is-glow': fl.glow > 0.05 }, fl.cssClass]"
        :style="{ color: fl.color, '--glow': String(fl.glow) }"
      >
        {{ fl.text }}
      </div>
    </div>
  </div>
</template>
