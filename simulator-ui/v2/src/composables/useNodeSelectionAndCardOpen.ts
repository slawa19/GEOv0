import { ref, watch, type Ref } from 'vue'

export function useNodeSelectionAndCardOpen(opts: { selectedNodeId: Ref<string | null> }) {
  const isNodeCardOpen = ref(false)

  function selectNode(id: string | null) {
    opts.selectedNodeId.value = id
    // Single selection should not implicitly open the card.
    isNodeCardOpen.value = false
  }

  function setNodeCardOpen(open: boolean) {
    isNodeCardOpen.value = open && !!opts.selectedNodeId.value
  }

  watch(opts.selectedNodeId, (id) => {
    if (!id) isNodeCardOpen.value = false
  })

  return {
    isNodeCardOpen,
    selectNode,
    setNodeCardOpen,
  }
}
