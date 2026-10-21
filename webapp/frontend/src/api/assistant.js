import request from './request'

// 小墨AI 会话（项目页写作对话）
export const listConversations = (pid) => request.get(`/projects/${pid}/assistant/conversations`)
export const createConversation = (pid, data) => request.post(`/projects/${pid}/assistant/conversations`, data)
export const getConversation = (pid, cid, params) =>
  request.get(`/projects/${pid}/assistant/conversations/${cid}`, { params })
export const sendMessage = (pid, cid, data) =>
  request.post(`/projects/${pid}/assistant/conversations/${cid}/messages`, data)
export const deleteConversation = (pid, cid) =>
  request.delete(`/projects/${pid}/assistant/conversations/${cid}`)
