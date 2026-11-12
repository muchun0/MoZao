<template>
  <div class="sg">
    <FactsPanelHead
      title="世界事实"
      sub="只读投影：人物、当前状态、地点、事件，以及设定规则（术语 / 纪律 / 机制口径 / 不变量与禁用词）。伏笔与冲突的检查结论在「全书体检」，按章事件流在「时间线」。"
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

      <nav class="sg-tabs">
        <button v-for="t in TABS" :key="t.key" type="button" class="sg-tab"
                :class="{ on: view === t.key }" @click="switchView(t.key)">
          {{ t.label }}<span class="sg-tab-n">{{ tabCount(t.key) }}</span>
        </button>
      </nav>

      <!-- 人物：世界观里的角色档案 -->
      <section v-show="view === 'people'" class="sg-card">
        <header class="sg-card-head">
          <h4 class="sg-h4">人物档案</h4>
          <span class="sg-count">{{ worldCharacters.length }} 人 · 出自世界观设定；此刻的状态看「当前状态」页</span>
        </header>
        <div v-if="worldCharacters.length" class="sg-people">
          <article v-for="p in worldCharacters" :key="p.name" class="sg-person">
            <div class="sg-person-head">
              <span class="sg-avatar">{{ p.name?.slice(0, 1) }}</span>
              <strong class="sg-person-name">{{ p.name }}</strong>
              <el-tag v-if="p.role" size="small" effect="plain">{{ p.role }}</el-tag>
            </div>
            <dl class="sg-person-dl">
              <template v-for="f in PERSON_FIELDS" :key="f.key">
                <dt>{{ f.label }}</dt><dd>{{ p[f.key] || '—' }}</dd>
              </template>
            </dl>
          </article>
        </div>
        <el-empty v-else description="还没有人物档案（世界观生成后出现）" :image-size="56" />
      </section>

      <!-- 当前状态：账本 characters 视图 -->
      <section v-show="view === 'state'" class="sg-card">
        <header class="sg-card-head">
          <h4 class="sg-h4">当前状态</h4>
          <span class="sg-count">写在第 {{ canon.at_ch || 0 }} 章时的快照，点章号可跳章</span>
        </header>
        <el-table v-if="chars.length" :data="chars" size="small" class="sg-table">
          <el-table-column label="人物" width="110">
            <template #default="{ row }"><strong>{{ row.name }}</strong></template>
          </el-table-column>
          <el-table-column label="当前状态" min-width="200" prop="state" />
          <el-table-column label="所在地" min-width="140">
            <template #default="{ row }"><span class="sg-dim">{{ row.location || '—' }}</span></template>
          </el-table-column>
          <el-table-column label="当前目标" min-width="220" prop="goal" />
          <el-table-column label="最近章" width="90">
            <template #default="{ row }">
              <span v-if="row.last_ch">第 {{ row.last_ch }} 章</span>
              <span v-else class="sg-dim">—</span>
            </template>
          </el-table-column>
        </el-table>
        <p v-else class="sg-hint">{{ loadingChar ? '读取中…' : '还没有状态记录。写章时会记下人物的状态、所在地与当前目标。' }}</p>
      </section>

      <!-- 地点：世界观里的地点设定 -->
      <section v-show="view === 'place'" class="sg-card">
        <header class="sg-card-head">
          <h4 class="sg-h4">地点</h4>
          <span class="sg-count">{{ locations.length }} 处 · 地形 / 光线 / 通路 / 危险，写场景时照这个来</span>
        </header>
        <div v-if="locations.length" class="sg-places">
          <article v-for="l in locations" :key="l.name" class="sg-place">
            <div class="sg-place-head"><strong>{{ l.name }}</strong></div>
            <dl class="sg-person-dl">
              <template v-for="f in PLACE_FIELDS" :key="f.key">
                <dt>{{ f.label }}</dt><dd>{{ l[f.key] || '—' }}</dd>
              </template>
            </dl>
          </article>
        </div>
        <el-empty v-else description="还没有地点设定（世界观生成后出现）" :image-size="56" />
      </section>

      <!-- 事件：账本里登记过的事件条目 -->
      <section v-show="view === 'event'" class="sg-card">
        <header class="sg-card-head">
          <h4 class="sg-h4">事件</h4>
          <span class="sg-count">{{ events.length }} 条 · 按章倒序；要看按章顺序的流水与「发展方向」，去「时间线」</span>
        </header>
        <div v-if="eventGroups.length" class="sg-events">
          <div v-for="g in eventGroups" :key="g.ch" class="sg-event-group">
            <div class="sg-event-ch">第 {{ g.ch }} 章 <span class="sg-dim">{{ g.items.length }} 条</span></div>
            <ul class="sg-event-list">
              <li v-for="(e, i) in g.items" :key="i">
                <el-tag size="small" effect="plain" :type="kindTag(e.kind)">{{ kindText(e.kind) }}</el-tag>
                <span class="sg-event-desc">{{ e.desc }}</span>
                <span v-if="e.participants?.length" class="sg-dim">{{ e.participants.join('、') }}</span>
              </li>
            </ul>
          </div>
        </div>
        <p v-else class="sg-hint">{{ loadingEv ? '读取中…' : '还没有事件记录。事件在写完一章后自动登记；早期章节如果是在事件流修复前生成的，就没有记录——从下一章开始才会出现。' }}</p>
      </section>

      <!-- 设定规则：术语 / 永久纪律 / 机制口径 / 不变量与禁用词 -->
      <section v-show="view === 'rules'" class="sg-rules">
        <div class="sg-card">
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
        </div>

        <div class="sg-card">
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
        </div>

        <div class="sg-card">
          <header class="sg-card-head">
            <h4 class="sg-h4">机制口径</h4>
            <span class="sg-count">右侧是正文要遵守的口径；括号里是内部登记字段名，可忽略</span>
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
        </div>

        <div class="sg-card">
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
        </div>
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
const view = ref('people')
const showAllForbidden = ref(false)
const FORBIDDEN_SHOWN = 12

