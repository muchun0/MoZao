import request from './request'

export const listProjects = (params) => request.get('/projects', { params })
export const createProject = (data) => request.post('/projects', data)
export const getProject = (id) => request.get(`/projects/${id}`)
export const updateProject = (id, data) => request.put(`/projects/${id}`, data)
export const deleteProject = (id) => request.delete(`/projects/${id}`)
export const archiveProject = (id) => request.post(`/projects/${id}/archive`)
export const cloneProject = (id) => request.post(`/projects/${id}/clone`)
export const cancelGenerate = (id) => request.post(`/projects/${id}/stop`)
export const getStatus = (id) => request.get(`/projects/${id}`)
export const getBalance = () => request.get('/me')
export const getMyLlm = () => request.get('/me/llm')
export const saveMyLlm = (data) => request.put('/me/llm', data)
export const downloadProject = (id, format) => request.get(`/projects/${id}/download`, {
  params: { format },
  responseType: 'blob',
})

// Agent 统一触发入口（TECH-Agent用户化与版本化 §4，阶段 1B/1C）
// startAgentRun 返回 202 + { task_id, billing_mode, estimated_price }；轮询 getAgentRun 拿四态
export const startAgentRun = (id, data) => request.post(`/projects/${id}/agent-runs`, data)
export const getAgentRun = (id, taskId) => request.get(`/projects/${id}/agent-runs/${taskId}`)
export const stopAgentRun = (id, taskId) => request.post(`/projects/${id}/agent-runs/${taskId}/stop`)
