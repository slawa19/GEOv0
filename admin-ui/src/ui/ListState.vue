<script setup lang="ts">
import LoadErrorAlert from './LoadErrorAlert.vue'
import { t } from '../i18n'

/**
 * The state of a list or a card that loads (032 S7, D-2): exactly one of the failure, the skeleton, the empty
 * state or the data. The four are a single `v-if` / `v-else-if` chain here, so "the load failed" and "the list is
 * empty" cannot be shown together - the pages used to write the failure as a separate `v-if` before the chain and
 * showed an empty list under every failure.
 *
 * Order matters: a failure wins over everything (a retry clears it before it starts the next request); a request in
 * flight shows the skeleton; only an answered, successful, empty list is "empty".
 */
withDefaults(
  defineProps<{
    /** The text of the failure of the last load, or `null`. */
    error: string | null
    loading: boolean
    /** There is nothing to show (the list is empty / there is no payload yet). Only read once the load has succeeded. */
    empty: boolean
    /** What "empty" says; defaults to the generic "No data". */
    emptyText?: string
    skeletonRows?: number
  }>(),
  { emptyText: undefined, skeletonRows: 10 },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <LoadErrorAlert
    v-if="error"
    :title="error"
    :busy="loading"
    @retry="emit('retry')"
  />
  <el-skeleton
    v-else-if="loading"
    animated
    :rows="skeletonRows"
  />
  <el-empty
    v-else-if="empty"
    :description="emptyText ?? t('common.noData')"
  />
  <slot v-else />
</template>
