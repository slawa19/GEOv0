import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../pages/DashboardPage.vue'),
    meta: { titleKey: 'dashboard.title' },
  },
  {
    path: '/integrity',
    name: 'Integrity',
    component: () => import('../pages/IntegrityPage.vue'),
    meta: { titleKey: 'integrity.title' },
  },
  {
    path: '/liquidity',
    // The Liquidity screen is gone (032 S5, F-2): the per-equivalent sums moved to the Dashboard.
    redirect: '/dashboard',
  },
  {
    path: '/incidents',
    // The incidents screen is gone (032 S5, F-4): the equivalents on an integrity hold are shown and cleared on
    // the Integrity screen, so an old link lands there.
    redirect: '/integrity',
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
    // The flags are config keys; the separate page and its `/admin/feature-flags` client are gone (032 S4).
    redirect: '/config',
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
  {
    // Last, so every real path above wins. The address bar keeps what the operator typed.
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('../pages/NotFoundPage.vue'),
    meta: { titleKey: 'notFound.title' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
