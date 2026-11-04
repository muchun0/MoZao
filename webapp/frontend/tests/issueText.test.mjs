// R1 前端展示口径单测（node --test）：证据/改法的留白规则，避免 detail 冒充原文。
import test from 'node:test'
import assert from 'node:assert/strict'

import { evidenceInfo, fixInfo, EVIDENCE_EMPTY_TEXT, FIX_EMPTY_TEXT } from '../src/components/issueText.js'

test('无 evidence 时显示未记录原文', () => {
  const info = evidenceInfo({ detail: '与前文重复率偏高' })
  assert.equal(info.text, EVIDENCE_EMPTY_TEXT)
  assert.equal(info.empty, true)
})

test('evidence 与 detail 相同视为问题描述，不冒充原文', () => {
  const detail = '与前文重复率偏高'
  const info = evidenceInfo({ detail, evidence: detail })
  assert.equal(info.text, EVIDENCE_EMPTY_TEXT)
  assert.equal(info.empty, true)
})

test('真实 evidence 原样展示并去掉首尾空白', () => {
  const info = evidenceInfo({ detail: '与前文重复率偏高', evidence: '  他回头看了一眼界碑。 ' })
  assert.equal(info.text, '他回头看了一眼界碑。')
  assert.equal(info.empty, false)
})

test('无 fix_suggestion 时显示未给出改法', () => {
  const info = fixInfo({ detail: '与前文重复率偏高' })
  assert.equal(info.text, FIX_EMPTY_TEXT)
  assert.equal(info.empty, true)
})

test('有 fix_suggestion 时展示具体改法', () => {
  const info = fixInfo({ fix_suggestion: '把重复的景物描写换成动作细节' })
  assert.equal(info.text, '把重复的景物描写换成动作细节')
  assert.equal(info.empty, false)
})
