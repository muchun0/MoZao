<template>
  <div class="cp">
    <header class="cp-head">
      <div>
        <h3 class="cp-title">全书体检</h3>
        <p class="cp-sub">只读汇总已有账本、全书校验与章节质检结果：哪里不一致、哪条伏笔逾期，以及人物/设定都出现在哪些章。</p>
      </div>
      <el-button size="small" :loading="loading" @click="loadOverview">重新体检</el-button>
    </header>

    <!-- 无正文时给出「如何产生数据」的指引，不伪造示例结果 -->
    <el-empty v-if="!loading && !overview?.chapters_written" description="还没有可体检的正文">
      <div class="cp-guide">
        <p>体检数据来自正文与生成过程，按下面顺序先攒素材：</p>
        <ol>
          <li>在「大纲」页生成或导入大纲、世界观（伏笔埋点与回收章来自世界观）。</li>
          <li>在「Agent」页生成正文；每章质检与全书校验会记录一致性问题。</li>
          <li>回到本页点「重新体检」，即可看到覆盖、伏笔与冲突结论。</li>
        </ol>
      </div>
    </el-empty>

    <template v-else-if="overview">
      <!-- 概览卡：四类关键计数 -->
      <div class="cp-stats">
        <div class="cp-stat">
          <span class="cp-stat-num">{{ overview.baseline_ch }}</span>
          <span class="cp-stat-label">已写到第 N 章</span>
        </div>
        <div class="cp-stat" :class="{ warn: overview.coverage.lagging_views.length }">
          <span class="cp-stat-num">{{ overview.coverage.lagging_views.length }}</span>
          <span class="cp-stat-label">账本落后项</span>
        </div>
        <div class="cp-stat" :class="{ danger: (overview.foreshadow.counts.overdue || 0) > 0 }">
          <span class="cp-stat-num">{{ overview.foreshadow.counts.overdue || 0 }}</span>
          <span class="cp-stat-label">逾期伏笔</span>
        </div>
        <div class="cp-stat" :class="{ danger: (overview.conflicts.counts.high || 0) > 0 }">
          <span class="cp-stat-num">{{ overview.conflicts.total }}</span>
          <span class="cp-stat-label">一致性冲突</span>
        </div>
      </div>

      <el-alert v-if="overview.coverage.lagging_views.length" type="warning" :closable="false" show-icon
        :title="`账本落后：${overview.coverage.lagging_views.join('、')}（这些派生视图没跟上已写章节，影响后续一致性判断）`" />

      <el-tabs v-model="sub" class="cp-tabs">
        <!-- 1. 账本覆盖 -->
        <el-tab-pane label="账本覆盖" name="coverage">
          <el-table :data="overview.coverage.views" size="small" class="cp-table">
            <el-table-column prop="view" label="派生视图" min-width="160" />
            <el-table-column prop="label" label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'lagging' ? 'danger' : row.status === 'ahead' ? 'warning' : row.status === 'synced' ? 'success' : 'info'" effect="plain">{{ row.label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="覆盖到第 N 章" width="130">
              <template #default="{ row }">{{ row.covered_ch ?? '未记录' }}</template>
            </el-table-column>
          </el-table>
          <p class="cp-tip">覆盖检查与命令行 <code>tools/projection_check.py</code> 同源；落后项说明该视图还没处理到最新章。</p>
        </el-tab-pane>

        <!-- 2. 伏笔台账 -->
        <el-tab-pane :label="`伏笔台账 (${overview.foreshadow.total})`" name="foreshadow">
          <div class="cp-filter">
            <el-select v-model="fsState" size="small" clearable placeholder="全部状态" class="cp-select" @change="reloadForeshadow">
              <el-option v-for="s in FORESHADOW_STATES" :key="s.value" :value="s.value" :label="s.label" />
            </el-select>
            <span class="cp-count">{{ fsTotal }} 条</span>
          </div>
          <el-empty v-if="!fsItems.length" description="没有符合筛选的伏笔。伏笔埋点与回收章来自世界观设计" :image-size="60" />
          <div v-for="it in fsItems" :key="it.name" class="cp-row">
            <div class="cp-row-main">
              <div class="cp-row-title">
                <el-tag size="small" :type="severityTag(it.severity)" effect="light">{{ it.state_label }}</el-tag>
                <strong>{{ it.name }}</strong>
                <span class="cp-meta">埋于第 {{ it.buried_ch }} 章 · 计划回收第 {{ it.reveal_ch }} 章<span v-if="it.revealed_ch"> · 已在第 {{ it.revealed_ch }} 章回收</span></span>
              </div>
              <p v-if="it.excerpt" class="cp-excerpt">原文：「{{ it.excerpt }}」</p>
            </div>
            <el-button size="small" text type="primary" @click="$emit('locate', it.buried_ch)">去第 {{ it.buried_ch }} 章</el-button>
          </div>
          <el-button v-if="fsItems.length < fsTotal" size="small" text type="primary" :loading="fsLoading" @click="loadMore('foreshadow')">加载更多</el-button>
        </el-tab-pane>

        <!-- 3. 一致性冲突 -->
        <el-tab-pane :label="`一致性冲突 (${overview.conflicts.total})`" name="conflicts">
          <div class="cp-filter">
            <el-select v-model="confSeverity" size="small" clearable placeholder="全部严重度" class="cp-select" @change="reloadConflicts">
              <el-option value="high" label="高" /><el-option value="medium" label="中" /><el-option value="low" label="低" />
            </el-select>
            <span class="cp-count">{{ confTotal }} 条（按严重度排序）</span>
          </div>
          <el-empty v-if="!confItems.length" description="暂无记录的一致性冲突。结论来自全书校验与章节质检，未记录不代表全书无矛盾" :image-size="60" />
          <div v-for="(it, i) in confItems" :key="i" class="cp-row">
            <div class="cp-row-main">
              <div class="cp-row-title">
                <el-tag size="small" :type="severityTag(it.severity)" effect="dark">{{ severityLabel(it.severity) }}</el-tag>
                <span class="cp-type">{{ it.type }}</span>
                <span class="cp-meta">{{ it.source === 'verify' ? '全书一致性校验' : '章节质检' }}</span>
              </div>
              <p class="cp-detail">{{ it.detail }}</p>
              <p v-if="it.evidence" class="cp-excerpt">原文证据：「{{ it.evidence }}」</p>
              <p v-if="it.suggestion" class="cp-suggest">建议：{{ it.suggestion }}</p>
            </div>
            <el-button size="small" text type="primary" @click="$emit('locate', it.chapter)">去第 {{ it.chapter }} 章</el-button>
          </div>
          <el-button v-if="confItems.length < confTotal" size="small" text type="primary" :loading="confLoading" @click="loadMore('conflicts')">加载更多</el-button>
        </el-tab-pane>

        <!-- 4. 人物·设定索引 -->
        <el-tab-pane label="人物·设定索引" name="entities">
          <div class="cp-filter">
            <el-input v-model="entityQuery" size="small" clearable placeholder="搜索姓名 / 别名" class="cp-input"
              @keyup.enter="reloadEntities" @clear="reloadEntities" />
            <el-select v-model="entityKind" size="small" clearable placeholder="全部类型" class="cp-select" @change="reloadEntities">
              <el-option v-for="k in ENTITY_KIND_OPTIONS" :key="k.value" :value="k.value" :label="k.label" />
            </el-select>
            <span class="cp-count">{{ entityTotal }} 个</span>
          </div>
          <el-empty v-if="!entityItems.length" description="没有可索引的人物或设定。先生成/导入世界观" :image-size="60" />
          <el-table v-else :data="entityItems" size="small" class="cp-table" @row-click="openMentions">
            <el-table-column label="名称" min-width="130">
              <template #default="{ row }">
                <span class="cp-link">{{ row.name }}</span>
                <div v-if="row.aliases.length" class="cp-alias">别名：{{ row.aliases.join('、') }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="kind_label" label="类型" width="70" />
            <el-table-column label="出现章节" min-width="220">
              <template #default="{ row }">
                <template v-if="row.chapters.length">
                  <el-button v-for="c in row.chapters.slice(0, 6)" :key="c.no" size="small" text type="primary"
                    @click.stop="$emit('locate', c.no)">第{{ c.no }}章<template v-if="c.count > 1">×{{ c.count }}</template></el-button>
                  <span v-if="row.chapters.length > 6" class="cp-meta">…共 {{ row.chapters.length }} 章</span>
                </template>
                <span v-else class="cp-gap">正文未出现</span>
              </template>
            </el-table-column>
            <el-table-column label="首见 / 末见" width="110">
              <template #default="{ row }">{{ row.first_ch ?? '—' }} / {{ row.last_ch ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="次数" prop="total_mentions" width="64" />
          </el-table>
          <el-button v-if="entityItems.length < entityTotal" size="small" text type="primary" :loading="entityLoading" @click="loadMore('entities')">加载更多</el-button>
        </el-tab-pane>
      </el-tabs>

      <p v-for="gap in overview.data_gaps" :key="gap" class="cp-gap-note">· {{ gap }}</p>
    </template>

    <!-- 实体章节定位抽屉 -->
    <el-drawer v-model="drawerOpen" :title="drawer?.name ? `${drawer.name} · 章节定位` : '章节定位'" size="480px">
      <div v-if="drawerLoading" class="cp-tip">扫描中…</div>
      <template v-else-if="drawer">
        <p class="cp-tip">匹配别名：{{ drawer.aliases.join('、') }} · 命中 {{ drawer.total_chapters }} 章<template v-if="drawer.truncated">（仅展示前 50 章）</template></p>
        <div v-for="c in drawer.chapters" :key="c.no" class="cp-mention">
          <div class="cp-mention-head">
            <strong>第{{ c.no }}章 {{ c.title }}</strong>
            <el-button size="small" text type="primary" @click="jump(c.no)">打开章节</el-button>
          </div>
          <div v-for="(o, i) in c.occurrences" :key="i" class="cp-mention-occ">
            <span class="cp-offset">第 {{ o.offset }} 字</span>
            <span>{{ o.snippet }}</span>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getConsistencyOverview, getForeshadowLedger, getConsistencyConflicts,
  getEntityIndex, getEntityMentions,
} from '../../api/consistency'
import { FORESHADOW_STATES, ENTITY_KIND_OPTIONS, severityMeta } from '../../utils/consistency'

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
// 冲突
const confItems = ref([])
const confTotal = ref(0)
const confSeverity = ref('')
const confLoading = ref(false)
// 实体
const entityItems = ref([])
const entityTotal = ref(0)
const entityQuery = ref('')
const entityKind = ref('')
const entityLoading = ref(false)
const drawerOpen = ref(false)
const drawer = ref(null)
const drawerLoading = ref(false)

