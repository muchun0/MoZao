<template>
  <div class="sg">
    <FactsPanelHead
      title="状态图谱"
      sub="只读投影：同一份账本给出四张视图（世界事实 / 角色认知 / 发展线 / 可能分支），带内容指纹与来源规模。这里只看不改——要改请回「章节」或「世界观」。"
      :stats="stats"
      :loading="loading"
      @refresh="reload"
    />

    <el-empty v-if="off" description="状态图谱未启用（后台开关 state_graph 关闭）">
      <p class="sg-tip">后台「开关管理」打开 state_graph 后回到这里刷新即可，无需重新生成。</p>
    </el-empty>

    <template v-else>
      <div v-if="meta" class="sg-meta">
        <span class="sg-tag mono">版本 {{ meta.version }}</span>
        <span v-if="meta.sources?.stale" class="sg-tag warn">上游设定已更新，视图可能落后</span>
      </div>

      <el-tabs v-model="tab" class="sg-tabs">
        <!-- 世界事实 -->
        <el-tab-pane name="canon">
          <template #label>世界事实 <span class="sg-tab-count">{{ canonCount }}</span></template>

          <section class="sg-card">
            <header class="sg-card-head">
              <h4 class="sg-h4">术语</h4>
              <span class="sg-count">{{ canon.terms?.length || 0 }} 条 · 正文里该用规范写法</span>
            </header>
            <el-table v-if="canon.terms?.length" :data="canon.terms" size="small" class="sg-table">
              <el-table-column label="词条" width="180">
                <template #default="{ row }">{{ row.term }}</template>
              </el-table-column>
              <el-table-column label="规范写法" width="180">
                <template #default="{ row }"><span class="sg-strong">{{ row.canonical }}</span></template>
              </el-table-column>
              <el-table-column label="分类" width="110">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ categoryText(row.category) }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="还没有术语（世界观生成或同步后出现）" :image-size="56" />
          </section>

          <section class="sg-card">
            <header class="sg-card-head">
              <h4 class="sg-h4">永久纪律</h4>
              <span class="sg-count">{{ disciplineGroups.length }} 条 · 每条只显示一次，示例可展开</span>
            </header>
            <div v-if="disciplineGroups.length" class="sg-disc">
              <div v-for="(g, i) in disciplineGroups" :key="i" class="sg-disc-row">
                <span class="sg-disc-no">{{ i + 1 }}</span>
                <span class="sg-disc-rule">{{ g.rule }}</span>
                <el-popover v-if="g.examples.length" placement="top" width="560" trigger="click">
                  <template #reference>
                    <el-button link type="primary" size="small">命中 {{ g.examples.length }} 次 · 看示例</el-button>
                  </template>
                  <ul class="sg-pop-list">
                    <li v-for="(ex, j) in g.examples.slice(0, 5)" :key="j">{{ ex }}</li>
                  </ul>
                  <p v-if="g.examples.length > 5" class="sg-dim">仅显示前 5 条，共 {{ g.examples.length }} 条。</p>
                </el-popover>
              </div>
            </div>
            <el-empty v-else description="还没有登记永久纪律" :image-size="56" />
          </section>

          <section class="sg-card">
            <header class="sg-card-head">
              <h4 class="sg-h4">机制口径</h4>
              <span class="sg-count">右侧是正文要遵守的口径；字段名是内部登记名，可忽略</span>
            </header>
            <div v-if="baselineRows.length" class="sg-kv">
              <div v-for="row in baselineRows" :key="row.k" class="sg-kv-row">
                <span class="sg-k">
                  {{ row.label }}<span v-if="row.showRaw" class="sg-dim">（{{ row.k }}）</span>
                </span>
                <span class="sg-v">{{ row.v }}</span>
              </div>
            </div>
            <el-empty v-else description="还没有机制基线（面板数字类字段会从这里取口径）" :image-size="56" />
            <div v-if="registryRows.length" class="sg-reg">
              <span class="sg-hint">派生注册表（未登记的数字字段会被判违规并回退整章）：</span>
              <div class="sg-chips">
                <span v-for="r in registryRows" :key="r.field" class="sg-chip">
                  {{ r.field }} · {{ r.direction }}
                </span>
              </div>
            </div>
          </section>

          <section class="sg-card">
            <header class="sg-card-head">
              <h4 class="sg-h4">不变量与禁用词</h4>
              <span class="sg-count">
                禁用词 {{ canon.forbidden?.length || 0 }} 条
                <template v-if="invariantRows.length"> · 额外不变量 {{ invariantRows.length }} 项</template>
              </span>
            </header>
            <div v-if="invariantRows.length" class="sg-kv">
              <div v-for="row in invariantRows" :key="row.k" class="sg-kv-row">
                <span class="sg-k">{{ row.k }}</span>
                <span class="sg-v">{{ row.v }}</span>
              </div>
            </div>
            <p v-else class="sg-hint">没有额外的不变量记录（机制口径见上方）。</p>

            <div v-if="visibleForbidden.length" class="sg-tags">
              <el-tag v-for="w in visibleForbidden" :key="w" type="danger" size="small" effect="plain">{{ w }}</el-tag>
              <el-button v-if="forbiddenHidden" link type="primary" size="small" @click="showAllForbidden = true">
                还有 {{ (canon.forbidden?.length || 0) - visibleForbidden.length }} 条，展开
              </el-button>
              <el-button v-else-if="showAllForbidden && (canon.forbidden?.length || 0) > FORBIDDEN_SHOWN"
                         link size="small" @click="showAllForbidden = false">收起</el-button>
            </div>
            <p v-else class="sg-hint">没有登记禁用词。</p>
          </section>
        </el-tab-pane>

        <!-- 角色认知 -->
        <el-tab-pane name="characters">
          <template #label>角色认知 <span class="sg-tab-count">{{ characters.length }}</span></template>
          <el-table v-if="characters.length" :data="characters" size="small" class="sg-table">
            <el-table-column label="人物" width="140">
              <template #default="{ row }">
                <span class="sg-avatar">{{ (row.name || '?').slice(0, 1) }}</span>
                <strong class="sg-name">{{ row.name }}</strong>
              </template>
            </el-table-column>
            <el-table-column label="当前状态" min-width="220">
              <template #default="{ row }">{{ row.state || '—' }}</template>
            </el-table-column>
            <el-table-column label="所在地" min-width="150">
              <template #default="{ row }"><span class="sg-dim">{{ row.location || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="当前目标" min-width="220">
              <template #default="{ row }">{{ row.goal || '—' }}</template>
            </el-table-column>
            <el-table-column label="最近章" width="92">
              <template #default="{ row }"><span class="sg-ch">{{ row.last_ch ? `第 ${row.last_ch} 章` : '—' }}</span></template>
            </el-table-column>
            <el-table-column label="来源" width="140">
              <template #default="{ row }"><span class="sg-dim">{{ row.source || '—' }}</span></template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="还没有角色状态记录">
            <p class="sg-empty-hint">写章时会把人物状态、所在地、目标记进账本，这里按最近一次记录汇总。</p>
          </el-empty>
        </el-tab-pane>

        <!-- 发展线 -->
        <el-tab-pane name="timeline">
          <template #label>发展线 <span class="sg-tab-count">{{ filteredEvents.length }}</span></template>
          <div class="sg-filter">
            <el-select v-model="participant" size="small" clearable placeholder="按参与人筛选" style="width: 200px">
              <el-option v-for="(n, name) in (timeline.participants || {})" :key="name"
                         :label="`${name}（${n}）`" :value="name" />
            </el-select>
            <span class="sg-hint">共 {{ filteredEvents.length }} 条事件，按章分组</span>
          </div>
          <div v-if="byCh.length" class="sg-tl">
            <div v-for="g in byCh" :key="g.ch" class="sg-tl-group">
              <div class="sg-tl-ch">第 {{ g.ch }} 章 <span class="sg-dim">{{ g.count }} 条</span></div>
              <ul class="sg-list">
                <li v-for="(e, i) in g.events" :key="i">
                  <el-tag size="small" effect="plain" :type="kindTag(e.kind)">{{ kindText(e.kind) }}</el-tag>
                  <span class="sg-event">{{ e.desc }}</span>
                </li>
              </ul>
            </div>
          </div>
          <el-empty v-else description="还没有事件">
            <p class="sg-empty-hint">
              {{ emptyTimelineHint }}
            </p>
          </el-empty>
        </el-tab-pane>

        <!-- 可能分支 -->
        <el-tab-pane name="threads">
          <template #label>可能分支 <span class="sg-tab-count">{{ threads.length }}</span></template>
          <el-table v-if="threads.length" :data="threads" size="small" class="sg-table"
                   :row-class-name="threadRowClass">
            <el-table-column label="伏笔" min-width="210">
              <template #default="{ row }">
                <div class="sg-strong">{{ row.name }}</div>
                <div class="sg-sub">{{ row.desc || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="埋设 / 应收" width="150">
              <template #default="{ row }">
                <span class="sg-ch-sm">第 {{ row.planted_ch }} 章</span>
                <span class="sg-arrow">→</span>
                <span class="sg-ch-sm" :class="{ 'sg-overdue': row.status === 'overdue' }">第 {{ row.due_ch }} 章</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small" effect="plain">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="层级" width="80">
              <template #default="{ row }">{{ levelText(row.level) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="没有未回收伏笔">
            <p class="sg-empty-hint">伏笔来自世界观：给线索写上埋在第几章、打算第几章收，这里就会显示进度和逾期情况。</p>
          </el-empty>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getFactsGraph } from '../../api/facts'
import FactsPanelHead from './FactsPanelHead.vue'

const props = defineProps({ pid: { type: String, required: true } })

const loading = ref(false)
const off = ref(false)
const graph = ref(null)
const tab = ref('canon')
const participant = ref('')
const showAllForbidden = ref(false)
const FORBIDDEN_SHOWN = 12

const STATUS_TEXT = { overdue: '已逾期', due: '临期', due_soon: '临期', stalled: '长期未推进', buried: '未回收', resolved: '已回收' }
const statusText = (s) => STATUS_TEXT[s] || s || '—'
const statusTag = (s) => (s === 'overdue' ? 'danger' : s === 'due_soon' ? 'warning' : s === 'stalled' ? 'info' : '')

const LEVEL_TEXT = { main: '主线', sub: '支线', minor: '次线' }
const levelText = (l) => LEVEL_TEXT[l] || l || '主线'

const KIND_TEXT = { state: '状态', object: '物品', relationship: '关系', foreshadow: '伏笔' }
const KIND_TAG = { state: '', object: 'success', relationship: 'info', foreshadow: 'warning' }
const kindText = (k) => KIND_TEXT[k] || '状态'
const kindTag = (k) => KIND_TAG[k] || ''

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

const stats = computed(() => [
  { label: '写到第几章', value: meta.value?.sources?.at_ch || 0 },
  { label: '事件', value: meta.value?.sources?.events ?? 0 },
  { label: '伏笔', value: threads.value.length },
  { label: '术语', value: canon.value.terms?.length || 0 },
  { label: '纪律', value: disciplineGroups.value.length },
])

const canonCount = computed(() =>
  (canon.value.terms?.length || 0) + disciplineGroups.value.length
  + Object.keys(canon.value.mechanism_baseline || {}).length)

const emptyTimelineHint = computed(() => {
  if ((meta.value?.sources?.events ?? 0) === 0 && (meta.value?.sources?.at_ch || 0) > 0) {
    return `已写到第 ${meta.value.sources.at_ch} 章，但没有事件记录——多半是这些章在事件流修好之前生成的。继续写新章会自动追加。`
  }
  return '事件在写完一章后自动追加；开启 event_stream 后新写的章才会有记录。'
})

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

const visibleForbidden = computed(() => {
  const all = canon.value.forbidden || []
  return showAllForbidden.value ? all : all.slice(0, FORBIDDEN_SHOWN)
})
const forbiddenHidden = computed(() => (canon.value.forbidden?.length || 0) > visibleForbidden.value.length)

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
    groups.get(ch).push(r)
  }
  return [...groups.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([ch, events]) => ({ ch, count: events.length, events }))
})

