// REQ-R23 P0-4 生成发起统一封装单测（node --test）
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  runAgentWith, preflight, balanceOf, chargeText, errorDetail, BalanceError, INSUFFICIENT_BALANCE_TEXT,
} from '../src/utils/agentRun.js'

// 假 API：记录调用顺序，便于断言「先预估 → 再校验余额 → 最后启动」
function fakeApi({ estimate, me, startResult } = {}) {
  const calls = []
  return {
    calls,
    estimateAgentRun: async (pid, payload) => {
      calls.push(['estimate', pid, payload])
      if (estimate instanceof Error) throw estimate
      return estimate === undefined ? null : estimate
    },
    getBalance: async () => {
      calls.push(['balance'])
      if (me instanceof Error) throw me
      return me
    },
    startAgentRun: async (pid, payload) => {
      calls.push(['start', pid, payload])
      return startResult || { task_id: 't1', billing_mode: 'platform', estimated_price: 1.2 }
    },
  }
}

const PLATFORM_ESTIMATE = { billing_mode: 'platform', estimated_price: 1.2 }

test('余额充足：预估 → 余额 → 启动，顺序固定', async () => {
  const api = fakeApi({ estimate: PLATFORM_ESTIMATE, me: { user: { balance: 5 } } })
  const { body, estimate } = await runAgentWith(api, 'p1', { agent: 'writer.chapter' })
  assert.deepEqual(api.calls.map((c) => c[0]), ['estimate', 'balance', 'start'])
  assert.equal(body.task_id, 't1')
  assert.equal(estimate.estimated_price, 1.2)
})

test('余额不足：抛 BalanceError 且不启动（不依赖后端报错）', async () => {
  const api = fakeApi({ estimate: PLATFORM_ESTIMATE, me: { user: { balance: 0.5 } } })
  await assert.rejects(
    () => runAgentWith(api, 'p1', { agent: 'writer.chapter' }),
    (e) => e instanceof BalanceError && e.detail === INSUFFICIENT_BALANCE_TEXT)
  assert.deepEqual(api.calls.map((c) => c[0]), ['estimate', 'balance'])
})

test('预估失败不阻断启动（后端仍做最终校验）', async () => {
  const api = fakeApi({ estimate: new Error('network'), me: { user: { balance: 0 } } })
  await preflight('p1', { agent: 'writer.chapter' }, api)
  assert.deepEqual(api.calls.map((c) => c[0]), ['estimate'])
  const { body } = await runAgentWith(api, 'p1', { agent: 'writer.chapter' })
  assert.equal(body.task_id, 't1')
  assert.equal(api.calls.some((c) => c[0] === 'balance'), false)
})

test('余额读取失败不阻断启动（降级为后端校验）', async () => {
  const api = fakeApi({ estimate: PLATFORM_ESTIMATE, me: new Error('401') })
  const { body } = await runAgentWith(api, 'p1', { agent: 'writer.chapter' })
  assert.equal(body.task_id, 't1')
  assert.deepEqual(api.calls.map((c) => c[0]), ['estimate', 'balance', 'start'])
})

test('自带 Key（非平台计费）不查余额', async () => {
  const api = fakeApi({ estimate: { billing_mode: 'byok', estimated_price: 1.2 }, me: { user: { balance: 0 } } })
  await runAgentWith(api, 'p1', { agent: 'architect.outline' })
  assert.deepEqual(api.calls.map((c) => c[0]), ['estimate', 'start'])
})

test('余额字段兼容：扁平结构、缺失、非法值都按 0 兜底', () => {
  assert.equal(balanceOf({ user: { balance: '3.50' } }), 3.5)
  assert.equal(balanceOf({ balance: 8 }), 8)
  assert.equal(balanceOf({}), 0)
  assert.equal(balanceOf(null), 0)
  assert.equal(balanceOf({ user: { balance: 'abc' } }), 0)
})

test('计费文案：仅平台计费且有金额时提示', () => {
  assert.equal(chargeText({ billing_mode: 'platform', estimated_price: 1.25 }),
    '预估扣费 ¥1.25（按实际生成字数结算）')
  assert.equal(chargeText({ billing_mode: 'platform', estimated_price: 0 }), '')
  assert.equal(chargeText({ billing_mode: 'byok', estimated_price: 2 }), '')
  assert.equal(chargeText(null), '')
})

test('错误文案统一取值：后端字符串 / 后端对象 / 本地 BalanceError / 兜底', () => {
  assert.equal(errorDetail({ response: { data: { detail: '后端拒绝' } } }), '后端拒绝')
  assert.equal(errorDetail({ response: { data: { detail: { message: '对象消息' } } } }), '对象消息')
  assert.equal(errorDetail(new BalanceError()), INSUFFICIENT_BALANCE_TEXT)
  assert.equal(errorDetail(new Error('boom'), '启动失败'), 'boom')
  assert.equal(errorDetail({}, '启动失败'), '启动失败')
})
