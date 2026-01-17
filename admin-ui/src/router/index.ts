import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../pages/DashboardPage.vue'),
    meta: { titleKey: 'dashboard.title' },
  },
  {
    path: '/liquidity',
    name: 'Liquidity',
    component: () => import('../pages/LiquidityPage.vue'),
    meta: { titleKey: 'liquidity.title' },
  },
  {
    path: '/integrity',
    name: 'Integrity',
    component: () => import('../pages/IntegrityPage.vue'),
    meta: { titleKey: 'integrity.title' },
  },
  {
    path: '/incidents',
    name: 'Incidents',
    component: () => import('../pages/IncidentsPage.vue'),
    meta: { titleKey: 'incidents.title' },
  },
  {
    path: '/trustlines',
    name: 'Trustlines',
    component: () => import('../pages/TrustlinesPage.vue'),
    meta: { titleKey: 'trustlines.title' },
  },
  {
    path: '/participants',
    name: 'Participants',
    component: () => import('../pages/ParticipantsPage.vue'),
    meta: { titleKey: 'participant.title' },
  },
  {
    path: '/config',
    name: 'Config',
    component: () => import('../pages/ConfigPage.vue'),
    meta: { titleKey: 'config.title' },
  },
  {
    path: '/feature-flags',
    name: 'Feature Flags',
    component: () => import('../pages/FeatureFlagsPage.vue'),
    meta: { titleKey: 'featureFlags.title' },
  },
  {
    path: '/audit-log',
    name: 'Audit Log',
    component: () => import('../pages/AuditLogPage.vue'),
    meta: { titleKey: 'auditLog.title' },
  },
  {
    path: '/equivalents',
    name: 'Equivalents',
    component: () => import('../pages/EquivalentsPage.vue'),
    meta: { titleKey: 'equivalents.title' },
  },
  {
    path: '/graph',
    name: 'Graph',
    component: () => import('../pages/GraphPage.vue'),
    meta: { titleKey: 'graph.title' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
