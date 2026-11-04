// 全书体检前端展示口径测试（纯函数，无需 DOM）
// 运行：node --test tests/frontend_consistency.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  SEVERITY_ORDER, FORESHADOW_STATES, ENTITY_KIND_OPTIONS,
  severityMeta, sortBySeverity, countSeverity, chapterJumpLabel,
} from '../webapp/frontend/src/utils/consistency.js'

test('严重度排序：高 → 中 → 低 → 提示，未知值按提示处理', () => {
  const items = [
    { name: 'a', severity: 'low' },
    { name: 'b', severity: 'high' },
    { name: 'c', severity: 'unknown' },
    { name: 'd', severity: 'medium' },
  ]
  assert.deepEqual(sortBySeverity(items).map(i => i.name), ['b', 'd', 'a', 'c'])
})

test('排序不修改原数组（避免面板状态被就地改写）', () => {
  const items = [{ severity: 'low' }, { severity: 'high' }]
  sortBySeverity(items)
  assert.equal(items[0].severity, 'low')
})

test('严重度计数：未知值计入提示', () => {
  assert.deepEqual(countSeverity([
    { severity: 'high' }, { severity: 'high' }, { severity: 'medium' }, { severity: 'x' },
  ]), { high: 2, medium: 1, low: 0, info: 1 })
})

test('展示口径：严重度标签与状态选项齐全', () => {
  assert.equal(severityMeta('high').type, 'danger')
  assert.equal(severityMeta(undefined).label, '提示')
  assert.deepEqual(Object.keys(SEVERITY_ORDER), ['high', 'medium', 'low', 'info'])
  assert.equal(FORESHADOW_STATES.length, 5)
  assert.ok(ENTITY_KIND_OPTIONS.some(k => k.value === 'character'))
})

test('章节跳转文案：未记录章节不显示伪章号', () => {
  assert.equal(chapterJumpLabel(3), '第 3 章')
  assert.equal(chapterJumpLabel(0), '未记录')
})
