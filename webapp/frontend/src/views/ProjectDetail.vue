<template>
  <div v-loading="loading" class="pd">
    <!-- 顶栏：书名 + 元数据 + 状态徽章；生成类操作全部收敛到「Agent / 大纲」页，顶部仅保留导出与取消 -->
    <header class="pd-head">
      <div class="pd-head-main">
        <h2 class="pd-title">{{ project?.config?.title || '加载中…' }}</h2>
        <div class="pd-meta">
          <el-tag size="small" :type="bookStatus.type" effect="light">{{ bookStatus.text }}</el-tag>
          <template v-if="project">
            <span v-if="project.config?.genre">{{ project.config.genre }}</span>
            <span v-if="project.config?.total_words">目标 {{ fmtWords(project.config.total_words) }}</span>
            <span>已写 {{ fmtWords(totalWords) }}</span>
            <span>章节进度 {{ genDoneCount }} / {{ genTotal || '—' }} 章</span>
          </template>
        </div>
      </div>
      <div class="pd-head-actions">
        <el-button text @click="$router.push('/')">← 返回书架</el-button>
        <el-dropdown v-if="chapters.length" trigger="click" @command="doDownload">
          <el-button :disabled="genRunning" :loading="exporting">导出 ▾</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="txt">TXT 文本</el-dropdown-item>
              <el-dropdown-item command="epub">EPUB 电子书</el-dropdown-item>
              <el-dropdown-item command="docx">DOCX Word</el-dropdown-item>
              <el-dropdown-item command="md">Markdown</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 生成中横幅：全项目唯一的进度指示组件（统一进度条 + 阶段文案 + 取消/继续），
         各阶段共用，不再出现第二个进度条；状态/进度/文案由 useGenerationProgress 统一驱动 -->
    <GenerationProgress>
      <template #actions>
        <el-button v-if="isRunning" size="small" plain @click="cancelGen">取消</el-button>
        <el-button v-else-if="resumable" type="primary" size="small" @click="startGen">继续生成</el-button>
      </template>
    </GenerationProgress>

    <!-- R27 分步确认：只渲染后端状态；确认/推进由 GenerationFlow 自己调接口 -->
    <GenerationFlow :pid="pid" @changed="refreshAfterGen(true)" @edit="openFlowStage" />

    <!-- 主体：左侧栏导航 + 工作区 -->
    <div class="pd-body">
      <ProjectSideNav v-model="tab" :chapter-count="chapters.length" @update:model-value="navTouched = true" />

      <main class="pd-main">
        <!-- 章节 -->
        <section v-if="visited.chapters" v-show="tab === 'chapters'">
          <!-- 门 4 批末卡片：本批字数/质检合格率/扣费；继续下一批或结束 -->
          <GateCard v-if="batchCard" title="本批已完成" status="batch" status-label="批末"
            :summary="batchCard.summary" :checks="batchCard.checks" :actions="['continue', 'finish']"
            hint="批量运行期间正文只读；要继续先点「继续下一批」"
            @continue="startGen" @finish="batchCard = null" @locate="locateAnchor" />
          <!-- 正文入口：默认一批 5 章，批末自动停；「一键全部」是小字逃生舱（二次确认+预估） -->
          <div v-if="outlineReady" class="ch-gen-bar">
            <el-button v-if="nextUnwritten" type="primary" size="small" :disabled="genRunning" @click="startGen">
              写第 {{ nextUnwritten }}–{{ batchEnd }} 章（每批 {{ BATCH_SIZE }} 章）
            </el-button>
            <el-tag v-else size="small" type="success" effect="plain">全部章节已写完</el-tag>
            <el-button v-if="nextUnwritten" link size="small" class="ch-gen-all" :disabled="genRunning"
                       title="不经过审阅门，一次写完；会显示预估总额并二次确认"
                       @click="startGenAll">一键全部生成 ›</el-button>
            <span v-if="genRunning" class="ch-gen-readonly">批量生成中，正文只读</span>
          </div>
          <div v-if="chapters.length" class="ch-quality-bar">
            <div class="ch-quality-summary">
              <el-tag v-if="worstText" type="danger" size="small" effect="plain" class="ch-worst-tag clickable"
                      role="button" tabindex="0" title="查看该章的改稿建议与改法"
                      @click="openWorstVersions" @keydown.enter.prevent="openWorstVersions">
                {{ worstText }}
              </el-tag>
              <span v-else class="ch-quality-empty">{{ qualityGap || '本书暂无质检记录，无法比较章节质量' }}</span>
              <span v-if="worstText && qualityGap" class="ch-quality-gap">{{ qualityGap }}</span>
            </div>
            <div class="ch-quality-controls">
              <el-radio-group v-model="qualitySort" size="small">
                <el-radio-button value="chapter">按章号</el-radio-button>
                <el-radio-button value="quality">质量最差优先</el-radio-button>
              </el-radio-group>
              <el-checkbox v-model="qualityOnly" size="small">只看需要处理的章</el-checkbox>
            </div>
          </div>
          <!-- 档1/档2 过期提示：读时推导，只提示不阻断；两种成因分开说，点章号直接跳章 -->
          <div v-if="staleHint || prevRewriteHint || staleUnknownHint" class="ch-stale-bar">
            <el-tag v-if="staleHint" size="small" type="warning" effect="plain">上游已更新</el-tag>
            <span v-if="staleHint" class="ch-stale-text">{{ staleHint }}</span>
            <el-tag v-if="prevRewriteHint" size="small" type="danger" effect="plain">前文已重写</el-tag>
            <span v-if="prevRewriteHint" class="ch-stale-text">{{ prevRewriteHint }}</span>
            <span v-if="staleUnknownHint" class="ch-stale-sub">{{ staleUnknownHint }}</span>
            <span v-if="staleHint || prevRewriteHint" class="ch-stale-jumps">
              <el-button v-for="n in staleJumpChapters.slice(0, 6)" :key="n" size="small" link type="warning"
                         @click="jumpToStale(n)">第{{ n }}章</el-button>
              <span v-if="staleJumpChapters.length > 6" class="ch-stale-sub">…</span>
            </span>
          </div>
          <div v-if="chapters.length" class="ch-list">
            <div v-for="g in displayGroups" :key="g.key" class="ch-group">
              <div class="ch-group-title">{{ g.name }}</div>
              <div v-for="c in g.list" :key="c.no" class="ch-item" @click="openChapter(c)">
                <span class="ch-no">第{{ c.no }}章</span>
                <span class="ch-title-wrap">
                  <span class="ch-title">{{ c.title }}</span>
                </span>
                <!-- 质量徽标：只做索引（等级/分数/条数），点击进入唯一的改稿建议详情面 -->
                <span v-if="c.quality" class="ch-quality" role="button" tabindex="0"
                      :title="qualityIndexTitle(c.quality)" @click.stop="openVersions(c)"
                      @keydown.enter.prevent.stop="openVersions(c)">
                  <el-tag size="small" :type="qualityMeta(c.quality.severity).type" effect="plain">
                    {{ qualityMeta(c.quality.severity).label }}
                  </el-tag>
                  <span v-if="c.quality.has_quality && c.quality.score !== null" class="ch-score">
                    {{ c.quality.score }}分
                  </span>
                  <span v-if="c.quality.has_quality" class="ch-issue-count">{{ c.quality.issue_count }}处</span>
                </span>
                <span class="ch-words">{{ c.words ? fmtWords(c.words) : '未生成' }}</span>
                <el-tag v-if="isStale(c.no)" size="small" type="warning" effect="plain"
                        :title="staleReasonTitle(c.no)">
                  可能过期
                </el-tag>
                <el-tag v-if="c.status" :type="statusType(c.status)" size="small" effect="plain">{{ statusText(c.status) }}</el-tag>
                <div class="ch-actions" @click.stop>
                  <el-button size="small" type="warning" link @click="regenChapter(c)" :loading="regenNo === c.no" :disabled="genRunning">重生成</el-button>
                  <el-button size="small" type="primary" link @click="openVersions(c)">版本</el-button>
                  <el-button size="small" :type="isChLocked(c.no) ? 'success' : 'default'" link
                    :aria-label="isChLocked(c.no) ? `取消第${c.no}章的${LOCK_TERM}` : `给第${c.no}章加${LOCK_TERM}（重生成时不覆盖）`"
                    :title="isChLocked(c.no) ? `已加${LOCK_TERM}，点击取消` : `${LOCK_TERM}：重生成时不覆盖本章`"
                    @click="toggleLock('chapter', c.no)">
                    {{ isChLocked(c.no) ? '🔒' : '🔓' }}
                  </el-button>
                </div>
              </div>
            </div>
            <el-empty v-if="!displayGroups.length" description="没有符合筛选条件的章节">
              <el-button size="small" @click="qualityOnly = false">查看全部章节</el-button>
            </el-empty>
          </div>
          <el-empty v-else :description="outlineReady ? '大纲已就绪，还没有章节正文' : '还没有章节，需要先生成大纲'">
            <el-button v-if="outlineReady" type="primary" @click="tab = 'agent'">去 Agent 页生成正文</el-button>
            <el-button v-else type="primary" @click="tab = 'outline'">去生成大纲</el-button>
          </el-empty>
        </section>

        <!-- 大纲：原顶栏的大纲类操作收敛为页内工具条 -->
        <section v-if="visited.outline" v-show="tab === 'outline'">
          <div v-if="outlineReady || hadOutline" class="panel-toolbar">
            <div class="panel-toolbar-left">
              <el-button size="small" @click="doRegenOutline" :disabled="genRunning" title="重生成总纲/分卷">重生成大纲</el-button>
              <el-button size="small" @click="openImport" :disabled="genRunning" title="重新导入你写的大纲（覆盖前自动存一版，可在版本弹窗还原）">导入大纲</el-button>
              <el-button size="small" @click="openProductVersions('outline')" title="每一版大纲都在这里：AI 重生成、你手动保存、导入前自动备份">历史版本</el-button>
            </div>
          </div>
          <el-empty v-if="!outlineReady && !hadOutline" description="还没有大纲。AI 生成或导入你写的都可以">
            <div class="outline-empty-actions">
              <el-button type="primary" @click="runOutline" :loading="genRunning">
                {{ genRunning ? '生成中…' : '生成大纲（完成后停下等你审阅）' }}
              </el-button>
              <el-button @click="openImport" :disabled="genRunning">导入我写的大纲</el-button>
            </div>
          </el-empty>
          <!-- 门 1：大纲跑完即停，这里给判断材料与三个动作（通过 / 先编辑 / 重生成） -->
          <GateCard v-if="outlineReady"
            title="门 1 · 大纲已生成，先审阅再继续"
            :status="gateOf('outline').status" :status-label="gateLabel('outline')"
            :summary="gateReview('outline').summary || []" :checks="gateReview('outline').checks || []"
            :busy="gateBusy === 'outline'" approve-text="通过，继续生成世界观"
            :hint="gateHint('outline')" :actions="outlineGateActions"
            @approve="approveGateNow('outline')"
            @regen="doRegenOutline" @locate="locateAnchor" />
          <div v-if="outlineReady && gateApproved('outline') && !worldReady" class="gate-next">
            <el-button type="primary" @click="runWorld">生成世界观（按字数计费）</el-button>
            <span class="gate-next-hint">也可以先跳过：直接去 Agent 页写正文，世界观缺失会降低一致性</span>
          </div>
          <OutlineEditor v-if="outlineReady || hadOutline" :key="outlineReloadKey" :pid="pid" @saved="loadAll" @open-versions="openProductVersions('outline')" />
        </section>

        <!-- 创作图（门 1）：分章约束卡。Plan 层（可改）与账本（Canon，只增不改）分开 -->
        <section v-if="visited.plan" v-show="tab === 'plan'">
          <PlanGraphPanel :pid="pid" :chapter-count="chapters.length" />
        </section>

        <!-- 世界观 -->
        <section v-if="visited.world" v-show="tab === 'world'">
          <!-- 门 2：世界观审阅（含"受影响章纲清单"反向提示） -->
          <GateCard v-if="worldReady"
            title="门 2 · 世界观已生成，先审阅再写正文"
            :status="gateOf('world').status" :status-label="gateLabel('world')"
            :summary="gateReview('world').summary || []" :checks="gateReview('world').checks || []"
            :busy="gateBusy === 'world'" approve-text="通过，进入写正文"
            :hint="gateHint('world')" :actions="worldGateActions"
            @approve="approveGateNow('world')" @skip="skipWorldGate"
            @regen="doRegenWorld" @locate="locateAnchor" />
          <WorldEditor ref="worldEditorRef" :pid="pid" :lock="lock"
            @toggle-lock="toggleLock" @open-versions="openProductVersions" @regen-world="doRegenWorld" />
        </section>

        <!-- 小墨AI -->
        <section v-if="visited.assistant" v-show="tab === 'assistant'">
          <AssistantPanel :project-id="pid" :chapters="chapters" :current-ch="currentCh" @locate="locateChapter" />
        </section>

        <!-- Agent：生成操作统一入口 -->
        <section v-if="visited.agent" v-show="tab === 'agent'">
          <AgentPanel :pid="pid" :preset-range="agentPreset" @changed="loadAll" @recharge="rechargeRef?.open()" @started="onAgentStarted" />
        </section>

        <!-- 工具 -->
        <section v-if="visited.tools" v-show="tab === 'tools'">
          <ToolsPanel :pid="pid" @open-health="tab = 'health'" />
        </section>

        <!-- 时间线：账本事件流只读视图（已发生的事件 / 发展方向的事件） -->
        <section v-if="visited.facts" v-show="tab === 'facts'">
          <FactsTimelinePanel :pid="pid" />
        </section>

        <!-- 世界事实：账本事实侧只读视图（人物 / 状态 / 地点 / 事件 / 设定规则） -->
        <section v-if="visited.graph" v-show="tab === 'graph'">
          <StateGraphPanel :pid="pid" @open-rules="openRulesPanel" />
        </section>

        <!-- 全书体检：检查记录只读汇总（覆盖 / 伏笔 / 冲突 / 人物·设定索引） -->
        <section v-if="visited.health" v-show="tab === 'health'">
          <ConsistencyPanel :pid="pid" @locate="locateChapter" @open-versions="openVersionsByNo" />
        </section>
      </main>
    </div>

    <!-- 统一版本弹窗（阶段 1C）：chapter/outline/world 共用 -->
    <VersionDialog v-model="versionsDialog" :pid="pid" :kind="versionsKind" :chapter-no="versionsNo"
                   @changed="onVersionChanged" @appeal="appealIssue" />

    <RechargeDialog ref="rechargeRef" />

    <!-- 档3：生成前一致性门禁（可确认继续，不硬拒绝） -->
    <StaleGateDialog ref="staleGateRef" @started="onGateStarted" @open-agent="goAgentRewrite" />

    <el-dialog v-model="readerVisible" :title="currentCh?.title" width="800px" top="3vh" @closed="hideSelFloat">
      <template #header>
        <div class="reader-header">
          <span>第{{ currentCh?.no }}章 {{ currentCh?.title }}</span>
          <div class="reader-tools">
            <el-button-group size="small">
              <el-button @click="readerTheme = readerTheme === 'light' ? 'dark' : 'light'" :title="readerTheme === 'light' ? '夜间模式' : '日间模式'">
                {{ readerTheme === 'light' ? '🌙' : '☀️' }}
              </el-button>
              <el-button @click="fontSize = Math.max(14, fontSize - 2)" title="缩小字体">A-</el-button>
              <el-button @click="fontSize = Math.min(24, fontSize + 2)" title="放大字体">A+</el-button>
            </el-button-group>
            <el-button v-if="currentCh" size="small" type="primary"
                       @click="$router.push(`/project/${pid}/write?ch=${currentCh.no}`)">写作页</el-button>
            <el-button v-if="!editing" size="small" type="primary" @click="startEdit">编辑</el-button>
            <template v-else>
              <el-button size="small" @click="cancelEdit">取消</el-button>
              <el-button size="small" type="success" @click="saveEdit" :loading="saving">保存</el-button>
            </template>
          </div>
        </div>
      </template>
      <div v-if="!editing" class="reader-content" :class="readerTheme" :style="{fontSize: fontSize + 'px'}" v-html="currentContent" @mouseup="onReaderMouseup"></div>
      <el-input v-else v-model="editContent" type="textarea" :rows="22" class="editor-area" @mouseup="onEditMouseup" />
    </el-dialog>

    <teleport to="body">
      <div v-show="selFloat.show" class="sel-float" :style="{ left: selFloat.x + 'px', top: selFloat.y + 'px' }">
        <span class="sel-float-item" @click="doExpand('expand')">扩写</span>
        <span class="sel-float-sep">|</span>
        <span class="sel-float-item" @click="doExpand('continue')">续写</span>
      </div>
    </teleport>

    <el-dialog v-model="importVisible" title="导入大纲" width="560px">
      <p class="import-hint">将用你的新内容覆盖当前大纲（覆盖前自动备份上一版，可在「版本」弹窗里还原）。AI 不会改写你的内容。</p>
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <input type="file" ref="importFileInput" accept=".txt,.md,.docx" @change="onImportFilePick" />
        <span class="muted" style="font-size:12px">.txt / .md / .docx，≤5MB</span>
      </div>
      <div v-if="importFileMsg" class="file-msg" :class="importFileMsg.includes('识别') || importFileMsg.includes('解析完成') ? 'file-ok' : 'file-err'">{{ importFileMsg }}</div>
      <el-input v-model="importText" type="textarea" :rows="8" placeholder="或直接粘贴大纲内容（章节建议用「第X章」标题开头）" style="margin-top:8px" />
      <template #footer>
        <el-button @click="importVisible=false">取消</el-button>
        <el-button type="primary" :loading="reimporting" @click="doImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted, h } from 'vue'
