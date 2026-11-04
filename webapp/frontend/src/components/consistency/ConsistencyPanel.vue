<template>
  <div class="cp">
    <header class="cp-head">
      <div>
        <h3 class="cp-title">全书体检</h3>
        <p class="cp-sub">帮你把全书过一遍：哪里前后对不上、哪条伏笔该收了、某个人物或设定都在哪几章出现过。这里只看不改，不会动你的正文。</p>
      </div>
      <el-button size="small" :loading="loading" @click="loadOverview">重新体检</el-button>
    </header>

    <!-- 没有正文：说清数据从哪来，不伪造示例结果 -->
    <el-empty v-if="!loading && !overview?.chapters_written" description="还没有正文可以体检">
      <div class="cp-guide">
        <p>体检用的是你已有的正文和生成记录，先攒一点素材再来：</p>
        <ol>
          <li>去「大纲」生成或导入大纲和世界观（伏笔埋在哪、打算哪章收，都在这里定）。</li>
          <li>再去「Agent」生成正文，每写完一章会顺手记下这一章的问题。</li>
          <li>回到这里点「重新体检」，就能看到全书哪里需要注意。</li>
        </ol>
      </div>
    </el-empty>

    <template v-else-if="overview">
      <!-- 先给结论：一句话说清整体状态 -->
      <div class="cp-verdict" :class="verdict.level">
        <span class="cp-verdict-tag">{{ verdict.tag }}</span>
        <p class="cp-verdict-text">{{ verdict.text }}</p>
      </div>

      <div class="cp-stats">
        <div class="cp-stat">
          <span class="cp-stat-num">{{ overview.chapters_written }}</span>
          <span class="cp-stat-label">已经写完的章数</span>
        </div>
        <div class="cp-stat" :class="{ danger: counts.urgent }">
          <span class="cp-stat-num">{{ counts.urgent }}</span>
          <span class="cp-stat-label">必须先处理</span>
        </div>
        <div class="cp-stat" :class="{ warn: counts.suggest }">
          <span class="cp-stat-num">{{ counts.suggest }}</span>
          <span class="cp-stat-label">建议处理</span>
        </div>
        <div class="cp-stat">
          <span class="cp-stat-num">{{ counts.minor }}</span>
          <span class="cp-stat-label">可以先了解</span>
        </div>
      </div>

      <!-- 按轻重分组，默认只展开最需要处理的那一组 -->
      <section v-for="g in groupList" :key="g.key" class="cp-group">
        <button class="cp-group-head" type="button" @click="toggleGroup(g.key)">
          <span class="cp-caret" :class="{ open: openGroups[g.key] }">▸</span>
          <span class="cp-group-name">{{ g.title }}</span>
          <el-tag size="small" :type="g.tagType" effect="plain">{{ g.total }} 条</el-tag>
          <span class="cp-group-hint">{{ g.hint }}</span>
        </button>

        <div v-show="openGroups[g.key]" class="cp-group-body">
          <p v-if="!g.items.length" class="cp-group-empty">{{ g.empty }}</p>
          <article v-for="(it, i) in g.items" :key="`${g.key}-${i}`" class="cp-card">
            <div class="cp-card-top">
              <span class="cp-card-tag" :class="`sev-${it.severity}`">{{ severityLabel(it.severity) }}</span>
              <strong class="cp-card-title">{{ it.title }}</strong>
            </div>
            <p class="cp-card-where">{{ it.where }}</p>
            <p class="cp-card-advice"><span class="cp-advice-label">建议</span>{{ it.advice }}</p>
            <p v-if="it.evidence" class="cp-card-evidence">原文：「{{ it.evidence }}」</p>
            <div class="cp-card-actions">
              <el-button v-if="it.jump" size="small" text type="primary" @click="emitLocate(it.jump)">打开第 {{ it.jump }} 章</el-button>
            </div>
          </article>
          <p v-if="g.total > g.items.length" class="cp-group-more">
            这一类共有 {{ g.total }} 条，上面先列出 {{ g.items.length }} 条，完整清单见下方「按类别细看」。
          </p>
        </div>
      </section>

      <div class="cp-detail">
        <div class="cp-detail-head">
          <strong>按类别细看</strong>
          <span class="cp-meta">要逐条核对、筛选或搜索时用这里</span>
        </div>

        <el-tabs v-model="sub" class="cp-tabs">
          <!-- 1. 设定小抄是否跟上正文 -->
          <el-tab-pane label="设定跟上了吗" name="coverage">
            <p class="cp-purpose">
              这块看：AI 写新章节时会参考的那些「设定小抄」（章节摘要、伏笔回收计划、人物当前状态等）有没有跟到最新章节。落后的时候，后面写出来的内容更容易和前面打架。
            </p>
            <p v-if="!coverageViews.length" class="cp-group-empty">
              暂时还没有可对照的记录。等生成过正文后，这里会显示每项「小抄」跟到了第几章。
            </p>
            <div v-for="row in coverageViews" :key="row.view" class="cp-row-card">
              <div>
                <strong>{{ viewName(row.view) }}</strong>
                <p class="cp-meta">{{ coverageText(row) }}</p>
              </div>
              <div class="cp-row-card-right">
                <el-tag size="small" :type="coverageTag(row)" effect="plain">{{ coverageState(row) }}</el-tag>
                <el-button v-if="row.status === 'lagging' && baselineCh" size="small" text type="primary"
                  @click="emitLocate(baselineCh)">看看最新一章</el-button>
              </div>
            </div>
            <p class="cp-footnote">
              小抄会在生成新章节时自动更新。如果某一项一直不动，说明那一步没跑到，后面的伏笔和人物状态判断会不准。
            </p>
          </el-tab-pane>

          <!-- 2. 伏笔 -->
          <el-tab-pane :label="`埋下的伏笔和回收进度 (${overview.foreshadow.total})`" name="foreshadow">
            <p class="cp-purpose">
              这块看：故事里已经埋下、还没给读者交代的线索。「早该收了」最要紧，「很久没推进」次之，「近期该收」可以先排进计划。
            </p>
            <div class="cp-filter">
              <el-select v-model="fsState" size="small" clearable placeholder="全部状态" class="cp-select"
                @change="reloadForeshadow">
                <el-option v-for="s in FORESHADOW_STATES" :key="s.value" :value="s.value" :label="s.label" />
              </el-select>
              <span class="cp-count">{{ fsLoaded ? `${fsTotal} 条` : '读取中…' }}</span>
            </div>
            <p v-if="fsLoaded && !fsItems.length" class="cp-group-empty">{{ fsEmptyText }}</p>
            <article v-for="it in fsItems" :key="it.name" class="cp-card">
              <div class="cp-card-top">
                <span class="cp-card-tag" :class="`sev-${it.severity}`">{{ fsLabel(it.state) }}</span>
                <strong class="cp-card-title">{{ fsProblem(it) }}</strong>
              </div>
              <p class="cp-card-where">{{ fsWhere(it) }}</p>
              <p class="cp-card-advice"><span class="cp-advice-label">建议</span>{{ fsAdvice(it) }}</p>
              <p v-if="it.excerpt" class="cp-card-evidence">原文：「{{ it.excerpt }}」</p>
              <div class="cp-card-actions">
                <el-button size="small" text type="primary" @click="emitLocate(it.buried_ch)">
                  打开第 {{ it.buried_ch }} 章
                </el-button>
              </div>
            </article>
            <el-button v-if="fsItems.length < fsTotal" size="small" text type="primary" :loading="fsLoading"
              @click="loadMore('foreshadow')">再看一些</el-button>
          </el-tab-pane>

          <!-- 3. 前后不一致 -->
          <el-tab-pane :label="`前后对不上的地方 (${overview.conflicts.total})`" name="conflicts">
            <p class="cp-purpose">
              这块看：已有的全书通读检查和单章质检里，指出「和前面的设定或情节对不上」的地方。没被检查到的矛盾，这里看不到。
            </p>
            <div class="cp-filter">
              <el-select v-model="confSeverity" size="small" clearable placeholder="全部轻重" class="cp-select"
                @change="reloadConflicts">
                <el-option value="high" label="必须处理" />
                <el-option value="medium" label="建议处理" />
                <el-option value="low" label="了解一下" />
              </el-select>
              <span class="cp-count">{{ confLoaded ? `${confTotal} 条` : '读取中…' }}</span>
            </div>
            <p v-if="confLoaded && !confItems.length" class="cp-group-empty">{{ confEmptyText }}</p>
            <article v-for="(it, i) in confItems" :key="i" class="cp-card">
              <div class="cp-card-top">
                <span class="cp-card-tag" :class="`sev-${it.severity}`">{{ severityLabel(it.severity) }}</span>
                <strong class="cp-card-title">{{ it.detail }}</strong>
              </div>
              <p class="cp-card-where">第 {{ it.chapter }} 章 · 来自{{ sourceName(it.source) }}</p>
              <p class="cp-card-advice"><span class="cp-advice-label">建议</span>{{ adviceOf(it) }}</p>
              <p v-if="it.evidence" class="cp-card-evidence">原文：「{{ it.evidence }}」</p>
              <div class="cp-card-actions">
                <el-button size="small" text type="primary" @click="emitLocate(it.chapter)">打开第 {{ it.chapter }} 章</el-button>
              </div>
            </article>
            <el-button v-if="confItems.length < confTotal" size="small" text type="primary" :loading="confLoading"
              @click="loadMore('conflicts')">再看一些</el-button>
          </el-tab-pane>

          <!-- 4. 人物 / 设定索引 -->
          <el-tab-pane label="谁/什么在哪几章出现过" name="entities">
            <p class="cp-purpose">
              这块看：人物、地点、势力、物件在正文里各出现在哪些章。点一行能看到具体句子，方便确认某条设定到底有没有落实。
            </p>
            <div class="cp-filter">
              <el-input v-model="entityQuery" size="small" clearable placeholder="搜名字或别名，例如「林舟」"
                class="cp-input" @keyup.enter="reloadEntities" @clear="reloadEntities" />
              <el-select v-model="entityKind" size="small" clearable placeholder="全部类型" class="cp-select"
                @change="reloadEntities">
                <el-option v-for="k in ENTITY_KIND_OPTIONS" :key="k.value" :value="k.value" :label="k.label" />
              </el-select>
              <span class="cp-count">{{ entityLoaded ? `${entityTotal} 个` : '读取中…' }}</span>
            </div>
            <p v-if="entityLoaded && !entityItems.length" class="cp-group-empty">{{ entityEmptyText }}</p>
            <article v-for="row in entityItems" :key="row.name" class="cp-row-card"
              :class="{ clickable: row.chapters.length }" @click="openMentions(row)">
              <div>
                <div class="cp-row-title">
                  <strong>{{ row.name }}</strong>
                  <el-tag size="small" effect="plain">{{ row.kind_label }}</el-tag>
                  <span v-if="row.aliases.length" class="cp-meta">也叫 {{ row.aliases.join('、') }}</span>
                </div>
                <p class="cp-meta">{{ summaryOf(row) }}</p>
              </div>
              <span v-if="row.chapters.length" class="cp-link">看出现在哪 ›</span>
            </article>
            <el-button v-if="entityItems.length < entityTotal" size="small" text type="primary" :loading="entityLoading"
              @click="loadMore('entities')">再看一些</el-button>
            <p v-if="entityTruncated" class="cp-footnote">
              设定条目太多，这次只体检了前 400 个。搜具体名字仍然能查到。
            </p>
          </el-tab-pane>
        </el-tabs>
      </div>

      <!-- 数据边界：把「哪里查不到」翻成人话 -->
      <div v-if="gapNotes.length" class="cp-boundary">
        <strong>这份体检的边界</strong>
        <p v-for="(note, i) in gapNotes" :key="i">· {{ note }}</p>
      </div>
    </template>

    <!-- 实体章节定位抽屉：原文片段保留，字符位置作为次级信息 -->
    <el-drawer v-model="drawerOpen" :title="drawer?.name ? `${drawer.name} 出现在哪些章` : '出现在哪些章'" size="480px">
      <div v-if="drawerLoading" class="cp-tip">正在翻书…</div>
      <template v-else-if="drawer">
        <p class="cp-tip">
          <template v-if="drawer.aliases.length > 1">连同别名「{{ drawer.aliases.slice(1).join('、') }}」一起找，</template>
          共出现在 {{ drawer.total_chapters }} 章<template v-if="drawer.truncated">（章节较多，先列前 50 章）</template>。
        </p>
        <el-empty v-if="!drawer.chapters.length" description="正文里还没有出现" :image-size="60" />
        <div v-for="c in drawer.chapters" :key="c.no" class="cp-mention">
          <div class="cp-mention-head">
            <strong>第 {{ c.no }} 章 {{ c.title }}</strong>
            <el-button size="small" text type="primary" @click="jump(c.no)">打开这一章</el-button>
          </div>
          <p v-for="(o, i) in c.occurrences" :key="i" class="cp-mention-occ">
            {{ o.snippet }}<span class="cp-pos">（约第 {{ o.offset }} 字）</span>
          </p>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getConsistencyOverview, getForeshadowLedger, getConsistencyConflicts,
  getEntityIndex, getEntityMentions,
} from '../../api/consistency'
import {
  FORESHADOW_STATES, ENTITY_KIND_OPTIONS, SEVERITY_ORDER,
  severityMeta, severityGroup, projectionViewLabel, coverageStateLabel,
  coverageStateType, foreshadowStateLabel, foreshadowProblem, foreshadowWhere,
  foreshadowAdvice, sourceLabel, conflictAdvice, entitySummary, humanizeGap,
} from '../../utils/consistency'

