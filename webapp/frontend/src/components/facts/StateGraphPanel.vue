<template>
  <div class="sg">
    <header class="sg-head">
      <div>
        <h3 class="sg-title">状态图谱</h3>
        <p class="sg-sub">
          只读投影：同一份账本给出四张视图（世界事实 / 角色认知 / 发展线 / 可能分支），
          带内容指纹与来源规模。这里只看不改——要改请回「章节」或「世界观」。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
    </header>

    <el-empty v-if="off" description="状态图谱未启用（后台开关 state_graph 关闭）">
      <p class="sg-tip">后台「开关管理」打开 state_graph 后回到这里刷新即可，无需重新生成。</p>
    </el-empty>

    <template v-else>
      <div v-if="meta" class="sg-meta">
        <span class="sg-tag">版本 {{ meta.version }}</span>
        <span class="sg-tag">写到第 {{ meta.sources?.at_ch || 0 }} 章</span>
        <span class="sg-tag">事件 {{ meta.sources?.events ?? 0 }}</span>
        <span class="sg-tag">伏笔 {{ meta.sources?.threads ?? 0 }}</span>
        <span class="sg-tag">术语 {{ meta.sources?.terms ?? 0 }}</span>
        <span class="sg-tag">纪律 {{ disciplineGroups.length }}</span>
        <el-tag v-if="meta.sources?.stale" type="warning" size="small" effect="plain">上游已更新</el-tag>
      </div>

      <el-tabs v-model="tab" class="sg-tabs">
        <!-- 世界事实 -->
        <el-tab-pane label="世界事实" name="canon">
          <div class="sg-block">
            <h4 class="sg-h4">术语 <span class="sg-count">{{ canon.terms?.length || 0 }}</span></h4>
            <el-table v-if="canon.terms?.length" :data="canon.terms" size="small" border>
              <el-table-column prop="term" label="词条" width="180" />
              <el-table-column prop="canonical" label="规范写法" width="180" />
              <el-table-column label="分类" width="120">
                <template #default="{ row }">{{ categoryText(row.category) }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="还没有术语（世界观生成或同步后出现）" :image-size="56" />
          </div>

          <div class="sg-block">
            <h4 class="sg-h4">永久纪律 <span class="sg-count">{{ disciplineGroups.length }} 条</span></h4>
            <p v-if="disciplineGroups.length" class="sg-hint">
              同一条纪律只显示一次，右侧是它被逐章重复登记的命中次数，点「看示例」查看具体章节原句。
            </p>
            <div v-if="disciplineGroups.length" class="sg-disc">
              <div v-for="(g, i) in disciplineGroups" :key="i" class="sg-disc-row">
                <span class="sg-disc-rule">{{ g.rule }}</span>
                <span v-if="g.examples.length" class="sg-dim">命中 {{ g.examples.length }} 次</span>
                <el-popover v-if="g.examples.length" placement="top" width="560" trigger="click">
                  <template #reference>
                    <el-button link type="primary" size="small">看示例</el-button>
                  </template>
                  <ul class="sg-pop-list">
                    <li v-for="(ex, j) in g.examples.slice(0, 5)" :key="j">{{ ex }}</li>
                  </ul>
                  <p v-if="g.examples.length > 5" class="sg-dim">仅显示前 5 条，共 {{ g.examples.length }} 条。</p>
                </el-popover>
              </div>
            </div>
            <el-empty v-else description="还没有登记永久纪律" :image-size="56" />
          </div>

          <div class="sg-block">
            <h4 class="sg-h4">机制口径</h4>
            <p class="sg-hint">
              左边是生成时登记的机制字段（内部名，作者可忽略），右边才是正文要遵守的口径。
            </p>
            <div v-if="baselineRows.length" class="sg-kv">
              <div v-for="row in baselineRows" :key="row.k" class="sg-kv-row">
                <span class="sg-k">
                  {{ row.label }}<span v-if="row.showRaw" class="sg-dim">（{{ row.k }}）</span>
                </span>
                <span class="sg-v">{{ row.v }}</span>
              </div>
            </div>
            <el-empty v-else description="还没有机制基线（面板数字类字段会从这里取口径）" :image-size="56" />
            <template v-if="registryRows.length">
              <p class="sg-hint">派生注册表：正文出现下列字段才算合法，未登记的数字字段会被判违规并回退整章。</p>
              <el-table :data="registryRows" size="small" border>
                <el-table-column prop="field" label="字段" width="200" />
                <el-table-column prop="direction" label="方向" width="140" />
              </el-table>
            </template>
          </div>

          <div class="sg-block">
            <h4 class="sg-h4">不变量与禁用词</h4>
            <div v-if="invariantRows.length" class="sg-kv">
              <div v-for="row in invariantRows" :key="row.k" class="sg-kv-row">
                <span class="sg-k">{{ row.k }}</span>
                <span class="sg-v">{{ row.v }}</span>
              </div>
            </div>
            <p v-else class="sg-hint">没有额外的不变量记录（机制口径见上方）。</p>
            <div v-if="canon.forbidden?.length" class="sg-tags">
              <el-tag v-for="w in canon.forbidden" :key="w" type="danger" size="small" effect="plain">{{ w }}</el-tag>
            </div>
            <p v-else class="sg-hint">没有登记禁用词。</p>
          </div>
        </el-tab-pane>

        <!-- 角色认知 -->
        <el-tab-pane label="角色认知" name="characters">
          <el-table v-if="characters.length" :data="characters" size="small" border>
            <el-table-column prop="name" label="人物" width="130" />
            <el-table-column prop="state" label="当前状态" min-width="220" />
            <el-table-column prop="location" label="所在地" min-width="150" />
            <el-table-column prop="goal" label="当前目标" min-width="220" />
            <el-table-column prop="last_ch" label="最近章" width="90" />
            <el-table-column prop="source" label="来源" width="150" />
          </el-table>
          <el-empty v-else description="还没有角色状态记录（写章入账后出现）" />
        </el-tab-pane>

        <!-- 发展线 -->
        <el-tab-pane label="发展线" name="timeline">
          <div class="sg-filter">
            <el-select v-model="participant" size="small" clearable placeholder="按参与人筛选"
                       style="width: 200px">
              <el-option v-for="(n, name) in (timeline.participants || {})" :key="name"
                         :label="`${name}（${n}）`" :value="name" />
            </el-select>
            <span class="sg-hint">共 {{ filteredEvents.length }} 条事件，按章分组</span>
          </div>
          <div v-if="byCh.length" class="sg-tl">
            <div v-for="g in byCh" :key="g.ch" class="sg-tl-group">
              <div class="sg-tl-ch">第 {{ g.ch }} 章 <span class="sg-dim">{{ g.count }} 条</span></div>
              <ul class="sg-list">
                <li v-for="(d, i) in g.descs" :key="i">{{ d }}</li>
              </ul>
            </div>
          </div>
          <el-empty v-else description="还没有事件（开启 event_stream 并写完一章后会追加）" />
        </el-tab-pane>

        <!-- 可能分支 -->
        <el-tab-pane label="可能分支" name="threads">
          <el-table v-if="threads.length" :data="threads" size="small" border>
            <el-table-column prop="name" label="伏笔" min-width="180" />
            <el-table-column prop="planted_ch" label="埋设章" width="90" />
            <el-table-column prop="due_ch" label="应收章" width="90" />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small" effect="plain">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="level" label="层级" width="90" />
            <el-table-column prop="desc" label="说明" min-width="260" />
          </el-table>
          <el-empty v-else description="没有未回收伏笔（或本书还没登记伏笔）" />
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getFactsGraph } from '../../api/facts'