import { useRoute } from 'vue-router'
import { getProject, cancelGenerate, getStatus, downloadProject, getAgentRun, getGates, approveGate, getGateReview, estimateAgentRun } from '../api/project'
import { getChapters, getChapterQualityOverview, getChapterStaleness, getOutline, getWorld, getChapter, updateChapterContent, getLock, setLock, importOutline, expandSelection, appealChapterIssue } from '../api/novel'
import {
  qualityMeta, sortRowsByQuality, filterRows, qualityIndexTitle,
  qualityWorstText, qualityGapText,
} from '../utils/chapterQuality'
// REQ-R23 P0-4：所有就近生成入口统一走 utils/agentRun（预估 → 余额校验 → /agent-runs 单轨）
import { runAgent, errorDetail } from '../utils/agentRunClient'
// REQ-R23 P0-5：术语统一（防覆盖锁定 / 写作铁律）
import { LOCK_TERM } from '../utils/toolTerms'
import { parseContent } from '../api/tools'
import ToolsPanel from '../components/tools/ToolsPanel.vue'
import OutlineEditor from '../components/outline/OutlineEditor.vue'
import WorldEditor from '../components/world/WorldEditor.vue'
import AssistantPanel from '../components/assistant/AssistantPanel.vue'
import AgentPanel from '../components/agent/AgentPanel.vue'
import ProjectSideNav from '../components/project/ProjectSideNav.vue'
import PlanGraphPanel from '../components/project/PlanGraphPanel.vue'
import ConsistencyPanel from '../components/consistency/ConsistencyPanel.vue'
import FactsTimelinePanel from '../components/facts/FactsTimelinePanel.vue'
import StateGraphPanel from '../components/facts/StateGraphPanel.vue'
import VersionDialog from '../components/VersionDialog.vue'
import RechargeDialog from '../components/RechargeDialog.vue'
import GenerationProgress from '../components/common/GenerationProgress.vue'
import GateCard from '../components/common/GateCard.vue'
// R27：分步确认式生成流（步骤条 + 当前阶段确认卡；状态由后端 generation_stage.json 唯一权威）
import GenerationFlow from '../components/flow/GenerationFlow.vue'
// 档3：409 stale_gate 的二次确认弹窗（复用 StaleNotice 视觉）
import StaleGateDialog from '../components/common/StaleGateDialog.vue'
import {
  begin, applyStatus, isRunning, errorText, reset, AGENT_PHASE,
} from '../composables/useGenerationProgress'
import { ElMessage, ElMessageBox } from 'element-plus'

