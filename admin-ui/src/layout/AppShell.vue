<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useHealthStore } from '../stores/health'
import { useAuthStore } from '../stores/auth'
import { useConfigStore } from '../stores/config'
import { HEALTH_POLL_INTERVAL_MS } from '../constants/timing'
import { locale, setLocale, t } from '../i18n'
import { getTooltipContent } from '../content/tooltips'
import CopyIconButton from '../ui/CopyIconButton.vue'
import { readQueryString, toLocationQueryRaw } from '../router/query'
import type { TooltipKey } from '../content/tooltips'

type NavItem = {
  path: string
  labelKey: string
  tooltipKey: TooltipKey
}

const navItems: NavItem[] = [
  { path: '/dashboard', labelKey: 'nav.dashboard.label', tooltipKey: 'nav.dashboard' },
  { path: '/liquidity', labelKey: 'nav.liquidity.label', tooltipKey: 'nav.liquidity' },
  { path: '/integrity', labelKey: 'nav.integrity.label', tooltipKey: 'nav.integrity' },
  { path: '/incidents', labelKey: 'nav.incidents.label', tooltipKey: 'nav.incidents' },
  { path: '/trustlines', labelKey: 'nav.trustlines.label', tooltipKey: 'nav.trustlines' },
  { path: '/graph', labelKey: 'nav.graph.label', tooltipKey: 'nav.graph' },
  { path: '/participants', labelKey: 'nav.participants.label', tooltipKey: 'nav.participants' },
  { path: '/config', labelKey: 'nav.config.label', tooltipKey: 'nav.config' },
  { path: '/feature-flags', labelKey: 'nav.featureFlags.label', tooltipKey: 'nav.featureFlags' },
  { path: '/audit-log', labelKey: 'nav.auditLog.label', tooltipKey: 'nav.auditLog' },
  { path: '/equivalents', labelKey: 'nav.equivalents.label', tooltipKey: 'nav.equivalents' },
]

const route = useRoute()
const router = useRouter()

const isMockMode = computed(() => (import.meta.env.VITE_API_MODE || 'mock').toString().toLowerCase() !== 'real')

const apiBaseLabel = computed(() => {
  if (isMockMode.value) return t('app.apiBase.fixtures', { path: '/admin-fixtures/v1' })
  const env = import.meta.env as unknown as Record<string, unknown>
  const envVal = env.VITE_API_BASE_URL
  const raw = (envVal === undefined || envVal === null ? '' : String(envVal)).trim()
  if (raw) return raw
  if (import.meta.env.DEV) return t('app.apiBase.defaultDev', { url: 'http://127.0.0.1:18000' })
  return t('app.apiBase.sameOrigin')
})

const apiModeBadge = computed(() => (isMockMode.value ? t('app.apiMode.mock') : t('app.apiMode.real')))
const apiModeBadgeType = computed(() => (isMockMode.value ? 'warning' : 'success'))

const healthStore = useHealthStore()
const authStore = useAuthStore()
const configStore = useConfigStore()

onMounted(() => {
  healthStore.startPolling(HEALTH_POLL_INTERVAL_MS)
  authStore.load()
  void configStore.load()
})

onBeforeUnmount(() => {
  healthStore.stopPolling()
})

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    healthStore.stopPolling()
  })
}

const scenario = computed({
  get: () => readQueryString(route.query.scenario) || 'happy',
  set: (v: string) => {
    void router.replace({ query: toLocationQueryRaw({ ...route.query, scenario: v }) })
  },
})