const props = defineProps({ pid: { type: String, required: true } })

const loading = ref(false)
const off = ref(false)
const graph = ref(null)
const tab = ref('canon')
const participant = ref('')

const STATUS_TEXT = { overdue: '已逾期', due: '临期', due_soon: '临期', stalled: '长期未推进', buried: '未回收', resolved: '已回收' }
const statusText = (s) => STATUS_TEXT[s] || s || '—'
const statusTag = (s) => (s === 'overdue' ? 'danger' : s === 'due_soon' ? 'warning' : s === 'stalled' ? 'info' : '')

// 账本里的分类/方向是生成侧的英文枚举，直接给作者看没有意义，这里统一转中文
const CATEGORY_TEXT = {
  terminology: '术语', term: '术语', discipline: '纪律', mechanism: '机制',
  person: '人物', character: '人物', place: '地名', location: '地名',
  item: '器物', org: '组织', organization: '组织', event: '事件',
}
const categoryText = (c) => CATEGORY_TEXT[String(c || '').toLowerCase()] || (c ? String(c) : '—')

const REGISTRY_DIRECTION = {
  decrease: '只减不增', increase: '只增不减', fixed: '固定不变', constant: '固定不变',
  reset: '按章重置', any: '不限方向',
}
const directionText = (d) => REGISTRY_DIRECTION[String(d || '').toLowerCase()] || (d ? String(d) : '—')

