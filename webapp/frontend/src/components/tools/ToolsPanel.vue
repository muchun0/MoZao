<template>
  <!-- REQ-R23：工具页从「11 张平铺卡片」收敛为三组只读诊断/参考中心。
       生成入口只保留 Agent 页一处；这里删掉「批量生成」与「全书质检」两个重复入口。 -->
  <div class="tools-page">
    <section class="tool-section">
      <header class="tool-section-head">
        <h3>{{ SECTIONS.quality.title }}</h3>
        <p class="muted">{{ SECTIONS.quality.hint }}</p>
      </header>
      <div class="tool-health-link">
        <span>全书检查结论（覆盖 / 伏笔 / 冲突 / 人物·设定索引）统一在「全书体检」，这里只补充单章与一致性诊断，不再跑第二次全书检查。</span>
        <el-button size="small" type="primary" link @click="emit('open-health')">去全书体检 →</el-button>
      </div>
      <div class="tools-grid">
        <!-- 三期 F2：场景体检（Scene Inspector） -->
        <el-card class="tool-card">
          <template #header><b>场景体检</b></template>
          <p class="tool-use">适用于：某章读着不对劲，想先知道哪里不对</p>
          <p class="muted">对已生成章节做字数/事件/重复/承接/钩子体检并给修复建议</p>
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
            <el-input-number v-model="inspectNo" :min="1" size="small" controls-position="right" style="width:90px" />
            <el-button size="small" type="primary" @click="runInspect" :loading="inspectLoading">体检本章</el-button>
          </div>
          <div v-if="inspectReport" class="result-area">
            <div v-for="(d, i) in inspectReport.dimensions" :key="i"
                 :style="{padding:'6px 8px',marginBottom:'4px',borderRadius:'4px',borderLeft:'3px solid ' + (d.level==='bad'?'#f56c6c':d.level==='warn'?'#e6a23c':'#67c23a')}">
              <b>{{ d.dim }}</b>：{{ d.detail }}
              <div v-if="d.suggestion" class="muted" style="margin-top:2px">→ {{ d.suggestion }}</div>
            </div>
            <div v-if="inspectReport.beat_gate" class="muted">
              Beat 覆盖：{{ inspectReport.beat_gate.coverage ?? '—' }}（{{ inspectReport.beat_gate.verdict }}）
            </div>
          </div>
        </el-card>

        <!-- 三期 F4：思维链可视化（生成过程） -->
        <el-card class="tool-card">
          <template #header><b>生成过程</b></template>
          <p class="tool-use">适用于：某章出问题时排障，看 AI 当时怎么想的</p>
          <p class="muted">查看本章「计划→产出→校验」链条与中间产物（只读，不能编辑）</p>
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
            <el-input-number v-model="cotNo" :min="1" size="small" controls-position="right" style="width:90px" />
            <el-button size="small" type="primary" @click="runCot" :loading="cotLoading">查看</el-button>
          </div>
          <div v-if="cotView" class="result-area">
            <div><b>计划</b>：{{ cotView.plan.core_event || '（无）' }}</div>
            <div class="muted" style="margin:2px 0">拍序列：{{ cotView.plan.character_moves || '—' }}</div>
            <div style="margin-top:4px"><b>产出 · 场景清单</b>
              <span v-if="sceneCountHint" class="muted" style="margin-left:6px">{{ sceneCountHint }}</span>
            </div>
            <!-- R13：这一章改过正文时，提示下面的场景清单可能还是改之前的 -->
            <StaleNotice v-if="staleNotice" :notice="staleNotice" />
            <p v-if="!sceneItems.length" class="cot-empty">{{ sceneEmptyText }}</p>
            <div v-for="(sc, i) in sceneItems" :key="i" class="cot-scene">
              <div class="cot-scene-head" @click="toggleScene(i)">
                <span class="cot-scene-no">{{ sc.index }}</span>
                <span class="cot-scene-title">{{ openScenes[i] ? sc.title : sc.shortTitle }}</span>
                <span class="cot-link">{{ openScenes[i] ? '收起' : '详情' }}</span>
              </div>
              <div v-show="openScenes[i]" class="cot-scene-body">
                <p v-for="(d, j) in sc.details" :key="j" class="cot-line">
                  <span class="cot-label">{{ d.label }}</span>{{ d.text }}
                </p>
                <p v-if="sc.anchors.length" class="cot-line">
                  <span class="cot-label">细节锚点</span>{{ sc.anchors.join('；') }}
                </p>
              </div>
            </div>
            <div class="cot-block">
              <div class="cot-block-head" @click="showDesign = !showDesign">
                <b>本章设计</b>
                <span v-if="designRows.length" class="cot-link">{{ showDesign ? '收起' : '展开' }}</span>
              </div>
              <p v-if="!designRows.length" class="cot-empty">{{ designEmptyText }}</p>
              <div v-else v-show="showDesign">
                <p v-for="(d, j) in designRows" :key="j" class="cot-line">
                  <span class="cot-label">{{ d.label }}</span>{{ d.text }}
                </p>
              </div>
            </div>
            <div class="cot-block">
              <div class="cot-block-head" @click="showSummary = !showSummary">
                <b>本章摘要</b>
                <span v-if="summaryText" class="cot-link">{{ showSummary ? '收起' : '展开' }}</span>
              </div>
              <p v-if="!summaryText" class="cot-empty">{{ summaryEmptyText }}</p>
              <p v-else class="cot-line">{{ showSummary ? summaryText : summaryShort }}</p>
            </div>
            <div class="muted" style="margin-top:4px"><b>校验</b>：
              beat覆盖 {{ cotView.review.beat_gate ? cotView.review.beat_gate.verdict : '—' }}
              <span v-if="cotView.review.quality">，质检 {{ cotView.review.quality.score }} 分</span>
            </div>
          </div>
        </el-card>

        <!-- 质量校验（临时自检，结果不落库） -->
        <el-card class="tool-card">
          <template #header><b>质量校验</b></template>
          <p class="tool-use">适用于：手动抽查一次设定/逻辑/合规，不落库</p>
          <p class="muted">临时自检设定一致性/剧情逻辑/合规，结果不写入章节质检记录；逐章改稿建议见「章节 → 版本」弹窗。</p>
          <el-space wrap>
            <el-button size="small" @click="runVerify('consistency', '设定一致性')">设定一致性</el-button>
            <el-button size="small" @click="runVerify('logic', '剧情逻辑')">剧情逻辑</el-button>
            <el-button size="small" @click="runVerify('compliance', '合规检测')">合规检测</el-button>
          </el-space>
          <div class="result-area">
            <div v-for="(item, i) in verifyResult" :key="i" :class="item.ok ? 'ok-box' : 'issue-box'">
              {{ item.text }}
            </div>
          </div>
        </el-card>
      </div>
    </section>

    <section class="tool-section">
      <header class="tool-section-head">
        <h3>{{ SECTIONS.reference.title }}</h3>
        <p class="muted">{{ SECTIONS.reference.hint }}</p>
      </header>
      <div class="tools-grid">
        <!-- 多方案生成 -->
        <el-card class="tool-card">
          <template #header><b>多方案生成</b></template>
          <p class="tool-use">适用于：卡文，想要几个不同走向挑一个</p>
          <p class="muted">为章节目标生成3个走向（参考，不写正文）</p>
          <el-input v-model="altGoal" placeholder="输入章节目标" />
          <el-button size="small" type="primary" style="margin-top:8px" @click="genAlts" :loading="altLoading">生成方案</el-button>
          <div class="result-area">
            <div v-for="(a, i) in alts" :key="i" class="alt-box">
              <b>方案{{ i+1 }}：{{ a.title }}</b>
              <p>{{ a.description }}</p>
            </div>
          </div>
        </el-card>

        <!-- 三期 F3：三分支续写选择 -->
        <el-card class="tool-card">
          <template #header><b>三分支续写</b></template>
          <p class="tool-use">适用于：想知道下一章还能往哪写</p>
          <p class="muted">基于末章结尾生成 3 个方向（冲突升级/埋伏笔/转折）的续写候选，选一条复制使用</p>
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
            <el-input-number v-model="branchNo" :min="1" size="small" controls-position="right" style="width:90px" />
            <el-button size="small" type="primary" @click="runBranches" :loading="branchLoading">生成3个分支</el-button>
          </div>
          <div v-if="branchList.length" class="result-area">
            <div v-for="(b, i) in branchList" :key="i"
                 style="padding:8px;margin-bottom:6px;border:1px solid #e4e7ed;border-radius:6px">
              <b style="color:#409eff">{{ b.label || b.direction }}</b>
              <div class="muted" style="margin:4px 0;max-height:120px;overflow:auto">{{ b.preview }}</div>
              <el-button size="small" @click="pickBranch(b)">记录并复制</el-button>
            </div>
          </div>
        </el-card>

        <!-- 节奏分析 -->
        <el-card class="tool-card">
          <template #header><b>节奏分析</b></template>
          <p class="tool-use">适用于：怀疑全书节奏有断层</p>
          <p class="muted">每章字数分布与进度</p>
          <el-button size="small" @click="loadRhythm">查看节奏</el-button>
          <div class="result-area">
            <div v-for="(c, i) in rhythmData" :key="i" class="rhythm-row">
              <span class="rhythm-label">第{{ c.no }}章</span>
              <div class="rhythm-bar" :style="{width: (c.words/maxRhythm*100)+'%'}"></div>
              <span class="rhythm-words">{{ c.words }}字</span>
            </div>
          </div>
        </el-card>
      </div>
    </section>

    <section class="tool-section">
      <header class="tool-section-head">
        <h3>{{ SECTIONS.asset.title }}</h3>
        <p class="muted">{{ SECTIONS.asset.hint }}</p>
      </header>
      <div class="tools-grid">
        <!-- 封面生成 -->
        <el-card class="tool-card">
          <template #header><b>小说封面</b></template>
          <p class="tool-use">独立功能：只影响书架展示，不写入正文</p>
          <p class="muted">AI 生成小说封面（每本限 5 次）</p>
          <div v-if="coverUrl" class="cover-preview" style="margin-bottom:10px">
            <img :src="coverUrl" alt="封面" style="max-width:200px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.15)" />
          </div>
          <el-button size="small" type="primary" @click="genCover" :loading="coverLoading">
            {{ coverLoading ? '生成中…' : (coverUrl ? '重新生成' : '生成封面') }}
          </el-button>
          <span v-if="coverRemaining !== null" class="muted" style="margin-left:8px">剩余 {{ coverRemaining }} 次</span>
        </el-card>

        <!-- 伏笔（REQ-R23 P1-3）：唯一账本是结构化伏笔账本，全书体检与时间线同源 -->
        <el-card class="tool-card">
          <template #header>
            <div class="fs-head">
              <b>伏笔</b>
              <span class="muted">唯一台账：埋点 · 计划回收 · 进度；改这里，全书体检与时间线自动跟着变</span>
            </div>
          </template>
          <p class="tool-use">适用于：新埋了线索要登记，或旧伏笔要补章号</p>
          <div v-if="!fsSummary.total" class="muted">
            还没有登记伏笔。可在下方添加，或在世界观中补充伏笔计划（含埋设章与计划回收章）。
          </div>
          <template v-else>
            <div class="fs-summary">
              <span>已回收 <b>{{ fsSummary.revealed }}</b> / 共 <b>{{ fsSummary.total }}</b>
                <span v-if="!fsSummary.tracked" class="muted">（均未登记章号）</span>
              </span>
              <span class="muted">已写 {{ fsSummary.baseline_ch }} 章</span>
            </div>
            <template v-if="fsSummary.tracked">
            <div class="fs-badges">
              <el-tag v-for="f in fsFilterList" :key="f.key" size="small" :type="f.type"
                :effect="fsFilter === f.key ? 'dark' : 'plain'" class="fs-badge"
                @click="fsFilter = f.key">{{ f.label }} {{ f.count }}</el-tag>
            </div>
            <div class="result-area fs-area">
              <div v-for="item in fsVisible" :key="item.index" class="fs-item">
                <div class="fs-item-top">
                  <span class="fs-name">{{ item.name }}</span>
                  <el-tag size="small" :type="stateTag(item.state)">{{ item.state_label }}</el-tag>
                  <el-button size="small" link type="primary" @click="editFs(item)">改</el-button>
                  <el-button size="small" link type="danger" @click="delFs(item.name)">删</el-button>
                </div>
                <div class="fs-meta">
                  埋在第 {{ item.buried_ch }} 章 · 原计划第 {{ item.reveal_ch }} 章收 · {{ foreshadowProgressText(item) }}
                </div>
                <div v-if="item.state === 'revealed' && item.revealed_ch" class="fs-meta">
                  实际回收：第 {{ item.revealed_ch }} 章
                </div>
                <div v-if="item.excerpt" class="fs-excerpt"
                  :class="{ expanded: !!fsExpanded[item.index] }"
                  @click="fsExpanded[item.index] = !fsExpanded[item.index]">
                  {{ item.excerpt }}
                </div>
              </div>
              <div v-if="!fsVisible.length" class="muted">当前筛选下没有伏笔。</div>
            </div>
            </template>
            <div v-if="fsUnstructured.length" class="fs-unstructured">
              <div class="muted">未纳入跟踪（缺章号）</div>
              <div v-for="u in fsUnstructured" :key="u.index" class="fs-row">
                <span class="fs-name">{{ u.name }}</span>
                <span class="muted fs-note">{{ unstructuredNote(u) }}</span>
                <el-button size="small" link type="primary" @click="editFs(u)">补章号</el-button>
                <el-button size="small" link type="danger" @click="delFs(u.name)">删</el-button>
              </div>
            </div>
          </template>
          <div class="fs-form">
            <el-input v-model="fsName" placeholder="伏笔内容（同名再提交即更新，不会重复登记）" style="flex:1" />
            <el-input-number v-model="fsBuried" :min="0" size="small" controls-position="right"
              placeholder="埋点章" title="埋点章：这条线索第一次出现在第几章" />
            <el-input-number v-model="fsReveal" :min="0" size="small" controls-position="right"
              placeholder="回收章" title="计划回收章：打算在第几章交代" />
            <el-select v-model="fsLevel" size="small" style="width:96px" title="跨度：章内/卷内/全篇">
              <el-option value="small" label="章内" />
              <el-option value="medium" label="卷内" />
              <el-option value="large" label="全篇" />
            </el-select>
            <el-button size="small" type="primary" @click="addFs">{{ fsEditing ? '更新' : '添加' }}</el-button>
            <el-button v-if="fsEditing" size="small" @click="resetFsForm">取消</el-button>
          </div>
          <p class="muted fs-hint">不填章号也能登记，但只有补全「埋点章」后才会纳入进度跟踪。</p>
        </el-card>

        <!-- 写作铁律（原「核心设定锁定」）：这是写作约束，不是防覆盖锁 -->
        <el-card class="tool-card">
          <template #header>
            <div class="fs-head">
              <b>{{ RULE_TERM }}</b>
              <span class="muted">原{{ RULE_TERM_LEGACY }}</span>
            </div>
          </template>
          <p class="tool-use">适用于：有 AI 必须遵守的硬约束（保存即注入后续生成）</p>
          <p class="muted">{{ RULE_TERM_DESC }}</p>
          <el-space>
            <el-input v-model="lkInput" placeholder="如：主角不能杀人" style="flex:1" />
            <el-button size="small" @click="addLk">添加</el-button>
          </el-space>
          <div class="result-area">
            <div v-for="(f, i) in locked" :key="i" class="fs-row">
              <span>· {{ f }}</span>
              <el-button size="small" link type="danger" @click="delLk(i)">删</el-button>
            </div>
            <div v-if="!locked.length" class="muted">还没有铁律。章节/角色上的「{{ LOCK_TERM }}」只防覆盖，不写在这里。</div>
          </div>
        </el-card>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import * as toolsApi from '../../api/tools'
