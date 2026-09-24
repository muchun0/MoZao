import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import Login from '../views/Login.vue'

const routes = [
  { path: '/login', name: 'Login', component: Login },
  { path: '/register', redirect: '/login?tab=register' },
  { path: '/forgot', redirect: '/login?tab=forgot' },
  {
    path: '/',
    component: () => import('../layouts/MainLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', name: 'ProjectList', component: () => import('../views/ProjectList.vue') },
      { path: 'project/:id', name: 'ProjectDetail', component: () => import('../views/ProjectDetail.vue'), props: true },
      { path: 'teardowns', name: 'TeardownLibrary', component: () => import('../views/TeardownLibrary.vue') },
      { path: 'admin', name: 'Admin', component: () => import('../views/Admin.vue'), meta: { requiresAdmin: true } },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isLoggedIn) return '/login'
  if (to.meta.requiresAdmin && !auth.isAdmin) return '/'
})

export default router
