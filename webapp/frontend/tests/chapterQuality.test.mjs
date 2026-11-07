// R12 章节质量总览前端口径单测（node --test）
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  qualityMeta, qualityCountsText, countsText, sortRowsByQuality, needsAttention, filterRows,
  issueCounts, issueSummaryText, qualityIndexTitle, qualityWorstText, qualityGapText,
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
  assert.equal(countsText(row.counts), '')
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

test('R15 列表行只做索引：徽标说明报分数/条数，不再渲染问题类型', () => {
  assert.equal(
    qualityIndexTitle({ has_quality: true, score: 75, issue_count: 10 }),
    '质检 75 分 · 10 处改稿建议，点击查看证据与改法')
  assert.match(qualityIndexTitle({ has_quality: true, score: null, issue_count: 0 }), /未记录分数/)
  assert.match(qualityIndexTitle({ has_quality: false }), /还没跑质检/)
})

test('R15 改稿建议分桶：未知等级不计入分桶', () => {
  assert.deepEqual(
    issueCounts([{ level: '大改' }, { level: '大改' }, { level: '中改' }, { level: '小改' }, { level: '未知' }, {}, null]),
    { high: 2, medium: 1, low: 1 })
  assert.deepEqual(issueCounts([]), { high: 0, medium: 0, low: 0 })
})

test('R15 详情面标题与列表行同口径：分桶 + 总数', () => {
  const issues = [{ level: '大改' }, { level: '大改' }, { level: '中改' }]
  // issue_count 可能大于明细条数（历史记录只存总数），如实显示总数
  assert.equal(issueSummaryText(issues, 10), '大改 2 处、中改 1 处，共 10 处')
  assert.equal(issueSummaryText(issues, 3), '大改 2 处、中改 1 处，共 3 处')
  assert.equal(issueSummaryText([], 0), '')
  assert.equal(issueSummaryText([], 4), '共 4 处')
  // 缺 issue_count 时退回明细条数
  assert.equal(issueSummaryText(issues), '大改 2 处、中改 1 处，共 3 处')
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
