<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useHealthStore } from '../stores/health'
import { useAuthStore } from '../stores/auth'
import { useConfigStore } from '../stores/config'

const route = useRoute()
const router = useRouter()

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

const THEME_KEY = 'admin-ui.theme'
const dark = ref(true)

onMounted(() => {
  const saved = (localStorage.getItem(THEME_KEY) || '').toLowerCase()
  // Default dark unless user has explicitly chosen a theme.
  dark.value = saved ? saved === 'dark' : true
})

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
    <el-aside width="240px" class="aside">
      <div class="brand" @click="navigate('/dashboard')">
        <div class="brand__title">GEO Hub</div>
        <div class="brand__subtitle">Admin Console (prototype)</div>
      </div>

      <el-menu :default-active="activePath" router class="menu">
        <el-menu-item index="/dashboard" @click="navigate('/dashboard')">Dashboard</el-menu-item>
        <el-menu-item index="/integrity" @click="navigate('/integrity')">Integrity</el-menu-item>
        <el-menu-item index="/incidents" @click="navigate('/incidents')">Incidents</el-menu-item>
        <el-menu-item index="/trustlines" @click="navigate('/trustlines')">Trustlines</el-menu-item>
        <el-menu-item index="/participants" @click="navigate('/participants')">Participants</el-menu-item>
        <el-menu-item index="/config" @click="navigate('/config')">Config</el-menu-item>
        <el-menu-item index="/feature-flags" @click="navigate('/feature-flags')">Feature Flags</el-menu-item>
        <el-menu-item index="/audit-log" @click="navigate('/audit-log')">Audit Log</el-menu-item>
        <el-menu-item index="/equivalents" @click="navigate('/equivalents')">Equivalents</el-menu-item>
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
            <el-tag type="info">scenario: {{ scenario }}</el-tag>
          </div>
        </div>

        <div class="header__right">
          <el-select v-model="authStore.role" size="small" style="width: 150px">
            <el-option label="admin" value="admin" />
            <el-option label="operator" value="operator" />
            <el-option label="auditor (read-only)" value="auditor" />
          </el-select>

          <el-select v-model="scenario" size="small" style="width: 190px">
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