const route = useRoute()
const pid = route.params.id
const loading = ref(false)
const project = ref(null)
const chapters = ref([])
const outline = ref(null)
const lock = ref({})
const worldEditorRef = ref(null)
// REQ-章节版本化：版本选择弹窗状态
const versionsDialog = ref(false)
const versionsNo = ref(0)
const versionsKind = ref('chapter')  // 阶段 1C：统一版本弹窗的当前产物类型（chapter/outline/world）
const rechargeRef = ref(null)
const staleGateRef = ref(null)
const outlineReady = computed(() => !!(outline.value && outline.value.chapters && outline.value.chapters.length))

// === 创作阶段审阅门（REQ-创作阶段审阅门 M0）===
// 门状态由后端按"审批版本 vs 产物当前版本"实时判定，前端只读展示 + 触发通过
const BATCH_SIZE = 5
const gates = ref({})
const gateReviews = reactive({})
const gateBusy = ref('')
const batchCard = ref(null)
const worldReady = ref(false)
const gateOf = (g) => gates.value?.[g] || { status: 'pending', current_version: null, approved_version: null }
const gateApproved = (g) => gateOf(g).status === 'approved'
const gateLabel = (g) => ({ pending: '待审阅', approved: '已通过', skipped: '已跳过' }[gateOf(g).status] || '')
function gateHint(g) {
  const st = gateOf(g)
  if (st.status === 'approved' || st.status === 'skipped') return `审批版本 ${st.approved_version}`
  if (!st.current_version) return '产物尚未生成'
  if (st.approved_version) return `产物已变更为 ${st.current_version}，原审批（${st.approved_version}）已失效，请重新审阅`
  return `当前版本 ${st.current_version}`
}
// 门卡片的动作：编辑始终允许（编辑器就在卡片下方），所以按钮只留"通过/重生成"，
// 原先的「先编辑」是空操作（点了只是切到当前 tab），已移除
const outlineGateActions = computed(() =>
  gateApproved('outline') ? ['regen'] : ['approve', 'regen'])
const worldGateActions = computed(() =>
  (gateOf('world').status === 'approved' || gateOf('world').status === 'skipped')
    ? ['regen'] : ['approve', 'regen', 'skip'])
const gateReview = (g) => gateReviews[g] || {}

// 未写章推算：按大纲章号顺序找第一个没写正文的章，本批到 from+4 为止
const outlineChapters = computed(() =>
  (outline.value?.chapters || []).map(c => Number(c.no)).filter(n => n > 0).sort((a, b) => a - b))
const writtenNos = computed(() => new Set(chapters.value.map(c => Number(c.no))))
const nextUnwritten = computed(() => {
  const set = writtenNos.value
  for (const no of outlineChapters.value) if (!set.has(no)) return no
  return 0
})
const batchEnd = computed(() => {
  if (!nextUnwritten.value || !outlineChapters.value.length) return 0
  const last = outlineChapters.value[outlineChapters.value.length - 1]
  return Math.min(nextUnwritten.value + BATCH_SIZE - 1, last)
})

