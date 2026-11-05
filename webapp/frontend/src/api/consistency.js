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
