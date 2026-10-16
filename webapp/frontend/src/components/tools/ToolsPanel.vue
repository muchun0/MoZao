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
      <p class="muted">查看本章「计划→产出→校验」链条（只读）</p>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <el-input-number v-model="cotNo" :min="1" size="small" controls-position="right" style="width:90px" />
        <el-button size="small" type="primary" @click="runCot" :loading="cotLoading">查看</el-button>
      </div>
      <div v-if="cotView" class="result-area">
        <div><b>计划</b>：{{ cotView.plan.core_event || '（无）' }}</div>
        <div class="muted" style="margin:2px 0">拍序列：{{ cotView.plan.character_moves || '—' }}</div>
        <div style="margin-top:4px"><b>产出</b>：{{ (cotView.production.scenes||[]).length }} 个场景</div>
        <div class="muted" style="margin-top:4px"><b>校验</b>：
          beat覆盖 {{ cotView.review.beat_gate ? cotView.review.beat_gate.verdict : '—' }}
          <span v-if="cotView.review.quality">，质检 {{ cotView.review.quality.score }} 分</span>
        </div>
      </div>
    </el-card>

    <!-- 质量校验 -->
    <el-card class="tool-card">
      <template #header><b>质量校验</b></template>
      <p class="muted">AI检查设定一致性/剧情逻辑/合规</p>
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
      <p class="muted">全书质量汇总与终审；每章改稿建议见「章节 → 版本」弹窗</p>
      <el-button size="small" @click="loadReview">查看全书质检</el-button>
      <div class="result-area">
        <div v-if="!reviewLoaded" class="muted">点击按钮加载</div>
        <template v-else>
          <div v-if="!reviewSummary.chapter_count && !finalReview && !verifyIssues.length" class="ok-box">暂无质检记录</div>
          <div v-if="reviewSummary.chapter_count" class="summary-box">
            <b>全书质量汇总</b>
            <div class="summary-line">质检章节 {{ reviewSummary.chapter_count }} 章｜改稿问题 {{ reviewSummary.issue_total }} 处
              <span v-if="reviewSummary.score_avg">｜平均质检分 {{ reviewSummary.score_avg }}/100</span>
            </div>
            <div v-if="Object.keys(reviewSummary.issue_by_type||{}).length" class="summary-line">
              问题类型：<span v-for="(n, t) in reviewSummary.issue_by_type" :key="t" class="tag-todo">{{ t }}×{{ n }}</span>
            </div>
            <div v-if="reviewSummary.verify_issue_total" class="summary-line">
              一致性/审稿校验问题 {{ reviewSummary.verify_issue_total }} 条（涉及 {{ Object.keys(reviewSummary.verify_by_chapter||{}).length }} 章）
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
              <span class="review-pos">第{{ it.chapter }}章：</span>{{ it.problem }}<span v-if="it.suggestion" class="muted">（建议：{{ it.suggestion }}）</span>
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

    <!-- 伏笔管理 -->
    <el-card class="tool-card">
      <template #header><b>伏笔管理</b></template>
      <p class="muted">追踪已埋伏笔</p>
      <el-space>
        <el-input v-model="fsInput" placeholder="添加伏笔" style="flex:1" />
        <el-button size="small" @click="addFs">添加</el-button>
      </el-space>
      <div class="result-area">
        <div v-for="(f, i) in foreshadowing" :key="i" class="fs-row">
          <span>{{ f }}</span>
          <el-button size="small" link type="danger" @click="delFs(i)">删</el-button>
        </div>
      </div>
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

  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as toolsApi from '../../api/tools'
import { getProject, getStatus } from '../../api/project'
import { batchGenerate } from '../../api/novel'
import { ElMessage } from 'element-plus'

const props = defineProps({ pid: String })

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
async function runCot() {
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
  try {
    await batchGenerate(props.pid, { start: batchStart.value, end: batchEnd.value })
    ElMessage.success(`已开始批量生成第${batchStart.value}-${batchEnd.value}章`)
    batchRunning.value = true
    batchError.value = ''
    batchPct.value = 0
    batchStatus.value = '批量生成中…'
    stopBatchPolling()
    batchTimer = setInterval(pollBatch, 3000)
    pollBatch()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '批量生成失败') }
}

const fsInput = ref('')
const foreshadowing = ref([])
const lkInput = ref('')
const locked = ref([])

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
  try { const r = await toolsApi.listForeshadowing(props.pid); foreshadowing.value = r.foreshadowing || [] } catch {}
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
.verify-block { margin-top: 8px; }
.final-review-box { background: #f0f9eb; border: 1px solid #d4ecc9; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; font-size: 13px; }
.review-issue { font-size: 12px; color: #606266; padding: 2px 0; line-height: 1.5; }
.review-feeling { font-size: 12px; color: #e6a23c; padding: 2px 0 4px; font-style: italic; }
.lv-small { color: #909399; }
.lv-big { color: #f56c6c; font-weight: 700; }
.review-type { color: #409eff; }
.review-pos { color: #909399; }
.batch-err { padding: 8px 10px; background: #fef0f0; border-left: 3px solid #f56c6c; border-radius: 4px; font-size: 13px; }
</style>