async function loadGates() {
  try {
    gates.value = (await getGates(pid)).gates || {}
    await loadGateReviews()
  } catch { /* 门状态读取失败不阻塞项目页 */ }
}
async function loadGateReviews() {
  for (const g of ['outline', 'world']) {
    const hasProduct = g === 'outline' ? outlineReady.value : worldReady.value
    if (!hasProduct || !gateOf(g).current_version) continue
    try { gateReviews[g] = await getGateReview(pid, g) } catch { /* 材料获取失败只影响提示 */ }
  }
}
async function loadWorldFlag() {
  try { worldReady.value = !!((await getWorld(pid))?.world_view) } catch { worldReady.value = false }
}
async function approveGateNow(g) {
  gateBusy.value = g
  try {
    gates.value = (await approveGate(pid, g)).gates || {}
    ElMessage.success(g === 'outline' ? '大纲已通过，可以生成世界观了' : '世界观已通过，可以开始写正文')
    await loadGates()
  } catch (e) {
    ElMessage.error(errorDetail(e, '操作失败'))
  } finally { gateBusy.value = '' }
}
async function skipWorldGate() {
  try {
    await ElMessageBox.confirm('跳过世界观直接写正文，前后一致性会下降（可随时回来生成）。确定跳过？',
      '跳过世界观门', { type: 'warning', confirmButtonText: '确定跳过' })
  } catch { return }
  gateBusy.value = 'world'
  try {
    gates.value = (await approveGate(pid, 'world', { skip: true })).gates || {}
    ElMessage.success('已跳过世界观门')
  } catch (e) {
    ElMessage.error(errorDetail(e, '操作失败'))
  } finally { gateBusy.value = '' }
}
// 体检项定位：anchor 形如 chapter:12 → 切章节页并打开该章
function locateAnchor(anchor) {
  const m = /^chapter:(\d+)$/.exec(anchor || '')
  if (m) { tab.value = 'chapters'; locateChapter(Number(m[1])) }
}
// 记忆"曾生成过大纲"：重生成大纲期间后端会先删 outline.json，若此时重载大纲会拿到空值，
// 导致 outlineReady 翻转、编辑器被空态替换。用 hadOutline 保住工具条与编辑器（生成中置灰而非隐藏）。
const hadOutline = ref(false)
const isChLocked = (no) => !!(lock.value.chapter || {})[no]
async function toggleLock(kind, key = '', forced) {
  try {
    const locked = forced !== undefined ? forced : !((lock.value[kind] || {})[key])
    await setLock(pid, { kind, key: String(key), locked })
    lock.value = await getLock(pid)
    ElMessage.success(locked ? `已加${LOCK_TERM}，重生成时不会覆盖` : `已取消${LOCK_TERM}`)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}
const tab = ref('chapters')
// 用户是否手动切换过侧栏导航；未手动切换时，loadAll 按数据状态引导默认页（无大纲→大纲页）
const navTouched = ref(false)
// 面板懒挂载：首次切到才挂载（保留各面板 onMounted 语义），之后 v-show 保持状态
const visited = reactive({ chapters: true })
watch(tab, (t) => { visited[t] = true })
const readerVisible = ref(false)
const currentCh = ref(null)
const currentContent = ref('')
const editing = ref(false)
const editContent = ref('')
const saving = ref(false)
const readerTheme = ref('light')
const fontSize = ref(16)
const exporting = ref(false)
const regenNo = ref(0)
// 生成完成后自增，强制重新挂载 OutlineEditor（它只在 onMounted 时拉一次大纲）
const outlineReloadKey = ref(0)
// 生成中（驱动横幅显示与按钮置灰）：直接复用统一进度 store 的运行态
const genRunning = isRunning
// 错误文案（断点继续/成本防护提示）：统一进度 store
const genError = errorText
// 成本防护/异常终止后可断点继续：后端提示含「可从断点继续」时，展示醒目的「继续生成」入口
const resumable = computed(() =>
  genError.value.includes('可从断点继续') || genError.value.includes('成本防护'))
// 章节生成进度（总章节数 / 已生成数），驱动顶栏元数据与状态徽章
const genTotal = ref(0)
const genDoneCount = ref(0)
const totalWords = computed(() => chapters.value.reduce((sum, c) => sum + (c.words || 0), 0))
const bookStatus = computed(() => {
  if (genRunning.value) return { text: '生成中', type: 'warning' }
  const total = genTotal.value
  const done = genDoneCount.value
  if (!total && !done) return { text: '待开始', type: 'info' }
  if (total && done >= total) return { text: '已完成', type: 'success' }
  return { text: '进行中', type: 'primary' }
})

// 字数格式化：过万用「万字」缩写，保持元数据行紧凑
function fmtWords(n) {
  if (!n) return '0 字'
  return n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, '') + ' 万字' : n + ' 字'
}

// 点击启动类按钮后立即给出进行中的横幅，不必等轮询
function beginGenUI(statusText) {
  reset()
  begin({ label: statusText })
}

// 按分卷分组的章节菜单：以 outline.volumes 为骨架，已生成章节按 no 合并状态
const groupedChapters = computed(() => {
  const vols = (outline.value && outline.value.volumes) || []
  const out = []
  const chMap = {}
  ;(chapters.value || []).forEach(c => { chMap[c.no] = c })
  const olChs = (outline.value && outline.value.chapters) || []
  vols.forEach(v => {
    const list = olChs.filter(c => c.volume_no === v.no).map(c => ({ ...chMap[c.no], ...c }))
    if (list.length) out.push({ key: 'vol-' + v.no, name: v.name || ('第' + v.no + '卷'), list })
  })
  const unassigned = olChs.filter(c => !c.volume_no || c.volume_no === 0).map(c => ({ ...chMap[c.no], ...c }))
  if (unassigned.length) out.push({ key: 'vol-0', name: '未分卷', list: unassigned })
  // 兜底：outline 为空时直接用 chapters
  if (!olChs.length && chapters.value.length) {
    out.push({ key: 'all', name: '全部章节', list: chapters.value })
  }
  return out
})

// ===== R12 章节质量总览（只读展示） =====
const qualityOverview = ref(null)   // GET /chapters/quality-overview 的响应
const qualitySort = ref('chapter')  // chapter=按章号 / quality=质量最差优先
const qualityOnly = ref(false)      // 只看需要处理的章（未通过或含大改）
// 章号 → 质量摘要（后端按选中版本统一计算，前端只做展示）
const qualityMap = computed(() => {
  const map = {}
  ;((qualityOverview.value || {}).chapters || []).forEach(q => { map[q.no] = q })
  return map
})
// 把质量摘要挂到章节行；未写正文的章不挂（不显示「未质检」误导）
const displayGroups = computed(() => {
  let groups = groupedChapters.value.map(g => ({ ...g, list: g.list.map(row => ({
    ...row, quality: row.words ? qualityMap.value[row.no] : null,
  })) }))
  if (qualityOnly.value) {
    groups = groups
      .map(g => ({ ...g, list: filterRows(g.list, true) }))
      .filter(g => g.list.length)
  }
  if (qualitySort.value === 'quality') {
    // 最差优先时按章平铺，分卷骨架会打乱全局排名
    const flat = sortRowsByQuality(groups.flatMap(g => g.list))
    return flat.length ? [{ key: 'quality', name: '按质量最差优先', list: flat }] : []
  }
  return groups
})
const worstText = computed(() => qualityWorstText(qualityOverview.value, chapters.value))
const qualityGap = computed(() => qualityGapText(qualityOverview.value))

