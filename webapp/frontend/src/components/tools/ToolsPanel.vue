<template>
  <div class="tools-grid">
    <!-- 封面生成 -->
    <el-card class="tool-card">
      <template #header><b>小说封面</b></template>
      <p class="muted">AI 生成小说封面（每本限 5 次）</p>
      <div v-if="coverUrl" class="cover-preview" style="margin-bottom:10px">
        <img :src="coverUrl" alt="封面" style="max-width:200px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.15)" />
      </div>
      <el-button size="small" type="primary" @click="genCover" :loading="coverLoading">
        {{ coverLoading ? '生成中…' : (coverUrl ? '重新生成' : '生成封面') }}
      </el-button>
      <span v-if="coverRemaining !== null" class="muted" style="margin-left:8px">剩余 {{ coverRemaining }} 次</span>
    </el-card>

    <!-- 三期 F2：场景体检（Scene Inspector） -->
    <el-card class="tool-card">
      <template #header><b>场景体检</b></template>
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

    <!-- 三期 F3：三分支续写选择 -->
    <el-card class="tool-card">
      <template #header><b>三分支续写</b></template>
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

    <!-- 三期 F4：思维链可视化（生成过程） -->
    <el-card class="tool-card">
      <template #header><b>生成过程</b></template>
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

    <!-- 质量校验 -->
    <el-card class="tool-card">
      <template #header><b>质量校验</b></template>
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

    <!-- 全书质检（全书质量汇总/终审/一致性校验；每章改稿建议已整合进章节版本弹窗） -->
    <el-card class="tool-card">
      <template #header><b>全书质检</b></template>
      <p class="muted">这里发起检查、查看原始记录；每章改稿建议见「章节 → 版本」弹窗。要按作者视角看分类汇总，去左侧「全书体检」，那里只读汇总、不重复跑检查。</p>
      <el-button size="small" @click="loadReview">查看全书质检</el-button>
      <div class="result-area">
        <div v-if="!reviewLoaded" class="muted">点击按钮加载</div>
        <template v-else>
          <div v-if="!reviewSummary.chapter_count && !finalReview && !verifyIssues.length" class="ok-box">暂无质检记录</div>
          <div v-if="reviewSummary.chapter_count" class="summary-box">
            <b>全书质量汇总</b>
            <div class="summary-line">质检章节 {{ reviewSummary.chapter_count }} 章｜改稿建议 {{ reviewSummary.issue_total }} 处
              <span v-if="reviewSummary.score_avg">｜平均质检分 {{ reviewSummary.score_avg }}/100</span>
            </div>
            <!-- P0-5/P1：进度盘色带（与 Admin 质量报表一致：未过红/追更>=4绿/自动重写黄/通过灰） -->
            <div v-if="(reviewSummary.chapter_status||[]).length" class="progress-strip">
              <div class="progress-dots">
                <span v-for="s in reviewSummary.chapter_status" :key="s.chapter" class="dot"
                      :class="'dot-' + (s.color||'gray')"
                      :title="`第${s.chapter}章 质检${s.score ?? '—'}分${s.passed?' ✓':' ✗'}${s.hook_score!=null?` 追更${s.hook_score}/5`:''}${s.auto_revised?' 自动重写':''}`"></span>
              </div>
              <div class="progress-legend">
                <span><i class="dot dot-red"></i>未过</span>
                <span><i class="dot dot-green"></i>追更≥4</span>
                <span><i class="dot dot-yellow"></i>自动重写</span>
                <span><i class="dot dot-gray"></i>通过</span>
              </div>
            </div>
            <div v-if="Object.keys(reviewSummary.issue_by_type||{}).length" class="summary-line">
              改稿建议类型：<span v-for="(n, t) in reviewSummary.issue_by_type" :key="t" class="tag-todo">{{ t }}×{{ n }}</span>
            </div>
            <div v-if="reviewSummary.verify_issue_total" class="summary-line">
              一致性/审稿校验问题 {{ reviewSummary.verify_issue_total }} 条（涉及 {{ Object.keys(reviewSummary.verify_by_chapter||{}).length }} 章）
            </div>
            <!-- 口径说明：两份记录来源不同，数字本来就不必相等，避免作者以为是同一个数 -->
            <div class="summary-line muted">
              「改稿建议」是逐章质检的全部条目（含字数、对话、钩子等非一致性项），逐条证据与改法在对应章的「版本」弹窗；「一致性/审稿校验问题」是全书通读检查记录。两者来源不同，数字不必相等；体检页会把后者与单章一致性质检合并，所以会再大一些。
            </div>
          </div>
          <div v-if="finalReview" class="final-review-box">
            <b>全书终审</b>
            <div class="summary-line">{{ finalReview.summary || '' }}</div>
            <div v-if="(finalReview.issues||[]).length">
              <div v-for="(it, i) in finalReview.issues" :key="i" class="review-issue">
                <span class="review-type">[{{ it.type }}]</span>{{ it.problem }}<span v-if="it.suggestion" class="muted">（建议：{{ it.suggestion }}）</span>
              </div>
            </div>
          </div>
          <div v-if="verifyIssues.length" class="verify-block">
            <b>一致性校验 / 读者自检 / 编辑审稿问题</b>
            <div v-for="(it, i) in verifyIssues" :key="i" class="review-issue">
              <span class="review-pos">第{{ it.chapter }}章：</span>{{ it.problem || it.detail || it.title || '（原记录未单列问题描述）' }}<span v-if="it.suggestion" class="muted">（建议：{{ it.suggestion }}）</span>
            </div>
          </div>
        </template>
      </div>
    </el-card>

    <!-- 节奏分析 -->
    <el-card class="tool-card">
      <template #header><b>节奏分析</b></template>
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

    <!-- 多方案生成 -->
    <el-card class="tool-card">
      <template #header><b>多方案生成</b></template>
      <p class="muted">为章节目标生成3个走向</p>
      <el-input v-model="altGoal" placeholder="输入章节目标" />
      <el-button size="small" type="primary" style="margin-top:8px" @click="genAlts" :loading="altLoading">生成方案</el-button>
      <div class="result-area">
        <div v-for="(a, i) in alts" :key="i" class="alt-box">
          <b>方案{{ i+1 }}：{{ a.title }}</b>
          <p>{{ a.description }}</p>
        </div>
      </div>
    </el-card>

    <!-- 批量生成 -->
    <el-card class="tool-card">
      <template #header><b>批量生成</b></template>
      <p class="muted">指定章节范围后台逐章生成</p>
      <el-space>
        <el-input-number v-model="batchStart" :min="1" controls-position="right" style="width:90px" />
        <span>至</span>
        <el-input-number v-model="batchEnd" :min="1" controls-position="right" style="width:90px" />
        <el-button size="small" type="primary" @click="doBatch" :loading="batchRunning" :disabled="batchRunning">开始</el-button>
      </el-space>
      <div v-if="batchRunning || batchError" style="margin-top:10px">
        <el-progress v-if="batchRunning" :percentage="batchPct" :stroke-width="10"
          :indeterminate="batchPct === 0" :status="batchPct >= 100 ? 'success' : ''" />
        <div v-if="batchRunning && batchStatus" class="muted" style="margin-top:6px">{{ batchStatus }}</div>
        <div v-if="batchError" class="batch-err">
          <div><b>批量生成未完成：</b>{{ batchError }}</div>
          <div class="muted" style="margin-top:4px">已生成的章节已保留，可把「开始」章改为失败的章节号后再次开始续跑。</div>
        </div>
      </div>
    </el-card>

    <!-- 伏笔管理（REQ-R10 A：进度视图） -->
    <el-card class="tool-card">
      <template #header>
        <div class="fs-head">
          <b>伏笔管理</b>
          <span class="muted">埋点 · 计划回收 · 当前进度</span>
        </div>
      </template>
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
              <el-button size="small" link type="danger" @click="delFs(item.index)">删</el-button>
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
            <el-button size="small" link type="danger" @click="delFs(u.index)">删</el-button>
          </div>
        </div>
      </template>
      <el-space style="margin-top:8px;width:100%">
        <el-input v-model="fsInput" placeholder="添加伏笔（补全章号后才会纳入进度跟踪）" style="flex:1" />
        <el-button size="small" @click="addFs">添加</el-button>
      </el-space>
    </el-card>

    <!-- 核心设定锁定 -->
    <el-card class="tool-card">
      <template #header><b>核心设定锁定</b></template>
      <p class="muted">AI必须遵守的铁律</p>
      <el-space>
        <el-input v-model="lkInput" placeholder="如：主角不能杀人" style="flex:1" />
        <el-button size="small" @click="addLk">添加</el-button>
      </el-space>
      <div class="result-area">
        <div v-for="(f, i) in locked" :key="i" class="fs-row">
          <span>🔒 {{ f }}</span>
          <el-button size="small" link type="danger" @click="delLk(i)">删</el-button>
        </div>
      </div>
    </el-card>

    <!-- 档3：批量生成的生成前一致性门禁（409 stale_gate）二次确认 -->
    <StaleGateDialog ref="staleGateRef" />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import * as toolsApi from '../../api/tools'