const props = defineProps({ pid: { type: String, required: true } })
const emit = defineEmits(['locate'])

const loading = ref(false)
const overview = ref(null)
const sub = ref('coverage')
const PAGE_SIZE = 100

// 伏笔
const fsItems = ref([])
const fsTotal = ref(0)
const fsState = ref('')
const fsLoading = ref(false)
const fsLoaded = ref(false)
// 冲突
const confItems = ref([])
const confTotal = ref(0)
const confSeverity = ref('')
const confLoading = ref(false)
const confLoaded = ref(false)
// 实体
const entityItems = ref([])
const entityTotal = ref(0)
const entityTruncated = ref(false)
const entityQuery = ref('')
const entityKind = ref('')
const entityLoading = ref(false)
const entityLoaded = ref(false)
const drawerOpen = ref(false)
const drawer = ref(null)
const drawerLoading = ref(false)

// 分组默认只展开最需要处理的一组
const openGroups = reactive({ must: true, suggest: false, later: false })

const severityLabel = (sev) => severityMeta(sev).label
const fsLabel = (state) => foreshadowStateLabel(state)
const fsProblem = (it) => foreshadowProblem(it)
const fsWhere = (it) => foreshadowWhere(it)
const fsAdvice = (it) => foreshadowAdvice(it)
const sourceName = (source) => sourceLabel(source)
const adviceOf = (it) => conflictAdvice(it)
const summaryOf = (row) => entitySummary(row)
const viewName = (view) => projectionViewLabel(view)
const coverageState = (row) => coverageStateLabel(row.status)
const coverageTag = (row) => coverageStateType(row.status)