// 机制字段是生成时登记的内部名；已知的给中文口语名，未知的原样显示（不猜错语义）
// 机制字段的中文名只从字段名的结构推断（起始值/当前值/终点值…），不绑定任何一本书的设定，
// 换书也能读；推断不出语义时原样显示字段名，绝不替作者猜错含义。
const MECH_PATTERNS = [
  [/^per_|_per_/, '每次变化量'],
  [/_start$|^start$/, '起始值'],
  [/_end$|^end$/, '终点值'],
  [/_current$|^current$/, '当前值'],
  [/_init(ial)?$|^init(ial)?$/, '初始值'],
  [/_max$|^max$/, '上限'],
  [/_min$|^min$/, '下限'],
  [/_step$|^step$/, '步长'],
  [/_format$|^format$/, '数字格式'],
  [/_rule$|^rule$/, '规则'],
  [/_unit$|^unit$/, '单位'],
  [/_count$|_times$|^count$|^times$/, '次数'],
]
const hasCJK = (s) => /[\u4e00-\u9fff]/.test(s)
function mechLabel(key) {
  if (hasCJK(key)) return key
  const lower = String(key || '').toLowerCase()
  for (const [re, text] of MECH_PATTERNS) {
    if (re.test(lower)) return text
  }
  return key
}

const meta = computed(() => graph.value)
const canon = computed(() => graph.value?.views?.canon || {})
const characters = computed(() => graph.value?.views?.characters?.characters || [])
const timeline = computed(() => graph.value?.views?.timeline || {})
const threads = computed(() => graph.value?.views?.threads?.threads || [])

// 永久纪律：同一句 rule 会被逐章重复登记，按 rule 去重后展示（示例按内容去重）
const disciplineGroups = computed(() => {
  const map = new Map()
  for (const d of (canon.value.disciplines || [])) {
    const rule = String(d?.rule || '').trim()
    if (!rule) continue
    if (!map.has(rule)) map.set(rule, new Set())
    const ex = String(d?.example || '').trim()
    if (ex) map.get(rule).add(ex)
  }
  return [...map.entries()].map(([rule, ex]) => ({ rule, examples: [...ex] }))
})

const baselineRows = computed(() =>
  Object.entries(canon.value.mechanism_baseline || {}).map(([k, v]) => ({
    k,
    label: mechLabel(k),
    showRaw: mechLabel(k) !== k,
    v: v && typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—'),
  })))
