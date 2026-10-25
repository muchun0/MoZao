<template>
  <div class="agent-panel">
    <!-- 计费模式标识（TECH §10.2：不再按是否配置 Key 分流，所有用户可见） -->
    <el-alert :closable="false" class="billing-banner" :type="billingMode === 'platform' ? 'warning' : 'success'">
      <template #title>
        <template v-if="billingMode === 'user_llm'">自备 Key · 平台不扣费（使用您自己的模型额度）</template>
        <template v-else>平台计费 · 按 AI 实际生成字数扣费（普通 2 元/万字，高质量 3 元/万字；候选/重写版本同样计费），当前余额 ¥{{ balance.toFixed(2) }}</template>
      </template>
    </el-alert>
    <div v-if="billingMode === 'platform' && balance <= 0" class="balance-block">
      <span>余额不足，Agent 生成入口已禁用。</span>
      <el-button size="small" type="primary" @click="$emit('recharge')">去充值</el-button>
    </div>

    <SkillPicker v-model="skillIds" class="panel-skills" />

    <div class="agent-cards">
      <div v-for="a in AGENTS" :key="a.key" class="agent-card">
        <div class="agent-head">
          <span class="agent-name">{{ a.name }}</span>
          <el-tag size="small" type="info">{{ promptVersion(a.key) }}</el-tag>
          <el-button size="small" link type="primary" @click="openPromptEditor(a)">编辑人设</el-button>
        </div>
        <div class="agent-desc">{{ a.desc }}</div>

        <!-- writer.chapter 参数：scope 三态（§4.1 params schema） -->
        <template v-if="a.key === 'writer.chapter'">
          <el-radio-group v-model="chapterScope" size="small" class="agent-params">
            <el-radio-button value="all">全部未完成章</el-radio-button>
            <el-radio-button value="single">单章</el-radio-button>
            <el-radio-button value="range">区间</el-radio-button>
          </el-radio-group>
          <div v-if="chapterScope === 'single'" class="agent-params">
            第 <el-input-number v-model="chNo" :min="1" size="small" /> 章
          </div>
          <div v-else-if="chapterScope === 'range'" class="agent-params">
            第 <el-input-number v-model="fromCh" :min="1" size="small" /> 章 至
            第 <el-input-number v-model="toCh" :min="fromCh" size="small" /> 章（可留空到末尾）
            <el-checkbox v-model="toChOpen" size="small" @change="(v) => { if (v) toCh = 0 }">到末尾</el-checkbox>
          </div>
        </template>
        <div v-else-if="a.key === 'architect.volume'" class="agent-params">
          分卷号 <el-input-number v-model="volNo" :min="1" size="small" />
        </div>

        <el-input
          v-model="userPrompts[a.key]"
          type="textarea" :rows="2" maxlength="2000" show-word-limit
          placeholder="补充指令（可选）：本次生成的额外要求，如「加强冲突」「节奏加快」"
          class="agent-prompt"
        />

        <div class="agent-actions">
          <template v-if="running && runningAgent === a.key">
            <el-button size="small" type="danger" @click="stopRun">停止</el-button>
            <span class="agent-status">运行中…</span>
          </template>
          <el-button
            v-else
            size="small" type="primary"
            :disabled="entryDisabled"
            @click="startRun(a.key)"
          >运行</el-button>
          <span v-if="estimates[a.key] > 0" class="agent-est">预估 ¥{{ estimates[a.key].toFixed(2) }}</span>
        </div>
      </div>
    </div>

    <!-- 人设编辑对话框：保存即新建不可变版本（author=user） -->
    <el-dialog v-model="promptDialog" :title="`编辑人设 · ${editingAgentName}`" width="640px">
      <el-alert :closable="false" type="info" class="prompt-tip">
        <template #title>
          人设将作为 system 提示词注入该 Agent（追加到默认模板末尾）。保存即生成新版本，历史版本不可变、可追溯；当前版本 {{ editingVersion }}。请勿写入越权指令，输出仍受平台合规校验约束。
        </template>
      </el-alert>
      <el-input v-model="editingText" type="textarea" :rows="10" maxlength="8000" show-word-limit />
      <template #footer>
        <el-button @click="promptDialog = false">取消</el-button>
        <el-button type="primary" :loading="promptSaving" @click="savePrompt">保存为新版本</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