const chars = ref([])
const events = ref([])
const loadingChar = ref(false)
const loadingEv = ref(false)
const loadedChar = ref(false)
const loadedEv = ref(false)

const TABS = [
  { key: 'people', label: '人物' },
  { key: 'state', label: '状态' },
  { key: 'place', label: '地点' },
  { key: 'event', label: '事件' },
  { key: 'rules', label: '设定规则' },
]
const PERSON_FIELDS = [
  { key: 'personality', label: '性格' },
  { key: 'desire', label: '想要' },
  { key: 'appearance', label: '外貌' },
  { key: 'relations', label: '关系' },
]
const PLACE_FIELDS = [
  { key: 'terrain', label: '地形' },
  { key: 'light', label: '光线' },
  { key: 'exits', label: '通路' },
  { key: 'danger', label: '危险' },
]

// 账本里的分类是生成侧的英文枚举，直接给作者看没有意义，这里统一转中文
const CATEGORY_TEXT = {
  terminology: '术语', term: '术语', discipline: '纪律', mechanism: '机制',
  person: '人物', character: '人物', place: '地名', location: '地名',
  item: '器物', org: '组织', organization: '组织', event: '事件',
}
const categoryText = (c) => CATEGORY_TEXT[String(c || '').toLowerCase()] || (c ? String(c) : '—')

