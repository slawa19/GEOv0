import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export type AdminRole = 'admin' | 'operator' | 'auditor'

const ROLE_KEY = 'admin-ui.role'

export const useAuthStore = defineStore('auth', () => {
  const role = ref<AdminRole>('admin')

  function load() {
    const saved = (localStorage.getItem(ROLE_KEY) || '').trim().toLowerCase()
    if (saved === 'admin' || saved === 'operator' || saved === 'auditor') role.value = saved
  }

  watch(
    role,
    (v) => {
      localStorage.setItem(ROLE_KEY, v)
    },
    { immediate: true },
  )

  const isReadOnly = computed(() => role.value === 'auditor')

  return { role, load, isReadOnly }
})
