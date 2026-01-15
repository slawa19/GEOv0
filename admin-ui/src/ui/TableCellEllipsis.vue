<script setup lang="ts">
defineProps<{ text: string | number | null | undefined }>()
</script>

<template>
  <span
    class="cell"
    :title="text === null || text === undefined || String(text).trim() === '' ? '' : String(text)"
  >
    {{ text === null || text === undefined || String(text).trim() === '' ? '—' : String(text) }}
  </span>
</template>

<style scoped>
.cell {
  display: inline-block;
  flex: 1 1 auto;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
</style>