const baselineCh = computed(() => overview.value?.baseline_ch || 0)
const coverageViews = computed(() => overview.value?.coverage?.views || [])
const gapNotes = computed(() => (overview.value?.data_gaps || []).map(humanizeGap))

// 各分组真实总数（来自后端计数，不受首屏预览条数限制）
const counts = computed(() => {
  const ov = overview.value
  if (!ov) return { urgent: 0, suggest: 0, minor: 0 }
  const c = ov.conflicts?.counts || {}
  const f = ov.foreshadow?.counts || {}
  return {
    urgent: (c.high || 0) + (f.overdue || 0) + (ov.coverage?.lagging_views?.length || 0),
    suggest: (c.medium || 0) + (f.stalled || 0),
    minor: (c.low || 0) + (c.info || 0) + (f.due_soon || 0) + (f.planted || 0) + (f.revealed || 0),
  }
})

// 结论：只说「有多少、先看哪组」；没有记录时明确说明没有判断依据
const verdict = computed(() => {
  const ov = overview.value
  const c = counts.value
  const hasSignal = (ov?.conflicts?.total || 0) + (ov?.foreshadow?.total || 0) + (ov?.coverage?.lagging_views?.length || 0)
  if (!hasSignal) {
    return { level: 'ok', tag: '还没结论', text: '目前还没有可判断的记录（还没有通读检查或质检记录）。多写几章、跑一次全书检查后，这里才会给出结论。' }
  }
  if (c.urgent) {
    return { level: 'danger', tag: '要处理', text: `全书有 ${c.urgent} 处建议马上处理，先在下面「必须先处理」里挑最要紧的看。` }
  }
  if (c.suggest) {
    return { level: 'warning', tag: '还行', text: `没有必须马上处理的问题，有 ${c.suggest} 处建议抽空看看。` }
  }
  return { level: 'ok', tag: '暂时没事', text: '按已有记录，暂时没发现需要处理的问题。这里只汇总已经记下来的检查结果，没记录不等于全书没问题。' }
})

