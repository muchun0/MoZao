// 剧情走向（当前难点）展示口径测试（纯函数，无需 DOM）
// 运行：node --test tests/frontend_direction.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'

import { STUCK_TYPES, sourceTagLabel, directionInstruction } from '../webapp/frontend/src/utils/direction.js'

test('难点选项为人话措辞，value 键保持后端契约不变', () => {
  const byValue = Object.fromEntries(STUCK_TYPES.map((s) => [s.value, s.label]))
  assert.equal(byValue[''], '不指定难点')
  assert.equal(byValue['pace'], '节奏偏慢')
  assert.equal(byValue['foreshadow'], '伏笔该收了')
  assert.equal(byValue['commitment'], '承诺要兑现')
  assert.equal(byValue['conflict'], '冲突不够强')
  assert.equal(byValue['custom'], '自定义')
})

test('来源标签映射保持可用', () => {
  assert.equal(sourceTagLabel('foreshadow'), '收伏笔')
  assert.equal(sourceTagLabel('commitment'), '兑承诺')
  assert.equal(sourceTagLabel('unknown'), '')
})

test('走向指令拼接方向与摘要', () => {
  const text = directionInstruction({ direction: '主角反杀', summary: '冲突升级' })
  assert.match(text, /按这个走向续写：主角反杀。冲突升级/)
})
