<template>
  <div class="agent-workspace">
    <!-- 左栏：角色导航 + 计费环境（原横贯顶部的告警条收敛到这里，减少视觉噪音） -->
    <aside class="agent-rail">
      <div class="rail-title">角色</div>
      <div class="rail-list" role="tablist" aria-label="Agent 角色">
        <button
          v-for="a in AGENTS"
          :key="a.key"
          type="button"
          role="tab"
          :aria-selected="a.key === selectedAgent"
          class="rail-item"
          :class="{ active: a.key === selectedAgent }"
          @click="selectedAgent = a.key"
        >
          <span class="rail-item-main">
            <span class="rail-name">{{ a.name }}</span>
            <span class="rail-version">{{ versionShort(a.key) }}</span>
          </span>
          <span class="rail-desc">{{ a.desc }}</span>
          <span v-if="isRunningAgent(a.key)" class="rail-running">
            <span class="pulse" />运行中
          </span>
        </button>
      </div>

      <div class="rail-billing">
        <div class="billing-line">
          <span class="billing-dot" :class="billingMode" />
          <span>{{ billingMode === 'user_llm' ? '自备 Key · 平台不扣费' : '平台计费' }}</span>
        </div>
        <div class="billing-detail">
          {{ billingMode === 'user_llm'
            ? '使用您自己的模型额度，产物仍按版本记账'
            : '按 AI 实际生成字数结算：统一 3 元/万字；候选/重写版本同样计费' }}
        </div>
        <template v-if="billingMode === 'platform'">
          <div class="billing-balance" :class="{ danger: balance <= 0 }">
            <span>余额 ¥{{ balance.toFixed(2) }}</span>
            <el-button v-if="balance <= 0" size="small" type="primary" link @click="$emit('recharge')">去充值</el-button>
          </div>
          <div v-if="balance <= 0" class="billing-warn">余额不足，运行入口已禁用</div>
        </template>
      </div>
    </aside>

    <!-- 右栏：当前角色工作区（一次只展开一个角色，参数/指令/运行收敛到这里） -->
    <section class="agent-stage">
      <transition name="stage" mode="out-in">
        <div :key="currentAgent.key" class="stage-body">
          <header class="stage-head">
            <div class="stage-title">
              <h3>{{ currentAgent.name }}</h3>
              <el-tag size="small" type="info" effect="plain">{{ promptVersion(currentAgent.key) }}</el-tag>
            </div>
            <div class="stage-head-actions">
              <span v-if="isRunningAgent(currentAgent.key)" class="stage-running"><span class="pulse" />运行中</span>
              <el-button size="small" link type="primary" @click="openPromptEditor(currentAgent)">编辑人设</el-button>
            </div>
          </header>
          <p class="stage-desc">{{ currentAgent.desc }}</p>

          <!-- writer.chapter 参数：scope 三态（§4.1 params schema） -->
          <div v-if="currentAgent.key === 'writer.chapter'" class="stage-field">
            <div class="field-label">生成范围</div>
            <el-radio-group v-model="chapterScope" size="small">
              <el-radio-button value="all">全部未完成章</el-radio-button>
              <el-radio-button value="single">单章</el-radio-button>
              <el-radio-button value="range">区间</el-radio-button>
            </el-radio-group>
            <div v-if="chapterScope === 'single'" class="field-row">
              <span>第</span>
              <el-input-number v-model="chNo" :min="1" size="small" controls-position="right" />
              <span>章</span>
            </div>
            <div v-else-if="chapterScope === 'range'" class="field-row">
              <span>第</span>
              <el-input-number v-model="fromCh" :min="1" size="small" controls-position="right" />
              <span>章 至</span>
              <el-input-number v-model="toCh" :min="fromCh" size="small" controls-position="right" />
              <span>章</span>
              <el-checkbox v-model="toChOpen" size="small" @change="(v) => { if (v) toCh = 0 }">到末尾</el-checkbox>
            </div>
          </div>

          <div v-else-if="currentAgent.key === 'architect.volume'" class="stage-field">
            <div class="field-label">分卷号</div>
            <el-input-number v-model="volNo" :min="1" size="small" controls-position="right" />
          </div>

          <div class="stage-field">
            <div class="field-label">补充指令<span class="field-optional">可选</span></div>
            <el-input
              v-model="userPrompts[currentAgent.key]"
              type="textarea" :rows="3" maxlength="2000" show-word-limit
              placeholder="本次生成的额外要求，如「加强冲突」「节奏加快」"
            />
          </div>

          <div class="stage-actions">
            <template v-if="isRunningAgent(currentAgent.key)">
              <el-button type="danger" @click="stopRun">停止运行</el-button>
              <span class="action-hint">已完成部分保留，未完成章节不提交</span>
              <!-- U1：运行进度统一收敛到顶部 GenerationProgress 组件（单一进度条）。
                   此处只保留紧凑文字状态行，不再渲染第二个进度条（修复双进度条）。 -->
              <div v-if="runProgress" class="run-statusline">
                <span>{{ runPhaseText }}</span>
                <span v-if="runProgress.chapters_total" class="run-status-ch">
                  第 {{ runCurrentText }} / {{ runProgress.chapters_total }} 章
                </span>
                <span class="run-status-elapsed">已用 {{ elapsedText }}</span>
                <span class="action-hint">进度见顶部进度条</span>
              </div>
            </template>
            <template v-else>
              <el-button type="primary" :disabled="entryDisabled" @click="startRun(currentAgent.key)">
                运行 {{ currentAgent.name }}
              </el-button>
              <span v-if="estimates[currentAgent.key] > 0" class="action-hint">
                预估 ¥{{ estimates[currentAgent.key].toFixed(2) }}（按实际生成字数结算）
              </span>
              <span v-else-if="running" class="action-hint">「{{ runningAgentName }}」运行中，结束后可再次运行</span>
              <span v-else-if="billingMode === 'platform' && balance <= 0" class="action-hint danger">余额不足，请先充值</span>
            </template>
          </div>

          <!-- 技能挂载：默认折叠，避免长期占据首屏 -->
          <div class="stage-skills">
            <button type="button" class="skills-toggle" :aria-expanded="skillsOpen" @click="skillsOpen = !skillsOpen">
              <span>挂载写作技能</span>
              <span class="skills-count">{{ skillIds.length }}/5</span>
              <span class="skills-chevron" :class="{ open: skillsOpen }">▾</span>
            </button>
            <div v-show="skillsOpen" class="skills-body">
              <p class="skills-hint">技能注入该角色 system 提示词，对全部角色运行生效</p>
              <SkillPicker v-model="skillIds" />
            </div>
          </div>
        </div>
      </transition>
    </section>

    <!-- 人设编辑对话框：保存即新建不可变版本（author=user） -->
    <el-dialog v-model="promptDialog" :title="`编辑人设 · ${editingAgentName}`" width="640px">
      <el-alert :closable="false" type="info" class="prompt-tip">
        <template #title>
          人设将作为 system 提示词注入该 Agent（追加到默认模板末尾）。保存即生成新版本，历史版本不可变、可追溯；当前版本 {{ editingVersion }}。请勿写入越权指令，输出仍受平台合规校验约束。
        </template>
      </el-alert>
      <div class="prompt-mode-bar">
        <el-radio-group v-model="promptMode" size="small">
          <el-radio-button value="preview">预览</el-radio-button>
          <el-radio-button value="edit">编辑（Markdown 源码）</el-radio-button>
        </el-radio-group>
      </div>
      <div v-if="promptMode === 'preview'" class="prompt-preview markdown-body" v-html="renderedPrompt"></div>
      <el-input v-else v-model="editingText" type="textarea" :rows="12" maxlength="8000" show-word-limit />
      <template #footer>
        <el-button @click="promptDialog = false">取消</el-button>
        <el-button type="primary" :loading="promptSaving" @click="savePrompt">保存为新版本</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
