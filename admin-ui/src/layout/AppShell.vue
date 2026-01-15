<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useHealthStore } from '../stores/health'
import { useAuthStore } from '../stores/auth'
import { useConfigStore } from '../stores/config'
import * as Tooltips from '../content/tooltips'
import type { TooltipKey } from '../content/tooltips'

type NavItem = {
  path: string
  label: string
  tooltipKey: TooltipKey
}

const navItems: NavItem[] = [
  { path: '/dashboard', label: 'Dashboard', tooltipKey: 'nav.dashboard' },
  { path: '/integrity', label: 'Integrity', tooltipKey: 'nav.integrity' },
  { path: '/incidents', label: 'Incidents', tooltipKey: 'nav.incidents' },
  { path: '/trustlines', label: 'Trustlines', tooltipKey: 'nav.trustlines' },
  { path: '/graph', label: 'Network Graph', tooltipKey: 'nav.graph' },
  { path: '/participants', label: 'Participants', tooltipKey: 'nav.participants' },
  { path: '/config', label: 'Config', tooltipKey: 'nav.config' },
  { path: '/feature-flags', label: 'Feature Flags', tooltipKey: 'nav.featureFlags' },
  { path: '/audit-log', label: 'Audit Log', tooltipKey: 'nav.auditLog' },
  { path: '/equivalents', label: 'Equivalents', tooltipKey: 'nav.equivalents' },
]

function getTooltipContent(key: TooltipKey): string {
  const tooltips = ((Tooltips as any).TOOLTIPS || (Tooltips as any).default || {}) as Record<string, any>
  const t = tooltips[key]
  if (!t) return ''
  return t.body.join(' ')
}

const route = useRoute()
const router = useRouter()

const isMockMode = computed(() => (import.meta.env.VITE_API_MODE || 'mock').toString().toLowerCase() !== 'real')

const apiBaseLabel = computed(() => {
  if (isMockMode.value) return 'fixtures: /admin-fixtures/v1'
  const envVal = (import.meta.env as any).VITE_API_BASE_URL
  const raw = (envVal === undefined || envVal === null ? '' : String(envVal)).trim()
  if (raw) return raw
  if (import.meta.env.DEV) return 'http://127.0.0.1:18000 (default)'
  return '(same origin)'
})

const apiModeBadge = computed(() => (isMockMode.value ? 'MOCK DATA' : 'REAL API'))
const apiModeBadgeType = computed(() => (isMockMode.value ? 'warning' : 'success'))

const healthStore = useHealthStore()
const authStore = useAuthStore()
const configStore = useConfigStore()

onMounted(() => {
  healthStore.startPolling(15000)
  authStore.load()
  void configStore.load()
})

const scenario = computed({
  get: () => (route.query.scenario as string | undefined) || 'happy',
  set: (v: string) => {
    void router.replace({ query: { ...route.query, scenario: v } })
  },
})

// Scenario is a mock-only UI feature; strip it in real mode.
onMounted(() => {
  if (isMockMode.value) return
  if (!('scenario' in (route.query || {}))) return
  const { scenario: _ignored, ...rest } = (route.query || {}) as any
  void router.replace({ query: rest })
})

const THEME_KEY = 'admin-ui.theme'
const dark = ref(true)

try {
  const saved = (localStorage.getItem(THEME_KEY) || '').toLowerCase()
  // Default dark unless user has explicitly chosen a theme.
  dark.value = saved ? saved === 'dark' : true
} catch {
  // ignore
}

watch(
  dark,
  (v) => {
    document.documentElement.classList.toggle('dark', v)
    localStorage.setItem(THEME_KEY, v ? 'dark' : 'light')
  },
  { immediate: true },
)

const activePath = computed(() => route.path)

const title = computed(() => (route.meta.title as string | undefined) || 'Admin')

function navigate(path: string) {
  void router.push({ path, query: { ...route.query } })
}
</script>

<template>
  <el-container class="app-root">
    <el-aside width="168px" class="aside">
      <div class="brand" @click="navigate('/dashboard')">
        <div class="brand__title">GEO Hub</div>
        <div class="brand__subtitle">Admin Console<span v-if="isMockMode"> (prototype)</span></div>
      </div>

      <el-menu :default-active="activePath" router class="menu">
        <el-tooltip
          v-for="item in navItems"
          :key="item.path"
          placement="right"
          :show-after="850"
          effect="dark"
          popper-class="geoTooltip geoTooltip--menu"
        >
          <template #content>
            <span class="geoTooltipText geoTooltipText--clamp2">{{ getTooltipContent(item.tooltipKey) }}</span>
          </template>
          <el-menu-item :index="item.path" @click="navigate(item.path)">
            {{ item.label }}
          </el-menu-item>
        </el-tooltip>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header__left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>Admin</el-breadcrumb-item>
            <el-breadcrumb-item>{{ title }}</el-breadcrumb-item>
          </el-breadcrumb>

          <div class="status">
            <el-tooltip v-if="healthStore.error" :content="healthStore.error" placement="bottom" effect="dark">
              <el-tag type="danger">health error</el-tag>
            </el-tooltip>
            <el-tag v-else type="success">ok</el-tag>

            <el-tooltip placement="bottom" effect="dark" :show-after="850" popper-class="geoTooltip geoTooltip--menu">
              <template #content>
                <span class="geoTooltipText geoTooltipText--clamp2">API source: {{ apiBaseLabel }}</span>
              </template>
              <el-tag :type="apiModeBadgeType" effect="plain">{{ apiModeBadge }}</el-tag>
            </el-tooltip>

            <el-tag v-if="isMockMode" type="info">scenario: {{ scenario }}</el-tag>
          </div>
        </div>

        <div class="header__right">
          <el-select v-model="authStore.role" size="small" style="width: 150px">
            <el-option label="admin" value="admin" />
            <el-option label="operator" value="operator" />
            <el-option label="auditor (read-only)" value="auditor" />
          </el-select>

          <el-select v-if="isMockMode" v-model="scenario" size="small" style="width: 190px">
            <el-option label="happy" value="happy" />
            <el-option label="empty" value="empty" />
            <el-option label="error500" value="error500" />
            <el-option label="admin_forbidden403" value="admin_forbidden403" />
            <el-option label="integrity_unauthorized401" value="integrity_unauthorized401" />
            <el-option label="slow" value="slow" />
          </el-select>

          <el-switch v-model="dark" size="small" active-text="Dark" inactive-text="Light" />
        </div>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-root {
  height: 100vh;
}

.aside {
  border-right: 1px solid var(--el-border-color);
}

.menu :deep(.el-menu-item) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-left: 12px;
  padding-right: 12px;
}

.brand__title,
.brand__subtitle {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.brand {
  padding: 14px 14px 8px;
  cursor: pointer;
  user-select: none;
}
.brand__title {
  font-weight: 700;
}
.brand__subtitle {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--el-border-color);
}

.header__left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.status {
  display: flex;
  gap: 8px;
  align-items: center;
}

.header__right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.main {
  padding: 16px;
}
</style>
