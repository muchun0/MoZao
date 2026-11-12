import request from './request'

// REQ-R22 M2：账本只读视图（版本 + 来源 + canon/characters/timeline/threads）
// view='all' 或单视图（canon/characters/timeline/threads）；开关未开启时后端统一 404
export const getFactsGraph = (pid, params = {}) =>
  request.get(`/projects/${pid}/book-facts/graph`, { params })