// Agent 工作区（TECH-Agent用户化与版本化 §10.2，阶段 1C）：
// 左侧角色导航（5 个可独立触发的 agent）+ 右侧当前角色工作区（人设版本/参数/补充指令/运行停止/预估金额）。
// 计费模式与余额常驻左栏底部；平台模式余额 ≤0 时禁用运行并引导充值。
// ProjectDetail 顶部的「生成/重生成」按钮与本面板同为 UI 快捷入口，统一走 POST /agent-runs 单轨（旧生成接口已删除，见 TECH §4.0 决策变更）。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { startAgentRun, getAgentRun, stopAgentRun, getMyLlm, getBalance, estimateAgentRun } from '../../api/project'
import { getAgentPrompts, saveAgentPrompt } from '../../api/novel'
import SkillPicker from './SkillPicker.vue'
import MarkdownIt from 'markdown-it'
import { applyAgentProgress } from '../../composables/useGenerationProgress'

// 人设 Markdown 预览：html=false 转义原始 HTML（防 XSS），linkify 自动识别链接
const mdRenderer = new MarkdownIt({ html: false, linkify: true, breaks: false })

const props = defineProps({
  pid: { type: String, required: true },
})
const emit = defineEmits(['changed', 'recharge', 'started'])

const AGENTS = [
  { key: 'researcher', name: '题材调研', desc: '联网调研题材共性，产出写作建议与参考资料' },
  { key: 'architect.outline', name: '大纲架构', desc: '设计/重生成全书大纲（结构、节奏、冲突递进）' },
  { key: 'architect.world', name: '世界观架构', desc: '构建/重生成世界观、人物卡与伏笔体系' },
  { key: 'architect.volume', name: '卷纲架构', desc: '重写指定分卷的简介与章规划' },
  { key: 'writer.chapter', name: '章节写手', desc: '按范围生成章节正文（逐章结算、余额不足自动停止）' },
]

