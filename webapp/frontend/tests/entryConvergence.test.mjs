// REQ-R23 P0-4 / P0-5：入口→封装映射与术语落地的源码级断言（node --test）
// 说明：这两条验收要求「断言入口→封装映射」「扫描不再出现旧名」，
// 而 qian 端没有组件挂载测试基建，因此直接对 SFC 源码断言：
//   1) 四个就近生成入口必须走 utils/agentRunClient 的 runAgent，且不再直连 startAgentRun；
//   2) 旧术语「核心设定锁定」不得再出现在界面源码里，防覆盖锁定必须来自统一常量。
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8')
// 模板注释里可以保留历史说明（如「原核心设定锁定」），断言只看会渲染出去的内容
const visible = (rel) => read(rel).replace(/<!--[\s\S]*?-->/g, '')

test('章节行重生成 / 顶部继续生成 / 大纲重生成 / 重生成世界观 都走统一封装', () => {
  const detail = read('src/views/ProjectDetail.vue')
  // 不再直连单轨接口：所有入口必须经过封装（预估 + 余额校验）
  assert.equal(detail.includes('startAgentRun'), false, 'ProjectDetail 不应再直接调用 startAgentRun')
  assert.match(detail, /import \{ runAgent, errorDetail \} from '\.\.\/utils\/agentRunClient'/)
  // 单章重生成与顶部继续生成共用 payload 变量
  assert.ok(detail.includes('await runAgent(pid, payload)'), '缺少 runAgent(pid, payload) 入口')
  // 大纲 / 世界观（含大纲完成后链式生成世界观）
  assert.ok(detail.includes("runAgent(pid, { agent: 'architect.outline', params: {} })"))
  assert.ok(detail.includes("runAgent(pid, { agent: 'architect.world', params: {} })"))
})

test('重生成本卷 走统一封装，且不再直连 startAgentRun', () => {
  const outline = read('src/components/outline/OutlineEditor.vue')
  assert.equal(outline.includes('startAgentRun'), false)
  assert.ok(outline.includes("runAgent(props.pid, { agent: 'architect.volume', params: { vol_no: vol.no } })"))
})

test('旧名「核心设定锁定」不再出现在界面源码，写作铁律来自统一常量', () => {
  const tools = visible('src/components/tools/ToolsPanel.vue')
  assert.equal(tools.includes('核心设定锁定'), false, '工具页不应再出现旧名')
  assert.ok(tools.includes('RULE_TERM_LEGACY'), '应保留旧名来源说明（原核心设定锁定）')
  assert.ok(tools.includes('RULE_TERM'), '卡片标题应使用统一常量')
  const graph = visible('src/components/facts/StateGraphPanel.vue')
  assert.ok(graph.includes('RULE_TERM'), '设定规则页应指向写作铁律')
})

test('章节 / 世界观的锁统一表述为防覆盖锁定', () => {
  const detail = read('src/views/ProjectDetail.vue')
  const world = read('src/components/world/WorldEditor.vue')
  assert.ok(detail.includes('LOCK_TERM'))
  assert.ok(world.includes('LOCK_TERM'))
  // 不再使用会把「锁」与「写作约束」混在一起的旧文案
  assert.equal(/锁定后重生成不覆盖/.test(detail), false)
  assert.equal(/锁定后重生成不覆盖/.test(world), false)
})
