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
  formatChapterList, coverageGap, coverageEffect, coverageAdvice,
  conflictCategory, conflictHeadline, conflictBasis, conflictSides,
  conflictReason, conflictScope, conflictAdvice, conflictCheckType,
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

// 覆盖卡片：必须给出可核对的章号，不能只说「落后了」
test('章号压缩：连续区间与离散章号都能读', () => {
  assert.equal(formatChapterList([5, 7]), '5、7')
  assert.equal(formatChapterList([9, 10, 11, 12]), '9–12')
  assert.equal(formatChapterList([5, 7, 8, 9]), '5、7–9')
  assert.equal(formatChapterList([]), '')
})

test('覆盖缺口：能逐章定位时说清缺哪几章、数据来自哪一步', () => {
  const row = {
    view: 'scenes', status: 'lagging', covered_ch: 6,
    locatable: true, missing_chapters: [5, 7], missing_count: 2,
    record_source: '每写完一章写入的场景清单',
  }
  const text = coverageGap(row, 7)
  assert.ok(text.includes('第 1–7 章已写'))
  assert.ok(text.includes('第 5、7 章'))
  assert.ok(text.includes('共缺 2 章'))
  assert.ok(!text.includes('落后了'))
  assert.ok(coverageEffect(row).includes('场景'))
  assert.ok(coverageAdvice(row).includes('不用改正文'))
})

test('覆盖缺口：只能拿到水位时明说无法定位具体章节', () => {
  const row = {
    view: 'chapter_state', status: 'lagging', covered_ch: 8,
    locatable: false, missing_chapters: [], missing_count: 4,
    record_source: '这一项只记录最后更新到第几章，看不到具体缺哪章',
  }
  const text = coverageGap(row, 12)
  assert.ok(text.includes('第 1–12 章已写'))
  assert.ok(text.includes('落后 4 章'))
  assert.ok(text.includes('无法定位具体缺哪几章'))
})

test('覆盖缺口：连水位都没有时不硬算落后章数', () => {
  const row = {
    view: 'character_states', status: 'unknown', covered_ch: null,
    locatable: false, missing_chapters: [], missing_count: 0,
    record_source: '这一项没有记录更新到第几章，无法判断缺口',
  }
  const text = coverageGap(row, 7)
  assert.ok(text.includes('无法判断缺了哪几章'))
  assert.ok(!text.includes('落后'))
})

// 冲突卡片：必须带依据 / 预期 vs 实际 / 判定理由；雷同类必须改名
test('雷同描写：不叫冲突，讲清「不是设定矛盾」并给两处原文', () => {
  const item = {
    category: 'repetition', category_label: '雷同描写', severity: 'low',
    detail: '前一处：甲抓笔划字；后一处：乙抓笔划字',
    expected: '甲抓笔划字', actual: '乙抓笔划字',
    entities: ['二舅公', '三表叔'], chapter: 2, check_type: '自相矛盾-事件重复',
    reason: '两处是不同人物的同类动作，不是同一事件的第二个版本；按一致性评审规则不构成矛盾。',
    basis: [{ kind: 'check', name: '单章质检 · 一致性评审记录', excerpt: '' }],
  }
  assert.equal(conflictCategory(item), '雷同描写')
  assert.equal(conflictHeadline(item), '两处同类动作写得几乎一样')
  assert.ok(conflictSides(item).expectedLabel === '前一处')
  assert.ok(conflictSides(item).actualLabel === '后一处')
  assert.ok(conflictReason(item).includes('不构成矛盾'))
  assert.ok(conflictAdvice(item).includes('不是设定矛盾'))
  assert.ok(conflictScope(item).includes('二舅公、三表叔'))
  assert.ok(conflictScope(item).includes('第 2 章'))
  assert.equal(conflictCheckType(item), '自相矛盾-事件重复')
})

test('真冲突：展示设定来源与设定原文，标签为「设定要求 / 正文写法」', () => {
  const item = {
    category: 'conflict', category_label: '前后矛盾', severity: 'high',
    expected: '林舟左手残疾', actual: '林舟左手握刀',
    entities: ['林舟'], chapter: 4,
    basis: [
      { kind: 'check', name: '全书通读检查记录', excerpt: '' },
      { kind: 'setting', name: '林舟', excerpt: '林舟左手残疾' },
    ],
  }
  const sides = conflictSides(item)
  assert.equal(sides.expectedLabel, '设定要求')
  assert.equal(sides.actualLabel, '正文写法')
  const basis = conflictBasis(item)
  assert.ok(basis.some(b => b.kind === 'setting' && b.text.includes('林舟')))
  assert.ok(basis.some(b => b.kind === 'check' && b.text.includes('全书通读检查')))
})

test('依据不足：没有预期原文时不硬凑对照，改法按「了解一下」处理', () => {
  const item = { category: 'insufficient', category_label: '依据不足', severity: 'low', detail: '前后不一致' }
  assert.equal(conflictSides(item), null)
  assert.ok(conflictReason(item).includes('没有写判定依据'))
  assert.ok(conflictAdvice(item).includes('不当作冲突'))
})

test('同一事件重复：不叫冲突，改法指向删/合并', () => {
  const item = {
    category: 'duplicate_event', category_label: '同一事件重复', severity: 'high',
    detail: '同一人物面对同一冲突，实际做出同一件事且结果相同，却写了两个版本。',
    expected: '', actual: '同一人物面对同一冲突…',
    entities: ['沈决'], chapter: 5, check_type: '自相矛盾-事件重复',
    basis: [{ kind: 'check', name: '单章质检 · 一致性评审记录' }],
  }
  assert.equal(conflictHeadline(item), '同一件事写了两遍')
  assert.equal(conflictSides(item), null)
  assert.ok(conflictAdvice(item).includes('这不是设定矛盾'))
  assert.ok(conflictAdvice(item).includes('删掉或合并'))
})

test('阅读/节奏建议：不叫冲突，降为「了解一下」', () => {
  const item = {
    category: 'advisory', category_label: '阅读/节奏提醒', severity: 'low',
    detail: '关键证据只用一句口述交代，读者看不到过程。', suggestion: '补一段调取过程的描写。',
    expected: '', actual: '关键证据只用一句口述交代…', entities: ['沈决'], chapter: 5,
    basis: [{ kind: 'check', name: '单章质检 · 一致性评审记录' }],
  }
  assert.equal(conflictHeadline(item), '这是一条阅读/节奏建议')
  assert.equal(conflictSides(item), null)
  assert.equal(conflictAdvice(item), '补一段调取过程的描写。')
})

test('冲突卡展示文本不出现内部代号', () => {
  const item = {
    category: 'repetition', category_label: '雷同描写', severity: 'low',
    detail: '前一处：甲抓笔；后一处：乙抓笔', expected: '甲抓笔', actual: '乙抓笔',
    entities: ['二舅公', '三表叔'], chapter: 2, check_type: '事件重复',
    reason: '不是同一事件', basis: [{ kind: 'check', name: '单章质检 · 一致性评审记录' }],
  }
  const text = [
    conflictHeadline(item), conflictCategory(item), conflictScope(item),
    conflictReason(item), conflictAdvice(item), conflictCheckType(item),
    ...conflictBasis(item).map(b => b.text),
  ].join(' ')
  assert.ok(!/[a-z_]{4,}/.test(text), `不应出现内部英文键：${text}`)
})
