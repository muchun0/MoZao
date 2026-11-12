<template>
  <div class="sg">
    <FactsPanelHead
      title="世界事实"
      sub="只读投影：术语、永久纪律、机制口径、不变量与禁用词——写新章时 AI 会照着这些执行。人物状态、伏笔、事件流已统一收进「全书体检」，这里只放设定侧事实。"
      :stats="stats"
      :loading="loading"
      @refresh="reload"
    />

    <el-empty v-if="off" description="世界事实未启用（后台开关 state_graph 关闭）">
      <p class="sg-tip">后台「开关管理」打开 state_graph 后回到这里刷新即可，无需重新生成。</p>
    </el-empty>

    <template v-else>
      <div v-if="meta" class="sg-meta">
        <span class="sg-tag mono">版本 {{ meta.version }}</span>
        <span v-if="meta.sources?.stale" class="sg-tag warn">上游设定已更新，视图可能落后</span>
      </div>

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
            <span v-for="r in registryRows" :key="r.field" class="sg-chip">{{ r.field }} · {{ r.direction }}</span>
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
const showAllForbidden = ref(false)
const FORBIDDEN_SHOWN = 12

// 账本里的分类是生成侧的英文枚举，直接给作者看没有意义，这里统一转中文
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

// 机制字段的中文名只从字段名结构推断（起始值/当前值/终点值…），不绑定任何一本书的设定，
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
// 单视图接口返回 { view, version, sources, data }，canon 在 data 里
const canon = computed(() => graph.value?.data || {})

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

const stats = computed(() => [
  { label: '写到第几章', value: meta.value?.sources?.at_ch || 0 },
  { label: '术语', value: canon.value.terms?.length || 0 },
  { label: '纪律', value: disciplineGroups.value.length },
  { label: '禁用词', value: canon.value.forbidden?.length || 0 },
])

async function reload() {
  loading.value = true
  off.value = false
  showAllForbidden.value = false
  try {
    graph.value = await getFactsGraph(props.pid, { view: 'canon' })
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
.sg-card {
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 12px 14px;
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
.sg-table { width: 100%; }
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
</style>
