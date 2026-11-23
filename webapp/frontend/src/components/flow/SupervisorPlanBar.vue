<template>
  <!-- R32 固化 Supervisor 计划条：只渲染 GET /supervisor 的只读投影，不发起任何写操作 -->
  <section v-if="visible" class="sup-bar" :class="{ 'is-shadow': shadow }">
    <header class="sb-head">
      <div class="sb-title">
        <span class="sb-badge">编排计划</span>
        <el-tag v-if="shadow" size="small" type="warning" effect="plain">影子模式（只记录，不生效）</el-tag>
        <el-tag v-else size="small" type="success" effect="plain">{{ modeLabel }}</el-tag>
        <span class="sb-status">{{ statusText }}</span>
      </div>
      <div class="sb-meta">
        <span v-if="cursorText" class="sb-cursor">{{ cursorText }}</span>
        <span v-if="budgetText" class="sb-budget">{{ budgetText }}</span>
        <el-button link size="small" @click="open = !open">
          {{ open ? '收起决策' : `编排决策（${decisions.length}）` }}
        </el-button>
      </div>
    </header>

    <div class="sb-stages">
      <span v-for="s in stages" :key="s.key" class="sb-stage" :class="`is-${s.status}`"
            :title="`${s.role || s.key} · ${statusMap[s.status] || s.status}`">
        <span class="sb-dot" />{{ stageLabel(s.key) }}
        <em class="sb-role">{{ s.role }}</em>
      </span>
      <span v-if="chapterText" class="sb-stage is-running">
        <span class="sb-dot" />{{ chapterText }}
        <em v-if="stepText" class="sb-role">{{ stepText }}</em>
      </span>
    </div>

    <p v-if="stopText" class="sb-stop">{{ stopText }}</p>

    <div v-if="roles.length" class="sb-roles">
      <span class="sb-sub">最近角色</span>
      <span v-for="r in roles" :key="`${r.ts}-${r.role}-${r.node_id}`" class="sb-role-chip">
        {{ roleLabel(r.role) }}<em>{{ r.node_id }}</em>
      </span>
    </div>

    <div v-if="open" class="sb-timeline">
      <p v-if="!decisions.length" class="sb-empty">暂无决策记录</p>
      <ol v-else>
        <li v-for="(d, i) in recentDecisions" :key="`${d.ts}-${i}`">
          <span class="tl-time">{{ shortTime(d.ts) }}</span>
          <span class="tl-act">{{ d.action }}</span>
          <span class="tl-node">{{ d.node_id || '—' }}</span>
          <span class="tl-reason">{{ d.reason_code }}</span>
          <span v-if="d.detail" class="tl-detail">{{ d.detail }}</span>
        </li>
      </ol>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getSupervisor } from '../../api/supervisor'

const props = defineProps({
  pid: { type: String, required: true },
})
const emit = defineEmits(['plan-state'])

const data = ref(null)
const open = ref(false)
const timer = ref(null)

const supported = computed(() => !!data.value?.supported)
const shadow = computed(() => data.value?.mode === 'shadow')
const plan = computed(() => data.value?.plan || null)
const visible = computed(() => supported.value)
const stages = computed(() => plan.value?.stages || [])
const decisions = computed(() => plan.value?.decisions || [])
const recentDecisions = computed(() => decisions.value.slice(-20).reverse())
const roles = computed(() => (data.value?.role_invocations || []).slice(-6).reverse())
const next = computed(() => data.value?.next_action || null)
const cc = computed(() => plan.value?.chapter_cycle || {})
const cursor = computed(() => plan.value?.cursor || {})
const budget = computed(() => plan.value?.budget || {})

const statusMap = {
  pending: '未开始', ready: '待开始', running: '执行中', done: '已完成',
  confirmed: '已确认', skipped: '已跳过', blocked: '已阻塞', failed: '失败',
}
const planStatusMap = {
  planning: '规划中', running: '运行中', waiting: '等待中',
  blocked: '已阻塞', done: '已完成', failed: '失败', aborted: '已终止',
}
const stageLabels = { research: '调研', outline: '大纲', world: '世界观', chapters: '正文' }
const roleLabels = {
  researcher: '调研', planner: '大纲', worldbuilder: '世界观', writer: '写作',
  reviewer: '审校', loremaster: '设定维护',
}
const modeLabels = { on: '已启用', shadow: '影子', off: '关闭' }

const stageLabel = (key) => stageLabels[key] || key
const roleLabel = (role) => roleLabels[role] || role || '—'
const modeLabel = computed(() => modeLabels[data.value?.mode] || data.value?.mode || '')

const statusText = computed(() => {
  if (!plan.value) return '未开始'
  const s = plan.value?.status
  return planStatusMap[s] || s || '—'
})

const cursorText = computed(() => {
  const c = cursor.value || {}
  if (!c.node_id) return ''
  const step = c.step ? ` · ${c.step}` : ''
  return `当前节点 ${c.node_id}${step}`
})

