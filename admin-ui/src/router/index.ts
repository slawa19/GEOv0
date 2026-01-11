import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../pages/DashboardPage.vue'),
    meta: { title: 'Dashboard' },
  },
  {
    path: '/integrity',
    name: 'Integrity',
    component: () => import('../pages/IntegrityPage.vue'),
    meta: { title: 'Integrity' },
  },
  {
    path: '/incidents',
    name: 'Incidents',
    component: () => import('../pages/IncidentsPage.vue'),
    meta: { title: 'Incidents' },
  },
  {
    path: '/trustlines',
    name: 'Trustlines',
    component: () => import('../pages/TrustlinesPage.vue'),
    meta: { title: 'Trustlines' },
  },
  {
    path: '/participants',
    name: 'Participants',
    component: () => import('../pages/ParticipantsPage.vue'),
    meta: { title: 'Participants' },
  },
  {
    path: '/config',
    name: 'Config',
    component: () => import('../pages/ConfigPage.vue'),
    meta: { title: 'Config' },
  },
  {
    path: '/feature-flags',
    name: 'Feature Flags',
    component: () => import('../pages/FeatureFlagsPage.vue'),
    meta: { title: 'Feature Flags' },
  },
  {
    path: '/audit-log',
    name: 'Audit Log',
    component: () => import('../pages/AuditLogPage.vue'),
    meta: { title: 'Audit Log' },
  },
  {
    path: '/equivalents',
    name: 'Equivalents',
    component: () => import('../pages/EquivalentsPage.vue'),
    meta: { title: 'Equivalents' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
