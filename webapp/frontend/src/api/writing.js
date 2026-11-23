import request from './request'

// R29 写作侧栏：当前章的只读聚合（章计划 / 出场人物 / 本章伏笔 / 五态计数）
// 只读接口，不写盘、不计费、不调用模型
export const getWritingContext = (pid, ch) =>
  request.get(`/projects/${pid}/writing-context`, { params: { ch } })
