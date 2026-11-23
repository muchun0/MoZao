// R27 分步确认式生成流：阶段状态 / 确认卡 / 模式切换（前端只渲染，不持有权威状态）
import request from './request'

export const getGenerationFlow = (id, withCard = 1) =>
  request.get(`/projects/${id}/generation/flow`, { params: { with_card: withCard } })

export const startGenerationFlow = (id, mode) =>
  request.post(`/projects/${id}/generation/flow/start`, { mode })

export const confirmGenerationStage = (id, data) =>
  request.post(`/projects/${id}/generation/flow/confirm`, data)

export const setGenerationMode = (id, mode) =>
  request.post(`/projects/${id}/generation/flow/mode`, { mode })

export const abortGenerationFlow = (id) =>
  request.post(`/projects/${id}/generation/flow/abort`)