const registryRows = computed(() =>
  Object.entries(canon.value.registry || {}).map(([field, meta]) => {
    const raw = typeof meta === 'string' ? meta : (meta?.type || '')
    return { field, direction: directionText(raw) }
  }))

// 不变量：mechanism/disciplines 已在上面两块展示，避免同一信息重复三遍
const INVARIANT_LABELS = { mechanism: '机制', disciplines: '纪律', locked: '锁定' }
function shortValue(v) {
  if (Array.isArray(v)) return `${v.length} 项`
  if (v && typeof v === 'object') return JSON.stringify(v)
  return String(v ?? '—')
}
const invariantRows = computed(() => {
  const inv = canon.value.invariants || {}
  const skip = new Set(['mechanism', 'disciplines', 'locked'])
  const rows = []
  for (const [k, v] of Object.entries(inv)) {
    if (skip.has(k)) continue
    rows.push({ k: INVARIANT_LABELS[k] || k, v: shortValue(v) })
  }
  const locked = Array.isArray(inv.locked) ? inv.locked.length : (inv.locked ? 1 : 0)
  if (locked) rows.push({ k: '锁定项', v: `${locked} 项` })
  return rows
})

const filteredEvents = computed(() => {
  const rows = timeline.value.events || []
  if (!participant.value) return rows
  return rows.filter((r) => (r.participants || []).includes(participant.value))
})
const byCh = computed(() => {
  const groups = new Map()
  for (const r of filteredEvents.value) {
    const ch = Number(r.ch) || 0
    if (!groups.has(ch)) groups.set(ch, [])
    groups.get(ch).push(r.desc || '')
  }
  return [...groups.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([ch, descs]) => ({ ch, count: descs.length, descs }))
})

async function reload() {
  loading.value = true
  off.value = false
  try {
    graph.value = await getFactsGraph(props.pid, { view: 'all' })
  } catch (e) {
    if (e?.response?.status === 404) off.value = true
    graph.value = null
  } finally {
    loading.value = false
  }
}

onMounted(reload)
watch(() => props.pid, reload)
</script>

<style scoped>
.sg { display: flex; flex-direction: column; gap: 12px; }
.sg-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.sg-title { font-size: 18px; margin: 0 0 4px; }
.sg-sub { margin: 0; font-size: 13px; color: var(--muted); max-width: 760px; line-height: 1.6; }
.sg-tip { font-size: 13px; color: var(--muted); }
.sg-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sg-tag { font-size: 12px; color: var(--ink-soft); background: var(--indigo-soft); border-radius: 6px; padding: 2px 8px; font-variant-numeric: tabular-nums; }
.sg-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.sg-block { margin-bottom: 18px; }
.sg-h4 { font-size: 14px; margin: 0 0 8px; display: flex; align-items: center; gap: 8px; }
.sg-count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.sg-list { margin: 0; padding-left: 18px; line-height: 1.8; font-size: 13px; color: var(--ink-soft); }
.sg-dim { color: var(--muted); font-size: 12px; }
.sg-hint { font-size: 12px; color: var(--muted); margin: 8px 0; line-height: 1.6; }
.sg-kv { display: flex; flex-direction: column; gap: 4px; }
.sg-kv-row { display: flex; gap: 12px; font-size: 13px; }
.sg-k { width: 240px; color: var(--muted); flex-shrink: 0; }
.sg-v { color: var(--ink-soft); word-break: break-all; }
.sg-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.sg-filter { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.sg-tl { display: flex; flex-direction: column; gap: 12px; }
.sg-tl-group { border-left: 2px solid var(--indigo-soft); padding-left: 12px; }
.sg-tl-ch { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.sg-disc { display: flex; flex-direction: column; gap: 8px; }
.sg-disc-row { display: flex; align-items: center; gap: 10px; font-size: 13px; line-height: 1.7; }
.sg-disc-rule { flex: 1; }
.sg-pop-list { margin: 0; padding-left: 18px; line-height: 1.8; font-size: 12px; }
</style>
