// 章节质检展示口径测试（纯函数，无需 DOM）
// 运行：node --test tests/frontend_chapter_quality.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  QUALITY_META, qualityMeta, levelLabel, countsText,
  qualityIndexTitle, qualityWorstText, issueSummaryText,
} from '../webapp/frontend/src/utils/chapterQuality.js'

test('等级展示标签为人话措辞（severity 键不变）', () => {
  assert.equal(qualityMeta('high').label, '重点修改')
  assert.equal(qualityMeta('medium').label, '建议调整')
  assert.equal(qualityMeta('low').label, '可润色')
  assert.equal(QUALITY_META.info.label, '无问题')
  assert.equal(QUALITY_META.none.label, '未质检')
})

test('后端中文等级原值映射到展示标签，未知值回退原值', () => {
  assert.equal(levelLabel('大改'), '重点修改')
  assert.equal(levelLabel('中改'), '建议调整')
  assert.equal(levelLabel('小改'), '可润色')
  assert.equal(levelLabel('未知等级'), '未知等级')
})

test('分桶文案使用人话标签', () => {
  assert.equal(countsText({ high: 3, low: 2 }), '重点修改 3 处、可润色 2 处')
  assert.equal(countsText({ medium: 1 }), '建议调整 1 处')
  assert.equal(countsText({}), '')
})

test('有建议时索引标题不使用「待改」等焦虑措辞', () => {
  const title = qualityIndexTitle({ has_quality: true, issue_count: 3, counts: { high: 1, low: 2 } })
  assert.match(title, /自检建议/)
  assert.doesNotMatch(title, /待改|大改|中改|小改/)
})

test('无建议时提示可保留现状', () => {
  const title = qualityIndexTitle({ has_quality: true, issue_count: 0, score: 88 })
  assert.match(title, /可保留现状/)
})

test('最需要处理提示弱化为「建议优先看」', () => {
  const text = qualityWorstText(
    { worst: { no: 3, issue_count: 2, counts: { low: 2 } } },
    [{ no: 3, title: '第一章' }],
  )
  assert.match(text, /建议优先看/)
  assert.doesNotMatch(text, /最需要处理|待改/)
})

test('详情面摘要使用人话分桶', () => {
  const issues = [{ level: '大改' }, { level: '小改' }, { level: '小改' }]
  assert.equal(issueSummaryText(issues, 3), '重点修改 1 处、可润色 2 处，共 3 处')
})
