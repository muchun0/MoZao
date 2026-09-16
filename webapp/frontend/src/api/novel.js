import request from './request'

export const getChapters = (pid) => request.get(`/projects/${pid}/chapters`)
export const getChapter = (pid, no) => request.get(`/projects/${pid}/chapters/${no}`)
export const regenerateChapter = (pid, no) => request.post(`/projects/${pid}/chapters/${no}/regenerate`)
export const batchGenerate = (pid, data) => request.post(`/projects/${pid}/chapters/batch`, data)
export const getOutline = (pid) => request.get(`/projects/${pid}/outline`)
export const saveOutline = (pid, data) => request.put(`/projects/${pid}/outline`, data)
export const getWorld = (pid) => request.get(`/projects/${pid}/world`)