const chapterText = computed(() => {
  const cur = Number(cc.value?.current || 0)
  const to = Number(cc.value?.to || 0)
  if (!to || cur > to) return ''
  return `第 ${cur} / ${to} 章`
})

const stepText = computed(() => {
  const step = cc.value?.current_step
  if (!step) return ''
  const map = { write: '写作', review: '审校', repair: '修复', commit: '记账' }
  return map[step] || step
})

const budgetText = computed(() => {
  const b = budget.value || {}
  const parts = []
  if (b.max_steps) parts.push(`步数 ${b.steps_used || 0}/${b.max_steps}`)
  if (b.max_repairs) parts.push(`修复 ${b.repairs_used || 0}/${b.max_repairs}`)
  return parts.join(' · ')
})

const stopText = computed(() => {
  if (!plan.value) return '尚未启动生成，启动后显示计划与决策'
  const a = next.value
  if (!a) return ''
  const kind = a.kind
  if (kind === 'run') return a.role ? `下一步：${roleLabel(a.role)}（${a.node_id}）` : ''
  if (kind === 'confirm') return '等待你确认后继续'
  if (kind === 'blocked') return `已暂停：${reasonLabel(a.reason_code)}`
  if (kind === 'fail') return `失败：${reasonLabel(a.reason_code)}`
  if (kind === 'done') return '全书计划已完成'
  if (kind === 'wait') return `等待中：${reasonLabel(a.reason_code)}`
  return ''
})

function reasonLabel(code) {
  const map = {
    pending_review: '有待审工单（去「全书体检」处理）', handover_blocking: '有阻塞工单（去「全书体检」处理）',
    active_run: '有 run 在执行', stopped: '已停止',
    budget_steps: '步数预算用尽', budget_repairs: '修复预算用尽',
    repair_exhausted: '修复已达上限', chapter_repair_limit: '本章修复次数用尽',
    task_repair_limit: '任务修复次数用尽', stepwise_confirm_required: '分步确认',
    all_chapters_done: '全部章节完成',
  }
  return map[code] || code || '—'
}

function shortTime(ts) {
  if (!ts) return ''
  const s = String(ts)
  return s.length > 16 ? s.slice(5, 16).replace('T', ' ') : s
}

async function load() {
  try {
    const res = await getSupervisor(props.pid)
    data.value = res
    // 顶栏状态徽章要与计划真实状态一致（否则会出现「已完成」+「已暂停」并存）
    emit('plan-state', {
      status: res?.plan?.status || '',
      nextKind: res?.next_action?.kind || '',
      reason: res?.next_action?.reason_code || '',
    })
  } catch {
    // 只读观测失败不打断页面
  }
}

onMounted(() => {
  load()
  timer.value = setInterval(load, 5000)
})
onBeforeUnmount(() => {
  if (timer.value) clearInterval(timer.value)
})
</script>

<style scoped>
.sup-bar { margin: 8px 0 4px; padding: 10px 14px; border: 1px solid #e5e7eb; border-radius: 10px; background: #fafafa; }
.sup-bar.is-shadow { border-style: dashed; }
.sb-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.sb-title { display: flex; align-items: center; gap: 8px; }
.sb-badge { font-weight: 600; font-size: 13px; color: #111827; }
.sb-status { font-size: 12px; color: #6b7280; }
.sb-meta { display: flex; align-items: center; gap: 12px; font-size: 12px; color: #4b5563; }
.sb-stages { display: flex; align-items: center; gap: 14px; margin-top: 8px; flex-wrap: wrap; font-size: 12px; }
.sb-stage { display: inline-flex; align-items: center; gap: 6px; color: #9ca3af; }
.sb-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.sb-stage.is-done, .sb-stage.is-confirmed { color: #16a34a; }
.sb-stage.is-running { color: #2563eb; }
.sb-stage.is-failed, .sb-stage.is-blocked { color: #dc2626; }
.sb-stage.is-ready { color: #d97706; }
.sb-role { font-style: normal; font-size: 11px; color: #9ca3af; }
.sb-stop { margin: 8px 0 0; font-size: 12px; color: #b45309; }
.sb-roles { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; font-size: 12px; }
.sb-sub { color: #6b7280; }
.sb-role-chip { padding: 1px 8px; border-radius: 10px; background: #eef2ff; color: #3730a3; }
.sb-role-chip em { font-style: normal; margin-left: 4px; color: #6b7280; font-size: 11px; }
.sb-timeline { margin-top: 10px; border-top: 1px dashed #e5e7eb; padding-top: 8px; }
.sb-empty { margin: 0; font-size: 12px; color: #9ca3af; }
.sb-timeline ol { margin: 0; padding-left: 0; list-style: none; max-height: 220px; overflow: auto; }
.sb-timeline li { display: flex; gap: 8px; font-size: 12px; line-height: 1.8; color: #4b5563; }
.tl-time { color: #9ca3af; min-width: 86px; }
.tl-act { min-width: 44px; color: #111827; font-weight: 500; }
.tl-node { min-width: 110px; color: #2563eb; }
.tl-reason { min-width: 120px; }
.tl-detail { color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
