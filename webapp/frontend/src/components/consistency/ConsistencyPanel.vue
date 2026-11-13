<template>
  <div ref="rootRef" class="cp">
    <header class="cp-head">
      <div>
        <h3 class="cp-title">全书体检</h3>
        <p class="cp-sub">汇总已有的通读检查与单章质检记录；不跑新检查、不改正文、不扣费。</p>
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
      <!-- 结论与关键数字压成一条：先看整体，再往下翻明细 -->
      <div class="cp-hero" :class="verdict.level">
        <span class="cp-hero-tag">{{ verdict.tag }}</span>
        <p class="cp-hero-text">{{ verdict.text }}</p>
        <div class="cp-hero-kpis">
          <span class="cp-kpi">{{ overview.chapters_written }} 章</span>
          <span v-if="counts.urgent" class="cp-kpi danger">{{ counts.urgent }} 必须处理</span>
          <span v-if="counts.suggest" class="cp-kpi warn">{{ counts.suggest }} 建议处理</span>
        </div>
      </div>

      <!-- R13：正文改过后派生记录没重算，先提醒结论可能过期（文案来自后端，两处共用） -->
      <StaleNotice v-if="staleNotice" :notice="staleNotice" />
      <!-- 一条导航到底：待处理汇总 + 四个分类明细，筛选跟着分类走 -->
      <div class="cp-nav">
        <div class="cp-nav-tabs">
          <button v-for="t in SUB_TABS" :key="t.name" type="button" class="cp-tab"
            :class="{ active: sub === t.name }" @mousedown="rememberScroll" @click="switchSub(t.name)">
            {{ t.label }}<span v-if="t.count !== undefined" class="cp-tab-count">{{ t.count }}</span>
          </button>
        </div>
        <div class="cp-nav-filter">
          <template v-if="sub === 'foreshadow'">
            <el-select v-model="fsState" size="small" clearable placeholder="全部状态" class="cp-select"
              @change="reloadForeshadow">
              <el-option v-for="s in FORESHADOW_STATES" :key="s.value" :value="s.value" :label="s.label" />
            </el-select>
            <span class="cp-count">{{ fsLoaded ? `${fsTotal} 条` : '读取中…' }}</span>
          </template>
          <template v-else-if="sub === 'conflicts'">
            <el-select v-model="confSeverity" size="small" clearable placeholder="全部轻重" class="cp-select"
              @change="reloadConflicts">
              <el-option value="high" label="必须处理" />
              <el-option value="medium" label="建议处理" />
              <el-option value="low" label="了解一下" />
            </el-select>
            <span class="cp-count">{{ confLoaded ? `${confTotal} 条` : '读取中…' }}</span>
          </template>
          <template v-else-if="sub === 'entities'">
            <el-input v-model="entityQuery" size="small" clearable placeholder="搜名字或别名，例如「林舟」"
              class="cp-input" @keyup.enter="reloadEntities" @clear="reloadEntities" />
            <el-select v-model="entityKind" size="small" clearable placeholder="全部类型" class="cp-select"
              @change="reloadEntities">
              <el-option v-for="k in ENTITY_KIND_OPTIONS" :key="k.value" :value="k.value" :label="k.label" />
            </el-select>
            <span class="cp-count">{{ entityLoaded ? `${entityTotal} 个` : '读取中…' }}</span>
          </template>
        </div>
      </div>

      <p class="cp-purpose">{{ PURPOSE[sub] }}</p>

      <!-- 0. 待处理：把「必须先处理 / 建议处理」合成一条清单，默认只看这里 -->
      <div v-show="sub === 'todo'" class="cp-pane">
        <p v-if="!todoItems.length" class="cp-group-empty">没有需要马上处理的问题。想逐条翻记录，切上面的分类。</p>
        <article v-for="(it, i) in todoItems" :key="`t-${i}`" class="cp-item">
          <div class="cp-item-top">
            <span class="cp-dot" :class="`sev-${it.severity}`"></span>
            <strong class="cp-item-title">{{ it.title }}</strong>
            <span v-if="it.categoryLabel" class="cp-item-cat">{{ it.categoryLabel }}</span>
            <span v-if="it.recordStale" class="cp-stale-tag">记录可能过期</span>
          </div>
          <p class="cp-item-where">{{ it.where }}</p>
          <div v-if="it.missingChapters && it.missingChapters.length" class="cp-chips">
            <span class="cp-chip-label">缺记录</span>
            <button v-for="no in it.missingChapters" :key="no" type="button" class="cp-chap-chip"
              @click="emitLocate(no)">第 {{ no }} 章</button>
            <span v-if="it.missingCount > it.missingChapters.length" class="cp-meta">等共 {{ it.missingCount }} 章</span>
          </div>
          <p class="cp-item-advice"><span class="cp-advice-label">建议</span>{{ it.advice }}</p>
          <div v-if="it.details?.length || it.sides" v-show="isExpanded(`t-${i}`)" class="cp-item-detail">
            <p v-if="it.sides?.expected"><span class="cp-side-label">{{ it.sides.expectedLabel }}</span>{{ it.sides.expected }}</p>
            <p v-if="it.sides?.actual"><span class="cp-side-label">{{ it.sides.actualLabel }}</span>{{ it.sides.actual }}</p>
            <p v-for="(d, di) in it.details || []" :key="di">
              <span class="cp-detail-label">{{ d.label }}</span>{{ d.text }}
            </p>
          </div>
          <div class="cp-item-foot">
            <el-button v-if="it.details?.length || it.sides" size="small" text @click="toggleExpand(`t-${i}`)">
              {{ isExpanded(`t-${i}`) ? '收起依据' : '看依据' }}
            </el-button>
            <el-button v-if="it.jump" size="small" text type="primary" @click="emitLocate(it.jump)">
              打开第 {{ it.jump }} 章
            </el-button>
            <el-button v-if="it.source === 'chapter_quality' && it.chapter" size="small" text type="primary"
              @click="emit('open-versions', it.chapter)">看第 {{ it.chapter }} 章改稿建议</el-button>
          </div>
        </article>
        <p v-if="todoMore" class="cp-footnote">还有 {{ todoMore }} 条没列，完整的在「前后对不上」「伏笔回收」里按分类看。</p>
      </div>

        <!-- 1. 设定进度：只列作者看得到的记录，明细按需展开 -->
        <div v-show="sub === 'coverage'" class="cp-pane">
          <p v-if="!authorViews.length" class="cp-group-empty">
            暂时还没有可对照的记录。等生成过正文后，这里会显示每项记录跟到了第几章。
          </p>
          <article v-for="(row, ri) in authorViews" :key="row.view" class="cp-item">
            <div class="cp-item-top">
              <strong class="cp-item-title">{{ viewName(row.view) }}</strong>
              <el-tag size="small" :type="coverageDisplayTag(row)" effect="plain">{{ coverageDisplayState(row) }}</el-tag>
              <span v-if="row.staleChapters.length" class="cp-stale-tag">记录可能过期</span>
            </div>
            <p class="cp-item-where">{{ row.gapText }}</p>
            <div v-if="row.missingChapters.length" class="cp-chips">
              <span class="cp-chip-label">缺记录</span>
              <button v-for="no in row.missingChapters.slice(0, 8)" :key="no" type="button" class="cp-chap-chip"
                @click="emitLocate(no)">第 {{ no }} 章</button>
              <span v-if="row.missingCount > row.missingChapters.slice(0, 8).length" class="cp-meta">
                等共 {{ row.missingCount }} 章
              </span>
            </div>
            <div v-if="row.staleChapters.length" class="cp-chips">
              <span class="cp-chip-label">过期章</span>
              <button v-for="no in row.staleChapters.slice(0, 8)" :key="no" type="button" class="cp-chap-chip"
                @click="emitLocate(no)">第 {{ no }} 章</button>
            </div>
            <div v-show="isExpanded(`cv-${ri}`)" class="cp-item-detail">
              <p><span class="cp-detail-label">会影响</span>{{ row.effectText }}</p>
              <p><span class="cp-advice-label">建议</span>{{ row.adviceText }}</p>
              <div v-if="row.recordedChapters.length" class="cp-records">
                <div class="cp-records-head">
                  <span class="cp-chip-label">清单里已有的</span>
                  <span class="cp-meta">
                    {{ row.recordedChapterCount }} 章 · {{ row.recordedItemCount }} 条
                    <template v-if="row.recordedTruncated">（先列前 {{ row.recordedChapters.length }} 章）</template>
                  </span>
                </div>
                <div v-for="c in row.recordedChapters.slice(0, 6)" :key="c.ch" class="cp-record-row">
                  <button type="button" class="cp-chap-link" @click="emitLocate(c.ch)">第 {{ c.ch }} 章</button>
                  <div class="cp-record-items">
                    <p v-for="(t, ti) in c.items" :key="ti" :class="{ 'cp-muted-item': t === NO_DESC }">{{ t }}</p>
                    <p v-if="c.count > c.items.length" class="cp-meta">还有 {{ c.count - c.items.length }} 条</p>
                  </div>
                </div>
                <p v-if="row.recordedChapters.length > 6" class="cp-meta">还有 {{ row.recordedChapters.length - 6 }} 章没展开。</p>
              </div>
              <p v-else-if="row.locatable" class="cp-meta">这份清单里还没有记录可以列。</p>
            </div>
            <div class="cp-item-foot">
              <el-button size="small" text @click="toggleExpand(`cv-${ri}`)">
                {{ isExpanded(`cv-${ri}`) ? '收起明细' : '看影响与已有记录' }}
              </el-button>
              <el-button v-if="row.firstMissingCh" size="small" text type="primary"
                @click="emitLocate(row.firstMissingCh)">打开第 {{ row.firstMissingCh }} 章</el-button>
            </div>
          </article>
          <p class="cp-footnote">
            <template v-if="hiddenViews.length">
              另有 {{ hiddenViews.length }} 项系统内部账本只做自检，界面上不展开，也不用你维护。
            </template>
            <template v-else>上面只列你能看到内容的记录。</template>
          </p>
        </div>

          <!-- 2. 伏笔：只给结论，台账在工具中心「伏笔」 -->
          <div v-show="sub === 'foreshadow'" class="cp-pane">
            <p v-if="fsLoaded && !fsActionable.length" class="cp-group-empty">{{ fsEmptyText }}</p>
            <article v-for="(it, i) in fsActionable" :key="it.name" class="cp-item">
              <div class="cp-item-top">
                <span class="cp-dot" :class="`sev-${it.severity}`"></span>
                <strong class="cp-item-title">{{ fsProblem(it) }}</strong>
                <span class="cp-item-cat">{{ fsLabel(it.state) }}</span>
                <span v-if="it.record_stale" class="cp-stale-tag">记录可能过期</span>
              </div>
              <p class="cp-item-where">{{ fsWhere(it) }}</p>
              <p class="cp-item-advice"><span class="cp-advice-label">建议</span>{{ fsAdvice(it) }}</p>
              <div v-show="isExpanded(`fs-${i}`)" class="cp-item-detail">
                <p v-if="it.excerpt"><span class="cp-detail-label">原文</span>「{{ it.excerpt }}」</p>
              </div>
              <div class="cp-item-foot">
                <el-button v-if="it.excerpt" size="small" text @click="toggleExpand(`fs-${i}`)">
                  {{ isExpanded(`fs-${i}`) ? '收起原文' : '看原文' }}
                </el-button>
                <el-button v-if="it.buried_ch" size="small" text type="primary" @click="emitLocate(it.buried_ch)">
                  打开第 {{ it.buried_ch }} 章
                </el-button>
              </div>
            </article>
            <el-button v-if="fsItems.length < fsTotal" size="small" text type="primary" :loading="fsLoading"
              @click="loadMore('foreshadow')">再看一些</el-button>
            <p class="cp-footnote">
              台账（改名、埋点章、计划回收章、等级）在「工具中心 · 伏笔」；这里只列需要处理的，
              <template v-if="fsLoaded && fsItems.length > fsActionable.length">其余正常推进的不重复列。</template>
            </p>
          </div>

          <!-- 3. 前后不一致 -->
          <div v-show="sub === 'conflicts'" class="cp-pane">
            <p v-if="confLoaded && !confItems.length" class="cp-group-empty">{{ confEmptyText }}</p>
            <article v-for="(it, i) in confItems" :key="i" class="cp-item">
              <div class="cp-item-top">
                <span class="cp-dot" :class="`sev-${it.severity}`"></span>
                <strong class="cp-item-title">{{ it.headline }}</strong>
                <span v-if="it.categoryLabel" class="cp-item-cat">{{ it.categoryLabel }}</span>
                <span v-if="it.record_stale" class="cp-stale-tag">记录可能过期</span>
              </div>
              <p class="cp-item-where">涉及：{{ it.scopeText }}</p>
              <p class="cp-item-advice"><span class="cp-advice-label">建议</span>{{ adviceOf(it) }}</p>
              <div v-show="isExpanded(`c-${i}`)" class="cp-item-detail">
                <div v-if="it.sides">
                  <p v-if="it.sides.expected"><span class="cp-side-label">{{ it.sides.expectedLabel }}</span>{{ it.sides.expected }}</p>
                  <p v-if="it.sides.actual"><span class="cp-side-label">{{ it.sides.actualLabel }}</span>{{ it.sides.actual }}</p>
                </div>
                <p v-if="!it.sides && it.actualText"><span class="cp-detail-label">记录原文</span>{{ it.actualText }}</p>
                <p><span class="cp-detail-label">依据</span>{{ it.basisText }}</p>
                <p v-for="b in it.basisExcerpts" :key="b.name">
                  <span class="cp-detail-label">对照设定</span>「{{ b.name }}」：{{ b.excerpt }}
                </p>
                <p v-if="it.checkType"><span class="cp-detail-label">检查项</span>{{ it.checkType }}</p>
                <p v-if="it.note"><span class="cp-detail-label">说明</span>{{ it.note }}</p>
                <p><span class="cp-detail-label">为什么这样判</span>{{ it.reasonText }}</p>
              </div>
              <div class="cp-item-foot">
                <el-button size="small" text @click="toggleExpand(`c-${i}`)">
                  {{ isExpanded(`c-${i}`) ? '收起依据' : '看依据' }}
                </el-button>
                <el-button v-if="it.chapter && it.source !== 'chapter_quality'" size="small" text type="primary"
                  @click="emitLocate(it.chapter)">打开第 {{ it.chapter }} 章</el-button>
                <el-button v-if="it.chapter && it.source === 'chapter_quality'" size="small" text type="primary"
                  @click="emit('open-versions', it.chapter)">看第 {{ it.chapter }} 章改稿建议</el-button>
              </div>
            </article>
            <el-button v-if="confItems.length < confTotal" size="small" text type="primary" :loading="confLoading"
              @click="loadMore('conflicts')">再看一些</el-button>
            <p class="cp-footnote">{{ conflictSourceLine }}；逐条证据与改法在对应章的「版本」弹窗里。</p>
          </div>

          <!-- 4. 人物 / 设定索引 -->
          <div v-show="sub === 'entities'" class="cp-pane">
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
          </div>

      <!-- 数据边界：默认收起，需要时再展开 -->
      <div v-if="gapNotes.length" class="cp-boundary">
        <button type="button" class="cp-boundary-head" @click="showBoundary = !showBoundary">
          <span class="cp-caret" :class="{ open: showBoundary }">▸</span>
          这份体检的边界（{{ gapNotes.length }} 条查不到的地方）
        </button>
        <div v-show="showBoundary" class="cp-boundary-body">
          <p v-for="(note, i) in gapNotes" :key="i">· {{ note }}</p>
        </div>
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
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getConsistencyOverview, getForeshadowLedger, getConsistencyConflicts,
  getEntityIndex, getEntityMentions,
} from '../../api/consistency'
import {
  FORESHADOW_STATES, ENTITY_KIND_OPTIONS, SEVERITY_ORDER,
  severityMeta, severityGroup, projectionViewLabel, coverageStateLabel,
  coverageStateType, foreshadowStateLabel, foreshadowProblem, foreshadowWhere,
  foreshadowAdvice, conflictAdvice, entitySummary, humanizeGap,
  coverageGap, coverageEffect, coverageAdvice,
  coverageShowInMust, coverageWhatText,
  conflictCategory, conflictHeadline, conflictBasis, conflictSides,
  conflictReason, conflictScope, conflictCheckType, conflictSourceText,
} from '../../utils/consistency'
import { staleNoticeOf, isItemStale, staleChaptersOf } from '../../utils/staleness'
import StaleNotice from '../common/StaleNotice.vue'