import { getProject, getStatus, startAgentRun } from '../../api/project'
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
import StaleGateDialog from '../common/StaleGateDialog.vue'
import { staleNoticeOf } from '../../utils/staleness'

const props = defineProps({ pid: String })
// 档3：生成前一致性门禁弹窗（409 stale_gate）
const staleGateRef = ref(null)

const verifyResult = ref([])
const reviewSummary = ref({})
const finalReview = ref(null)
const verifyIssues = ref([])
const reviewLoaded = ref(false)
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
const batchStart = ref(1)
const batchEnd = ref(1)
// 批量生成进度跟踪：点击开始后轮询 /projects/{id}，展示进度条、状态与终止原因（含成本防护提示）
const batchRunning = ref(false)
const batchPct = ref(0)
const batchStatus = ref('')
const batchError = ref('')
let batchTimer = null

function stopBatchPolling() {
  if (batchTimer) { clearInterval(batchTimer); batchTimer = null }
}

async function pollBatch() {
  try {
    const s = await getStatus(props.pid)
    // 进度按本次批量范围计算：chapters_done 是项目整体已生成章数，扣除批量起点之前的存量
    const total = batchEnd.value - batchStart.value + 1
    if (total > 0 && s.chapters_done !== undefined) {
      const done = Math.max(0, s.chapters_done - batchStart.value + 1)
      batchPct.value = Math.min(100, Math.round(done / total * 100))
    }
    batchStatus.value = s.running
      ? (s.state?.phase === 'writing' ? '正在逐章写作…'
         : s.state?.phase ? `正在生成（${s.state.phase}）…` : '批量生成中…')
      : ''
    if (!s.running) {
      stopBatchPolling()
      batchRunning.value = false
      if (s.error) {
        batchError.value = s.error
        ElMessage.error('批量生成终止：' + s.error)
      } else {
        ElMessage.success('批量生成完成')
      }
    }
  } catch { /* 网络抖动忽略，下一轮轮询再试 */ }
}