import { getProject } from '../../api/project'
import { ElMessage } from 'element-plus'
import {
  clipText, sceneTitle, sceneDetails, sceneAnchors, sceneCountText, designLines,
  SCENE_EMPTY_TEXT, DESIGN_EMPTY_TEXT, SUMMARY_EMPTY_TEXT,
} from '../../utils/artifactView'
import {
  normalizeForeshadowSummary, buildForeshadowFilters, filterForeshadowItems,
  foreshadowStateTag, foreshadowProgressText, unstructuredNote,
} from '../../utils/foreshadow'
import StaleNotice from '../common/StaleNotice.vue'
import { staleNoticeOf } from '../../utils/staleness'
import { TOOL_SECTIONS, RULE_TERM, RULE_TERM_LEGACY, RULE_TERM_DESC, LOCK_TERM } from '../../utils/toolTerms'

const props = defineProps({ pid: String })
// 质量中心的「去全书体检」：跳转由 ProjectDetail 处理（这里不直接改父级 tab）
const emit = defineEmits(['open-health'])

const SECTIONS = Object.fromEntries(TOOL_SECTIONS.map((s) => [s.key, s]))

const verifyResult = ref([])
const coverUrl = ref('')
const coverLoading = ref(false)
const coverRemaining = ref(null)

