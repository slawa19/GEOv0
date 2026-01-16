import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { t } from '../i18n'

export const useConfigStore = defineStore('config', () => {
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  const config = ref<Record<string, unknown>>({})

  async function load() {
    loading.value = true
    error.value = null
    try {
      config.value = assertSuccess(await api.getConfig())
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : t('config.loadFailed')
    } finally {
      loading.value = false
    }
  }

  async function patch(patchObj: Record<string, unknown>) {
    saving.value = true
    error.value = null
    try {
      assertSuccess(await api.patchConfig(patchObj))
      config.value = { ...config.value, ...patchObj }
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : t('config.saveFailed')
      throw e
    } finally {
      saving.value = false
    }
  }

  const featureFlags = computed(() => ({
    multipath_enabled: Boolean(
      config.value['FEATURE_FLAGS_MULTIPATH_ENABLED'] ?? config.value['feature_flags.multipath_enabled'],
    ),
    full_multipath_enabled: Boolean(
      config.value['FEATURE_FLAGS_FULL_MULTIPATH_ENABLED'] ?? config.value['feature_flags.full_multipath_enabled'],
    ),
  }))

  return { loading, saving, error, config, featureFlags, load, patch }
})
