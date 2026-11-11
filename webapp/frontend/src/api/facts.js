import request from './request'

// REQ-R21 M1：事实账本只读查询（timeline_view 开关未开启时后端统一 404）
export const getFactsEvents = (pid, params = {}) =>
  request.get(`/projects/${pid}/book-facts/events`, { params })

export const getFactsThreads = (pid, params = {}) =>
  request.get(`/projects/${pid}/book-facts/threads`, { params })

export const getFactsTimeline = (pid, params = {}) =>
  request.get(`/projects/${pid}/book-facts/timeline`, { params })

export const getFactsViolations = (pid, params = {}) =>
  request.get(`/projects/${pid}/book-facts/violations`, { params })
