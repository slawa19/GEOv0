<script setup lang="ts">
/**
 * 048 S3 (decision 3, «одна форма ошибки»): the failure of an action, in its window - one sentence of the interface, and
 * APART from it the server's correlation id: `data-request-id` (AGENTS.md section 12). 049 S2 (decision 7, the owner's
 * word of 2026-10-20 «какая польза для юзера?»): the id is no longer a drawn line («код запроса: <id>») - the attribute and
 * the console carry it. The technical detail (the HTTP status, the code) is the `title` only - never drawn. The window's
 * error toast that duplicated this block (and cleared it after 4-8 s) is gone.
 */
const props = defineProps<{
  text: string
  requestId?: string | null
  detail?: string | null
  testid: string
}>()
</script>

<template>
  <!-- 048 S3 (§15 Codex P2): role="alert" - the failure is announced when it appears, as the removed toast announced it
       (`role="alert"`, assertive); no ancestor of the block in the windows is a live region, so it is said once. -->
  <div
    class="ds-alert ds-alert--err ds-mono web-block"
    role="alert"
    aria-atomic="true"
    :data-testid="props.testid"
    :data-request-id="props.requestId || undefined"
    :title="props.detail || undefined"
  >
    <span class="web-text" data-testid="error-text">{{ props.text }}</span>
  </div>
</template>

<style scoped>
.web-block {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}
</style>