// ===== 档1 上游过期标记（读时推导，只读展示） =====
const staleness = ref(null)   // GET /chapters/staleness 的响应
const staleMap = computed(() => {
  const map = {}
  ;((staleness.value || {}).chapters || []).forEach(r => { map[r.chapter] = r })
  return map
})
function fmtRange(r) { return r.from === r.to ? `第${r.from}章` : `第${r.from}–${r.to}章` }
// 一条轻量提示：「第 3–5 章可能已过期，因为大纲更新到 v5」
const staleHint = computed(() => {
  const s = staleness.value && staleness.value.summary
  if (!s || !s.stale_count) return ''
  const ranges = (s.ranges || []).map(fmtRange).join('、')
  const current = s.current || {}
  const causes = (s.causes || [])
    .map(c => `${c.label}更新到 v${current[c.kind + '_v']}`)
    .join('、')
  return `${ranges}可能已过期${causes ? `，因为${causes}` : ''}`
})
const staleUnknownHint = computed(() => {
  const s = staleness.value && staleness.value.summary
  return s && s.unknown_count
    ? `另有 ${s.unknown_count} 章缺少生成时的上游版本记录，无法判断是否过期`
    : ''
})
// 档2「前文重写」提示：与上游大纲/世界观成因分开说，如「第3章已重写（v1→v2），影响第4–6章」
const prevRewriteHint = computed(() => {
  const s = staleness.value && staleness.value.summary
  const items = (s && s.prev_rewrites) || []
  if (!items.length) return ''
  return items.map(it => {
    const act = it.direction === 'reverted' ? '已切回旧版' : '已重写'
    return `第${it.ch}章${act}（v${it.based_on}→v${it.current}），影响${fmtNos(it.affected)}`
  }).join('；')
})
const staleJumpChapters = computed(() => ((staleness.value || {}).summary || {}).stale_chapters || [])
function isStale(no) { return (staleMap.value[no] || {}).status === 'stale' }
// 连续章号压缩：「第4章、第5章」→「第4–5章」
function fmtNos(list) {
  const out = []
  let start = null
  let prev = null
  for (const n of list || []) {
    if (start === null) { start = prev = n; continue }
    if (n === prev + 1) { prev = n; continue }
    out.push(start === prev ? `第${start}章` : `第${start}–${prev}章`)
    start = prev = n
  }
  if (start !== null) out.push(start === prev ? `第${start}章` : `第${start}–${prev}章`)
  return out.join('、')
}
// 单章 tag 的 title：把两种成因都说清楚（大纲/世界观更新 + 前文被重写）
function staleReasonTitle(no) {
  const r = (staleMap.value[no] || {}).reason || {}
  const parts = []
  const dims = Object.keys(r.stale_dims || {}).map(k => (k === 'outline' ? '大纲' : '世界观'))
  if (dims.length) parts.push(`${dims.join('、')}已更新`)
  const rewrites = r.prev_rewrites || []
  if (rewrites.length) {
    parts.push(rewrites.map(it => `第${it.ch}章已重写（v${it.based_on}→v${it.current}）`).join('、'))
  }
  return parts.length ? `该章基于旧版生成：${parts.join('；')}，可能已过期` : '该章可能已过期'
}
function jumpToStale(no) {
  const c = chapters.value.find(x => Number(x.no) === Number(no))
  if (c) openChapter(c)
  else ElMessage.info(`第${no}章正文已不存在，无法跳转`)
}

async function loadStaleness() {
  try {
    staleness.value = await getChapterStaleness(pid)
  } catch {
    // 只读判定失败不阻断章节列表：降级为无提示
    staleness.value = { chapters: [], summary: { stale_count: 0, unknown_count: 0 }, data_gaps: ['过期检查失败，本次未展示'] }
  }
}

async function loadQualityOverview() {
  try {
    qualityOverview.value = await getChapterQualityOverview(pid)
  } catch {
    // 只读汇总失败不阻断章节列表：降级为无质量数据并给出说明
    qualityOverview.value = { chapters: [], worst: null, data_gaps: ['质量数据加载失败，本次未展示质检信息'] }
  }
}

async function regenChapter(c) {
  try { await ElMessageBox.confirm(`确定重新生成第${c.no}章《${c.title}》？将追加为新版本，旧版本保留。`, '重新生成章节', { type: 'warning' }) } catch { return }
  regenNo.value = c.no
  const payload = { agent: 'writer.chapter', params: { scope: 'single', ch_no: c.no } }
  try {
    const { body } = await runAgent(pid, payload)
    ElMessage.success(`已开始重新生成第${c.no}章`)
    // 单章重写走 getAgentRun 轮询（running 态带 progress；done 态 outputs 带质检/候选反馈，F1）
    const s = await new Promise((resolve) => {
      const t = setInterval(async () => {
        try {
          const r = await getAgentRun(pid, body.task_id)
          if (r.status !== 'running' && r.status !== 'resumed') { clearInterval(t); resolve(r) }
        } catch {}
      }, 2000)
    })
    await loadChapters()
    const fb = (s.outputs || []).find((o) => o.kind === 'chapter' && Number(o.no) === Number(c.no))
    if (fb && fb.kept_previous) {
      const q = fb.quality
      const score = (q && q.score != null) ? `（质检 ${q.score}${q.passed ? ' 通过' : ' 未过'}）` : ''
      ElMessage.warning(`第${c.no}章质检未通过${score}，已保留上一版，新稿存为候选 v${fb.candidate_version}，可在版本弹窗查看/切换`)
    } else {
      ElMessage.success(`第${c.no}章已重新生成`)
    }
  } catch (e) {
    // 档3：目标章之前存在未纳入本次的过期章 → 二次确认（仍然继续 / 从第X章起重写）
    if (!staleGateRef.value?.open(pid, e?.response?.data?.detail, payload, onGateStarted)) {
      ElMessage.error(errorDetail(e, '重生成失败'))
    }
  } finally { regenNo.value = 0 }
}

// 版本弹窗（阶段 1C）：列表/选择/删除/影响范围统一收敛到 components/VersionDialog.vue，
// 本页只保留打开入口与申诉回调（P2-3 申诉撤销：对某条质检/合规判罚发起申诉，误判兜底）
async function appealIssue(it) {
  const issueType = it.type || ''
  const label = issueType || '质检问题'
  try {
    const { value } = await ElMessageBox.prompt(
      `针对「${issueType || it.detail || '该问题'}」申诉：请简要说明为什么这是误判或可接受。`,
      `申诉 · ${label}`,
      { type: 'warning', inputPlaceholder: '例如：此段是战斗场景中的正常描写，非违规', confirmButtonText: '提交申诉', cancelButtonText: '取消', inputValidator: (v) => (v && v.trim() ? true : '请填写申诉理由') }
    )
    await appealChapterIssue(pid, versionsNo.value, { reason: value.trim(), issue_type: issueType })
    ElMessage.success('申诉已提交，将计入误判复核')
  } catch (e) {
    if (e === 'cancel' || e?.message === 'cancel' || e?.action === 'cancel') return
    ElMessage.error(e?.response?.data?.detail || '申诉提交失败')
  }
}

function openVersions(c) {
  versionsKind.value = 'chapter'
  versionsNo.value = c.no
  versionsDialog.value = true
}
// 列表顶部「最需要处理」只做索引：点击直达该章唯一的改稿建议详情面
function openWorstVersions() {
  const worst = qualityOverview.value && qualityOverview.value.worst
  if (!worst) return
  openVersionsByNo(worst.no)
}
// 体检页冲突卡直达单章改稿建议（不进入阅读、不触发任何检查）
function openVersionsByNo(no) {
  const ch = chapters.value.find(c => Number(c.no) === Number(no))
  if (ch) openVersions(ch)
  else ElMessage.warning(`第 ${no} 章还未生成`)
}
function openProductVersions(kind) {
  versionsKind.value = kind
  versionsNo.value = 0
  versionsDialog.value = true
}
function onVersionChanged() {
  loadChapters()
  if (versionsKind.value === 'outline') outlineReloadKey.value++
  if (versionsKind.value === 'world') worldEditorRef.value?.reload()
}
let pollTimer = null