// Scenario is a mock-only UI feature; strip it in real mode.
onMounted(() => {
  if (isMockMode.value) return
  if (!('scenario' in (route.query || {}))) return
  const { scenario: _ignored, ...rest } = route.query || {}
  void router.replace({ query: toLocationQueryRaw(rest) })
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

const title = computed(() => {
  const titleKey = route.meta?.titleKey as string | undefined
  const rawTitle = route.meta?.title as string | undefined
  if (titleKey) return t(titleKey)
  return rawTitle || t('app.titleFallback')
})

const uiLocale = computed({
  get: () => locale.value,
  set: (v: 'en' | 'ru') => setLocale(v),
})

const quickJump = ref('')

const runLocalOpen = ref(false)
const runLocalCommand = computed(() => '.\\scripts\\run_local.ps1 -Action start')

function normQuickJump(v: unknown): string {
  return String(v ?? '').trim()
}

function goParticipants() {
  const q = normQuickJump(quickJump.value)
  if (!q) return
  void router.push({ path: '/participants', query: toLocationQueryRaw({ ...route.query, q }) })
}

function goTrustlinesCreditor() {
  const pid = normQuickJump(quickJump.value)
  if (!pid) return
  void router.push({ path: '/trustlines', query: toLocationQueryRaw({ ...route.query, creditor: pid }) })
}

function goTrustlinesDebtor() {
  const pid = normQuickJump(quickJump.value)
  if (!pid) return
  void router.push({ path: '/trustlines', query: toLocationQueryRaw({ ...route.query, debtor: pid }) })
}

function goAuditLog() {
  const q = normQuickJump(quickJump.value)
  if (!q) return
  void router.push({ path: '/audit-log', query: toLocationQueryRaw({ ...route.query, q }) })
}

function navigate(path: string) {
  void router.push({ path, query: toLocationQueryRaw({ ...route.query }) })
}
</script>

<template>
  <el-container class="app-root">
    <el-aside
      width="168px"
      class="aside"
    >
      <div
        class="brand"
        @click="navigate('/dashboard')"
      >
        <div class="brand__title">
          GEO Hub
        </div>
        <div class="brand__subtitle">
          {{ t('app.brand.subtitle') }}<span v-if="isMockMode"> {{ t('app.brand.prototype') }}</span>
        </div>
      </div>

      <el-menu
        :default-active="activePath"
        router
        class="menu"
      >
        <el-tooltip
          v-for="item in navItems"
          :key="item.path"
          placement="right"
          :show-after="850"
          effect="dark"
          popper-class="geoTooltip geoTooltip--menu"
        >
          <template #content>
            <span class="geoTooltipText geoTooltipText--clamp2">{{ getTooltipContent(item.tooltipKey, locale) }}</span>
          </template>
          <el-menu-item
            :index="item.path"
            @click="navigate(item.path)"
          >
            {{ t(item.labelKey) }}
          </el-menu-item>
        </el-tooltip>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header__left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>{{ t('app.breadcrumb.admin') }}</el-breadcrumb-item>
            <el-breadcrumb-item>{{ title }}</el-breadcrumb-item>
          </el-breadcrumb>

          <div class="status">
            <el-tooltip
              v-if="healthStore.error"
              :content="healthStore.error"
              placement="bottom"
              effect="dark"
            >
              <el-tag type="danger">
                {{ t('app.status.healthError') }}
              </el-tag>
            </el-tooltip>
            <el-tag
              v-else
              type="success"
            >
              {{ t('app.status.ok') }}
            </el-tag>

            <el-tooltip
              placement="bottom"
              effect="dark"
              :show-after="850"
              popper-class="geoTooltip geoTooltip--menu"
            >
              <template #content>
                <span class="geoTooltipText geoTooltipText--clamp2">{{ t('app.status.apiSource', { label: apiBaseLabel }) }}</span>
              </template>
              <el-tag
                :type="apiModeBadgeType"
                effect="plain"
              >
                {{ apiModeBadge }}
              </el-tag>
            </el-tooltip>

            <el-tag
              v-if="isMockMode"
              type="info"
            >
              {{ t('app.status.scenario', { scenario }) }}
            </el-tag>
          </div>
        </div>

        <div class="header__right">
          <div class="quick">
            <el-input
              v-model="quickJump"
              size="small"
              clearable
              :placeholder="t('app.quickJump.placeholder')"
              style="width: 260px"
              @keyup.enter="goParticipants"
            />
            <el-button-group>
              <el-tooltip
                placement="bottom"
                effect="dark"
                :show-after="700"
              >
                <template #content>
                  {{ t('app.quickJump.participants') }}
                </template>
                <el-button
                  size="small"
                  @click="goParticipants"
                >
                  {{ t('nav.participants.label') }}
                </el-button>
              </el-tooltip>

              <el-tooltip
                placement="bottom"
                effect="dark"
                :show-after="700"
              >
                <template #content>
                  {{ t('app.quickJump.trustlinesAsCreditor') }}
                </template>
                <el-button
                  size="small"
                  @click="goTrustlinesCreditor"
                >
                  {{ t('trustlines.fromCreditor') }}
                </el-button>
              </el-tooltip>

              <el-tooltip
                placement="bottom"
                effect="dark"
                :show-after="700"
              >
                <template #content>
                  {{ t('app.quickJump.trustlinesAsDebtor') }}
                </template>
                <el-button
                  size="small"
                  @click="goTrustlinesDebtor"
                >
                  {{ t('trustlines.toDebtor') }}
                </el-button>
              </el-tooltip>

              <el-tooltip
                placement="bottom"
                effect="dark"
                :show-after="700"
              >
                <template #content>
                  {{ t('app.quickJump.auditLog') }}
                </template>
                <el-button
                  size="small"
                  @click="goAuditLog"
                >
                  {{ t('nav.auditLog.label') }}
                </el-button>
              </el-tooltip>
            </el-button-group>
          </div>

          <el-tooltip
            placement="bottom"
            effect="dark"
            :show-after="700"
          >
            <template #content>
              {{ t('app.runLocal.tooltip') }}
            </template>
            <el-button
              size="small"
              @click="runLocalOpen = true"
            >
              {{ t('app.runLocal.label') }}
            </el-button>
          </el-tooltip>

          <el-dialog
            v-model="runLocalOpen"
            :title="t('app.runLocal.title')"
            width="520px"
          >
            <div class="geoHint">
              {{ t('app.runLocal.hint') }}
            </div>
            <div class="runLocalCmd">
              <span class="runLocalCmd__text">{{ runLocalCommand }}</span>
              <CopyIconButton
                :text="runLocalCommand"
                :label="t('app.runLocal.label')"
              />
            </div>
            <template #footer>
              <el-button
                @click="runLocalOpen = false"
              >
                {{ t('common.close') }}
              </el-button>
            </template>
          </el-dialog>

          <el-select
            v-model="uiLocale"
            size="small"
            style="width: 140px"
          >
            <el-option
              :label="t('app.locale.en')"
              value="en"
            />
            <el-option
              :label="t('app.locale.ru')"
              value="ru"
            />
          </el-select>

          <el-select
            v-model="authStore.role"
            size="small"
            style="width: 150px"
          >
            <el-option
              :label="t('app.role.admin')"
              value="admin"
            />
            <el-option
              :label="t('app.role.operator')"
              value="operator"
            />
            <el-option
              :label="t('app.role.auditor')"
              value="auditor"
            />
          </el-select>

          <el-select
            v-if="isMockMode"
            v-model="scenario"
            size="small"
            style="width: 190px"
          >
            <el-option
              label="happy"
              value="happy"
            />
            <el-option
              label="empty"
              value="empty"
            />
            <el-option
              label="error500"
              value="error500"
            />
            <el-option
              label="admin_forbidden403"
              value="admin_forbidden403"
            />
            <el-option
              label="integrity_unauthorized401"
              value="integrity_unauthorized401"
            />
            <el-option
              label="slow"
              value="slow"
            />
          </el-select>

          <el-switch
            v-model="dark"
            size="small"
            :active-text="t('app.theme.dark')"
            :inactive-text="t('app.theme.light')"
          />
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

.runLocalCmd {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.02);
}

.runLocalCmd__text {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
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

.quick {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.main {
  padding: 16px;
}
</style>
