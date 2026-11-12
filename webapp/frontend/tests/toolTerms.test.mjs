// REQ-R23 P0-1/P0-5 入口收敛与术语单测（node --test）
// 目的：改名与分组是「文案级」改动，容易被后续文案调整悄悄改回去，这里锁住口径。
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  TOOL_TAB_NAME, RULE_TERM, RULE_TERM_LEGACY, LOCK_TERM, LOCK_TERM_HINT, PLAN_EVENT_HINT, TOOL_SECTIONS,
} from '../src/utils/toolTerms.js'

test('工具页三组顺序固定：质量中心 → 参考与候选 → 资产与约束', () => {
  assert.deepEqual(TOOL_SECTIONS.map((s) => s.key), ['quality', 'reference', 'asset'])
  assert.equal(TOOL_SECTIONS[0].title, '质量中心')
  assert.equal(TOOL_SECTIONS[1].title, '参考与候选')
  assert.equal(TOOL_SECTIONS[2].title, '资产与约束')
  // 每组都要有职责说明，避免只剩标题
  TOOL_SECTIONS.forEach((s) => assert.match(s.hint, /。$/))
})

test('质量中心明确指向全书体检，不再暗示工具页跑全书检查', () => {
  assert.match(TOOL_SECTIONS[0].hint, /全书体检/)
})

test('术语改名：写作铁律 与 防覆盖锁定 是两个概念，各有旧名/说明', () => {
  assert.equal(RULE_TERM, '写作铁律')
  assert.equal(RULE_TERM_LEGACY, '核心设定锁定')
  assert.equal(LOCK_TERM, '防覆盖锁定')
  assert.match(LOCK_TERM_HINT, /不代表写作约束/)
})

test('计划事件口径：明确「计划 ≠ 已发生」', () => {
  assert.match(PLAN_EVENT_HINT, /计划 ≠ 已发生/)
  assert.match(PLAN_EVENT_HINT, /写进正文后才算事实/)
})

test('导航名表明这是工具中心，而不是第二个生成台', () => {
  assert.equal(TOOL_TAB_NAME, '工具中心')
})