const props = defineProps({ pid: { type: String, required: true } })
const emit = defineEmits(['locate', 'open-versions'])

const loading = ref(false)
const overview = ref(null)
const sub = ref('todo')
const showBoundary = ref(false)
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

const severityLabel = (sev) => severityMeta(sev).label
const fsLabel = (state) => foreshadowStateLabel(state)
const fsProblem = (it) => foreshadowProblem(it)
const fsWhere = (it) => foreshadowWhere(it)
const fsAdvice = (it) => foreshadowAdvice(it)
const adviceOf = (it) => conflictAdvice(it)
const summaryOf = (row) => entitySummary(row)
const viewName = (view) => projectionViewLabel(view)
// 内部记录不暴露「落后」红标，统一叫「后台记录」，避免作者误以为要去补
const coverageState = (row) => (row.showInMust ? coverageStateLabel(row.status) : '后台记录')
const coverageTag = (row) => (row.showInMust ? coverageStateType(row.status) : 'info')
// 状态说「跟上了」却仍列出缺记录章时，按事实给标签，避免自相矛盾
const coverageDisplayState = (row) => (row.missingCount ? `缺 ${row.missingCount} 章` : coverageState(row))
const coverageDisplayTag = (row) => (row.missingCount && row.status !== 'lagging' ? 'warning' : coverageTag(row))

