// REQ-R10 A：伏笔进度卡片展示口径测试（纯函数，无需 DOM）
// 运行：node --test tests/frontend_foreshadow_progress.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  normalizeForeshadowSummary, buildForeshadowFilters, filterForeshadowItems,
  foreshadowStateTag, foreshadowProgressText, unstructuredNote,
} from '../webapp/frontend/src/utils/foreshadow.js'

test('summary 归一：缺字段补零，不因 undefined 白屏', () => {
  const s = normalizeForeshadowSummary({ total: 3, revealed: 1 })
  assert.equal(s.total, 3)
  assert.equal(s.revealed, 1)
  assert.equal(s.overdue, 0)
  assert.equal(s.baseline_ch, 0)
})

test('筛选项顺序：问题态优先，全部在最前', () => {
  const f = buildForeshadowFilters({ tracked: 5, overdue: 2, due_soon: 1, revealed: 1 })
  assert.deepEqual(f.map(x => x.key),
    ['all', 'overdue', 'due_soon', 'stalled', 'planted', 'revealed', 'not_buried'])
  assert.equal(f[0].count, 5)
  assert.equal(f[1].count, 2)
  assert.equal(f[3].count, 0)
})

test('筛选口径：all 全量、按 state 精确匹配、空值兜底', () => {
  const items = [{ state: 'overdue' }, { state: 'revealed' }, { state: 'planted' }]
  assert.equal(filterForeshadowItems(items, 'all').length, 3)
  assert.deepEqual(filterForeshadowItems(items, 'revealed').map(i => i.state), ['revealed'])
  assert.deepEqual(filterForeshadowItems(null, 'all'), [])
})

test('状态配色：未知状态回落 info', () => {
  assert.equal(foreshadowStateTag('overdue'), 'danger')
  assert.equal(foreshadowStateTag('revealed'), 'success')
  assert.equal(foreshadowStateTag('whatever'), 'info')
})

test('进度文案：优先用后端 distance_label（单一口径）', () => {
  assert.equal(foreshadowProgressText({ state: 'overdue', distance_label: '已逾期 2 章' }),
    '已逾期 2 章')
  assert.equal(foreshadowProgressText({ state: 'revealed', revealed_ch: 8 }),
    '第 8 章已收')
  assert.equal(foreshadowProgressText({ state: 'due_soon', reveal_ch: 10, distance: 3 }),
    '距到期还有 3 章')
  assert.equal(foreshadowProgressText({ state: 'not_buried', reveal_ch: 12, distance: 2 }),
    '距到期还有 2 章')
})

test('老格式兜底：如实提示缺章号，不猜', () => {
  assert.equal(unstructuredNote({}), '未登记章号，补全后可纳入跟踪')
  assert.equal(unstructuredNote({ note: '缺埋设章' }), '缺埋设章')
})
