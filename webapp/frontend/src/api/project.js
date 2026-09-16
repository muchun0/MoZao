import request from './request'

export const listProjects = (params) => request.get('/projects', { params })
export const createProject = (data) => request.post('/projects', data)
export const getProject = (id) => request.get(`/projects/${id}`)
export const updateProject = (id, data) => request.put(`/projects/${id}`, data)
export const deleteProject = (id) => request.delete(`/projects/${id}`)
export const archiveProject = (id) => request.post(`/projects/${id}/archive`)
export const cloneProject = (id) => request.post(`/projects/${id}/clone`)
export const startGenerate = (id, data) => request.post(`/projects/${id}/generate`, data)
export const cancelGenerate = (id) => request.post(`/projects/${id}/cancel`)
export const getStatus = (id) => request.get(`/projects/${id}/status`)
export const getBalance = () => request.get('/user/balance')