const baselineCh = computed(() => overview.value?.baseline_ch || 0)

// 冲突计数口径：全书校验 / 单章质检 / 合并去重后的条数，由后端同一次遍历产出
const conflictSourceLine = computed(() => conflictSourceText(overview.value?.conflicts?.sources))
// R13：记录可能过期的统一提示（文案与判定均来自后端，生成过程卡共用同一组件）
const staleNotice = computed(() => staleNoticeOf(overview.value))

// 覆盖清单里「没有可读描述」的条目统一标注，与后端常量保持一致
const NO_DESC = '只有编号，没有描述'

// 用途说明：切页时跟着换，一句话说清这块回答什么问题
const PURPOSE = {
  todo: '把需要动的地方按轻重排在一起，先看这一页；想按来源逐条翻，再切后面的分类。',
  coverage: '这块看：AI 写新章节时会参考的「章节摘要 / 场景清单」有没有跟到最新章节。',
  foreshadow: '这块看：埋下还没收的线索里，需要你处理的那几条；台账在「工具中心 · 伏笔」。',
  conflicts: '这块看：通读检查和单章质检说「和前面设定或情节对不上」的地方，只读汇总、不重跑检查。',
  entities: '这块看：人物、地点、势力、物件各出现在哪些章，点一行能看原文句子。',
}

