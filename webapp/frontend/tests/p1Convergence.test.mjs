// REQ-R23 P1-1/P1-2/P1-3：P1 收敛的源码级断言（node --test）
// 与 P0 的 entryConvergence 同思路：前端无组件挂载基建，验收要求又是「某个入口/某个写法不能再存在」，
// 因此直接对 SFC/后端源码断言，防止后续改动把旧入口悄悄加回来。
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const REPO = path.resolve(FRONTEND, '..', '..')
const read = (rel, base = FRONTEND) => fs.readFileSync(path.join(base, rel), 'utf8')

// ---------- P1-1 大纲快照并入版本库 ----------
test('大纲工具条不再有独立「大纲快照」入口，只留版本弹窗', () => {
  const detail = read('src/views/ProjectDetail.vue')
  assert.equal(detail.includes('大纲快照'), false, '工具条不应再有快照下拉')
  assert.equal(detail.includes('onSnapCmd'), false, '快照命令链应已删除')
  assert.equal(detail.includes('listSnapshots'), false, '不再从 tools api 读快照')
  const api = read('src/api/tools.js')
  assert.equal(api.includes('export const restoreSnapshot'), false, '快照还原不应再走独立 API')
})

test('后端快照接口改读 outline 版本库（带 label 的版本才是快照）', () => {
  const outline = read('webapp/routers/projects_outline.py', REPO)
  assert.ok(outline.includes('_import_legacy_snapshots'), '缺少旧快照一次性导入')
  assert.ok(outline.includes('VersionedProduct'), '快照必须读版本库')
  assert.ok(outline.includes('label='), '快照要写成带标签的版本')
  const versioned = read('novel_gen/store/versioned.py', REPO)
  assert.ok(versioned.includes('label: str = ""'), 'commit 应支持 label')
})

// ---------- P1-2 导入大纲前自动备份为版本 ----------
test('导入大纲前备份写入 outline 版本并带「导入前备份」标签', () => {
  const core = read('webapp/core_project.py', REPO)
  assert.ok(core.includes('_backup_outline'), '缺少导入前备份实现')
  assert.ok(core.includes('备份'), '备份版本必须带标签，否则用户在版本弹窗里认不出')
  assert.ok(core.includes('label=label'), '备份要作为带标签的版本提交')
  const base = read('webapp/routers/projects_base.py', REPO)
  assert.ok(base.includes('导入前备份'), '导入接口说明应告知「覆盖前自动存一版」')
})

// ---------- P1-3 伏笔数据彻底合并 ----------
test('伏笔只有一条写入路径：结构化 upsert，不再 append 字符串', () => {
  const world = read('webapp/routers/projects_world.py', REPO)
  assert.ok(world.includes('_normalize_foreshadowing'), '缺少结构化归一')
  assert.ok(world.includes('_migrate_foreshadowing'), '读路径缺少幂等迁移')
  assert.equal(world.includes('world.foreshadowing.append'), false, '伏笔不得再追加自由文本')
  assert.ok(world.includes('foreshadowing/name/'), '删除须支持按名称（索引会漂移）')
})

test('工具页伏笔卡直写结构化账本（名称/埋点/回收/等级），删改按名称', () => {
  const tools = read('src/components/tools/ToolsPanel.vue')
  assert.equal(tools.includes('fsInput'), false, '不应再是自由文本输入')
  assert.ok(tools.includes('fsBuried') && tools.includes('fsReveal') && tools.includes('fsLevel'))
  assert.ok(tools.includes('delForeshadowingByName'), '删除应走名称接口')
  const api = read('src/api/tools.js')
  assert.match(api, /delForeshadowingByName/)
})

// ---------- 409 弹窗重写入口收敛到 Agent ----------
test('409 覆盖确认弹窗不再自己二次提交重写，改为把人送到 Agent 页', () => {
  const dlg = read('src/components/common/StaleGateDialog.vue')
  assert.equal(dlg.includes('startAgentRun'), false, '弹窗不得直连接口')
  assert.equal(dlg.includes('rewriteFrom'), false, '「从第X章起重写」不应在本弹窗发起')
  assert.ok(dlg.includes("emit('open-agent'"), '应 emit open-agent 交给上层')
  assert.match(dlg, /import \{ runAgent/)
})

test('Agent 页接收区间预设，重写只在「章节写手 · 区间」发生一次', () => {
  const agent = read('src/components/agent/AgentPanel.vue')
  assert.ok(agent.includes('presetRange'), '缺少 presetRange prop')
  assert.match(agent, /watch\(\(\) => props\.presetRange/, '缺少预设 watcher')
  const detail = read('src/views/ProjectDetail.vue')
  assert.ok(detail.includes('preset-range="agentPreset"'))
  assert.ok(detail.includes('goAgentRewrite'))
})