async function loadAll() {
  loading.value = true
  try {
    project.value = await getProject(pid)
    outline.value = await getOutline(pid)
    hadOutline.value = hadOutline.value || !!(outline.value?.chapters?.length)
    genTotal.value = outline.value?.chapters?.length || 0
    // 章节列表、质量总览与上游过期标记并行拉取（后两者只读且已内部降级）
    const [chsRes] = await Promise.all([getChapters(pid), loadQualityOverview(), loadStaleness()])
    chapters.value = chsRes.chapters || []
    genDoneCount.value = chapters.value.length
    try { lock.value = await getLock(pid) } catch { lock.value = {} }
    // 门状态与世界观存在性：决定门 1/2 的按钮与判断材料（读取失败各自降级）
    await loadWorldFlag()
    await loadGates()
    // 首次进入且用户未手动选过导航时，按数据状态引导：无大纲→大纲页，有大纲→章节页
    if (!navTouched.value) tab.value = outlineReady.value ? 'chapters' : 'outline'
    await checkStatus()
  } catch (e) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

async function checkStatus() {
  try {
    const s = await getStatus(pid)
    applyStatus(s)
    if (s.chapters_total) genTotal.value = s.chapters_total
    genDoneCount.value = s.chapters_done ?? genDoneCount.value
    if (s.running) startPolling()
  } catch {}
}

function startPolling(onDone, quiet = false) {
  if (pollTimer) clearInterval(pollTimer)
  let lastDone = -1
  pollTimer = setInterval(async () => {
    try {
      const s = await getStatus(pid)
      applyStatus(s)
      if (s.chapters_total) genTotal.value = s.chapters_total
      genDoneCount.value = s.chapters_done ?? genDoneCount.value
      // 每生成完一章就刷新章节列表，让用户实时看到进度
      if (s.chapters_done !== lastDone) {
        lastDone = s.chapters_done
        loadChapters()
      }
      if (!s.running) {
        clearInterval(pollTimer)
        // 生成结束后全量刷新大纲/世界观/章节/锁，否则切到对应面板仍是旧空数据
        if (s.error) {
          if (!quiet) ElMessage.error('生成失败：' + s.error)
        } else {
          // 可选链式回调（如大纲完成后自动生成世界观）：返回 true 表示已接管后续流程，跳过默认刷新
          const chained = onDone ? await onDone(s) : false
          if (!chained) await refreshAfterGen(quiet)
        }
      }
    } catch {}
  }, 3000)
}

// 生成完成后的静默刷新：不弹全屏 loading，只重拉数据并强制重挂大纲编辑器
async function refreshAfterGen(quiet = false) {
  try {
    outline.value = await getOutline(pid)
    hadOutline.value = hadOutline.value || !!(outline.value?.chapters?.length)
    await worldEditorRef.value?.reload()
    await loadWorldFlag()
    await loadGates()
    try { lock.value = await getLock(pid) } catch { lock.value = {} }
    chapters.value = (await getChapters(pid)).chapters || []
    genDoneCount.value = chapters.value.length
    await loadStaleness()
    outlineReloadKey.value++
    loadSnapshots()
    if (!quiet) ElMessage.success('任务已完成，数据已刷新')
  } catch (e) {
    ElMessage.error('数据刷新失败，请手动刷新页面')
  }
}

// U1：Agent 面板启动运行时联动全局生成横幅（getStatus 跟随同一生成槽位）；
// quiet 轮询避免与 Agent 面板自身的完成提示重复弹 toast
const AGENT_GEN_LABEL = {
  researcher: '正在调研题材',
  'architect.outline': '正在生成大纲',
  'architect.world': '正在生成世界观',
  'architect.volume': '正在重写卷纲',
  'writer.chapter': '正在逐章写作',
}
function onAgentStarted({ agent, task_id }) {
  // U1：Agent 面板启动运行 → 立即给出顶部横幅；带真实阶段与 task_id，首个轮询到达后被精确进度接管
  reset()
  begin({
    label: AGENT_GEN_LABEL[agent] || '正在生成…',
    phase: AGENT_PHASE[agent] || '',
    taskId: task_id || '',
  })
  startPolling(null, true)
}

async function loadChapters() {
  // 切版本 / 生成完成后同步刷新质量总览与过期标记，保证列表分数/提示与版本一致
  const [chsRes] = await Promise.all([getChapters(pid), loadQualityOverview(), loadStaleness()])
  chapters.value = chsRes.chapters || []
}

async function openChapter(c) {
  currentCh.value = c
  readerVisible.value = true
  editing.value = false
  try {
    const res = await getChapter(pid, c.no)
    editContent.value = res.content || ''
    currentContent.value = editContent.value.replace(/\n/g, '<br>')
  } catch { currentContent.value = '内容加载失败' }
}

// 小墨AI「定位到第 X 章」：切回章节面板并打开阅读器
function locateChapter(no) {
  const ch = chapters.value.find(c => Number(c.no) === Number(no))
  if (ch) { tab.value = 'chapters'; openChapter(ch) }
  else ElMessage.warning(`第 ${no} 章还未生成`)
}
// 世界事实「设定规则」只读投影 → 跳工具中心维护写作铁律（只切页，不触发任何检查/生成）
function openRulesPanel() {
  navTouched.value = true
  tab.value = 'tools'
}

// R27 确认卡「打开编辑器」：只切页，不触发任何生成/计费
function openFlowStage(stage) {
  navTouched.value = true
  if (stage === 'outline') tab.value = 'outline'
  else if (stage === 'world') tab.value = 'world'
  else tab.value = 'agent'
}

function startEdit() {
  // M0 硬约束：批量生成期间正文只读（中途编辑会让记忆账本 stale，后续章基于旧账本写）
  if (genRunning.value) {
    ElMessage.warning('批量生成中正文只读，要修改请先停止本批')
    return
  }
  editing.value = true
}
function cancelEdit() {
  editing.value = false
}

// === 选中段落扩写/续写（恢复自旧版 webui 的浮动按钮入口） ===
const selFloat = ref({ show: false, x: 0, y: 0, text: '', fromEdit: false, editPos: -1 })
const expanding = ref(false)

function hideSelFloat() { selFloat.value.show = false }

function showSelFloat(e, text, fromEdit, editPos) {
  const maxX = Math.max(8, window.innerWidth - 130)
  const x = Math.min(Math.max(8, e.clientX), maxX)
  const y = Math.max(8, e.clientY - 44)
  selFloat.value = { show: true, x, y, text, fromEdit, editPos }
}

// 阅读模式：选中 v-html 渲染的正文
function onReaderMouseup(e) {
  const sel = window.getSelection()
  const text = sel ? sel.toString().trim() : ''
  if (!text || text.length < 5) { hideSelFloat(); return }
  showSelFloat(e, text, false, -1)
}

// 编辑模式：选中 textarea 内的文字
function onEditMouseup(e) {
  const ta = e.target
  if (!ta || ta.tagName !== 'TEXTAREA') { hideSelFloat(); return }
  const text = ta.value.substring(ta.selectionStart, ta.selectionEnd).trim()
  if (!text || text.length < 5) { hideSelFloat(); return }
  showSelFloat(e, text, true, ta.selectionEnd)
}

// 点击浮动条之外的区域时收起
function onDocMouseDown(e) {
  if (selFloat.value.show && !(e.target && e.target.closest && e.target.closest('.sel-float'))) hideSelFloat()
}

async function doExpand(mode) {
  if (expanding.value) return
  const { text, fromEdit, editPos } = selFloat.value
  hideSelFloat()
  if (!text) return
  expanding.value = true
  try {
    const res = await expandSelection(pid, { text, mode })
    const action = mode === 'continue' ? '续写' : '扩写'
    const ok = await ElMessageBox.confirm(
      h('pre', { style: 'white-space:pre-wrap;margin:0;max-height:300px;overflow:auto;font-size:13px;line-height:1.7;' }, res.result),
      `${action}结果`,
      { confirmButtonText: '插入到文中', cancelButtonText: '放弃', type: 'info' }
    ).then(() => true).catch(() => false)
    if (!ok) return
    // 插入位置：编辑态取光标处；阅读态取选中文本在原内容中的结束位置，找不到则追加到末尾
    let pos = fromEdit && editPos >= 0 ? editPos : -1
    if (pos < 0) {
      const idx = editContent.value.indexOf(text)
      pos = idx >= 0 ? idx + text.length : editContent.value.length
    }
    if (!editing.value) editing.value = true
    const prefix = pos === 0 || editContent.value[pos - 1] === '\n' ? '' : '\n'
    editContent.value = editContent.value.slice(0, pos) + prefix + res.result + editContent.value.slice(pos)
    ElMessage.success(`${action}内容已插入，可继续编辑后保存`)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '扩写/续写失败')
  } finally { expanding.value = false }
}