// 一条导航贯穿到底：待处理 → 四类明细；标签用短词，避免换行拥挤
const SUB_TABS = computed(() => [
  { name: 'todo', label: '待处理', count: (counts.value.urgent || 0) + (counts.value.suggest || 0) },
  { name: 'coverage', label: '设定进度', count: authorLagging.value || 0 },
  { name: 'foreshadow', label: '伏笔回收', count: overview.value?.foreshadow?.total ?? 0 },
  { name: 'conflicts', label: '前后对不上', count: overview.value?.conflicts?.total ?? 0 },
  { name: 'entities', label: '人物索引' },
])

// 长原文默认两行截断，按条展开
const expanded = ref({})
const isExpanded = (key) => Boolean(expanded.value[key])
function toggleExpand(key) {
  expanded.value = { ...expanded.value, [key]: !expanded.value[key] }
}

// ---- 切分类时保住阅读位置：记下滚动容器与偏移，内容加载后还原 ----
const rootRef = ref(null)
let savedScroller = null
let savedScrollTop = 0

function scrollParentOf(node) {
  let p = node?.parentElement
  while (p) {
    const style = getComputedStyle(p)
    if (/(auto|scroll)/.test(`${style.overflowY} ${style.overflow}`) && p.scrollHeight > p.clientHeight) return p
    p = p.parentElement
  }
  return null
}