const threadRowClass = ({ row }) => (row.status === 'overdue' ? 'sg-row-overdue' : '')

async function reload() {
  loading.value = true
  off.value = false
  showAllForbidden.value = false
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
.sg-tip { font-size: 13px; color: var(--muted); }
.sg-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sg-tag {
  font-size: 12px; color: var(--ink-soft); background: var(--chip, rgba(0, 0, 0, .04));
  border-radius: 6px; padding: 2px 8px;
}
.sg-tag.mono { font-family: var(--mono, ui-monospace, monospace); }
.sg-tag.warn { color: var(--gold-deep); }
.sg-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.sg-tab-count { margin-left: 4px; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }

.sg-card {
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 12px 14px; margin-bottom: 12px;
}
.sg-card-head {
  display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; margin-bottom: 8px;
}
.sg-h4 { font-size: 14px; margin: 0; }
.sg-count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.sg-hint { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 6px 0 0; }
.sg-dim { color: var(--muted); font-size: 12px; }
.sg-strong { font-size: 13px; font-weight: 600; }
.sg-sub { margin-top: 2px; font-size: 12px; color: var(--muted); line-height: 1.6; }
.sg-table { width: 100%; }
.sg-table :deep(.el-table__row.sg-row-overdue) { background: #fdf3e3; }
.sg-avatar {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; margin-right: 6px; border-radius: 50%;
  background: var(--indigo-soft); color: var(--indigo); font-size: 12px;
}
.sg-name { font-size: 13px; }

.sg-disc { display: flex; flex-direction: column; gap: 8px; }
.sg-disc-row { display: flex; align-items: flex-start; gap: 10px; font-size: 13px; line-height: 1.7; }
.sg-disc-no {
  flex: none; width: 20px; height: 20px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--chip, rgba(0, 0, 0, .04)); color: var(--muted); font-size: 11px;
}
.sg-disc-rule { flex: 1; }
.sg-pop-list { margin: 0; padding-left: 18px; line-height: 1.8; font-size: 12px; }

.sg-kv { display: flex; flex-direction: column; gap: 6px; }
.sg-kv-row { display: flex; gap: 16px; font-size: 13px; line-height: 1.7; }
.sg-k { width: 220px; flex-shrink: 0; color: var(--muted); }
.sg-v { color: var(--ink); word-break: break-word; }

.sg-reg { margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--card-line); }
.sg-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.sg-chip {
  font-size: 12px; color: var(--ink-soft); border: 1px solid var(--card-line);
  border-radius: 999px; padding: 2px 10px;
}
.sg-tags { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; }

.sg-filter { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.sg-tl { display: flex; flex-direction: column; gap: 14px; }
.sg-tl-group { border-left: 2px solid var(--indigo-soft); padding-left: 12px; }
.sg-tl-ch { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.sg-list { margin: 0; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.sg-list li { display: flex; align-items: flex-start; gap: 8px; font-size: 13px; line-height: 1.7; color: var(--ink-soft); }
.sg-event { flex: 1; }

.sg-ch { font-size: 12px; color: var(--ink-soft); font-variant-numeric: tabular-nums; white-space: nowrap; }
.sg-ch-sm { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.sg-overdue { color: var(--danger); font-weight: 600; }
.sg-arrow { margin: 0 6px; color: var(--muted); }
.sg-empty-hint { max-width: 560px; margin: 6px auto 0; font-size: 12px; color: var(--muted); line-height: 1.8; text-align: left; }
</style>
