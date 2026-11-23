import request from './request'

// R2 全书体检：全部为只读 GET，不触发重写
export const getConsistencyOverview = (pid) =>
  request.get(`/projects/${pid}/consistency/overview`)

export const getConsistencyCoverage = (pid, upTo = 0) =>
  request.get(`/projects/${pid}/consistency/coverage`, { params: { up_to: upTo } })

export const getForeshadowLedger = (pid, params = {}) =>
  request.get(`/projects/${pid}/consistency/foreshadow`, { params })

export const getConsistencyConflicts = (pid, params = {}) =>
  request.get(`/projects/${pid}/consistency/conflicts`, { params })

export const getEntityIndex = (pid, params = {}) =>
  request.get(`/projects/${pid}/consistency/entities`, { params })

// aliases 需要以重复参数形式发送（alias=a&alias=b），axios 1.x 用 indexes:null 关闭数组括号
export const getEntityMentions = (pid, name, aliases = []) =>
  request.get(`/projects/${pid}/consistency/mentions`, {
    params: { name, alias: aliases },
    paramsSerializer: { indexes: null },
  })

// R30 人机协作：交接工单 / 账本提案（只读列表 + 人工处理）
export const getHandovers = (pid, params = {}) =>
  request.get(`/projects/${pid}/handovers`, { params })

export const resolveHandover = (pid, hid, payload) =>
  request.post(`/projects/${pid}/handovers/${hid}/resolve`, payload)

export const getProposals = (pid, params = {}) =>
  request.get(`/projects/${pid}/proposals`, { params })

export const mergeProposal = (pid, proposalId, payload = {}) =>
  request.post(`/projects/${pid}/proposals/${proposalId}/merge`, payload)

export const rejectProposal = (pid, proposalId, payload = {}) =>
  request.post(`/projects/${pid}/proposals/${proposalId}/reject`, payload)
