// 全书体检前端展示口径测试（纯函数，无需 DOM）
// 运行：node --test tests/frontend_consistency.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  SEVERITY_ORDER, FORESHADOW_STATES, ENTITY_KIND_OPTIONS,
  severityMeta, sortBySeverity, countSeverity, chapterJumpLabel,
  projectionViewLabel, coverageStateLabel, foreshadowStateLabel,
  foreshadowProblem, foreshadowWhere, foreshadowAdvice,
  humanizeGap, entitySummary, severityGroup,
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

// 作者视角文案：不能把内部视图代号 / 英文状态键直接暴露给用户
test('账本视图与状态都有人话名称，未知值有兜底', () => {
  assert.equal(projectionViewLabel('chapter_state'), '全书进度记录')
  assert.equal(projectionViewLabel('unknown_view'), '这项记录')
  assert.ok(!/[a-z_]{4,}/.test(projectionViewLabel('book_memory')))
  assert.equal(coverageStateLabel('lagging'), '落后了')
  assert.equal(coverageStateLabel('odd'), '还没记录')
  assert.equal(foreshadowStateLabel('overdue'), '早该收了')
  assert.equal(foreshadowStateLabel('stalled'), '很久没推进')
})

test('伏笔条目具备「问题 / 位置 / 建议」三要素', () => {
  const item = { name: '青铜钥匙', state: 'overdue', buried_ch: 2, reveal_ch: 5, severity: 'high' }
  assert.ok(foreshadowProblem(item).includes('青铜钥匙'))
  assert.ok(foreshadowWhere(item).includes('埋在第 2 章'))
  assert.ok(foreshadowAdvice(item).length > 10)
})

test('数据缺口与大模型口径改写成作者能懂的句子，不回显内部词', () => {
  const gap = humanizeGap('未覆盖语义级跨章冲突全量检测：一致性结论来自已有全书校验与章节质检记录。')
  assert.ok(gap.includes('通读检查'))
  assert.ok(!gap.includes('语义级'))
  assert.equal(humanizeGap('未知缺口'), '有一小部分数据暂时没取到，结果可能不完整。')
})

test('人物索引摘要：零命中说明「还没用上」，有命中给次数与区间', () => {
  assert.ok(entitySummary({ chapters: [] }).includes('还没用上'))
  const row = { chapters: [{ no: 1 }, { no: 3 }], first_ch: 1, last_ch: 3, total_mentions: 7 }
  assert.ok(entitySummary(row).includes('7 次'))
  assert.ok(entitySummary(row).includes('第 1–3 章'))
})

test('严重度分组：高→必须先处理，中→建议处理，其余→了解一下', () => {
  assert.equal(severityGroup('high'), 'must')
  assert.equal(severityGroup('medium'), 'suggest')
  assert.equal(severityGroup('info'), 'later')
  assert.equal(severityGroup('unknown'), 'later')
})
