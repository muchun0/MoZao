import axios from 'axios'

const request = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

request.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 登录/注册类接口自身返回 401 时不能触发全局跳转，否则登录页的错误提示会被立刻刷掉
const AUTH_EXEMPT = ['/login', '/register', '/auth/login', '/auth/register', '/auth/refresh']

request.interceptors.response.use(
  res => res.data,
  err => {
    const url = err.config?.url || ''
    const exempt = AUTH_EXEMPT.some(p => url.startsWith(p))
    if (err.response?.status === 401 && !exempt) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default request