function rememberScroll() {
  savedScroller = scrollParentOf(rootRef.value)
  savedScrollTop = savedScroller ? savedScroller.scrollTop : window.scrollY
}

async function restoreScroll() {
  await nextTick()
  const target = savedScroller?.isConnected ? savedScroller : null
  const html = document.documentElement
  const prevBehavior = html.style.scrollBehavior
  // 全局样式是 html { scroll-behavior: smooth }，还原必须瞬时完成，否则切页瞬间会被当成动画而“飘”回去
  html.style.scrollBehavior = 'auto'
  try {
    const current = target ? target.scrollTop : window.scrollY
    if (Math.abs(current - savedScrollTop) > 1) {
      if (target) target.scrollTop = savedScrollTop
      else window.scrollTo(0, savedScrollTop)
    }
  } finally {
    html.style.scrollBehavior = prevBehavior
  }
}

function switchSub(name) {
  if (sub.value !== name) sub.value = name
}

// 覆盖项按当前基准章计算「缺了哪几章」，避免模板里反复取整章号
const coverageViews = computed(() => (overview.value?.coverage?.views || []).map((row) => ({
  ...row,
  gapText: coverageGap(row, baselineCh.value),
  whatText: coverageWhatText(row),
  showInMust: coverageShowInMust(row),
  effectText: coverageEffect(row),
  adviceText: coverageAdvice(row),
  firstMissingCh: (row.missing_chapters || [])[0] || 0,
  missingChapters: row.missing_chapters || [],
  missingCount: row.missing_count || 0,
  recordedChapters: row.recorded_chapters || [],
  recordedChapterCount: row.recorded_chapter_count || 0,
  recordedItemCount: row.recorded_item_count || 0,
  recordedTruncated: Boolean(row.recorded_truncated),
  staleChapters: staleChaptersOf(row),
})))

// REQ-R23 收敛：体检只展开「作者看得到内容」的记录（章节摘要/场景清单），
// 系统内部账本（关键剧情节点、人物当前状态、前情提要等）不在本页重复罗列——
// 它们各自的事实页面已经在别处，这里只留一句数量说明。
const authorViews = computed(() => coverageViews.value.filter((row) => row.showInMust))
const hiddenViews = computed(() => coverageViews.value.filter((row) => !row.showInMust))

// 冲突项预先把「依据 / 预期实际 / 判定理由」算好，模板只负责展示
function decorateConflict(it) {
  const basis = conflictBasis(it)
  const sides = conflictSides(it)
  return {
    ...it,
    categoryLabel: conflictCategory(it),
    headline: conflictHeadline(it),
    scopeText: conflictScope(it),
    basisText: basis.map((b) => b.text).join(' / ') || '检查记录',
    basisExcerpts: basis.filter((b) => b.kind === 'setting' && b.excerpt)
      .map((b) => ({ name: b.name, excerpt: b.excerpt })),
    checkType: conflictCheckType(it),
    sides,
    actualText: sides ? '' : (it.actual || '').trim(),
    reasonText: conflictReason(it),
    // 超过两行才需要「展开原文」，短条目不上按钮
    longContent: [it.expected, it.actual, it.detail].some((t) => (t || '').length > 70),
  }
}
const gapNotes = computed(() => (overview.value?.data_gaps || []).map(humanizeGap))

// 只统计作者可见记录的落后项：内部账本已不在本页展开，也不再计入「要处理」的数字
const authorLagging = computed(() => authorViews.value.filter((row) => row.status === 'lagging').length)