// 把三类信号统一成「问题 / 位置 / 建议」三要素卡片，再按轻重分组
function buildGroupedItems(ov) {
  const items = []
  if (!ov) return items

  // 设定小抄落后：作者能感知的是「后面容易写崩」
  for (const row of ov.coverage?.views || []) {
    if (row.status !== 'lagging') continue
    items.push({
      severity: 'high', group: 'must',
      title: `${projectionViewLabel(row.view)}还没跟上正文`,
      where: row.covered_ch
        ? `只跟到第 ${row.covered_ch} 章，正文已经写到第 ${ov.baseline_ch} 章`
        : `正文已经写到第 ${ov.baseline_ch} 章，这一项还没有跟上`,
      advice: '生成新章节时它会自动补上；如果一直不动，说明这一步没跑到，建议到「Agent」页重新生成下一章或检查生成过程。',
      jump: ov.baseline_ch || 0,
      evidence: '',
    })
  }

  for (const it of ov.foreshadow?.items || []) {
    const sev = it.severity || 'info'
    items.push({
      severity: sev, group: severityGroup(sev),
      title: foreshadowProblem(it), where: foreshadowWhere(it),
      advice: foreshadowAdvice(it), jump: it.buried_ch || 0,
      evidence: it.excerpt || '',
    })
  }

  for (const it of ov.conflicts?.items || []) {
    const sev = it.severity || 'medium'
    items.push({
      severity: sev, group: severityGroup(sev),
      title: it.detail || '有一处前后对不上',
      where: `第 ${it.chapter} 章 · 来自${sourceLabel(it.source)}`,
      advice: conflictAdvice(it), jump: it.chapter || 0,
      evidence: it.evidence || '',
    })
  }

  return items.sort((a, b) => (SEVERITY_ORDER[b.severity] || 0) - (SEVERITY_ORDER[a.severity] || 0))
}