const KIND_TEXT = { state: '状态', object: '物品', relationship: '关系', foreshadow: '伏笔' }
const KIND_TAG = { state: '', object: 'success', relationship: 'info', foreshadow: 'warning' }
const kindText = (k) => KIND_TEXT[k] || '状态'
const kindTag = (k) => KIND_TAG[k] || ''

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
// 单视图接口返回 { view, version, sources, data }，数据在 data 里
const canon = computed(() => graph.value?.data || {})
const worldCharacters = computed(() => canon.value.world_characters || [])
const locations = computed(() => canon.value.locations || [])

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
  Object.entries(canon.value.registry || {}).map(([field, m]) => {
    const raw = typeof m === 'string' ? m : (m?.type || '')
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

const eventGroups = computed(() => {
  const groups = new Map()
  for (const e of events.value) {
    const ch = Number(e.ch) || 0
    if (!groups.has(ch)) groups.set(ch, [])
    groups.get(ch).push(e)
  }
  return [...groups.entries()].sort((a, b) => b[0] - a[0]).map(([ch, items]) => ({ ch, items }))
})

const visibleForbidden = computed(() => {
  const all = canon.value.forbidden || []
  return showAllForbidden.value ? all : all.slice(0, FORBIDDEN_SHOWN)
})
const forbiddenHidden = computed(() => (canon.value.forbidden?.length || 0) > visibleForbidden.value.length)

function tabCount(key) {
  if (key === 'people') return worldCharacters.value.length
  if (key === 'state') return loadedChar.value ? chars.value.length : ''
  if (key === 'place') return locations.value.length
  if (key === 'event') return loadedEv.value ? events.value.length : ''
  if (key === 'rules') return (canon.value.terms?.length || 0) + disciplineGroups.value.length
  return ''
}

const stats = computed(() => [
  { label: '写到第几章', value: meta.value?.sources?.at_ch || 0 },
  { label: '人物', value: worldCharacters.value.length },
  { label: '地点', value: locations.value.length },
  { label: '事件', value: loadedEv.value ? events.value.length : '—' },
])

function switchView(key) {
  view.value = key
  if (key === 'state' && !loadedChar.value) loadCharacters()
  if (key === 'event' && !loadedEv.value) loadEvents()
}

async function loadCharacters() {
  loadingChar.value = true
  try {
    const d = await getFactsGraph(props.pid, { view: 'characters' })
    chars.value = d?.data?.characters || []
    loadedChar.value = true
  } catch (e) {
    if (e?.response?.status !== 404) chars.value = []
  } finally {
    loadingChar.value = false
  }
}

async function loadEvents() {
  loadingEv.value = true
  try {
    const d = await getFactsGraph(props.pid, { view: 'timeline' })
    events.value = d?.data?.events || []
    loadedEv.value = true
  } catch (e) {
    if (e?.response?.status !== 404) events.value = []
  } finally {
    loadingEv.value = false
  }
}

async function reload() {
  loading.value = true
  off.value = false
  showAllForbidden.value = false
  loadedChar.value = false
  loadedEv.value = false
  try {
    graph.value = await getFactsGraph(props.pid, { view: 'canon' })
  } catch (e) {
    if (e?.response?.status === 404) off.value = true
    graph.value = null
  } finally {
    loading.value = false
  }
  if (view.value === 'state') loadCharacters()
  if (view.value === 'event') loadEvents()
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
.sg-tabs { display: flex; flex-wrap: wrap; gap: 4px; }
.sg-tab {
  border: 1px solid var(--card-line); background: transparent; color: var(--ink-soft);
  border-radius: 999px; padding: 4px 12px; font-size: 13px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
}
.sg-tab.on { border-color: var(--indigo, #5b5bd6); color: var(--indigo, #5b5bd6); font-weight: 600; }
.sg-tab-n { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.sg-card {
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 12px 14px;
}
.sg-rules { display: flex; flex-direction: column; gap: 12px; }
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

/* 人物 / 地点档案卡 */
.sg-people, .sg-places { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; }
.sg-person, .sg-place {
  border: 1px solid var(--card-line); border-radius: 8px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 6px;
}
.sg-person-head { display: flex; align-items: center; gap: 8px; }
.sg-avatar {
  width: 26px; height: 26px; border-radius: 50%; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--chip, rgba(0, 0, 0, .05)); color: var(--ink-soft); font-size: 13px;
}
.sg-person-name { font-size: 14px; }
.sg-place-head strong { font-size: 14px; }
.sg-person-dl { margin: 0; display: flex; flex-direction: column; gap: 4px; }
.sg-person-dl dt { font-size: 12px; color: var(--muted); }
.sg-person-dl dd { margin: 0 0 4px; font-size: 13px; line-height: 1.7; color: var(--ink-soft); }

/* 事条目 */
.sg-events { display: flex; flex-direction: column; gap: 12px; }
.sg-event-ch { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.sg-event-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.sg-event-list li { display: flex; align-items: flex-start; gap: 8px; font-size: 13px; line-height: 1.7; }
.sg-event-desc { flex: 1; color: var(--ink-soft); }
</style>
