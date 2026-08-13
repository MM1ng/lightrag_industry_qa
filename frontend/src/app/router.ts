import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from './stores/session'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/chat', component: () => import('../views/ChatView.vue') },
    { path: '/graph', component: () => import('../views/GraphView.vue') },
    { path: '/admin/login', component: () => import('../views/admin/AdminGateView.vue') },
    { path: '/admin/knowledge-bases', component: () => import('../views/admin/KnowledgeBasesView.vue'), meta: { requiresAdmin: true } },
    { path: '/admin/documents', component: () => import('../views/admin/DocumentsView.vue'), meta: { requiresAdmin: true } },
    { path: '/admin/jobs', component: () => import('../views/admin/JobsView.vue'), meta: { requiresAdmin: true } },
    { path: '/admin/generations', component: () => import('../views/admin/GenerationsView.vue'), meta: { requiresAdmin: true } },
  ],
})

router.beforeEach((to) => {
  const session = useSessionStore()
  if (to.meta.requiresAdmin && !session.hasAdminAccess) {
    return { path: '/admin/login', query: { redirect: to.fullPath } }
  }
})

export default router
