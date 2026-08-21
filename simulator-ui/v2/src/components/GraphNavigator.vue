<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { GraphLink, GraphNode } from '../types'
import { keyEdge } from '../utils/edgeKey'

type Props = {
  nodes: GraphNode[]
  links: GraphLink[]
  edgeInspectDisabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  edgeInspectDisabled: false,
})

const emit = defineEmits<{
  inspectNode: [nodeId: string]
  inspectEdge: [link: GraphLink]
}>()

const sortedNodes = computed(() =>
  [...props.nodes].sort((a, b) => nodeLabel(a).localeCompare(nodeLabel(b), undefined, { numeric: true })),
)
const sortedLinks = computed(() =>
  [...props.links].sort((a, b) => edgeLabel(a).localeCompare(edgeLabel(b), undefined, { numeric: true })),
)

const selectedNodeId = ref('')
const selectedEdgeKey = ref('')
const announcement = ref('')

function nodeLabel(node: GraphNode): string {
  const name = node.name?.trim()
  return name && name !== node.id ? `${name} (${node.id})` : node.id
}

function edgeLabel(link: GraphLink): string {
  return `${link.source} → ${link.target}`
}

watch(
  sortedNodes,
  (nodes) => {
    if (!nodes.some((node) => node.id === selectedNodeId.value)) selectedNodeId.value = nodes[0]?.id ?? ''
  },
  { immediate: true },
)

watch(
  sortedLinks,
  (links) => {
    if (!links.some((link) => keyEdge(link.source, link.target) === selectedEdgeKey.value)) {
      const first = links[0]
      selectedEdgeKey.value = first ? keyEdge(first.source, first.target) : ''
    }
  },
  { immediate: true },
)

function inspectNode(): void {
  const node = sortedNodes.value.find((candidate) => candidate.id === selectedNodeId.value)
  if (!node) return
  emit('inspectNode', node.id)
  announcement.value = `Node details opened: ${nodeLabel(node)}`
}

function inspectEdge(): void {
  const link = sortedLinks.value.find((candidate) => keyEdge(candidate.source, candidate.target) === selectedEdgeKey.value)
  if (!link || props.edgeInspectDisabled) return
  emit('inspectEdge', link)
  announcement.value = `Trustline details opened: ${edgeLabel(link)}`
}
</script>

<template>
  <div class="graph-navigator ds-panel" role="region" aria-label="Graph navigator">
    <details>
      <summary>Inspect graph</summary>

      <div class="graph-navigator__controls">
      <label class="ds-label" for="graph-navigator-node">Node</label>
      <select
        id="graph-navigator-node"
        v-model="selectedNodeId"
        class="ds-select"
        :disabled="sortedNodes.length === 0"
      >
        <option v-for="node in sortedNodes" :key="node.id" :value="node.id">{{ nodeLabel(node) }}</option>
      </select>
      <button class="ds-btn ds-btn--secondary" type="button" :disabled="!selectedNodeId" @click="inspectNode">
        Inspect node
      </button>

      <label class="ds-label" for="graph-navigator-edge">Edge</label>
      <select
        id="graph-navigator-edge"
        v-model="selectedEdgeKey"
        class="ds-select"
        :disabled="sortedLinks.length === 0"
      >
        <option v-for="link in sortedLinks" :key="keyEdge(link.source, link.target)" :value="keyEdge(link.source, link.target)">
          {{ edgeLabel(link) }}
        </option>
      </select>
      <button
        class="ds-btn ds-btn--secondary"
        type="button"
        :disabled="selectedEdgeKey === '' || edgeInspectDisabled"
        :title="edgeInspectDisabled ? 'Edge details are available in real Interact mode.' : ''"
        @click="inspectEdge"
      >
        Inspect edge
      </button>
      </div>

      <p
        class="graph-navigator__status ds-help"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-testid="graph-navigator-status"
      >
        {{ announcement }}
      </p>
    </details>
  </div>
</template>

<style scoped>
.graph-navigator {
  width: min(100%, 520px);
  padding: 8px 10px;
}

.graph-navigator > details > summary {
  cursor: pointer;
  font-weight: 600;
}

.graph-navigator__controls {
  display: grid;
  grid-template-columns: auto minmax(120px, 1fr) auto;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}

.graph-navigator__status {
  min-height: 1.3em;
  margin: 6px 0 0;
}

@media (max-width: 620px) {
  .graph-navigator__controls {
    grid-template-columns: 1fr;
  }
}
</style>
