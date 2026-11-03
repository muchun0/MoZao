import request from './request'
import { newRequestId } from '../utils/requestId'

// 小墨AI 会话（项目页写作对话）
export const listConversations = (pid) => request.get(`/projects/${pid}/assistant/conversations`)
export const createConversation = (pid, data) => request.post(`/projects/${pid}/assistant/conversations`, data)
export const getConversation = (pid, cid, params) =>
  request.get(`/projects/${pid}/assistant/conversations/${cid}`, { params })
// D1=1A：幂等键统一在本层兜底生成，调用方漏传也不会丢 header（重复提交不重复扣费）
export const sendMessage = (pid, cid, data, requestId = newRequestId()) =>
  request.post(`/projects/${pid}/assistant/conversations/${cid}/messages`, data, {
    headers: { 'Idempotency-Key': requestId },
  })
export const deleteConversation = (pid, cid) =>
  request.delete(`/projects/${pid}/assistant/conversations/${cid}`)
