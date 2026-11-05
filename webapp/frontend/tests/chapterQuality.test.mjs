// R12 章节质量总览前端口径单测（node --test）
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  qualityMeta, qualityCountsText, sortRowsByQuality, needsAttention, filterRows,
  qualityIssueText, qualityWorstText, qualityGapText,
} from '../src/utils/chapterQuality.js'

const mkRow = (no, over = {}) => ({ no, title: `第${no}章`, has_quality: true, ...over })

test('等级词与颜色沿用既有口径', () => {
  assert.equal(qualityMeta('high').label, '大改')
  assert.equal(qualityMeta('high').type, 'danger')
  assert.equal(qualityMeta('medium').label, '中改')
  assert.equal(qualityMeta('medium').type, 'warning')
  assert.equal(qualityMeta('low').label, '小改')
  assert.equal(qualityMeta('info').label, '无问题')
  assert.equal(qualityMeta('none').label, '未质检')
  // 未知值按未质检兜底，不抛错
  assert.equal(qualityMeta(undefined).label, '未质检')
})

test('未质检不显示 0 分或问题数', () => {
  const row = { has_quality: false, score: null, issue_count: 0, severity: 'none' }
  assert.equal(qualityCountsText(row), '')
  assert.equal(qualityIssueText(row), '')
  assert.equal(qualityMeta(row.severity).label, '未质检')
})

test('问题分桶文案按大改 → 中改 → 小改', () => {
  const row = { has_quality: true, counts: { high: 3, medium: 0, low: 2 } }
  assert.equal(qualityCountsText(row), '大改 3 处、小改 2 处')
})

test('最差优先使用后端 rank，未质检排最后', () => {
  const rows = [
    { no: 3, quality: { rank: null } },
    { no: 2, quality: { rank: 2 } },
    { no: 5, quality: { rank: 1 } },
    { no: 1, quality: { rank: null } },
  ]
  assert.deepEqual(sortRowsByQuality(rows).map((r) => r.no), [5, 2, 1, 3])
})

test('只看需要处理：仅保留 needs_attention 章', () => {
  const rows = [
    mkRow(1, { quality: { needs_attention: true } }),
    mkRow(2, { quality: { needs_attention: false } }),
    { no: 3 }, // 无质检
  ]
  assert.equal(needsAttention(rows[0]), true)
  assert.deepEqual(filterRows(rows, true).map((r) => r.no), [1])
  assert.deepEqual(filterRows(rows, false).map((r) => r.no), [1, 2, 3])
})

test('差在哪类问题：最多 2 类并带同类计数', () => {
  const row = {
    has_quality: true,
    top_issues: [
      { type: '读者-人物一致性', count: 2 },
      { type: '读者-代入感', count: 1 },
      { type: '写作-字数超出目标', count: 1 },
    ],
  }
  assert.equal(qualityIssueText(row), '读者-人物一致性 ×2 · 读者-代入感')
})

test('最需要处理提示带章名、分数与问题分桶', () => {
  const overview = {
    worst: { no: 11, score: 15, counts: { high: 3, medium: 0, low: 2 } },
  }
  const chapters = [{ no: 11, title: '矿道' }]
  assert.equal(
    qualityWorstText(overview, chapters),
    '最需要处理：第11章《矿道》 · 15分 · 大改 3 处、小改 2 处')
  assert.equal(qualityWorstText({ worst: null }, chapters), '')
})

test('无 worst 时展示后端数据缺口说明', () => {
  assert.equal(
    qualityGapText({ data_gaps: ['本书暂无章节质检记录', '已降级'] }),
    '本书暂无章节质检记录；已降级')
  assert.equal(qualityGapText(null), '')
})
