import request from './request'

export const login = (data) => request.post('/auth/login', data)
export const register = (data) => request.post('/auth/register', data)
export const sendCode = (data) => request.post('/auth/send-code', data)
export const resetPassword = (data) => request.post('/auth/reset-password', data)
export const me = () => request.get('/auth/me')
export const logout = () => request.post('/auth/logout')