// ---------- 角色选择（默认聚焦章节写手：正文生成是项目详情页的主操作） ----------
const selectedAgent = ref('writer.chapter')
const currentAgent = computed(() => AGENTS.find((a) => a.key === selectedAgent.value) || AGENTS[0])

// ---------- 计费模式 / 余额 ----------
const billingMode = ref('platform')
const balance = ref(0)

// ---------- 人设版本 ----------
const prompts = ref({})
function promptVersion(agent) {
  return prompts.value[agent]?.version || `${agent}@v1`
}
// 左栏只显示版本号本身（v1/v2），完整 agent@v 版本标识留给右栏标签
function versionShort(agent) {
  const matched = /@v(\d+)$/.exec(promptVersion(agent))
  return matched ? `v${matched[1]}` : promptVersion(agent)
}

// ---------- 运行参数 ----------
const skillIds = ref([])
const skillsOpen = ref(false)
const chapterScope = ref('all')
const chNo = ref(1)
const fromCh = ref(1)
const toCh = ref(1)
const toChOpen = ref(false)
const volNo = ref(1)
const userPrompts = reactive({})
const estimates = reactive({})

// ---------- 运行状态（单任务轮询） ----------
const running = ref(false)
const runningAgent = ref('')
const runningTaskId = ref('')
const pollTimer = ref(null)
// U1：running 态进度（chapters_done/total、phase、elapsed）+ 前端计时器
const runProgress = ref(null)
const runStartedAt = ref(0)
const nowTs = ref(Date.now())
let clockTimer = null
// U5：轮询连续失败计数（≥3 次才停止，网络抖动不误停）
const pollFailCount = ref(0)
// U2：预估金额防抖刷新
let estTimer = null

const entryDisabled = computed(() =>
  running.value || (billingMode.value === 'platform' && balance.value <= 0))
