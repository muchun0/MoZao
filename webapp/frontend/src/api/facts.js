import request from './request'

// REQ-R22 M2：账本只读视图（版本 + 来源 + canon/characters/timeline/threads）
// view='all' 或单视图（canon/characters/timeline/threads）；开关未开启时后端统一 404
export const getFactsGraph = (pid, params = {}) =>
  request.get(`/projects/${pid}/book-facts/graph`, { params })

// REQ-R22 L3：作者计划事件（时间线可编辑卡片），全量覆盖保存
export const saveFactsPlannedEvents = (pid, items = []) =>
  request.put(`/projects/${pid}/book-facts/planned-events`, { items })