const groupedItems = computed(() => buildGroupedItems(overview.value))

const GROUP_DEFS = [
  { key: 'must', title: '必须先处理', hint: '不处理，读者容易觉得前后对不上', tagType: 'danger', empty: '没有必须马上处理的问题。' },
  { key: 'suggest', title: '建议处理', hint: '抽空看看，别拖太久', tagType: 'warning', empty: '没有建议处理的问题。' },
  { key: 'later', title: '了解一下', hint: '不影响阅读，知道就好', tagType: 'info', empty: '没有需要留意的记录。' },
]

// 分组总数：与 counts 的命名对应（must→urgent / suggest→suggest / later→minor）
const groupTotals = computed(() => ({
  must: counts.value.urgent,
  suggest: counts.value.suggest,
  later: counts.value.minor,
}))

const groupList = computed(() => GROUP_DEFS.map((g) => ({
  ...g,
  items: groupedItems.value.filter((it) => it.group === g.key),
  total: groupTotals.value[g.key],
})))

const fsEmptyText = computed(() => (fsState.value
  ? '这个状态下暂时没有伏笔，换个状态看看。'
  : '这本书还没有登记伏笔。伏笔来自世界观：给线索写上埋在第几章、打算第几章收，这里就会显示进度。'))

const confEmptyText = computed(() => (confSeverity.value
  ? '这个轻重下暂时没有记录，换个轻重看看。'
  : '暂时没发现前后对不上的地方。这里只汇总已有的通读检查和单章质检记录，没记录不等于全书没问题。'))

