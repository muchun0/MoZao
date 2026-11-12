// REQ-R23 P0-4：生成发起统一封装（纯逻辑层，不直接依赖 axios 客户端）
// 所有「就近生成入口」（章节行重生成、顶部继续生成、大纲重生成、重生成本卷、重生成世界观）
// 与 Agent 面板共用同一口径：先取预估 → 校验余额 → 走 /agent-runs 单轨。
// 前端实际调用入口见 utils/agentRunClient.js（注入真实 API），此处注入 api 便于单测。

export const INSUFFICIENT_BALANCE_TEXT = '余额不足，请先充值后再发起生成'

// /me 返回 { user: { balance } }；兼容扁平结构，非数字一律按 0 处理
export function balanceOf(me) {
  const raw = me?.user?.balance ?? me?.balance ?? 0
  const n = Number(raw)
  return Number.isFinite(n) ? n : 0
}

// 启动成功后的统一计费文案（平台计费才提示金额，自带 Key 不提示）
export function chargeText(body) {
  const price = Number(body?.estimated_price || 0)
  if (body?.billing_mode === 'platform' && price > 0) {
    return `预估扣费 ¥${price.toFixed(2)}（按实际生成字数结算）`
  }
  return ''
}

// 余额不足：带上 detail，调用方与后端错误用同一套取值逻辑
export class BalanceError extends Error {
  constructor(message = INSUFFICIENT_BALANCE_TEXT) {
    super(message)
    this.name = 'BalanceError'
    this.code = 'insufficient_balance'
    this.detail = message
  }
}

// 统一错误文案取值：后端 detail 可能是字符串或 {message}，本地错误带 detail
export function errorDetail(e, fallback = '启动失败') {
  const d = e?.response?.data?.detail
  if (typeof d === 'string' && d) return d
  if (d && typeof d.message === 'string' && d.message) return d.message
  return e?.detail || e?.message || fallback
}

// 发起前预检：预估失败不阻断启动（后端仍会做最终校验），余额读取失败同样不阻断
export async function preflight(pid, payload, api) {
  let estimate = null
  try {
    estimate = await api.estimateAgentRun(pid, payload)
  } catch {
    estimate = null
  }
  if (estimate && estimate.billing_mode === 'platform') {
    const price = Number(estimate.estimated_price || 0)
    if (price > 0) {
      let me = null
      try {
        me = await api.getBalance()
      } catch {
        me = null
      }
      if (me && balanceOf(me) < price) throw new BalanceError()
    }
  }
  return estimate
}

export async function runAgentWith(api, pid, payload) {
  const estimate = await preflight(pid, payload, api)
  const body = await api.startAgentRun(pid, payload)
  return { body, estimate }
}
