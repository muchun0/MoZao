<template>
  <!-- R27 分步确认式生成流：步骤条 + 当前确认卡 + 模式切换（状态全部来自后端） -->
  <section v-if="visible" class="gen-flow">
    <header class="gf-head">
      <div class="gf-steps">
        <span v-for="s in stages" :key="s.key" class="gf-step" :class="`is-${s.status}`" :title="stepTitle(s)">
          <span class="gf-dot" />{{ s.label }}
        </span>
      </div>
      <div class="gf-mode">
        <span class="gf-mode-label">生成方式</span>
        <el-radio-group :model-value="mode" size="small" :disabled="busy || !supported"
                        @update:model-value="changeMode">
          <el-radio-button value="stepwise">分步确认</el-radio-button>
          <el-radio-button value="auto">全自动</el-radio-button>
        </el-radio-group>
      </div>
    </header>

    <div v-if="!supported" class="gf-tip">扩写项目不使用分步确认流程</div>
    <div v-else-if="!flow" class="gf-empty">
      <span v-if="mode === 'stepwise'" class="gf-tip">新书默认分步：调研 → 大纲 → 世界观各阶段完成后停下等你确认，正文开跑前再确认一次。</span>
      <span v-else class="gf-tip">当前为全自动：沿用页面现有生成按钮，阶段之间不暂停。</span>
      <el-button v-if="mode === 'stepwise'" type="primary" size="small" :loading="busy"
                 @click="startFlow">开始分步生成</el-button>
    </div>
    <template v-else>
      <StageConfirmCard v-if="card" :card="card" :busy="busy"
                        @continue="onContinue" @regenerate="onRegenerate"
                        @edit="onEdit" @back-outline="onBackOutline" />
      <div v-else-if="runningStage && flow.plan_status === 'running' && flow.active_run" class="gf-tip">
        正在执行「{{ runningStage.label }}」…完成后会停在这里等你确认
      </div>
      <div v-else-if="runningStage && flow.plan_status === 'running'" class="gf-tip">
        「{{ runningStage.label }}」下一步待继续，点下方「一键全部生成」接着跑。
      </div>
      <div v-else-if="flow.plan_status === 'done'" class="gf-tip">全流程已完成，可导出成书。</div>
      <div v-else class="gf-tip">计划已暂停（{{ planStatusText }}），处理阻塞或调整模式后可继续。</div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getGenerationFlow, startGenerationFlow, confirmGenerationStage } from '../../api/flow'
import { setGenerationMode } from '../../api/flow'
import { errorDetail } from '../../utils/agentRunClient'
import StageConfirmCard from './StageConfirmCard.vue'

const props = defineProps({
  pid: { type: String, required: true },
})
const emit = defineEmits(['changed', 'edit'])

const data = ref(null)
const busy = ref(false)
let timer = null
let lastStatus = ''

const supported = computed(() => !!data.value?.supported)
const mode = computed(() => data.value?.mode || 'auto')
const flow = computed(() => data.value?.flow || null)
const stages = computed(() => flow.value?.stages || [])
const card = computed(() => (flow.value?.cards || [])[0] || null)
const runningStage = computed(() => stages.value.find((s) => s.status === 'running') || null)
const visible = computed(() => !!data.value && (supported.value || mode.value === 'stepwise'))
const planStatusText = computed(() => {
  const map = { planning: '规划中', running: '运行中', waiting: '等待中', blocked: '已阻塞',
    stopped: '已停止', failed: '失败', aborted: '已终止' }
  const s = flow.value?.plan_status
  return map[s] || s || '未知'
})

const stepTitle = (s) => {
  const map = {
    pending: '未开始', ready: '待确认开始', running: '执行中', done: '待确认',
    confirmed: '已确认', failed: '失败', stopped: '已停止', skipped: '已跳过',
  }
  return `${s.label}：${map[s.status] || s.status}${s.reason === 'merged_into_outline' ? '（并入大纲）' : ''}`
}