const entityEmptyText = computed(() => (entityQuery.value
  ? `没有找到叫「${entityQuery.value}」的人物或设定，换个名字或别名再搜。`
  : '还没有可查的人物或设定。先在大纲或世界观里生成人物与设定，这里就能查到它们出现在哪几章。'))

function coverageText(row) {
  if (row.status === 'lagging') {
    const gap = (overview.value?.baseline_ch || 0) - (row.covered_ch || 0)
    return `只跟到第 ${row.covered_ch} 章，比正文落后 ${gap > 0 ? gap : 1} 章，后面写的内容容易和前面打架`
  }
  if (row.status === 'ahead') {
    return `记到了第 ${row.covered_ch} 章，比已写正文还多，通常是旧记录，不影响阅读`
  }
  if (row.status === 'synced') {
    return `已经跟到第 ${row.covered_ch} 章，和正文一致`
  }
  return '还没有记录跟到第几章，等下一次生成或检查后才会更新'
}

function toggleGroup(key) {
  openGroups[key] = !openGroups[key]
}

function resetOpenGroups() {
  const first = counts.value.urgent ? 'must' : counts.value.suggest ? 'suggest' : 'later'
  for (const key of Object.keys(openGroups)) openGroups[key] = key === first
}

// 章节跳转：只在有真实章号时发事件
function emitLocate(no) {
  if (no) emit('locate', no)
}

async function loadOverview() {
  loading.value = true
  try {
    overview.value = await getConsistencyOverview(props.pid)
    resetOpenGroups()
    // 首屏按当前子页同步一次，避免切页后展示旧空表
    await loadCurrentSub()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '体检数据没取到，稍后再试一次')
  } finally {
    loading.value = false
  }
}

async function loadCurrentSub() {
  if (sub.value === 'foreshadow') await reloadForeshadow()
  else if (sub.value === 'conflicts') await reloadConflicts()
  else if (sub.value === 'entities') await reloadEntities()
}

// 伏笔：服务端筛选 + 分页，避免一次取全书
async function reloadForeshadow() {
  fsLoading.value = true
  try {
    const data = await getForeshadowLedger(props.pid, { state: fsState.value, limit: PAGE_SIZE, offset: 0 })
    fsItems.value = data.items
    fsTotal.value = data.total
    fsLoaded.value = true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '伏笔记录没取到，稍后再试一次')
  } finally {
    fsLoading.value = false
  }
}

async function reloadConflicts() {
  confLoading.value = true
  try {
    const data = await getConsistencyConflicts(props.pid, { severity: confSeverity.value, limit: PAGE_SIZE, offset: 0 })
    confItems.value = data.items
    confTotal.value = data.total
    confLoaded.value = true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '问题清单没取到，稍后再试一次')
  } finally {
    confLoading.value = false
  }
}