const runningAgentName = computed(() =>
  AGENTS.find((a) => a.key === runningAgent.value)?.name || '')

const PHASE_TEXT = {
  research: '正在调研题材…',
  outline: '正在生成大纲…',
  world: '正在构建世界观…',
  volume: '正在重写卷纲…',
  chapter: '正在逐章写作…',
}
const runPhaseText = computed(() =>
  PHASE_TEXT[runProgress.value?.phase] || '运行中…')
const runCurrentText = computed(() => {
  const p = runProgress.value
  if (!p) return 0
  return p.current_chapter || p.chapters_done || 0
})
const elapsedText = computed(() => {
  if (!runStartedAt.value) return '0秒'
  const s = Math.floor((nowTs.value - runStartedAt.value) / 1000)
  if (s <= 0) return '0秒'
  return s >= 60 ? `${Math.floor(s / 60)}分${s % 60}秒` : `${s}秒`
})

function isRunningAgent(agent) {
  return running.value && runningAgent.value === agent
}

function buildParams(agent) {
  if (agent === 'writer.chapter') {
    if (chapterScope.value === 'single') return { scope: 'single', ch_no: chNo.value }
    if (chapterScope.value === 'range') {
      const p = { scope: 'range', from_ch: fromCh.value }
      if (!toChOpen.value && toCh.value >= fromCh.value) p.to_ch = toCh.value
      return p
    }
    return { scope: 'all' }
  }
  if (agent === 'architect.volume') return { vol_no: volNo.value }
  return {}
}

async function startRun(agent) {
  try {
    const body = await startAgentRun(props.pid, {
      agent,
      params: buildParams(agent),
      user_prompt: userPrompts[agent] || '',
      skill_ids: skillIds.value,
    })
    estimates[agent] = body.estimated_price || 0
    running.value = true
    runningAgent.value = agent
    runningTaskId.value = body.task_id
    runProgress.value = null
    runStartedAt.value = body.started_at ? body.started_at * 1000 : Date.now()
    nowTs.value = Date.now()
    pollFailCount.value = 0
    if (!clockTimer) clockTimer = setInterval(() => { nowTs.value = Date.now() }, 1000)
    if (body.billing_mode === 'platform' && body.estimated_price > 0) {
      ElMessage.info(`已启动，预估扣费 ¥${body.estimated_price.toFixed(2)}（按实际生成字数结算）`)
    } else {
      ElMessage.success('已启动')
    }
    // U1：通知 ProjectDetail 触发全局生成横幅（getStatus 轮询跟随 agent_run 占槽状态）
    emit('started', { task_id: body.task_id, agent })
    pollTimer.value = setInterval(poll, 2000)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  }
}

async function poll() {
  try {
    const body = await getAgentRun(props.pid, runningTaskId.value)
    if (body.status === 'running' || body.status === 'resumed') {
      // U1：刷新进度（后端 task.json progress + elapsed）；成功轮询清零失败计数
      if (body.progress) {
        runProgress.value = body.progress
        // 同步到统一进度 store：让顶部单一进度条跟随真实进度（不再依赖任务视角推断）
        applyAgentProgress(body.progress, body.agent)
      }
      if (body.started_at) runStartedAt.value = body.started_at * 1000
      pollFailCount.value = 0
      return
    }
    stopPolling()
    if (body.status === 'done') {
      // F1：质检未过时保留上一版、新稿为候选——明确提示，不再只报「已重新生成」
      const chapterFb = (body.outputs || []).filter((o) => o.kind === 'chapter' && o.kept_previous)
      const billed = body.billing?.billed_price
      if (chapterFb.length) {
        const lines = chapterFb.map((o) => {
          const q = o.quality
          const score = (q && q.score != null)
            ? `（质检 ${q.score}${q.passed ? ' 通过' : ' 未过'}）` : ''
          return `第${o.no}章质检未通过${score}，已保留上一版，新稿存为候选 v${o.candidate_version}，可在版本弹窗查看/切换`
        })
        ElMessage.warning(lines.join('；'))
        if (billed) ElMessage.info(`本次扣费 ¥${Number(billed).toFixed(2)}`)
      } else {
        ElMessage.success(billed ? `完成，本次扣费 ¥${Number(billed).toFixed(2)}` : '完成')
      }
    } else if (body.status === 'stopped') {
      ElMessage.warning(body.error || '已停止（已完成部分保留）')
    } else {
      ElMessage.error(body.error || '运行失败')
    }
    emit('changed')
    refreshBilling()
  } catch {
    // U5：网络抖动不立即停轮询；连续失败达到阈值才停止并明确提示
    pollFailCount.value += 1
    if (pollFailCount.value >= 3) {
      stopPolling()
      ElMessage.warning('网络异常，运行状态轮询已中断。请检查网络后刷新页面查看结果。')
    }
  }
}

