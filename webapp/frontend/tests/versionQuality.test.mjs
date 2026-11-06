// R14 单章重检前端口径单测（node --test）
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  needsRecheck, noIssueText, recheckConfirmText, recheckResultText,
} from '../src/components/versionQuality.js'

test('仅「改写版且无质检」需要重新质检入口', () => {
  assert.equal(needsRecheck({ v: 2, source: 'rewrite', quality: null }), true)
  assert.equal(needsRecheck({ v: 1, source: 'ai', quality: null }), false)
  assert.equal(needsRecheck({ v: 2, source: 'rewrite', quality: { score: 80 } }), false)
  assert.equal(needsRecheck(null), false)
})

test('未质检文案不再说成「无问题」', () => {
  assert.equal(noIssueText({ source: 'rewrite', quality: null }), '该版本尚未质检（改写生成）')
  assert.equal(noIssueText({ source: 'ai', quality: null }), '该版本未质检，暂无问题记录')
  // 已质检且 0 问题才允许说「未发现问题」
  assert.equal(noIssueText({ quality: { score: 85, issues: [] } }), '该版本本次质检未发现问题（85 分）')
  assert.equal(noIssueText({ quality: { issues: [] } }), '该版本本次质检未发现问题')
})

test('确认文案如实告知调用模型、消耗 token、不按字数计费', () => {
  const text = recheckConfirmText(2)
  assert.match(text, /v2/)
  assert.match(text, /调用模型/)
  assert.match(text, /token/)
  assert.match(text, /不按字数计费/)
})

test('复用与真正重跑的成功提示可区分', () => {
  assert.equal(
    recheckResultText({ version: 2, score: 88, issue_count: 0, reused: true }),
    'v2 已有质检结果（88 分、0 处问题），本次未重复调用模型')
  assert.equal(
    recheckResultText({ version: 2, score: 72, issue_count: 3 }),
    'v2 质检完成：72 分、3 处问题')
})
