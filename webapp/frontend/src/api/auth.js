import request from './request'

export const login = (data) => request.post('/login', data)
export const register = (data) => request.post('/register', data)
export const sendCode = (data) => request.post('/send-register-code', data)
export const resetPassword = (data) => request.post('/reset-password', data)
export const me = () => request.get('/me')
export const logout = () => request.post('/logout')
