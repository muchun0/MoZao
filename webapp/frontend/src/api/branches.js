import request from './request'

// R26 剧情走向：生成（开关开启时计费）/ 采纳为便签（不计费）/ 便签读取与删除
export const generateDirections = (pid, no, data) =>
  request.post(`/projects/${pid}/chapters/${no}/branches/generate`, data)
export const adoptDirection = (pid, no, data) =>
  request.post(`/projects/${pid}/chapters/${no}/branches/adopt`, data)
export const getBranchNotes = (pid, no) =>
  request.get(`/projects/${pid}/chapters/${no}/branches/notes`)
export const deleteBranchNote = (pid, no, draftId) =>
  request.delete(`/projects/${pid}/chapters/${no}/branches/notes/${draftId}`)
