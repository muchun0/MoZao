// R13 派生记录标脏的前端口径测试（纯函数，无需 DOM）
// 运行：node --test tests/frontend_staleness.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'

import { staleNoticeOf, isItemStale, staleChaptersOf } from '../webapp/frontend/src/utils/staleness.js'

test('未过期或接口缺字段时不产出提示', () => {
  assert.equal(staleNoticeOf(null), null)
  assert.equal(staleNoticeOf({}), null)
  assert.equal(staleNoticeOf({ staleness: { has_stale: false, notice: null } }), null)
})

test('体检页与生成过程卡取到的是同一个提示对象（同一口径）', () => {
  const notice = { level: 'warning', title: '记录可能过期', text: '第 3 章…', advice: '重新生成…' }
  const overview = { staleness: { has_stale: true, notice } }
  const cot = { staleness: { has_stale: true, notice } }
  assert.deepEqual(staleNoticeOf(overview), notice)
  assert.deepEqual(staleNoticeOf(cot), notice)
})

test('条目标脏兼容接口 snake_case 与面板驼峰', () => {
  assert.equal(isItemStale({ record_stale: true }), true)
  assert.equal(isItemStale({ recordStale: true }), true)
  assert.equal(isItemStale({ record_stale: false }), false)
  assert.equal(isItemStale(null), false)
})

test('覆盖视图过期章号保持后端顺序并过滤非法值', () => {
  assert.deepEqual(staleChaptersOf({ stale_chapters: [3, 5] }), [3, 5])
  assert.deepEqual(staleChaptersOf({ staleChapters: [1] }), [1])
  assert.deepEqual(staleChaptersOf({ stale_chapters: [0, -2, 4] }), [4])
  assert.deepEqual(staleChaptersOf(null), [])
})