function stopPolling() {
  if (pollTimer.value) clearInterval(pollTimer.value)
  pollTimer.value = null
  if (clockTimer) clearInterval(clockTimer)
  clockTimer = null
  running.value = false
  runningAgent.value = ''
  runningTaskId.value = ''
  runProgress.value = null
  runStartedAt.value = 0
  pollFailCount.value = 0
}

async function stopRun() {
  try {
    await ElMessageBox.confirm('确定停止当前 Agent 运行？当前未完成章节不会提交，已完成部分保留。', '停止运行', { type: 'warning' })
  } catch { return }
  try {
    await stopAgentRun(props.pid, runningTaskId.value)
    ElMessage.info('已请求停止')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '停止失败')
  }
}

// ---------- 人设编辑 ----------
const promptDialog = ref(false)
const promptSaving = ref(false)
const editingAgent = ref('')
const editingAgentName = ref('')
const editingText = ref('')
const editingVersion = ref('')
// 弹窗默认渲染态（可读），可切到编辑态改 Markdown 源码
const promptMode = ref('preview')
const renderedPrompt = computed(() => mdRenderer.render(editingText.value || ''))

function openPromptEditor(a) {
  editingAgent.value = a.key
  editingAgentName.value = a.name
  editingText.value = prompts.value[a.key]?.text || ''
  editingVersion.value = promptVersion(a.key)
  promptMode.value = 'preview'
  promptDialog.value = true
}

async function savePrompt() {
  if (!editingText.value.trim()) {
    ElMessage.warning('人设内容不能为空')
    return
  }
  promptSaving.value = true
  try {
    const body = await saveAgentPrompt(props.pid, editingAgent.value, editingText.value.trim())
    ElMessage.success(`已保存为 ${body.version}`)
    promptDialog.value = false
    await loadPrompts()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    promptSaving.value = false
  }
}

// ---------- U2：预估金额随参数变化实时刷新（防抖，避免每次输入都请求） ----------
async function refreshEstimate() {
  if (running.value) return
  const agent = selectedAgent.value
  try {
    const body = await estimateAgentRun(props.pid, { agent, params: buildParams(agent) })
    estimates[agent] = body.estimated_price || 0
  } catch { /* 参数非法等：保留旧预估值 */ }
}
watch(
  [selectedAgent, chapterScope, chNo, fromCh, toCh, toChOpen, volNo],
  () => {
    clearTimeout(estTimer)
    estTimer = setTimeout(refreshEstimate, 400)
  },
  // U1-B：immediate 让首屏就请求预估价，不再等用户改参数才出现
  { immediate: true },
)

// ---------- F3：首次使用该入口给一次计费口径提示 ----------
const BILLING_NOTICE_KEY = 'mozao_agent_billing_notice_v1'

// ---------- 数据加载 ----------
async function loadPrompts() {
  try {
    const res = await getAgentPrompts(props.pid)
    prompts.value = res.prompts || {}
  } catch {
    prompts.value = {}
  }
}

