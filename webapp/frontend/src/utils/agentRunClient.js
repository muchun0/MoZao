// REQ-R23 P0-4：生成发起的前端实际入口（注入真实 API）
// 组件统一从这里取 runAgent，避免各处自己拼 startAgentRun / 预估 / 余额校验。
import { startAgentRun, estimateAgentRun, getBalance } from '../api/project'
import { runAgentWith, errorDetail, chargeText, BalanceError, INSUFFICIENT_BALANCE_TEXT } from './agentRun'

const defaultApi = { startAgentRun, estimateAgentRun, getBalance }

export function runAgent(pid, payload) {
  return runAgentWith(defaultApi, pid, payload)
}

export { errorDetail, chargeText, BalanceError, INSUFFICIENT_BALANCE_TEXT }