async function doBatch() {
  if (batchRunning.value) return
  if (batchEnd.value < batchStart.value) { ElMessage.warning('结束章不能小于起始章'); return }
  const payload = { agent: 'writer.chapter', params: { scope: 'range', from_ch: batchStart.value, to_ch: batchEnd.value } }
  try {
    await startAgentRun(props.pid, payload)
    onBatchStarted()
  } catch (e) {
    const d = e?.response?.data?.detail
    // 档3：目标章之前有未纳入本次的过期章 → 二次确认（仍然继续 / 从第X章起重写）
    if (!staleGateRef.value?.open(props.pid, d, payload, onBatchStarted)) {
      ElMessage.error(typeof d === 'string' ? d : (d?.message || '批量生成失败'))
    }
  }
}

// 批量生成启动成功后的统一后续（直接启动与门禁弹窗放行共用）
function onBatchStarted() {
  ElMessage.success(`已开始批量生成第${batchStart.value}-${batchEnd.value}章`)
  batchRunning.value = true
  batchError.value = ''
  batchPct.value = 0
  batchStatus.value = '批量生成中…'
  stopBatchPolling()
  batchTimer = setInterval(pollBatch, 3000)
  pollBatch()
}

const fsInput = ref('')
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

async function loadReview() {
  try {
    const r = await toolsApi.getReview(props.pid)
    reviewSummary.value = r.summary || {}
    verifyIssues.value = r.verify_issues || []
    finalReview.value = r.final_review || null
    reviewLoaded.value = true
  } catch (e) { ElMessage.error('全书质检加载失败') }
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
  if (!fsInput.value) return
  await toolsApi.addForeshadowing(props.pid, { item: fsInput.value })
  fsInput.value = ''
  loadFs()
}
async function delFs(i) {
  await toolsApi.delForeshadowing(props.pid, i)
  loadFs()
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

// 进入工具页即加载伏笔进度/锁定设定（此前未挂载加载，导致卡片永远空白）
onMounted(() => {
  loadFs()
  loadLk()
})
// 离开工具面板时停止批量轮询，避免泄漏定时器
onUnmounted(stopBatchPolling)

</script>

<style scoped>
.tools-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
.muted { color: #909399; font-size: 13px; }
.result-area { margin-top: 10px; max-height: 250px; overflow-y: auto; }
.issue-box { padding: 8px 10px; background: #fdf6ec; border-left: 3px solid #e6a23c; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.ok-box { padding: 8px 10px; background: #f0f9eb; border-left: 3px solid #67c23a; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.alt-box { padding: 8px 10px; background: #f5f7fa; border: 1px solid #e4e7ed; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.alt-box p { margin-top: 4px; color: #606266; }
.snap-row, .fs-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 13px; }
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
.rhythm-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 12px; }
.rhythm-label { width: 50px; color: #909399; }
.rhythm-bar { height: 12px; background: #c6e2ff; border-radius: 3px; }
.rhythm-words { color: #606266; }
.review-chapter { padding: 6px 8px; background: #f5f7fa; border-radius: 4px; margin-bottom: 6px; }
.review-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 13px; }
.tag-auto { background: #e1f3d8; color: #67c23a; padding: 0 6px; border-radius: 3px; font-size: 11px; }
.tag-todo { background: #fdf6ec; color: #e6a23c; padding: 0 6px; border-radius: 3px; font-size: 11px; }
.tag-ok { background: #f0f9eb; color: #67c23a; padding: 0 6px; border-radius: 3px; font-size: 11px; }
.tag-score { background: #ecf5ff; color: #409eff; padding: 0 6px; border-radius: 3px; font-size: 11px; }
.summary-box { background: #eef2ff; border: 1px solid #d8e0ff; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; font-size: 13px; }
.summary-line { margin-top: 4px; color: #33415c; }
.progress-strip { margin-top: 8px; padding: 6px 8px; background: #fff; border: 1px solid #ebeef5; border-radius: 4px; }
.progress-dots { display: flex; flex-wrap: wrap; gap: 4px; }
.dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; vertical-align: middle; }
.dot-red { background: #f56c6c; }
.dot-green { background: #67c23a; }
.dot-yellow { background: #e6a23c; }
.dot-gray { background: #c0c4cc; }
.progress-legend { display: flex; gap: 12px; margin-top: 6px; font-size: 12px; color: #909399; }
.verify-block { margin-top: 8px; }
.final-review-box { background: #f0f9eb; border: 1px solid #d4ecc9; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; font-size: 13px; }
.review-issue { font-size: 12px; color: #606266; padding: 2px 0; line-height: 1.5; }
.review-feeling { font-size: 12px; color: #e6a23c; padding: 2px 0 4px; font-style: italic; }
.lv-small { color: #909399; }
.lv-big { color: #f56c6c; font-weight: 700; }
.review-type { color: #409eff; }
.review-pos { color: #909399; }
.batch-err { padding: 8px 10px; background: #fef0f0; border-left: 3px solid #f56c6c; border-radius: 4px; font-size: 13px; }
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