async function reloadEntities() {
  entityLoading.value = true
  try {
    const data = await getEntityIndex(props.pid, {
      q: entityQuery.value, kind: entityKind.value, limit: PAGE_SIZE, offset: 0,
    })
    entityItems.value = data.items
    entityTotal.value = data.total
    entityTruncated.value = Boolean(data.truncated)
    entityLoaded.value = true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '人物与设定没取到，稍后再试一次')
  } finally {
    entityLoading.value = false
  }
}

// 加载更多：同一筛选条件下把下一页追加到现有列表
async function loadMore(kind) {
  if (kind === 'foreshadow') {
    fsLoading.value = true
    const data = await getForeshadowLedger(props.pid, { state: fsState.value, limit: PAGE_SIZE, offset: fsItems.value.length })
      .finally(() => { fsLoading.value = false })
    fsItems.value = fsItems.value.concat(data.items)
  } else if (kind === 'conflicts') {
    confLoading.value = true
    const data = await getConsistencyConflicts(props.pid, { severity: confSeverity.value, limit: PAGE_SIZE, offset: confItems.value.length })
      .finally(() => { confLoading.value = false })
    confItems.value = confItems.value.concat(data.items)
  } else {
    entityLoading.value = true
    const data = await getEntityIndex(props.pid, {
      q: entityQuery.value, kind: entityKind.value, limit: PAGE_SIZE, offset: entityItems.value.length,
    }).finally(() => { entityLoading.value = false })
    entityItems.value = entityItems.value.concat(data.items)
  }
}

// 实体行点击：拉取具体出现在哪些章、哪些句子
async function openMentions(row) {
  if (!row?.chapters?.length) return
  drawer.value = null
  drawerOpen.value = true
  drawerLoading.value = true
  try {
    drawer.value = await getEntityMentions(props.pid, row.name, row.aliases)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '章节定位没取到，稍后再试一次')
    drawerOpen.value = false
  } finally {
    drawerLoading.value = false
  }
}

// 抽屉内跳转：关闭抽屉并定位章节，避免遮挡阅读
function jump(no) {
  drawerOpen.value = false
  emit('locate', no)
}

onMounted(loadOverview)
// 切到未加载过的子页时按需拉取（冲突/索引不在首屏全量返回）
watch(sub, () => { if (overview.value) loadCurrentSub() })
</script>

<style scoped>
.cp { display: flex; flex-direction: column; gap: 14px; }
.cp-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.cp-title { font-family: var(--serif); font-size: 20px; }
.cp-sub { margin-top: 4px; font-size: 13px; color: var(--muted); line-height: 1.6; }
.cp-guide { max-width: 460px; text-align: left; font-size: 13px; color: var(--ink-soft); line-height: 1.8; }
.cp-guide ol { padding-left: 18px; margin-top: 6px; }

/* 结论条 */
.cp-verdict {
  display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px;
  border: 1px solid var(--card-line); border-left: 3px solid var(--ok);
  border-radius: var(--radius); background: var(--card);
}
.cp-verdict.danger { border-left-color: var(--danger); }
.cp-verdict.warning { border-left-color: var(--gold-deep); }
.cp-verdict-tag {
  flex: none; font-size: 12px; padding: 2px 8px; border-radius: 999px;
  background: var(--chip, rgba(0, 0, 0, .05)); color: var(--ink-soft);
}
.cp-verdict.danger .cp-verdict-tag { color: var(--danger); }
.cp-verdict.warning .cp-verdict-tag { color: var(--gold-deep); }
.cp-verdict-text { font-size: 13px; line-height: 1.7; color: var(--ink-soft); }

.cp-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.cp-stat {
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 4px;
}
.cp-stat-num { font-family: var(--serif); font-size: 22px; font-variant-numeric: tabular-nums; }
.cp-stat-label { font-size: 12px; color: var(--muted); }
.cp-stat.warn .cp-stat-num { color: var(--gold-deep); }
.cp-stat.danger .cp-stat-num { color: var(--danger); }

