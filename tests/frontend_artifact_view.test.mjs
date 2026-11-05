// 中间产物只读展示口径测试（纯函数，无需 DOM）
// 运行：node --test tests/frontend_artifact_view.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  clipText, sceneTitle, sceneDetails, sceneAnchors, sceneCountText, designLines,
  SCENE_EMPTY_TEXT, DESIGN_EMPTY_TEXT, SUMMARY_EMPTY_TEXT,
} from '../webapp/frontend/src/utils/artifactView.js'

test('长文本截断：46 字以内原样，超出补省略号', () => {
  assert.equal(clipText('短文本'), '短文本')
  assert.equal(clipText(''), '')
  assert.equal(clipText(null), '')
  const long = '甲'.repeat(60)
  assert.equal(clipText(long).length, 47)
  assert.ok(clipText(long).endsWith('…'))
})

test('场景标题：地点 · 事件；缺字段时逐级退回', () => {
  assert.equal(sceneTitle({ location: '餐厅包间', event: '沈决观察规则' }), '餐厅包间 · 沈决观察规则')
  assert.equal(sceneTitle({ event: '只有事件' }), '只有事件')
  assert.equal(sceneTitle({ location: '只有地点' }), '只有地点')
  assert.equal(sceneTitle({ scene_event_ref: '事件1' }), '事件1')
  assert.equal(sceneTitle({}), '未命名场景')
})

test('场景明细：过滤空字段，顺序固定', () => {
  const rows = sceneDetails({ conflict: '条款冲突', action: '', final_state: '存活', emotion: '警觉', time_marker: null })
  assert.deepEqual(rows.map((r) => r.label), ['冲突', '结尾状态', '情绪'])
  assert.equal(rows[0].text, '条款冲突')
  assert.deepEqual(sceneDetails({}), [])
})

test('细节锚点最多 5 条，过滤空值', () => {
  assert.deepEqual(sceneAnchors({ anchors: ['a', '', 'b', 'c', 'd', 'e', 'f'] }), ['a', 'b', 'c', 'd', 'e'])
  assert.deepEqual(sceneAnchors({}), [])
})

test('场景总览文案：0 个场景不输出数量提示', () => {
  assert.equal(sceneCountText(0), '')
  assert.equal(sceneCountText(3), '系统写本章时自动记下，共 3 个场景')
})

test('设计卡明细：只取有值的四项', () => {
  const rows = designLines({
    goal: '目标', conflict: { core: '核心冲突' },
    turning_point: { event: '转折' }, cliffhanger: '',
  })
  assert.deepEqual(rows.map((r) => r.label), ['本章目标', '核心冲突', '转折点'])
  assert.deepEqual(designLines({}), [])
})

test('空状态文案：如实说明「没有记录」而不是错误', () => {
  assert.ok(SCENE_EMPTY_TEXT.includes('整章生成'))
  assert.ok(SCENE_EMPTY_TEXT.includes('没有场景计划记录'))
  assert.ok(DESIGN_EMPTY_TEXT.includes('设计卡'))
  assert.ok(SUMMARY_EMPTY_TEXT.includes('摘要'))
})
