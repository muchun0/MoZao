import { reactive, computed } from 'vue'

// 小说生成流程的【单一进度数据源】（修复双进度条 / 进度不真实 / 文案滞后）。
// ProjectDetail（顶部横幅）与 AgentPanel（Agent 页）共享同一份状态，
// 不再各自轮询、各自维护进度条，避免出现两个进度条或文案打架。
//
// 关键设计：
// 1. 真实进度优先：来自 task.json 的 task_done/task_total（任意 scope 0→100%）。
//    后端已在 GET /projects/{id} 暴露（见 webapp/routers/projects_base.py），
//    不再只用 chapters_done/chapters_total 反推——大纲阶段 chapters_done 恒为 0，是 0% 卡死的根因。
// 2. 无真实进度时（大纲/世界观等前置阶段）用渐进逼近曲线平滑递增，逼近 90% 上限，
//    绝不模拟到 100%，避免"看起来跑完了"但其实还在跑的欺骗感；也不长时间停滞。
// 3. 阶段切换立即更新文案，并重置估算基线（以当前显示值为新起点），避免回退或跳变。

// 阶段 → 预估时间常数 τ（秒）：决定平滑递增的快慢，越长表示越"重"的阶段
const PHASE_TAU = {
  research: 40,
  outline: 150,
  editor_review: 60,
  world: 90,
  volume: 60,
  chapter: 120,
}
const DEFAULT_TAU = 120
// 预估上限：进度条最多走到 90%，真实 100% 只由后端精确进度或完成事件触发
const EST_CEILING = 90

// 阶段 → 文案（与后端 _update_task_progress 的 phase 取值一致）
const PHASE_LABEL = {
  research: '正在调研热门题材',
  outline: '正在生成大纲（分批进行，约几分钟）',
  editor_review: '正在审稿优化大纲',
  world: '正在生成世界观与人物',
  volume: '正在重写卷纲',
  chapter: '正在逐章写作',
}

// 旧状态机的 phase 别名归一化（core_generate 的 state.phase 取值）
const PHASE_ALIAS = { writing: 'chapter', outline_done: 'outline', world_done: 'world' }

// agent 标识 → 阶段（onAgentStarted 启动时先给一个合理阶段，首个轮询到达后由真实 phase 接管）
const AGENT_PHASE = {
  researcher: 'research',
  'architect.outline': 'outline',
  'architect.world': 'world',
  'architect.volume': 'volume',
  'writer.chapter': 'chapter',
}

const state = reactive({
  running: false,
  finished: false,
  phase: '',
  agent: '',
  taskId: '',
  label: '',          // 启动时手动指定的细分文案（如"正在重生成大纲"）；阶段切换即清空，交给真实 phase 文案
  hasExact: false,     // 是否有后端精确进度
  exactPercent: 0,
  estPercent: 0,      // 估算动画当前展示值（由 ticker 写回，保证响应式刷新）
  estFrom: 0,         // 阶段/精确态切换时的起算基线（当前展示值）
  estStartedAt: 0,
  chaptersDone: 0,
  chaptersTotal: 0,
  currentChapter: 0,
  startedAt: 0,       // 毫秒；优先用后端 started_at（秒）
  error: '',
  now: Date.now(),
})

let tickerTimer = null
let finishTimer = null

function startTicker() {
  if (tickerTimer) return
  tickerTimer = setInterval(() => {
    state.now = Date.now()
    // 估算态下随计时推进重算展示进度（百分比本身不在精确态才用）
    if (!state.hasExact && !state.finished) {
      state.estPercent = computeEstPercent()
    }
  }, 500)
}
function stopTicker() {
  if (tickerTimer) { clearInterval(tickerTimer); tickerTimer = null }
}

// 计算当前应展示的估算百分比（纯函数，供 ticker 回写）
function computeEstPercent() {
  const elapsed = Math.max(0, (Date.now() - state.estStartedAt) / 1000)
  const tau = PHASE_TAU[state.phase] || DEFAULT_TAU
  // 从 estFrom 起步，按 (1 - e^(-t/τ)) 渐近逼近 EST_CEILING，永不越过上限
  const v = state.estFrom + (EST_CEILING - state.estFrom) * (1 - Math.exp(-elapsed / tau))
  return Math.round(Math.min(EST_CEILING, Math.max(state.estFrom, v)))
}

// 阶段切换：立即换文案，并以当前展示值作为新基线，避免回退/跳变
function setPhase(phase) {
  const p = PHASE_ALIAS[phase] || phase
  if (!p || p === state.phase) return
  state.estFrom = displayPercentNow()
  state.estStartedAt = Date.now()
  state.phase = p
  state.label = '' // 阶段以真实 phase 文案为准，丢弃启动时的细分文案
}

// 切换"精确/估算"态时，把估算基线对齐到当前展示值，防止 0% 重置式跳变
function syncEstBaseline() {
  if (!state.hasExact) state.estFrom = displayPercentNow()
  state.estStartedAt = Date.now()
}

function displayPercentNow() {
  if (state.finished) return 100
  if (state.hasExact) return state.exactPercent
  return state.estPercent
}

function markFinished() {
  state.hasExact = true
  state.exactPercent = 100
  state.finished = true
  state.running = false
  clearTimeout(finishTimer)
  finishTimer = setTimeout(() => {
    state.finished = false
    state.hasExact = false
    state.exactPercent = 0
    state.label = ''
    state.phase = ''
    state.estPercent = 0
    state.estFrom = 0
    stopTicker()
  }, 1500)
}