async function refreshBilling() {
  try {
    const me = await getBalance()
    // /api/me 返回 { user: {...} }，余额在 user 下；直接读顶层会得到 undefined→0，误判余额不足
    balance.value = Number(me?.user?.balance ?? 0)
  } catch { /* 降级：余额展示保持旧值 */ }
  try {
    const llm = await getMyLlm()
    billingMode.value = (llm?.api_key && llm?.provider) ? 'user_llm' : 'platform'
  } catch { /* 降级：按平台计费展示 */ }
}

onMounted(() => {
  loadPrompts()
  refreshBilling()
  if (!localStorage.getItem(BILLING_NOTICE_KEY)) {
    ElMessage.info('提示：重生成与候选版本同样按本次生成字数计费；余额不足时自动停止')
    localStorage.setItem(BILLING_NOTICE_KEY, '1')
  }
})
onBeforeUnmount(() => {
  stopPolling()
  clearTimeout(estTimer)
})
</script>

<style scoped>
/* 两栏工作区：左导航 + 右工作区；与「小墨AI」面板同为侧栏式布局，保持一致的信息结构 */
.agent-workspace {
  display: grid;
  grid-template-columns: 236px minmax(0, 1fr);
  gap: 0 28px;
  max-width: 1080px;
  padding-top: 6px;
}

/* ---------- 左栏：角色导航（列表而非卡片，选中态用金线标记） ---------- */
.agent-rail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 24px;
  border-right: 1px solid var(--card-line);
}
.rail-title { font-size: 12px; color: var(--muted); letter-spacing: .08em; }
.rail-list { display: flex; flex-direction: column; gap: 2px; }
.rail-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-left: 2px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color .15s ease, border-color .15s ease;
}
.rail-item:hover { background: rgba(35, 47, 85, .05); }
.rail-item.active { background: var(--indigo-soft); border-left-color: var(--gold); }
.rail-item-main { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.rail-name { font-size: 14px; font-weight: 600; }
.rail-version { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.rail-desc {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.4;
  color: var(--muted);
}
.rail-running { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--gold-deep); }

/* ---------- 计费环境：常驻左栏底部，替代原先横贯顶部的告警条 ---------- */
.rail-billing {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
  padding-top: 14px;
  border-top: 1px solid var(--card-line);
}
.billing-line { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--ink-soft); }
.billing-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--gold); flex-shrink: 0; }
.billing-dot.user_llm { background: var(--ok); }
.billing-detail { font-size: 11px; line-height: 1.55; color: var(--muted); }
.billing-balance {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--ink-soft);
  font-variant-numeric: tabular-nums;
}
.billing-balance.danger { color: var(--danger); }
.billing-warn { font-size: 11px; color: var(--danger); }

/* ---------- 右栏：当前角色工作区 ---------- */
.agent-stage { min-width: 0; padding-top: 2px; }
.stage-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.stage-title { display: flex; align-items: center; gap: 10px; }
.stage-title h3 { font-family: var(--serif); font-size: 19px; font-weight: 600; }
.stage-head-actions { display: flex; align-items: center; gap: 10px; }
.stage-running { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--gold-deep); }
.stage-desc { max-width: 620px; margin: 6px 0 22px; font-size: 13px; color: var(--ink-soft); }
.stage-field { margin-bottom: 18px; }
.field-label { margin-bottom: 7px; font-size: 12px; color: var(--muted); }
.field-optional { margin-left: 6px; font-size: 11px; color: #b9bdc9; }
.field-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 13px;
  color: var(--ink-soft);
}
.stage-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.action-hint { font-size: 12px; color: var(--muted); }
.action-hint.danger { color: var(--danger); }