async function loadCover() {
  try {
    const info = await getProject(props.pid)
    if (info.has_cover) {
      coverUrl.value = `/api/projects/${props.pid}/cover?t=${Date.now()}&token=${localStorage.getItem('token')}`
    }
    coverRemaining.value = (info.cover_max || 5) - (info.cover_used || 0)
  } catch (e) { console.warn('loadCover', e) }
}

async function genCover() {
  coverLoading.value = true
  try {
    const token = localStorage.getItem('token')
    const res = await fetch(`/api/projects/${props.pid}/cover`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token }
    })
    const data = await res.json()
    if (data.cover_url) {
      coverUrl.value = data.cover_url + '?t=' + Date.now() + '&token=' + localStorage.getItem('token')
      coverRemaining.value = data.cover_remaining
      ElMessage.success('封面生成成功')
    } else {
      ElMessage.error(data.detail || '生成失败')
    }
  } catch (e) {
    ElMessage.error('生成失败: ' + e.message)
  } finally {
    coverLoading.value = false
  }
}

// ===== 三期产品化（2026-10-14）：场景体检 / 三分支续写 / 思维链 =====
const inspectNo = ref(1)
const inspectLoading = ref(false)
const inspectReport = ref(null)
async function runInspect() {
  inspectLoading.value = true
  try {
    inspectReport.value = await toolsApi.getSceneInspect(props.pid, inspectNo.value)
  } catch (e) {
    ElMessage.error('体检失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    inspectLoading.value = false
  }
}

const branchNo = ref(1)
const branchLoading = ref(false)
const branchList = ref([])
async function runBranches() {
  branchLoading.value = true
  try {
    const data = await toolsApi.continueBranches(props.pid, { chapter_no: branchNo.value })
    branchList.value = data.branches || []
    if (!branchList.value.length) ElMessage.warning('未生成分支，请稍后重试')
  } catch (e) {
    ElMessage.error('分支生成失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    branchLoading.value = false
  }
}
async function pickBranch(b) {
  try {
    await toolsApi.applyBranch(props.pid, {
      chapter_no: branchNo.value, direction: b.direction, label: b.label, preview: b.preview,
    })
    // 复制到剪贴板（预览模式：由用户粘贴进编辑器）
    try { await navigator.clipboard.writeText(b.preview) } catch (_) {}
    ElMessage.success('已记录草稿并复制到剪贴板')
  } catch (e) {
    ElMessage.error('记录失败: ' + (e.response?.data?.detail || e.message))
  }
}

const cotNo = ref(1)
const cotLoading = ref(false)
const cotView = ref(null)
// 中间产物只读展示：场景逐条展开，设计与摘要折叠查看（无任何写操作）
const openScenes = ref({})
const showDesign = ref(false)
const showSummary = ref(false)
const sceneItems = computed(() => ((cotView.value?.production?.scenes) || []).map((s, i) => ({
  index: `场景 ${i + 1}`,
  title: sceneTitle(s),
  shortTitle: clipText(sceneTitle(s)),
  details: sceneDetails(s),
  anchors: sceneAnchors(s),
})))
const sceneCountHint = computed(() => sceneCountText(sceneItems.value.length))
const designRows = computed(() => designLines(cotView.value?.design || {}))
const summaryText = computed(() => String(cotView.value?.summary || '').trim())
const summaryShort = computed(() => clipText(summaryText.value))
const sceneEmptyText = SCENE_EMPTY_TEXT
const designEmptyText = DESIGN_EMPTY_TEXT
const summaryEmptyText = SUMMARY_EMPTY_TEXT
// R13：改章后派生记录标脏提示（与体检页同一后端判定与文案）
const staleNotice = computed(() => staleNoticeOf(cotView.value))
function toggleScene(i) {
  openScenes.value = { ...openScenes.value, [i]: !openScenes.value[i] }
}
function resetArtifactView() {
  openScenes.value = {}
  showDesign.value = false
  showSummary.value = false
}
async function runCot() {
  resetArtifactView()
  cotLoading.value = true
  try {
    cotView.value = await toolsApi.getChainOfThought(props.pid, cotNo.value)
  } catch (e) {
    ElMessage.error('读取失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    cotLoading.value = false
  }
}

const rhythmData = ref([])
const maxRhythm = ref(1)
const altGoal = ref('')
const altLoading = ref(false)
const alts = ref([])

// 伏笔（REQ-R23 P1-3）：表单直接写结构化账本；同名提交＝更新，删改按名称定位
const fsName = ref('')
const fsBuried = ref(0)
const fsReveal = ref(0)
const fsLevel = ref('medium')
const fsEditing = ref('')
const fsData = ref({ items: [], unstructured: [], summary: null })
const fsFilter = ref('all')
const fsExpanded = ref({})
const lkInput = ref('')
const locked = ref([])

// REQ-R10 A：展示口径统一走 utils/foreshadow（状态判定仍在后端，避免第二套口径）
const stateTag = foreshadowStateTag
const fsSummary = computed(() => normalizeForeshadowSummary(fsData.value.summary))
const fsUnstructured = computed(() => fsData.value.unstructured || [])
const fsFilterList = computed(() => buildForeshadowFilters(fsSummary.value))
const fsVisible = computed(() => filterForeshadowItems(fsData.value.items, fsFilter.value))

async function runVerify(kind, label) {
  verifyResult.value = [{ text: `正在检查${label}…`, ok: false }]
  try {
    const apiMap = { consistency: toolsApi.verifyConsistency, logic: toolsApi.verifyLogic, compliance: toolsApi.verifyCompliance }
    const r = await apiMap[kind](props.pid)
    const issues = r.issues || r.problems || []
    if (!issues.length) verifyResult.value = [{ text: '未发现问题 ✓', ok: true }]
    else verifyResult.value = issues.map(i => ({ text: (i.problem || i.issue || JSON.stringify(i)) + (i.suggestion ? '（建议：' + i.suggestion + '）' : ''), ok: false }))
  } catch (e) {
    verifyResult.value = [{ text: '失败：' + (e.response?.data?.detail || e.message), ok: false }]
  }
}

async function loadRhythm() {
  try {
    const r = await toolsApi.getRhythm(props.pid)
    rhythmData.value = r.chapters || []
    maxRhythm.value = Math.max(...rhythmData.value.map(c => c.words), 1)
  } catch (e) { ElMessage.error('节奏分析失败') }
}

async function genAlts() {
  if (!altGoal.value) { ElMessage.warning('请输入章节目标'); return }
  altLoading.value = true
  try {
    const r = await toolsApi.genAlternatives(props.pid, { goal: altGoal.value })
    alts.value = r.alternatives || []
  } catch (e) { ElMessage.error('生成失败') } finally { altLoading.value = false }
}

async function loadFs() {
  try {
    const r = await toolsApi.listForeshadowing(props.pid)
    fsData.value = {
      items: r.items || [],
      unstructured: r.unstructured || [],
      summary: r.summary || null,
    }
  } catch { fsData.value = { items: [], unstructured: [], summary: null } }
}
async function addFs() {
  const name = fsName.value.trim()
  if (!name) { ElMessage.warning('请填写伏笔内容'); return }
  const body = {
    name,
    buried_ch: Number(fsBuried.value) || 0,
    reveal_ch: Number(fsReveal.value) ? Number(fsReveal.value) : null,
    level: fsLevel.value,
  }
  try {
    const r = await toolsApi.addForeshadowing(props.pid, body)
    ElMessage.success(r.updated ? `已更新「${name}」` : `已添加「${name}」`)
    resetFsForm()
    loadFs()
  } catch (e) {
    ElMessage.warning(e?.response?.data?.detail || '保存失败')
  }
}

function editFs(item) {
  fsEditing.value = item.name
  fsName.value = item.name
  fsBuried.value = Number(item.buried_ch) || 0
  fsReveal.value = Number(item.reveal_ch) || 0
  fsLevel.value = item.level || 'medium'
}

function resetFsForm() {
  fsEditing.value = ''
  fsName.value = ''
  fsBuried.value = 0
  fsReveal.value = 0
  fsLevel.value = 'medium'
}

async function delFs(name) {
  if (!name) return
  try {
    await toolsApi.delForeshadowingByName(props.pid, name)
    ElMessage.success(`已删除「${name}」`)
    if (fsEditing.value === name) resetFsForm()
    loadFs()
  } catch (e) {
    ElMessage.warning(e?.response?.data?.detail || '删除失败')
  }
}

async function loadLk() {
  try { const r = await toolsApi.listLocked(props.pid); locked.value = r.locked_settings || [] } catch {}
}
async function addLk() {
  if (!lkInput.value) return
  await toolsApi.addLocked(props.pid, { item: lkInput.value })
  lkInput.value = ''
  loadLk()
}
async function delLk(i) {
  await toolsApi.delLocked(props.pid, i)
  loadLk()
}

// 进入工具页即加载封面/伏笔进度/写作铁律（此前未挂载加载，导致卡片永远空白）
onMounted(() => {
  loadCover()
  loadFs()
  loadLk()
})

</script>

<style scoped>
.tools-page { display: flex; flex-direction: column; gap: 18px; }
.tool-section { display: flex; flex-direction: column; gap: 8px; }
.tool-section-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.tool-section-head h3 { margin: 0; font-size: 15px; font-weight: 600; }
.tool-section-head .muted { margin: 0; font-size: 12px; }
.tool-health-link { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 7px 10px;
  background: #fbf6ea; border-left: 3px solid var(--gold, #c8a24a); border-radius: 4px; font-size: 12.5px; color: #5c6470; }
/* REQ-R23 原型对齐：三列紧凑卡（视口≥1400 三列，≥1000 两列，窄屏一列），卡片高度不再被拉成两行空白 */
.tools-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; align-items: start; }
@media (min-width: 1000px) { .tools-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (min-width: 1400px) { .tools-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.tool-card :deep(.el-card__header) { padding: 8px 12px; }
.tool-card :deep(.el-card__body) { padding: 10px 12px; }
.tool-card :deep(.el-card__header b) { font-size: 13.5px; }
/* 卡面第一行：这张卡「什么时候用」——先讲用途，再讲操作 */
.tool-use { margin: 0 0 4px; font-size: 11.5px; color: #8a641f; background: #fbf6ea;
  border-radius: 3px; padding: 2px 6px; display: inline-block; }
.tool-card .muted { font-size: 12px; line-height: 1.6; }
.muted { color: #909399; font-size: 13px; }
.result-area { margin-top: 10px; max-height: 250px; overflow-y: auto; }
.issue-box { padding: 8px 10px; background: #fdf6ec; border-left: 3px solid #e6a23c; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.ok-box { padding: 8px 10px; background: #f0f9eb; border-left: 3px solid #67c23a; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.alt-box { padding: 8px 10px; background: #f5f7fa; border: 1px solid #e4e7ed; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.alt-box p { margin-top: 4px; color: #606266; }
.fs-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 13px; }
/* REQ-R10 A：伏笔进度卡片 */
.fs-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.fs-summary { display: flex; justify-content: space-between; align-items: baseline; font-size: 13px; color: #303133; }
.fs-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.fs-badge { cursor: pointer; }
.fs-area { max-height: 320px; }
.fs-item { padding: 8px 10px; background: #fafafa; border: 1px solid #ebeef5; border-radius: 6px; margin-bottom: 8px; }
.fs-item-top { display: flex; align-items: center; gap: 8px; }
.fs-item-top .el-button { margin-left: auto; }
.fs-name { font-size: 13px; color: #303133; font-weight: 600; word-break: break-all; }
.fs-meta { margin-top: 4px; font-size: 12px; color: #606266; }
.fs-excerpt { margin-top: 6px; font-size: 12px; color: #909399; line-height: 1.5; cursor: pointer;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.fs-excerpt.expanded { -webkit-line-clamp: unset; }
.fs-unstructured { margin-top: 8px; border-top: 1px dashed #ebeef5; padding-top: 6px; }
.fs-unstructured .fs-row { gap: 8px; justify-content: flex-start; }
.fs-note { font-size: 12px; }
.fs-form { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.fs-form :deep(.el-input-number) { width: 96px; }
.fs-hint { margin: 6px 0 0; font-size: 12px; }
.rhythm-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 12px; }
.rhythm-label { width: 50px; color: #909399; }
.rhythm-bar { height: 12px; background: #c6e2ff; border-radius: 3px; }
.rhythm-words { color: #606266; }
/* 中间产物只读展示（场景清单 / 设计卡 / 摘要） */
.cot-block { border-top: 1px solid #ebeef5; padding-top: 6px; margin-top: 8px; }
.cot-block-head { display: flex; align-items: center; font-size: 13px; }
.cot-scene { padding: 4px 0; border-bottom: 1px dashed #f2f3f5; }
.cot-scene-head { display: flex; align-items: baseline; gap: 6px; cursor: pointer; }
.cot-scene-no { color: #409eff; font-size: 12px; flex: none; }
.cot-scene-title { flex: 1; min-width: 0; font-size: 13px; color: #303133; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cot-scene-body { padding: 2px 0 4px; }
.cot-line { font-size: 12px; color: #606266; line-height: 1.6; margin: 2px 0; }
.cot-label { color: #909399; margin-right: 6px; }
.cot-link { color: #409eff; font-size: 12px; cursor: pointer; margin-left: auto; flex: none; }
.cot-empty { font-size: 12px; color: #909399; margin: 4px 0; }
</style>