// Agent 卡片面板（TECH-Agent用户化与版本化 §10.2，阶段 1C）：
// 5 个可独立触发的 agent 卡片（人设编辑/参数/补充指令/运行/停止/进度轮询）+
// 计费模式标识与预估金额 + 平台模式余额 ≤0 禁用引导充值。
// 旧「生成/重生成」按钮（平台通道）保留在 ProjectDetail 顶部，本面板是新增的自助通道。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { startAgentRun, getAgentRun, stopAgentRun, getMyLlm, getBalance } from '../../api/project'
import { getAgentPrompts, saveAgentPrompt } from '../../api/novel'
import SkillPicker from './SkillPicker.vue'

const props = defineProps({
  pid: { type: String, required: true },
})
const emit = defineEmits(['changed', 'recharge'])

const AGENTS = [
  { key: 'researcher', name: '题材调研', desc: '联网调研题材共性，产出写作建议与参考资料' },
  { key: 'architect.outline', name: '大纲架构', desc: '设计/重生成全书大纲（结构、节奏、冲突递进）' },
  { key: 'architect.world', name: '世界观架构', desc: '构建/重生成世界观、人物卡与伏笔体系' },
  { key: 'architect.volume', name: '卷纲架构', desc: '重写指定分卷的简介与章规划' },
  { key: 'writer.chapter', name: '章节写手', desc: '按范围生成章节正文（逐章结算、余额不足自动停止）' },
]

// ---------- 计费模式 / 余额 ----------
const billingMode = ref('platform')
const balance = ref(0)

// ---------- 人设版本 ----------
const prompts = ref({})
function promptVersion(agent) {
  return prompts.value[agent]?.version || `${agent}@v1`
}

// ---------- 运行参数 ----------
const skillIds = ref([])
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

const entryDisabled = computed(() =>
  running.value || (billingMode.value === 'platform' && balance.value <= 0))

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
    if (body.billing_mode === 'platform' && body.estimated_price > 0) {
      ElMessage.info(`已启动，预估扣费 ¥${body.estimated_price.toFixed(2)}（按实际生成字数结算）`)
    } else {
      ElMessage.success('已启动')
    }
    pollTimer.value = setInterval(poll, 2000)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  }
}

async function poll() {
  try {
    const body = await getAgentRun(props.pid, runningTaskId.value)
    if (body.status === 'running' || body.status === 'resumed') return
    stopPolling()
    if (body.status === 'done') {
      const billed = body.billing?.billed_price
      ElMessage.success(billed ? `完成，本次扣费 ¥${Number(billed).toFixed(2)}` : '完成')
    } else if (body.status === 'stopped') {
      ElMessage.warning(body.error || '已停止（已完成部分保留）')
    } else {
      ElMessage.error(body.error || '运行失败')
    }
    emit('changed')
    refreshBilling()
  } catch {
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer.value) clearInterval(pollTimer.value)
  pollTimer.value = null
  running.value = false
  runningAgent.value = ''
  runningTaskId.value = ''
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

function openPromptEditor(a) {
  editingAgent.value = a.key
  editingAgentName.value = a.name
  editingText.value = prompts.value[a.key]?.text || ''
  editingVersion.value = promptVersion(a.key)
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
    balance.value = Number(me?.balance ?? 0)
  } catch { /* 降级：余额展示保持旧值 */ }
  try {
    const llm = await getMyLlm()
    billingMode.value = (llm?.api_key && llm?.provider) ? 'user_llm' : 'platform'
  } catch { /* 降级：按平台计费展示 */ }
}

onMounted(() => {
  loadPrompts()
  refreshBilling()
})
onBeforeUnmount(stopPolling)
</script>

<style scoped>
.agent-panel { padding: 4px 0; }
.billing-banner { margin-bottom: 10px; }
.balance-block {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  color: var(--el-color-danger);
  font-size: 13px;
}
.panel-skills { margin-bottom: 14px; }
.agent-cards { display: flex; flex-direction: column; gap: 10px; }
.agent-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
}
.agent-head { display: flex; align-items: center; gap: 8px; }
.agent-name { font-weight: 600; }
.agent-desc { color: var(--el-text-color-secondary); font-size: 12px; margin: 4px 0 8px; }
.agent-params { margin-bottom: 8px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.agent-prompt { margin-bottom: 8px; }
.agent-actions { display: flex; align-items: center; gap: 10px; }
.agent-status { color: var(--el-color-primary); font-size: 12px; }
.agent-est { color: var(--el-color-warning); font-size: 12px; }
.prompt-tip { margin-bottom: 10px; }
</style>
