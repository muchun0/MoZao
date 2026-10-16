import request from './request'

export const getChapters = (pid) => request.get(`/projects/${pid}/chapters`)
export const getChapter = (pid, no) => request.get(`/projects/${pid}/chapters/${no}`)
export const updateChapterContent = (pid, no, data) => request.put(`/projects/${pid}/chapters/${no}`, data)
export const regenerateChapter = (pid, no) => request.post(`/projects/${pid}/chapters/${no}/regenerate`)
// 单章重写最多 3 轮质检重写（P0-3），耗时可达数分钟；全局 axios 超时 120s 不够，
// 单独放大到 10 分钟，避免"后端已成功落盘、前端却超时报重生成失败"
export const rewriteChapter = (pid, no) => request.post(`/projects/${pid}/chapters/${no}/rewrite`, null, { timeout: 600000 })
// REQ-章节版本化：章节版本列表 / 选择回退
export const getChapterVersions = (pid, no) => request.get(`/projects/${pid}/chapters/${no}/versions`)
export const selectChapterVersion = (pid, no, v) => request.post(`/projects/${pid}/chapters/${no}/versions/${v}/select`)
export const batchGenerate = (pid, data) => request.post(`/projects/${pid}/chapters/batch`, data)
export const getOutline = (pid) => request.get(`/projects/${pid}/outline`)
export const saveOutline = (pid, data) => request.put(`/projects/${pid}/outline`, data)
export const getWorld = (pid) => request.get(`/projects/${pid}/world`)
export const saveWorld = (pid, data) => request.put(`/projects/${pid}/world`, data)
export const getLock = (pid) => request.get(`/projects/${pid}/lock`)
export const setLock = (pid, data) => request.put(`/projects/${pid}/lock`, data)
export const regenerateVolume = (pid, volNo) => request.post(`/projects/${pid}/volumes/${volNo}/regenerate`)
export const genOutline = (pid) => request.post(`/projects/${pid}/generate-outline`)
export const regenOutline = (pid) => request.post(`/projects/${pid}/outline/regenerate`)
export const regenWorld = (pid) => request.post(`/projects/${pid}/world/regenerate`)
export const importOutline = (pid, data) => request.post(`/projects/${pid}/outline/import`, data)
export const expandSelection = (pid, data) => request.post(`/projects/${pid}/expand-selection`, data)