async function saveEdit() {
  saving.value = true
  try {
    await updateChapterContent(pid, currentCh.value.no, { content: editContent.value })
    currentContent.value = editContent.value.replace(/\n/g, '<br>')
    editing.value = false
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally { saving.value = false }
}

async function runOutline() {
  try {
    await runAgent(pid, { agent: 'architect.outline', params: {} })
    // M0：取消"大纲完成自动接世界观"的链式调用——跑完即停，由门 1 审阅后显式继续
    ElMessage.success('已开始生成大纲，完成后会停下等你审阅（不会自动生成世界观）')
    beginGenUI('正在生成大纲…')
    startPolling()
  } catch (e) { ElMessage.error(errorDetail(e, '启动失败')) }
}
// 门 1 通过后显式触发的第一次世界观生成（原先是 runOutline 内自动链过来的）
async function runWorld() {
  try {
    await runAgent(pid, { agent: 'architect.world', params: {} })
    ElMessage.success('已开始生成世界观（按字数计费），完成后会停下等你审阅')
    beginGenUI('正在生成世界观…')
    startPolling()
  } catch (e) { ElMessage.error(errorDetail(e, '启动失败')) }
}
async function doRegenOutline() {
  try { await ElMessageBox.confirm(`重生成大纲会覆盖现有总纲/分卷，已加${LOCK_TERM}的内容不受影响。继续？`, '重生成大纲', { type: 'warning' }) } catch { return }
  try {
    await runAgent(pid, { agent: 'architect.outline', params: {} })
    ElMessage.success('已开始重生成大纲')
    beginGenUI('正在重生成大纲…')
    startPolling()
    loadSnapshots()
  } catch (e) { ElMessage.error(errorDetail(e, '失败')) }
}
async function doRegenWorld() {
  try { await ElMessageBox.confirm(`重生成世界观会覆盖现有世界设定，已加${LOCK_TERM}的内容不受影响。继续？`, '重生成世界观', { type: 'warning' }) } catch { return }
  try {
    await runAgent(pid, { agent: 'architect.world', params: {} })
    ElMessage.success('已开始重生成世界观')
    beginGenUI('正在重生成世界观…')
    startPolling()
  } catch (e) { ElMessage.error(errorDetail(e, '失败')) }
}
async function startGen() {
  // M0：正文不再 scope=all，默认按 5 章一批（批末停，给批末卡片）
  const from = nextUnwritten.value
  if (!from) { ElMessage.info('全部章节都已写完'); return }
  const to = batchEnd.value
  const payload = { agent: 'writer.chapter', params: { scope: 'range', from_ch: from, to_ch: to } }
  try {
    await runAgent(pid, payload)
    batchCard.value = null
    ElMessage.success(`已开始写第 ${from}–${to} 章，本批结束会停下`)
    beginGenUI('正在写本批章节…')
    startPolling(async () => { await buildBatchCard(from, to); return false })
  } catch (e) {
    // 档3：目标章之前有未纳入本次的过期章 → 二次确认（仍然继续 / 从第X章起重写）
    if (!staleGateRef.value?.open(pid, e?.response?.data?.detail, payload, onGateStarted)) {
      ElMessage.error(errorDetail(e, '启动失败'))
    }
  }
}

// 批末材料：本批字数 / 质检合格率 / 扣费（后端确定性汇总，零 LLM）
async function buildBatchCard(from, to) {
  try {
    const r = await getGateReview(pid, 'batch', { from_ch: from, to_ch: to })
    batchCard.value = { from, to, summary: r.summary || [], checks: r.checks || [] }
    tab.value = 'chapters'
  } catch { /* 批末统计失败不影响正文已落盘 */ }
}

// 「一键全部生成」：小字逃生舱，二次确认 + 预估总额，跳过全部门
async function startGenAll() {
  const payload = { agent: 'writer.chapter', params: { scope: 'all' } }
  let estimate = null
  try { estimate = (await estimateAgentRun(pid, payload)).estimated_price } catch { /* 预估失败不阻塞 */ }
  const money = typeof estimate === 'number' ? `，预估约 ¥${estimate.toFixed(2)}` : ''
  try {
    await ElMessageBox.confirm(
      `将一次写完所有未完成章${money}。这条路径不经过审阅门，中途只能靠「停止」与版本回滚，确定继续？`,
      '一键全部生成', { type: 'warning', confirmButtonText: '确认，一次跑完' })
  } catch { return }
  try {
    await runAgent(pid, payload)
    ElMessage.success('已开始一次跑完全部未完成章')
    beginGenUI('正在逐章写作…')
    startPolling()
  } catch (e) {
    ElMessage.error(errorDetail(e, '启动失败'))
  }
}

// 门禁弹窗确认放行后的统一后续
function onGateStarted() {
  ElMessage.success('已开始处理')
  beginGenUI('正在续写…')
  startPolling()
}

// REQ-R23：409 弹窗不再自己二次提交重写，统一送到 Agent 页（章节写手 · 区间）发起
const agentPreset = ref(null)
function goAgentRewrite({ from_ch }) {
  agentPreset.value = { from_ch, stamp: Date.now() }
  tab.value = 'agent'
}

async function cancelGen() {
  try {
    await cancelGenerate(pid)
    ElMessage.info('已取消')
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    reset()  // 立即收起横幅（运行态置否），不依赖后续轮询
  } catch (e) { ElMessage.error('取消失败') }
}

// blob 错误响应（如 404 detail 以 JSON 返回时，axios 会把它包成 Blob）转成可读信息
async function blobErrorDetail(e) {
  try {
    const d = e?.response?.data
    if (d && typeof d.text === 'function') {
      const t = await d.text()
      try { return JSON.parse(t)?.detail || t } catch { return t }
    }
    return e?.response?.data?.detail || e?.message || ''
  } catch { return '' }
}

async function doDownload(fmt) {
  exporting.value = true
  try {
    const blob = await downloadProject(pid, fmt)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const title = (project.value?.config?.title || pid).replace(/[\\/:*?"<>|]/g, '_')
    a.href = url
    a.download = `${title}.${fmt}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${fmt.toUpperCase()}`)
  } catch (e) {
    const detail = await blobErrorDetail(e)
    ElMessage.error(detail || `导出 ${fmt.toUpperCase()} 失败，请确认已生成章节`)
  } finally { exporting.value = false }
}

function statusType(s) {
  if (s === 'done') return 'success'
  if (s === 'generating') return 'warning'
  if (s === 'failed') return 'danger'
  return 'info'
}
const STATUS_TEXT = { done: '已完成', generating: '生成中', pending: '待生成', failed: '失败' }
function statusText(s) { return STATUS_TEXT[s] || s }

const importVisible = ref(false)
const importText = ref('')
const importFileMsg = ref('')
const importFileInput = ref(null)
const reimporting = ref(false)

function openImport() {
  importText.value = ''
  importFileMsg.value = ''
  importVisible.value = true
}

function onImportFilePick(e) {
  const f = e.target.files[0]
  if (!f) return
  if (f.size > 5 * 1024 * 1024) { importFileMsg.value = '文件超过 5MB 限制，请精简后上传'; e.target.value = ''; return }
  const ext = ((f.name.match(/\.([^.]+)$/) || [])[1] || '').toLowerCase()
  if (!['txt', 'md', 'docx'].includes(ext)) { importFileMsg.value = '仅支持 .txt / .md / .docx 格式'; e.target.value = ''; return }
  importFileMsg.value = '正在解析…'
  const fd = new FormData()
  fd.append('file', f)
  parseContent(fd).then(res => {
    importText.value = res.full_text || ''
    const n = res.chapters?.length || 0
    importFileMsg.value = n ? `已识别 ${n} 个章节，已填入下方输入框` : '解析完成，已填入（未识别到章节标题）'
  }).catch(err => {
    importFileMsg.value = err.response?.data?.detail || '解析失败，请检查文件格式'
    e.target.value = ''
  })
}

async function doImport() {
  const text = importText.value.trim()
  if (!text) { ElMessage.warning('请粘贴或上传大纲内容'); return }
  if (text.length > 100000) { ElMessage.warning('粘贴内容超过 10 万字上限，请拆分或改用文件上传'); return }
  reimporting.value = true
  try {
    await importOutline(pid, { text })
    ElMessage.success('已导入（以你的内容为准）')
    importVisible.value = false
    loadAll()
  } catch (e) {
    ElMessage.warning(e.response?.data?.detail || '导入失败')
  } finally { reimporting.value = false }
}

onMounted(() => { document.addEventListener('mousedown', onDocMouseDown); loadAll() })
onUnmounted(() => { document.removeEventListener('mousedown', onDocMouseDown); if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
/* 门 1 通过后的下一步入口 + 正文批次入口（M0） */
.gate-next { display: flex; align-items: center; gap: 10px; margin: 4px 0 12px; flex-wrap: wrap; }
.gate-next-hint { color: var(--el-text-color-secondary); font-size: 12px; }
.ch-gen-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.ch-gen-all { color: var(--el-text-color-secondary); }
.ch-gen-readonly { color: var(--el-color-warning); font-size: 12px; }
/* ===== 顶栏：书名 + 元数据 + 导出 ===== */
.pd-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--card-line); margin-bottom: 16px; }
.pd-title { font-size: 24px; font-weight: 600; letter-spacing: .01em; line-height: 1.2; }
.pd-meta { display: flex; align-items: center; gap: 10px; margin-top: 8px; font-size: 12px; color: var(--muted); flex-wrap: wrap; font-variant-numeric: tabular-nums; }
.pd-head-actions { display: flex; gap: 8px; align-items: center; flex-shrink: 0; }

/* ===== 生成中横幅：全宽，仅生成/出错时出现 ===== */
.gen-banner {
  margin-bottom: 16px;
  padding: 10px 16px;
  background: rgba(201,162,39,.06);
  border: 1px solid rgba(201,162,39,.25);
  border-radius: 8px;
}
.gen-banner.is-error { border-color: #fbc4c4; background: #fef7f5; }
.gen-banner-top { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.gen-banner-status { flex: 1; font-size: 13px; color: var(--ink-soft); }
.gen-banner-pct { font-size: 12px; color: var(--gold-deep); font-variant-numeric: tabular-nums; font-weight: 500; }
.pulse { width: 6px; height: 6px; border-radius: 50%; background: var(--gold); animation: pd-pulse 1.4s ease-in-out infinite; flex-shrink: 0; }
@keyframes pd-pulse {
  0%, 100% { opacity: .35; transform: scale(.85); }
  50% { opacity: 1; transform: scale(1.15); }
}
.gen-status { font-size: 13px; color: #909399; }
.gen-error { display: inline-block; font-size: 13px; color: #f56c6c; word-break: break-all; flex: 1; }
.gen-error-wrap { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

/* ===== 主体：左侧栏 + 工作区 ===== */
.pd-body { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 0 28px; align-items: start; }
.pd-main { min-width: 0; padding-top: 2px; }

/* ===== 章节列表：纯行 + 分隔线，无卡片背景 ===== */
.ch-list { display: flex; flex-direction: column; }
.ch-group { margin-bottom: 16px; }
.ch-group-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-soft);
  padding: 6px 0;
  margin-bottom: 4px;
  border-bottom: 1px solid var(--card-line);
}
.ch-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--card-line);
  cursor: pointer;
  transition: background-color .12s ease;
}
.ch-item:last-child { border-bottom: none; }
.ch-item:hover { background: rgba(201,162,39,.04); }
.ch-no { color: var(--muted); font-size: 12px; width: 60px; flex-shrink: 0; }
/* 标题单行省略；问题类型不再在行内展示，统一进版本弹窗 */
.ch-title-wrap { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ch-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
/* 质量徽标：等级 + 分数 + 条数，点击进入唯一的改稿建议详情面 */
.ch-quality {
  display: flex; align-items: center; gap: 5px; flex-shrink: 0;
  padding: 2px 4px; margin: -2px 0; border-radius: 4px; cursor: pointer;
  transition: background-color .12s ease;
}
.ch-quality:hover, .ch-quality:focus-visible { background: rgba(201,162,39,.08); outline: none; }
.ch-score { font-size: 12px; color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.ch-issue-count { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.ch-words { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; flex-shrink: 0; }
.ch-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; opacity: 0; pointer-events: none; transition: opacity .15s ease; }
.ch-item:hover .ch-actions, .ch-item:focus-within .ch-actions { opacity: 1; pointer-events: auto; }
/* 触屏设备无 hover，操作常驻 */
@media (hover: none) { .ch-actions { opacity: 1; pointer-events: auto; } }

/* ===== R12 章节质量总览条：最需要处理提示 + 排序/筛选 ===== */
.ch-quality-bar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-wrap: wrap; margin-bottom: 10px;
}
.ch-quality-summary { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.ch-worst-tag { max-width: 100%; }
.ch-worst-tag.clickable { cursor: pointer; }
.ch-quality-empty, .ch-quality-gap { font-size: 12px; color: var(--muted); }
.ch-quality-gap { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.ch-quality-controls { display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap; }
/* ===== 档1 上游过期提示（轻量条，不占额外版块） ===== */
.ch-stale-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 10px; padding: 6px 10px; border-radius: 6px;
  background: var(--el-color-warning-light-9, #fdf6ec);
  border: 1px solid var(--el-color-warning-light-7, #faecd8);
}
.ch-stale-text { font-size: 12px; color: var(--el-color-warning-dark-2, #b88230); }
.ch-stale-sub { font-size: 12px; color: var(--muted); }
.ch-stale-jumps { display: inline-flex; align-items: center; gap: 2px; flex-wrap: wrap; }

/* ===== 面板工具条（大纲页） ===== */
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.panel-toolbar-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.outline-empty-actions { display: flex; gap: 10px; justify-content: center; }

/* ===== 阅读器 / 浮动条 / 导入（原有样式保留） ===== */
.reader-content { line-height: 1.9; padding: 8px 12px; }
.reader-content.dark { background: #1a1a2e; color: #e0e0e0; padding: 16px; border-radius: 8px; }
.reader-header { display: flex; justify-content: space-between; align-items: center; }
.reader-tools { display: flex; gap: 8px; align-items: center; }
.editor-area :deep(.el-textarea__inner) { font-family: inherit; line-height: 1.8; font-size: 15px; }
.file-msg { font-size: 12px; line-height: 1.6; margin-top: 4px; }
.file-ok { color: #67c23a; }
.file-err { color: #f56c6c; }
.import-hint { font-size: 13px; color: #606266; line-height: 1.7; margin-bottom: 8px; }
.sel-float { position: fixed; z-index: 9999; display: flex; align-items: center; gap: 10px; background: #5a4a3a; color: #f5f1e2; padding: 6px 12px; border-radius: 8px; font-size: 13px; box-shadow: 0 2px 12px rgba(0,0,0,.3); user-select: none; }
.sel-float-item { cursor: pointer; }
.sel-float-item:hover { color: #e6c98a; }
.sel-float-sep { opacity: .4; }

/* 窄屏：侧栏降为顶部横向导航 */
@media (max-width: 900px) {
  .pd-body { grid-template-columns: minmax(0, 1fr); gap: 14px; }
  .pd-head { flex-direction: column; align-items: stretch; }
  .pd-head-actions { justify-content: flex-end; }
}

@media (prefers-reduced-motion: reduce) {
  .pulse { animation: none; }
  .ch-item, .ch-actions { transition: none; }
}
</style>