/* ---------- U1：运行文字状态行（进度条已统一到顶部 GenerationProgress，此处不重复渲染） ---------- */
.run-statusline {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.run-status-ch { color: var(--ink-soft); }
.run-status-elapsed { color: var(--muted); }

/* 窄屏：状态行换行，避免与「停止运行」挤在一行溢出 */
@media (max-width: 520px) {
  .run-statusline { gap: 8px; }
}

/* ---------- 技能挂载：默认折叠，避免长期占据首屏 ---------- */
.stage-skills { margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--card-line); }
.skills-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  font-size: 13px;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}
.skills-count { font-size: 12px; color: var(--muted); }
.skills-chevron {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted);
  transition: transform .18s ease;
}
.skills-chevron.open { transform: rotate(180deg); }
.skills-body { margin-top: 10px; }
.skills-hint { margin-bottom: 8px; font-size: 12px; color: var(--muted); }

/* 角色切换只做 140ms 位移淡入：提示层级变化，不制造噪声 */
.stage-enter-active, .stage-leave-active { transition: opacity .14s ease, transform .14s ease; }
.stage-enter-from { opacity: 0; transform: translateY(4px); }
.stage-leave-to { opacity: 0; }

.pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--gold);
  animation: agent-pulse 1.4s ease-in-out infinite;
}
@keyframes agent-pulse {
  0%, 100% { opacity: .35; transform: scale(.85); }
  50% { opacity: 1; transform: scale(1.15); }
}

.prompt-tip { margin-bottom: 10px; }
.prompt-mode-bar { margin-bottom: 10px; }
.prompt-preview {
  max-height: 420px;
  overflow-y: auto;
  padding: 14px 18px;
  border: 1px solid var(--card-line);
  border-radius: 8px;
  background: var(--card);
  color: var(--ink);
  font-size: 14px;
  line-height: 1.7;
}
.markdown-body :deep(:first-child) { margin-top: 0; }
.markdown-body :deep(:last-child) { margin-bottom: 0; }
/* 小弹窗里标题层级要克制：与正文字号差控制在 1-2px，去掉下划线避免视觉跳脱 */
.markdown-body :deep(h1) { font-size: 16px; margin: 14px 0 8px; font-weight: 700; }
.markdown-body :deep(h2) {
  font-size: 15px; margin: 12px 0 6px; font-weight: 700;
}
.markdown-body :deep(h3) { font-size: 14px; margin: 10px 0 6px; font-weight: 700; }
.markdown-body :deep(p) { margin: 6px 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { margin: 6px 0; padding-left: 22px; }
.markdown-body :deep(li) { margin: 3px 0; }
.markdown-body :deep(blockquote) {
  margin: 8px 0; padding: 6px 12px;
  border-left: 3px solid var(--gold);
  background: var(--gold-soft);
  color: var(--ink-soft);
}
.markdown-body :deep(code) {
  padding: 2px 6px; border-radius: 4px;
  background: var(--indigo-soft);
  font-family: ui-monospace, Consolas, monospace; font-size: 13px;
}
.markdown-body :deep(pre) {
  padding: 10px 12px; border-radius: 6px; overflow-x: auto;
  background: var(--indigo-soft);
}
.markdown-body :deep(pre code) { padding: 0; background: none; }
.markdown-body :deep(a) { color: var(--gold-deep); }
.markdown-body :deep(strong) { color: var(--ink); font-weight: 700; }

@media (max-width: 860px) {
  .agent-workspace { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .agent-rail {
    padding-right: 0;
    padding-bottom: 14px;
    border-right: 0;
    border-bottom: 1px solid var(--card-line);
  }
  .rail-list { flex-direction: row; gap: 6px; overflow-x: auto; padding-bottom: 2px; }
  .rail-item { flex: 0 0 auto; width: 172px; }
  .rail-desc { -webkit-line-clamp: 1; }
  .rail-billing { margin-top: 0; }
  .agent-stage { padding-top: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .pulse { animation: none; }
  .stage-enter-active, .stage-leave-active { transition: none; }
}
</style>
