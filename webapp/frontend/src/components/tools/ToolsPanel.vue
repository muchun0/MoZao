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

    <!-- 改稿建议 -->
    <el-card class="tool-card">
      <template #header><b>改稿建议</b></template>
      <p class="muted">自动改稿记录与待人工处理的中改问题（含全书质量汇总）</p>
      <el-button size="small" @click="loadReview">查看改稿建议</el-button>
      <div class="result-area">
        <div v-if="!reviewLoaded" class="muted">点击按钮加载</div>
        <template v-else>
          <div v-if="!reviewItems.length && !reviewSummary.chapter_count" class="ok-box">暂无改稿记录</div>
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
          <div v-for="r in reviewItems" :key="r.chapter" class="review-chapter">
            <div class="review-head">
              <b>第{{ r.chapter }}章</b>
              <span v-if="r.auto_revised" class="tag-auto">已自动改</span>
              <span v-else-if="(r.issues||[]).length" class="tag-todo">待人工处理 {{ r.issues.length }} 处</span>
              <span v-else class="tag-ok">无问题</span>
              <span v-if="r.quality_check && r.quality_check.score !== undefined && r.quality_check.score !== null" class="tag-score">质检 {{ r.quality_check.score }}</span>
              <span v-if="r.reader_hook_score" class="tag-auto" style="margin-left:auto">追更欲 {{ r.reader_hook_score }}/5</span>
            </div>
            <div v-if="r.reader_feeling" class="review-feeling">读者感受：{{ r.reader_feeling }}</div>
            <div v-for="(it, i) in (r.issues||[])" :key="i" class="review-issue">
              <span :class="it.level === '小改' ? 'lv-small' : 'lv-big'">{{ it.level || '小改' }}</span>
              <span v-if="it.type" class="review-type">[{{ it.type }}]</span>
              <span v-if="it.pos" class="review-pos">{{ it.pos }}：</span>
              <span>{{ it.detail }}</span>
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
        <el-button size="small" type="primary" @click="doBatch">开始</el-button>
      </el-space>
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
import { ref, onMounted } from 'vue'
import * as toolsApi from '../../api/tools'
import { getProject } from '../../api/project'
import { batchGenerate } from '../../api/novel'
import { ElMessage } from 'element-plus'

const props = defineProps({ pid: String })

const verifyResult = ref([])
const reviewItems = ref([])
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
const rhythmData = ref([])
const maxRhythm = ref(1)
const altGoal = ref('')
const altLoading = ref(false)
const alts = ref([])
const batchStart = ref(1)
const batchEnd = ref(1)

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
    reviewItems.value = r.items || []
    reviewSummary.value = r.summary || {}
    verifyIssues.value = r.verify_issues || []
    finalReview.value = r.final_review || null
    reviewLoaded.value = true
  } catch (e) { ElMessage.error('改稿记录加载失败') }
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

async function doBatch() {
  try {
    await batchGenerate(props.pid, { start: batchStart.value, end: batchEnd.value })
    ElMessage.success(`已开始批量生成第${batchStart.value}-${batchEnd.value}章`)
  } catch (e) { ElMessage.error('批量生成失败') }
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
</style>