// ---- 外部调用入口 ----

// 用户点击启动类按钮：立即给出进行中的横幅，不必等轮询
export function begin({ label = '', phase = '', taskId = '', agent = '' } = {}) {
  state.running = true
  state.finished = false
  state.hasExact = false
  state.exactPercent = 0
  state.estPercent = 0
  state.estFrom = 0
  state.estStartedAt = Date.now()
  state.chaptersDone = 0
  state.chaptersTotal = 0
  state.currentChapter = 0
  state.error = ''
  state.label = label
  state.phase = (PHASE_ALIAS[phase] || phase) || ''
  state.taskId = taskId
  state.agent = agent || state.agent
  clearTimeout(finishTimer)
  startTicker()
}

// 来自 getAgentRun（running 态）的精确进度，优先采用
export function applyAgentProgress(progress, agent) {
  if (!progress) return
  if (agent) state.agent = agent
  const phase = progress.phase || state.phase || ''
  if (phase) setPhase(phase)
  if (progress.chapters_total != null) state.chaptersTotal = progress.chapters_total
  if (progress.chapters_done != null) state.chaptersDone = progress.chapters_done
  if (progress.current_chapter != null) state.currentChapter = progress.current_chapter
  if (progress.started_at && !state.startedAt) state.startedAt = progress.started_at * 1000

  const taskTotal = Number(progress.task_total || 0)
  const hadExact = state.hasExact
  if (taskTotal > 0) {
    state.hasExact = true
    state.exactPercent = Math.min(100, Math.round((Number(progress.task_done || 0) / taskTotal) * 100))
  } else if (Number(progress.chapters_total || 0) > 0) {
    state.hasExact = true
    state.exactPercent = Math.min(100, Math.round(
      (Number(progress.chapters_done || 0) / Number(progress.chapters_total)) * 100))
  } else {
    state.hasExact = false
  }
  if (hadExact !== state.hasExact) syncEstBaseline()
}

// 来自 GET /projects/{id} 的兜底进度（含新增的 progress 字段），与 applyAgentProgress 互斥补足
export function applyStatus(d) {
  if (!d) return
  const wasActive = state.running || state.finished
  state.running = !!d.running
  state.error = d.error || ''
  if (d.chapters_total != null) state.chaptersTotal = d.chapters_total
  if (d.chapters_done != null) state.chaptersDone = d.chapters_done
  if (d.task_id) state.taskId = d.task_id
  if (d.agent) state.agent = d.agent

  const p = (d.progress && typeof d.progress === 'object') ? d.progress : {}
  const phase = p.phase || (d.state && d.state.phase) || ''
  if (phase) setPhase(phase)

  if (d.running) {
    state.finished = false
    clearTimeout(finishTimer)
    startTicker()
    const hadExact = state.hasExact
    // 精确进度优先任务视角，回退书级完成度（仅当 agent 未提供更精确的 task_total 时采用）
    const taskTotal = Number(p.task_total || 0)
    if (taskTotal > 0) {
      state.hasExact = true
      state.exactPercent = Math.min(100, Math.round((Number(p.task_done || 0) / taskTotal) * 100))
    } else if (Number(p.chapters_total || 0) > 0) {
      state.hasExact = true
      state.exactPercent = Math.min(100, Math.round((Number(p.chapters_done || 0) / Number(p.chapters_total)) * 100))
    }
    // 否则 hasExact 维持原值（由 applyAgentProgress 决定）
    if (hadExact !== state.hasExact) syncEstBaseline()
  } else if (wasActive) {
    // 之前在跑，现在停了：触发完成态（弹 100% 一下再隐藏），并刷新由调用方负责
    markFinished()
    // 调用方（startPolling 的 onDone/默认刷新）应在此之后刷新数据
  }
}

// 取消/重置：横幅立即收起
export function reset() {
  state.running = false
  state.finished = false
  state.hasExact = false
  state.exactPercent = 0
  state.estPercent = 0
  state.estFrom = 0
  state.label = ''
  state.phase = ''
  state.taskId = ''
  state.error = ''
  clearTimeout(finishTimer)
  stopTicker()
}

// 外部读取（只读 computed）
export const isRunning = computed(() => state.running)
export const isVisible = computed(() => state.running || state.finished || !!state.error)

export const percent = computed(() => {
  if (state.finished) return 100
  if (state.hasExact) return state.exactPercent
  return state.estPercent
})

export const statusText = computed(() => {
  if (state.label) return state.label
  return PHASE_LABEL[state.phase] || '正在生成'
})

export const chapterText = computed(() => {
  if (state.phase === 'chapter' && state.chaptersTotal > 0) {
    const cur = state.currentChapter || state.chaptersDone || 0
    return `第 ${cur} / ${state.chaptersTotal} 章`
  }
  return ''
})

export const elapsedText = computed(() => {
  const start = state.startedAt || state.estStartedAt
  if (!start) return ''
  const s = Math.floor((state.now - start) / 1000)
  if (s <= 0) return '0秒'
  return s >= 60 ? `${Math.floor(s / 60)}分${s % 60}秒` : `${s}秒`
})

export const errorText = computed(() => state.error)
export const isError = computed(() => !!state.error && !state.running)

// 便捷导出：AGENT → 阶段、PHASE_LABEL（供调用方推导启动阶段）
export { AGENT_PHASE, PHASE_LABEL }
