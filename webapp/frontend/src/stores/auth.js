import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.is_admin || false)

  async function login(credentials) {
    const res = await authApi.login(credentials)
    token.value = res.token
    user.value = res.user
    localStorage.setItem('token', res.token)
    localStorage.setItem('user', JSON.stringify(res.user))
  }

  // 启动时刷新用户信息，避免 localStorage 里的旧数据导致 is_admin 等字段过期
  async function fetchMe() {
    if (!token.value) return
    try {
      const res = await authApi.me()
      user.value = res.user
      localStorage.setItem('user', JSON.stringify(res.user))
    } catch {
      token.value = ''
      user.value = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    }
  }

  async function logout() {
    try { await authApi.logout() } catch {}
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  return { token, user, isLoggedIn, isAdmin, login, logout, fetchMe }
})