const severityTag = (sev) => severityMeta(sev).type
const severityLabel = (sev) => severityMeta(sev).label

async function loadOverview() {
  loading.value = true
  try {
    overview.value = await getConsistencyOverview(props.pid)
    // 首屏按当前子页同步一次，避免切页后展示旧空表
    await loadCurrentSub()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '体检数据加载失败')
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
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '伏笔台账加载失败')
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
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '冲突清单加载失败')
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
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '人物索引加载失败')
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

// 实体行点击：拉取该实体的章节内定位（含原文片段与字符偏移）
async function openMentions(row) {
  if (!row?.chapters?.length) return
  drawer.value = null
  drawerOpen.value = true
  drawerLoading.value = true
  try {
    drawer.value = await getEntityMentions(props.pid, row.name, row.aliases)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '章节定位加载失败')
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

.cp-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.cp-stat {
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 4px;
}
.cp-stat-num { font-family: var(--serif); font-size: 22px; font-variant-numeric: tabular-nums; }
.cp-stat-label { font-size: 12px; color: var(--muted); }
.cp-stat.warn .cp-stat-num { color: var(--gold-deep); }
.cp-stat.danger .cp-stat-num { color: var(--danger); }

.cp-tabs { background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius); padding: 4px 14px 12px; }
.cp-table { background: transparent; }
.cp-filter { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.cp-select { width: 130px; }
.cp-input { width: 220px; }
.cp-count { font-size: 12px; color: var(--muted); }
.cp-tip { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.6; }
.cp-gap-note { font-size: 12px; color: var(--muted); line-height: 1.7; }

.cp-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 10px 4px; border-bottom: 1px dashed var(--card-line); }
.cp-row-main { min-width: 0; }
.cp-row-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cp-meta { font-size: 12px; color: var(--muted); }
.cp-type { font-size: 13px; color: var(--ink-soft); }
.cp-detail { margin-top: 4px; font-size: 13px; line-height: 1.6; }
.cp-excerpt { margin-top: 4px; font-size: 12px; color: var(--gold-deep); line-height: 1.6; }
.cp-suggest { margin-top: 2px; font-size: 12px; color: var(--ok); line-height: 1.6; }
.cp-gap { font-size: 12px; color: var(--danger); }
.cp-link { color: var(--indigo); cursor: pointer; font-weight: 600; }
.cp-alias { font-size: 12px; color: var(--muted); }

.cp-mention { padding: 8px 0; border-bottom: 1px dashed var(--card-line); }
.cp-mention-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 13px; }
.cp-mention-occ { margin-top: 6px; font-size: 12px; color: var(--ink-soft); line-height: 1.6; }
.cp-offset { color: var(--muted); margin-right: 6px; font-variant-numeric: tabular-nums; }

@media (max-width: 900px) {
  .cp-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
