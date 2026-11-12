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

// REQ-R22 M2：状态图谱四视图（一次取 版本 + 来源 + canon/characters/timeline/threads）
export const getFactsGraph = (pid, params = {}) =>
  request.get(`/projects/${pid}/book-facts/graph`, { params })
