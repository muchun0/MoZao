import request from './request'

export const getChapters = (pid) => request.get(`/projects/${pid}/chapters`)
export const getChapter = (pid, no) => request.get(`/projects/${pid}/chapters/${no}`)
export const updateChapterContent = (pid, no, data) => request.put(`/projects/${pid}/chapters/${no}`, data)
// REQ-章节版本化：章节版本列表 / 选择版本 / 删除版本
export const getChapterVersions = (pid, no) => request.get(`/projects/${pid}/chapters/${no}/versions`)
export const selectChapterVersion = (pid, no, v) => request.post(`/projects/${pid}/chapters/${no}/versions/${v}/select`)
export const deleteChapterVersion = (pid, no, v) => request.delete(`/projects/${pid}/chapters/${no}/versions/${v}`)
export const getOutline = (pid) => request.get(`/projects/${pid}/outline`)
export const saveOutline = (pid, data) => request.put(`/projects/${pid}/outline`, data)
export const getWorld = (pid) => request.get(`/projects/${pid}/world`)
export const saveWorld = (pid, data) => request.put(`/projects/${pid}/world`, data)
export const getLock = (pid) => request.get(`/projects/${pid}/lock`)
export const setLock = (pid, data) => request.put(`/projects/${pid}/lock`, data)
export const getOutlineCandidates = (pid) => request.get(`/projects/${pid}/outline/candidates`)
export const applyOutlineCandidate = (pid, index) => request.post(`/projects/${pid}/outline/candidates/${index}/apply`)
export const appealChapterIssue = (pid, no, data) => request.post(`/projects/${pid}/chapters/${no}/appeal`, data)
export const getAppeals = (pid) => request.get(`/projects/${pid}/appeals`)
export const overturnAppeal = (pid, appealId) => request.post(`/projects/${pid}/appeals/${appealId}/overturn`)
export const importOutline = (pid, data) => request.post(`/projects/${pid}/outline/import`, data)
export const expandSelection = (pid, data) => request.post(`/projects/${pid}/expand-selection`, data)

// 产物级版本库（阶段 1C，TECH §10.1）：outline/world 版本列表 / 选择版本 / 切换影响范围
export const getProductVersions = (pid, kind) => request.get(`/projects/${pid}/products/${kind}/versions`)
export const selectProductVersion = (pid, kind, v) => request.post(`/projects/${pid}/products/${kind}/versions/${v}/select`)
// 切换影响面：选择某版后哪些章会与上游不一致（只读评估，不写盘）
export const getVersionSelectImpact = (pid, kind, v) => request.get(`/projects/${pid}/products/${kind}/select-impact`, { params: { v } })
// 两版只读评审（REQ-版本对比与推荐）：chapter 需 chapter_no
// 返回审校/评审两视角的评论（引用原文 + 好在哪 / 问题定性 + 具体改法），
// 不再是 git 式的行级增删
export const getVersionReview = (pid, kind, v1, v2, chapterNo) =>
  request.get(`/projects/${pid}/products/${kind}/versions/review`, { params: { v1, v2, chapter_no: chapterNo || undefined } })

// Agent 人设提示词（阶段 1C）：当前人设列表（懒快照 v1）/ 保存为新版本
export const getAgentPrompts = (pid) => request.get(`/projects/${pid}/agent-prompts`)
export const saveAgentPrompt = (pid, agent, text) => request.put(`/projects/${pid}/agent-prompts/${agent}`, { text })