async function load() {
  try {
    const res = await getGenerationFlow(props.pid)
    data.value = res
    const marker = `${res?.flow?.flow_id || ''}:${(res?.flow?.stages || []).map((s) => s.status).join(',')}:${(res?.flow?.cards || []).length}`
    if (lastStatus && marker !== lastStatus) emit('changed')  // 状态变化才让父页面刷新数据
    lastStatus = marker
  } catch {
    // 只读状态拉取失败不打断页面：保持上一次渲染
  }
}

function startPoll() {
  stopPoll()
  timer = setInterval(load, 5000)
}
function stopPoll() {
  if (timer) { clearInterval(timer); timer = null }
}

async function startFlow() {
  busy.value = true
  try {
    await startGenerationFlow(props.pid, mode.value)
    await load()
    ElMessage.success('已启动分步生成')
    emit('changed')
  } catch (e) {
    ElMessage.error(errorDetail(e, '启动失败'))
  } finally { busy.value = false }
}

async function changeMode(value) {
  if (!value || value === mode.value) return
  busy.value = true
  try {
    await setGenerationMode(props.pid, value)
    await load()
    ElMessage.success(value === 'auto' ? '已切换为全自动' : '已切换为分步确认')
    emit('changed')
  } catch (e) {
    ElMessage.error(errorDetail(e, '切换失败'))
  } finally { busy.value = false }
}

async function confirmAction(action, extra = {}) {
  if (!card.value) return
  busy.value = true
  try {
    await confirmGenerationStage(props.pid, {
      stage: card.value.stage, action, digest: card.value.digest || '', ...extra,
    })
    await load()
    emit('changed')
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (detail?.code === 'STAGE_CHANGED') {
      ElMessage.warning('产物已更新，已为你刷新到最新版本，请再确认一次')
      await load()
    } else {
      ElMessage.error(errorDetail(e, '操作失败'))
    }
  } finally { busy.value = false }
}

function onContinue() {
  confirmAction('continue')
}

async function onRegenerate(c) {
  const money = typeof c.regenerate_estimate === 'number' && c.regenerate_estimate > 0
    ? `，预估 ¥${c.regenerate_estimate.toFixed(2)}` : ''
  try {
    await ElMessageBox.confirm(
      `将删除本步现有产物并重新生成${money}。重生成会产生新版本并再次扣费，前序阶段不受影响。`,
      `放弃本步重生成 · ${c.title}`, { type: 'warning', confirmButtonText: '确认重生成' })
  } catch { return }
  confirmAction('regenerate', { force: true })
}

function onEdit(stage) {
  emit('edit', stage)
}

function onBackOutline() {
  confirmAction('back_outline')
}

onMounted(async () => {
  await load()
  startPoll()
})
onUnmounted(stopPoll)
</script>

<style scoped>
.gen-flow { margin: 8px 0 12px; padding: 10px 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-fill-color-blank); }
.gf-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.gf-steps { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.gf-step { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--el-text-color-secondary); }
.gf-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--el-border-color); }
.gf-step.is-running .gf-dot { background: var(--el-color-primary); }
.gf-step.is-done .gf-dot, .gf-step.is-ready .gf-dot { background: var(--el-color-warning); }
.gf-step.is-confirmed .gf-dot { background: var(--el-color-success); }
.gf-step.is-failed .gf-dot { background: var(--el-color-danger); }
.gf-step.is-running, .gf-step.is-done, .gf-step.is-ready { color: var(--el-text-color-primary); font-weight: 600; }
.gf-mode { display: inline-flex; align-items: center; gap: 8px; }
.gf-mode-label { font-size: 12px; color: var(--el-text-color-secondary); }
.gf-tip { font-size: 12px; color: var(--el-text-color-secondary); margin: 8px 0 0; }
.gf-empty { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
@media (max-width: 900px) {
  .gf-empty { flex-direction: column; align-items: flex-start; }
}
</style>