/* 分组 */
.cp-group { background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius); overflow: hidden; }
.cp-group-head {
  width: 100%; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 10px 14px; background: transparent; border: 0; cursor: pointer;
  font: inherit; color: inherit; text-align: left;
}
.cp-group-head:hover { background: var(--hover, rgba(0, 0, 0, .02)); }
.cp-caret { color: var(--muted); transition: transform .15s ease; display: inline-block; }
.cp-caret.open { transform: rotate(90deg); }
.cp-group-name { font-weight: 600; font-size: 14px; }
.cp-group-hint { font-size: 12px; color: var(--muted); }
.cp-group-body { padding: 2px 14px 10px; }
.cp-group-empty { font-size: 12px; color: var(--muted); line-height: 1.7; padding: 6px 0 2px; }
.cp-group-more { font-size: 12px; color: var(--muted); line-height: 1.7; margin-top: 4px; }

/* 条目卡：问题 / 位置 / 建议三要素 */
.cp-card { padding: 10px 0; border-top: 1px dashed var(--card-line); }
.cp-card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cp-card-title { font-size: 13px; line-height: 1.6; }
.cp-card-tag { flex: none; font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid transparent; }
.cp-card-tag.sev-high { color: var(--danger); border-color: var(--danger); }
.cp-card-tag.sev-medium { color: var(--gold-deep); border-color: var(--gold-deep); }
.cp-card-tag.sev-low, .cp-card-tag.sev-info { color: var(--muted); border-color: var(--card-line); }
.cp-card-where { margin-top: 3px; font-size: 12px; color: var(--muted); line-height: 1.6; }
.cp-card-advice { margin-top: 4px; font-size: 12px; color: var(--ink-soft); line-height: 1.7; }
.cp-advice-label {
  display: inline-block; margin-right: 6px; padding: 0 6px; border-radius: 4px;
  background: var(--chip, rgba(0, 0, 0, .05)); color: var(--muted); font-size: 11px;
}
.cp-card-evidence { margin-top: 4px; font-size: 12px; color: var(--gold-deep); line-height: 1.6; }
.cp-card-actions { margin-top: 6px; display: flex; gap: 8px; }

/* 细看区 */
.cp-detail { background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius); padding: 10px 14px 12px; }
.cp-detail-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.cp-tabs { margin-top: 4px; }
.cp-purpose { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 2px 0 10px; }
.cp-filter { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.cp-select { width: 130px; }
.cp-input { width: 220px; }
.cp-count { font-size: 12px; color: var(--muted); }
.cp-tip { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.6; }
.cp-footnote { font-size: 12px; color: var(--muted); line-height: 1.7; margin-top: 10px; }

/* 列表行（设定小抄 / 人物索引），不用表格 */
.cp-row-card {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 9px 6px; border-top: 1px dashed var(--card-line);
}
.cp-row-card.clickable { cursor: pointer; }
.cp-row-card.clickable:hover { background: var(--hover, rgba(0, 0, 0, .02)); }
.cp-row-card-right { display: flex; align-items: center; gap: 8px; flex: none; }
.cp-row-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cp-meta { font-size: 12px; color: var(--muted); line-height: 1.6; }
.cp-link { color: var(--indigo); cursor: pointer; font-weight: 600; font-size: 12px; white-space: nowrap; }

/* 数据边界 */
.cp-boundary {
  font-size: 12px; color: var(--muted); line-height: 1.7;
  padding: 10px 14px; border: 1px dashed var(--card-line); border-radius: var(--radius);
}
.cp-boundary strong { color: var(--ink-soft); font-weight: 600; }
.cp-boundary p { margin-top: 2px; }

/* 抽屉内定位 */
.cp-mention { padding: 8px 0; border-bottom: 1px dashed var(--card-line); }
.cp-mention-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 13px; }
.cp-mention-occ { margin-top: 6px; font-size: 12px; color: var(--ink-soft); line-height: 1.7; }
.cp-pos { color: var(--muted); margin-left: 4px; }

@media (max-width: 900px) {
  .cp-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cp-row-card { flex-direction: column; }
}
</style>