// 各分组真实总数（来自后端计数，不受首屏预览条数限制）
const counts = computed(() => {
  const ov = overview.value
  if (!ov) return { urgent: 0, suggest: 0, minor: 0 }
  const c = ov.conflicts?.counts || {}
  const f = ov.foreshadow?.counts || {}
  return {
    urgent: (c.high || 0) + (f.overdue || 0) + authorLagging.value,
    suggest: (c.medium || 0) + (f.stalled || 0),
    minor: (c.low || 0) + (c.info || 0) + (f.due_soon || 0) + (f.planted || 0) + (f.revealed || 0),
  }
})

// 结论：只说「有多少、先看哪组」；没有记录时明确说明没有判断依据
const verdict = computed(() => {
  const ov = overview.value
  const c = counts.value
  const hasSignal = (ov?.conflicts?.total || 0) + (ov?.foreshadow?.total || 0) + authorLagging.value
  if (!hasSignal) {
    return { level: 'ok', tag: '还没结论', text: '目前还没有可判断的记录（还没有通读检查或质检记录）。多写几章、跑一次全书检查后，这里才会给出结论。' }
  }
  if (c.urgent) {
    return { level: 'danger', tag: '要处理', text: `全书有 ${c.urgent} 处建议马上处理，先看下面的待处理清单，已按轻重排好。` }
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

  // 设定小抄落后：只把「作者看得到内容」的记录放进必须先处理（内部账本不进）
  for (const row of coverageViews.value) {
    if (row.status !== 'lagging' || !row.showInMust) continue
    const sev = row.severity || 'high'
    items.push({
      severity: sev, group: severityGroup(sev),
      view: row.view,
      title: row.missing_count
        ? `${projectionViewLabel(row.view)}缺了 ${row.missing_count} 章的记录`
        : `${projectionViewLabel(row.view)}还没跟上正文`,
      what: row.whatText,
      where: row.gapText,
      details: [
        { label: '会影响', text: row.effectText },
      ],
      missingChapters: row.missingChapters,
      missingCount: row.missingCount,
      recordedChapters: row.recordedChapters,
      recordedChapterCount: row.recordedChapterCount,
      recordedItemCount: row.recordedItemCount,
      recordedTruncated: row.recordedTruncated,
      recordStale: row.staleChapters.length > 0,
      advice: row.adviceText,
      jump: row.firstMissingCh || ov.baseline_ch || 0,
    })
  }

  for (const it of ov.foreshadow?.items || []) {
    const sev = it.severity || 'info'
    items.push({
      severity: sev, group: severityGroup(sev),
      title: foreshadowProblem(it), where: foreshadowWhere(it),
      advice: foreshadowAdvice(it), jump: it.buried_ch || 0,
      recordStale: isItemStale(it),
      details: it.excerpt ? [{ label: '原文', text: `「${it.excerpt}」` }] : [],
    })
  }

  for (const raw of ov.conflicts?.items || []) {
    const it = decorateConflict(raw)
    const sev = it.severity || 'medium'
    items.push({
      severity: sev, group: severityGroup(sev),
      title: it.headline,
      where: `涉及：${it.scopeText}`,
      details: [
        { label: '依据', text: it.basisText },
        ...it.basisExcerpts.map((b) => ({ label: '对照设定', text: `「${b.name}」：${b.excerpt}` })),
        ...(it.actualText ? [{ label: '记录原文', text: it.actualText }] : []),
        ...(it.checkType ? [{ label: '检查项', text: it.checkType }] : []),
        ...(it.note ? [{ label: '说明', text: it.note }] : []),
        { label: '为什么这样判', text: it.reasonText },
      ],
      sides: it.sides,
      category: it.category,
      categoryLabel: it.categoryLabel,
      recordStale: isItemStale(it),
      advice: conflictAdvice(it), jump: it.chapter || 0,
      source: it.source, chapter: it.chapter || 0,
    })
  }

  return items.sort((a, b) => (SEVERITY_ORDER[b.severity] || 0) - (SEVERITY_ORDER[a.severity] || 0))
}

const groupedItems = computed(() => buildGroupedItems(overview.value))

// 待处理页：只放「必须 / 建议」两档，首屏最多 6 条，避免一屏堆满
const TODO_CAP = 6
const todoAll = computed(() => groupedItems.value.filter((it) => it.group === 'must' || it.group === 'suggest'))
const todoItems = computed(() => todoAll.value.slice(0, TODO_CAP))
const todoMore = computed(() => Math.max(0, todoAll.value.length - todoItems.value.length))

const fsEmptyText = computed(() => (fsState.value
  ? '这个状态下暂时没有需要处理的伏笔，换个状态看看。'
  : '现在没有需要处理的伏笔。台账（新增、改章号、改等级）在「工具中心 · 伏笔」。'))

// 只列需要处理的伏笔（已埋下/已收掉属于正常推进，不在体检页重复列）
const FS_ACTIONABLE = ['overdue', 'stalled', 'due_soon', 'not_buried']
const fsActionable = computed(() => fsItems.value.filter(
  (it) => FS_ACTIONABLE.includes(it.state) || it.record_stale))

const confEmptyText = computed(() => (confSeverity.value
  ? '这个轻重下暂时没有记录，换个轻重看看。'
  : '暂时没发现前后对不上的地方。这里只汇总已有的通读检查和单章质检记录，没记录不等于全书没问题。'))

const entityEmptyText = computed(() => (entityQuery.value
  ? `没有找到叫「${entityQuery.value}」的人物或设定，换个名字或别名再搜。`
  : '还没有可查的人物或设定。先在大纲或世界观里生成人物与设定，这里就能查到它们出现在哪几章。'))

// 章节跳转：只在有真实章号时发事件
function emitLocate(no) {
  if (no) emit('locate', no)
}

async function loadOverview() {
  loading.value = true
  try {
    overview.value = await getConsistencyOverview(props.pid)
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
    confItems.value = data.items.map(decorateConflict)
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
    confItems.value = confItems.value.concat(data.items.map(decorateConflict))
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
watch(sub, async () => {
  if (overview.value) await loadCurrentSub()
  // 切页后内容高度会变，这里把滚动位置还原回切换前
  await restoreScroll()
})
</script>

<style scoped>
.cp { display: flex; flex-direction: column; gap: 14px; }
.cp-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.cp-title { font-family: var(--serif); font-size: 20px; }
.cp-sub { margin-top: 4px; font-size: 13px; color: var(--muted); line-height: 1.6; }
.cp-guide { max-width: 460px; text-align: left; font-size: 13px; color: var(--ink-soft); line-height: 1.8; }
.cp-guide ol { padding-left: 18px; margin-top: 6px; }

/* 结论条 */
/* 结论 + 关键数字：一条横条，不再占四张大卡 */
.cp-hero {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 14px; border: 1px solid var(--card-line); border-left: 3px solid var(--ok);
  border-radius: var(--radius); background: var(--card);
}
.cp-hero.danger { border-left-color: var(--danger); }
.cp-hero.warning { border-left-color: var(--gold-deep); }
.cp-hero-tag { flex: none; font-size: 12px; padding: 2px 8px; border-radius: 999px; background: var(--chip, rgba(0, 0, 0, .05)); color: var(--ink-soft); }
.cp-hero.danger .cp-hero-tag { color: var(--danger); }
.cp-hero.warning .cp-hero-tag { color: var(--gold-deep); }
.cp-hero-text { flex: 1 1 240px; font-size: 13px; line-height: 1.6; color: var(--ink-soft); }
.cp-hero-kpis { display: flex; align-items: center; gap: 8px; flex: none; }
.cp-kpi { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.cp-kpi.danger { color: var(--danger); }
.cp-kpi.warn { color: var(--gold-deep); }

/* 统一条目：默认两行，明细按需展开 */
.cp-item { padding: 10px 0; border-top: 1px dashed var(--card-line); }
.cp-item:first-of-type { border-top: none; }
.cp-item-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cp-item-title {
  flex: 1 1 220px; font-size: 13px; line-height: 1.6; min-width: 0;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.cp-item-cat, .cp-item-where, .cp-item-advice { font-size: 12px; color: var(--muted); line-height: 1.6; }
.cp-item-cat { flex: none; }
.cp-item-where { margin-top: 2px; }
.cp-item-advice { margin-top: 3px; color: var(--ink-soft); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.cp-item-detail { margin-top: 6px; padding: 8px 10px; background: var(--soft, rgba(0, 0, 0, .02)); border-radius: 6px; font-size: 12px; color: var(--ink-soft); line-height: 1.7; }
.cp-item-detail p + p { margin-top: 4px; }
.cp-item-foot { margin-top: 6px; display: flex; gap: 8px; flex-wrap: wrap; }
.cp-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--ok); }
.cp-dot.sev-high { background: var(--danger); }
.cp-dot.sev-medium { background: var(--gold-deep); }
.cp-dot.sev-low, .cp-dot.sev-info { background: var(--card-line); }
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

/* 冲突卡：分类标签 + 依据 / 预期实际 / 判定理由 */
.cp-card-cat {
  flex: none; font-size: 11px; padding: 1px 7px; border-radius: 999px;
  background: var(--chip, rgba(0, 0, 0, .05)); color: var(--ink-soft);
}
.cp-card-cat.repeat { color: var(--gold-deep); }
.cp-card-detail { margin-top: 4px; font-size: 12px; color: var(--ink-soft); line-height: 1.7; }
.cp-detail-label {
  display: inline-block; margin-right: 6px; padding: 0 6px; border-radius: 4px;
  background: var(--chip, rgba(0, 0, 0, .05)); color: var(--muted); font-size: 11px;
}
.cp-sides {
  margin-top: 6px; padding: 8px 10px; border-left: 2px solid var(--card-line);
  background: var(--hover, rgba(0, 0, 0, .02)); border-radius: 4px;
}
.cp-sides p { font-size: 12px; color: var(--ink-soft); line-height: 1.7; margin-top: 2px; }
.cp-sides p:first-child { margin-top: 0; }
.cp-side-label {
  display: inline-block; margin-right: 6px; font-size: 11px; color: var(--muted);
}

/* 覆盖卡：事实 / 来源 / 影响 / 建议 */
.cp-coverage-card { padding: 10px 0; border-top: 1px dashed var(--card-line); min-width: 0; }
.cp-coverage-fact { margin-top: 4px; font-size: 12px; color: var(--ink-soft); line-height: 1.7; }

/* 明细区：一条导航 + 一类条目，不再套第二层卡片 */
.cp-nav {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; margin-top: 2px; padding-bottom: 6px; border-bottom: 1px solid var(--card-line);
}
.cp-nav-tabs { display: flex; align-items: center; gap: 2px; flex-wrap: wrap; }
.cp-tab {
  border: 0; background: transparent; font: inherit; font-size: 13px; color: var(--muted);
  padding: 5px 9px; border-radius: 999px; cursor: pointer; white-space: nowrap;
}
.cp-tab:hover { background: var(--hover, rgba(0, 0, 0, .03)); color: var(--ink-soft); }
.cp-tab.active { background: var(--chip, rgba(0, 0, 0, .06)); color: var(--ink); font-weight: 600; }
.cp-tab-count { margin-left: 4px; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.cp-nav-filter { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cp-pane { padding-top: 2px; }
.cp-purpose { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 6px 0 8px; }
/* 体检与工具的口径对照：一行放完，不新增横条 */
.cp-source-line { font-size: 12px; color: var(--ink-soft); line-height: 1.7; margin-top: 2px; }
.cp-source-hint { font-size: 12px; color: var(--muted); line-height: 1.6; margin-bottom: 6px; }
.cp-filter { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.cp-select { width: 128px; }
.cp-input { width: 200px; }
.cp-count { font-size: 12px; color: var(--muted); }
.cp-tip { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.6; }
.cp-footnote { font-size: 12px; color: var(--muted); line-height: 1.7; margin-top: 10px; }

/* 覆盖卡：缺章号 chip + 已记录清单 */
.cp-chips { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
/* R13：记录可能过期的逐条小标（与顶部提示同一口径） */
.cp-stale-tag {
  flex: none; font-size: 11px; padding: 1px 8px; border-radius: 999px;
  color: #a0521b; background: #fdf3e3; border: 1px solid #f3d19e;
}
.cp-stale-row .cp-chip-label { color: #a0521b; }
.cp-chip-label { font-size: 11px; color: var(--muted); }
.cp-chap-chip {
  border: 1px solid var(--card-line); background: var(--card); color: var(--ink-soft);
  font-size: 12px; padding: 2px 9px; border-radius: 999px; cursor: pointer; font-variant-numeric: tabular-nums;
}
.cp-chap-chip:hover { border-color: var(--indigo); color: var(--indigo); }
.cp-records { margin-top: 8px; padding-top: 6px; border-top: 1px dashed var(--card-line); }
.cp-records-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.cp-record-row { display: flex; align-items: flex-start; gap: 10px; padding: 4px 0; }
.cp-chap-link {
  flex: none; border: 0; background: transparent; color: var(--indigo);
  font-size: 12px; padding: 0; cursor: pointer; white-space: nowrap; font-variant-numeric: tabular-nums;
}
.cp-record-items { min-width: 0; }
.cp-record-items p { font-size: 12px; color: var(--ink-soft); line-height: 1.6; }
.cp-muted-item { color: var(--muted) !important; }

/* 长原文默认两行截断，点「展开原文」再看全 */
.clamp-text {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; text-overflow: ellipsis;
}

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

/* 数据边界：默认收起的一行 */
.cp-boundary {
  font-size: 12px; color: var(--muted); line-height: 1.7;
  padding: 8px 14px; border: 1px dashed var(--card-line); border-radius: var(--radius);
}
.cp-boundary-head {
  display: flex; align-items: center; gap: 6px; width: 100%;
  background: none; border: none; padding: 0; cursor: pointer;
  font-size: 12px; color: var(--muted); text-align: left;
}
.cp-boundary-body { margin-top: 6px; }
.cp-boundary-body p { margin-top: 2px; }

/* 抽屉内定位 */
.cp-mention { padding: 8px 0; border-bottom: 1px dashed var(--card-line); }
.cp-mention-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 13px; }
.cp-mention-occ { margin-top: 6px; font-size: 12px; color: var(--ink-soft); line-height: 1.7; }
.cp-pos { color: var(--muted); margin-left: 4px; }

@media (max-width: 900px) {
  .cp-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cp-row-card { flex-direction: column; }
}

/* 约 1280 宽的笔记本：导航与筛选自动换行到两行，但不出现横向滚动 */
@media (max-width: 1320px) {
  .cp-nav { align-items: flex-start; }
  .cp-nav-filter { justify-content: flex-end; }
  .cp-select { width: 120px; }
  .cp-input { width: 180px; }
  .cp-detail { padding: 10px 12px; }
}</style>
