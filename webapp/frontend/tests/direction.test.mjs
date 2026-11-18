// REQ-R26 剧情走向：纯函数单测（node --test）
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  STUCK_TYPES, SOURCE_TAG_LABELS, sourceTagLabel, directionInstruction, isGenericBatch,
} from '../src/utils/direction.js'

test('来源标签映射覆盖契约枚举', () => {
  for (const tag of ['foreshadow', 'commitment', 'conflict', 'twist', 'pacing']) {
    assert.ok(SOURCE_TAG_LABELS[tag])
    assert.equal(sourceTagLabel(tag), SOURCE_TAG_LABELS[tag])
  }
  assert.equal(sourceTagLabel('unknown'), '')
})

test('卡点枚举与后端 stuck_type 一致', () => {
  const values = STUCK_TYPES.map((s) => s.value)
  assert.deepEqual(values, ['', 'foreshadow', 'commitment', 'conflict', 'pace', 'custom'])
})

test('按此写指令：拼方向 + 说明，格式固定', () => {
  const text = directionInstruction({ direction: '收玉牌', summary: '把玉牌交给师父，引出旧案。' })
  assert.equal(text, '按这个走向续写：收玉牌。把玉牌交给师父，引出旧案。')
})

test('按此写指令：超长时截断且总长不超过 500', () => {
  const item = { direction: '转折', summary: '很长的说明'.repeat(200) }
  const text = directionInstruction(item)
  assert.ok(text.length <= 500)
  assert.ok(text.startsWith('按这个走向续写：转折。'))
  assert.ok(text.endsWith('…'))
})

test('按此写指令：缺字段不抛错', () => {
  assert.equal(directionInstruction(null), '按这个走向续写：。')
})

test('整批只有 pacing 时提示偏通用', () => {
  assert.equal(isGenericBatch([{ source_tag: 'pacing' }, { source_tag: 'pacing' }]), true)
  assert.equal(isGenericBatch([{ source_tag: 'pacing' }, { source_tag: 'foreshadow' }]), false)
  assert.equal(isGenericBatch([]), false)
})
