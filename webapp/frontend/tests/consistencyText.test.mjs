// R11 前端口径单测（node --test）：体检与工具的数字来源不同，拆分必须能自证。
import test from 'node:test'
import assert from 'node:assert/strict'

import { conflictSourceText, conflictHeadline } from '../src/utils/consistency.js'

test('口径文案把三类数字摊开，作者能相加核对', () => {
  const text = conflictSourceText({
    verify: { records: 21, included: 21, skipped: 0, fallback: 2 },
    chapter_quality: { candidates: 17, included: 17 },
    merged: { total: 38, deduped: 0 },
  })
  assert.equal(text, '全书校验 21 条 · 单章质检 17 条 · 合并后 38 条')
})

test('有去重时明确说明，不把差额藏起来', () => {
  const text = conflictSourceText({
    verify: { included: 8, skipped: 0 },
    chapter_quality: { included: 4 },
    merged: { total: 10, deduped: 2 },
  })
  assert.equal(text, '全书校验 8 条 · 单章质检 4 条 · 合并去重后 10 条')
})

test('无法定位章号的记录单独说明', () => {
  const text = conflictSourceText({
    verify: { included: 3, skipped: 1 },
    chapter_quality: { included: 0 },
    merged: { total: 3, deduped: 0 },
  })
  assert.match(text, /另有 1 条无法定位章号/)
})

test('缺 sources 时不抛错，按 0 兜底', () => {
  assert.equal(conflictSourceText(), '全书校验 0 条 · 单章质检 0 条 · 合并后 0 条')
})

test('文风提醒不说成阅读/节奏建议', () => {
  assert.equal(conflictHeadline({ category: 'advisory', category_label: '文风/表达提醒' }),
    '这是一条文风提醒，不是设定矛盾')
  assert.equal(conflictHeadline({ category: 'advisory', category_label: '阅读/节奏提醒' }),
    '这是一条阅读/节奏建议')
})
