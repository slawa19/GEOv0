import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useGeoSimulatorStore = defineStore('geoSimulator', () => {
  const selectedNodeId = ref<string | null>(null)
  const isClearing = ref(false)

  const hasSelection = computed(() => selectedNodeId.value !== null)

  function selectNode(nodeId: string | null) {
    selectedNodeId.value = nodeId
  }

  function setClearing(next: boolean) {
    isClearing.value = next
  }

  return {
    selectedNodeId,
    isClearing,
    hasSelection,
    selectNode,
    setClearing,
  }
})

