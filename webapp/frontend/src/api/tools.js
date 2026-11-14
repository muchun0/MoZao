import request from './request'

export const parseContent = (data) => request.post('/parse-content', data)
export const verifyConsistency = (pid) => request.post(`/projects/${pid}/verify/consistency`)
export const verifyLogic = (pid) => request.post(`/projects/${pid}/verify/logic`)
export const verifyCompliance = (pid) => request.post(`/projects/${pid}/verify/compliance`)
export const getRhythm = (pid) => request.get(`/projects/${pid}/stats/rhythm`)
export const getReview = (pid) => request.get(`/projects/${pid}/review`)
export const genAlternatives = (pid, data) => request.post(`/projects/${pid}/generate/alternatives`, data)
// 大纲快照（REQ-R23 P1-1）：已并入 outline 版本库，写入/还原统一走 products/outline 版本接口，
// 这里不再暴露独立快照 API，避免出现第二个入口。
export const listForeshadowing = (pid) => request.get(`/projects/${pid}/foreshadowing`)
export const addForeshadowing = (pid, data) => request.post(`/projects/${pid}/foreshadowing`, data)
// 伏笔（REQ-R23 P1-3）：删改一律按名称定位（索引会随增删漂移），增改同名即更新
export const delForeshadowing = (pid, idx) => request.delete(`/projects/${pid}/foreshadowing/${idx}`)
export const delForeshadowingByName = (pid, name) =>
  request.delete(`/projects/${pid}/foreshadowing/name/${encodeURIComponent(name)}`)
export const listLocked = (pid) => request.get(`/projects/${pid}/locked-settings`)
export const addLocked = (pid, data) => request.post(`/projects/${pid}/locked-settings`, data)
export const delLocked = (pid, idx) => request.delete(`/projects/${pid}/locked-settings/${idx}`)
export const genCover = (pid) => request.post(`/projects/${pid}/cover`)
export const listMySkills = () => request.get('/my-skills')
export const listPlatformSkills = () => request.get('/skills')
export const saveMySkill = (data) => request.post('/my-skills', data)
export const delMySkill = (id) => request.delete(`/my-skills/${id}`)
export const listMarketSkills = () => request.get('/skills/market')
export const publishToMarket = (source_id) => request.post('/skills/market', { source_id })
export const installMarketSkill = (id) => request.post(`/skills/market/${id}/install`)
export const unpublishMarketSkill = (id) => request.delete(`/skills/market/${id}`)
export const listStyleRoles = (params) => request.get('/style-roles', { params })
export const createStyleRole = (data) => request.post('/style-roles', data)
export const updateStyleRole = (id, data) => request.put(`/style-roles/${id}`, data)
export const deleteStyleRole = (id) => request.delete(`/style-roles/${id}`)
export const mountStyleRoleSkill = (id, data) => request.post(`/style-roles/${id}/skills`, data)
export const unmountStyleRoleSkill = (id, skillType, skillId) => request.delete(`/style-roles/${id}/skills/${skillType}/${skillId}`)

// 三期产品化（2026-10-14）：场景体检 / 三分支续写 / 思维链可视化
export const getSceneInspect = (pid, no) => request.get(`/projects/${pid}/chapters/${no}/scene-inspect`)
export const getChainOfThought = (pid, no) => request.get(`/projects/${pid}/chapters/${no}/chain-of-thought`)
export const continueBranches = (pid, data) => request.post(`/projects/${pid}/continue-branches`, data)
export const applyBranch = (pid, data) => request.post(`/projects/${pid}/apply-branch`, data)
