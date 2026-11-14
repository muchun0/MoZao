<template>
  <el-dialog :model-value="modelValue" :title="title" width="860px" @update:model-value="$emit('update:modelValue', $event)">
    <!-- 评审模式（两版对比结果：审校 + 评审两视角评论） -->
    <div v-if="reviewView" class="rv-bar">
      <el-button link type="primary" @click="reviewView = null">← 返回版本列表</el-button>
      <span class="rv-title">v{{ reviewView.v1 }} vs v{{ reviewView.v2 }}</span>
    </div>

    <div v-if="!reviewView" class="ver-list">
      <div v-for="v in versionList" :key="v.v" class="ver-item" :class="{ active: v.selected }">
        <!-- 第一层：版本号 + 状态徽章 + 生成时间 -->
        <div class="ver-head">
          <el-checkbox v-if="canCompare" class="ver-check" :model-value="compareSel.includes(v.v)"
                       :disabled="compareSel.length >= 2 && !compareSel.includes(v.v)"
                       @change="toggleCompare(v.v)" />
          <span class="ver-no">v{{ v.v }}</span>
          <div class="ver-badges">
            <el-tag v-if="v.badge === 'recommended'" size="small" type="warning" effect="dark">推荐</el-tag>
            <el-tag v-else-if="v.badge === 'user_managed'" size="small" type="primary">你的选择</el-tag>
            <!-- blocked_compliance 理论不入版本库（quality 合规命中不落盘），后端兜底不推荐，UI 不展示 -->
            <el-tag v-if="v.selected" size="small" type="success">当前</el-tag>
            <el-tag v-else size="small" type="info">{{ sourceText(v.source) }}</el-tag>
            <!-- REQ-R23 P1-1：快照/导入前备份并入版本库后，用标签说明这一版是什么 -->
            <el-tag v-if="v.label" size="small" type="warning" effect="plain">{{ v.label }}</el-tag>
          </div>
          <span v-if="v.ts" class="ver-ts">{{ fmtVerTime(v.ts) }}</span>
        </div>
        <!-- 第二层：元信息（字数 / 扣费 / 质检 / 追更 / 自动改 / 来源），弱化分隔，避免一行挤爆 -->
        <div class="ver-meta">
          <span class="ver-words">{{ v.words }}字</span>
          <span v-if="v.charged !== undefined" class="ver-charge">扣费 ¥{{ Number(v.charged).toFixed(2) }}</span>
          <el-tag v-if="v.quality && v.quality.score !== undefined && v.quality.score !== null"
                  size="small" :type="v.quality.passed ? 'success' : 'danger'">
            质检 {{ v.quality.score }}{{ v.quality.passed ? ' ✓' : ' ✗' }}
          </el-tag>
          <el-tag v-if="v.quality && v.quality.hook_score !== undefined && v.quality.hook_score !== null"
                  size="small" type="success">追更 {{ v.quality.hook_score }}/5</el-tag>
          <el-tag v-if="v.quality && v.quality.auto_revised" size="small" type="warning">自动改</el-tag>
        </div>
        <!-- 推荐理由 -->
        <div v-if="v.badge === 'recommended' && v.reasons && v.reasons.length" class="ver-reasons">
          <div v-for="(r, i) in v.reasons" :key="i" class="ver-reason">✓ {{ r }}</div>
        </div>
        <div v-if="v.fatal_issue" class="ver-fatal">⚠ {{ v.fatal_issue }}</div>
        <!-- 改稿建议：本弹窗是唯一的 issue 详情与操作面；标题与列表行同口径（分桶 + 总数） -->
        <div v-if="hasIssueDetail(v)" class="ver-issues">
          <div class="ver-issues-head">改稿建议（{{ issueText(v.quality) }}）</div>
          <div v-if="!(v.quality.issues || []).length" class="ver-issues-empty">
            本版只记录了问题数，逐条明细未留存；可重新质检生成明细。
          </div>
          <div v-for="(it, i) in visibleIssues(v)" :key="i" class="ver-issue-card">
            <div class="ver-issue">
              <span :class="it.level === '大改' ? 'lv-big' : (it.level === '中改' ? 'lv-mid' : 'lv-small')">{{ it.level || '小改' }}</span>
              <span v-if="it.type" class="ver-issue-type">[{{ it.type }}]</span>
              <span class="ver-issue-detail">{{ it.detail }}</span>
              <el-button v-if="kind === 'chapter'" size="small" link type="warning" @click="$emit('appeal', it)">申诉</el-button>
              <el-button v-if="kind === 'chapter'" size="small" link type="primary"
                         :loading="rewritingKey === `${v.v}-${i}`" :disabled="!!rewritingKey"
                         title="只改写这条建议命中的段落，其余正文保持不变"
                         @click="onRewriteIssue(it, v, i)">按此改</el-button>
            </div>
            <!-- R1：凭什么这么说（原文证据）与具体怎么改（改法说明），缺失时明确留白 -->
            <div class="ver-issue-why">
              <div class="why-line">
                <span class="why-label">证据</span>
                <span class="why-text" :class="{ 'why-empty': evidenceInfo(it).empty }">{{ evidenceInfo(it).text }}</span>
              </div>
              <div class="why-line">
                <span class="why-label">改法</span>
                <span class="why-text" :class="{ 'why-empty': fixInfo(it).empty }">{{ fixInfo(it).text }}</span>
              </div>
            </div>
          </div>
          <el-button v-if="(v.quality.issues || []).length > 3" size="small" link type="primary"
                     class="ver-issue-toggle" @click="toggleIssues(v.v)">
            {{ expanded[v.v] ? '收起' : `展开其余 ${(v.quality.issues || []).length - 3} 条` }}
          </el-button>
        </div>
        <div v-else-if="kind === 'chapter'" class="ver-noq">
          <span>{{ noIssueText(v) }}</span>
          <el-button v-if="needsRecheck(v)" size="small" link type="primary" class="ver-recheck"
                     :loading="recheckingKey === v.v" :disabled="!!recheckingKey"
                     :title="recheckConfirmText(v.v)"
                     @click="onRecheck(v)">重新质检</el-button>
        </div>
        <!-- 第四层：操作（切换 / 采用 / 删除） -->
        <div class="ver-actions">
          <el-button v-if="v.badge === 'recommended' && !v.selected" size="small" link type="warning"
                     title="采用系统推荐版本：把它设为当前生效版本"
                     @click="onSelect(v.v)">采用推荐版本</el-button>
          <el-button size="small" link :type="v.selected ? '' : 'primary'" :disabled="v.selected"
                     title="采用此版本：把它设为当前生效版本（旧版、新版都可以，不删除其它版本）"
                     @click="onSelect(v.v)">{{ v.selected ? '使用中' : '采用此版本' }}</el-button>
          <el-button v-if="kind === 'chapter' && !v.selected" size="small" link type="danger" @click="onDelete(v.v)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!versionList.length" :description="emptyText" />
      <!-- 版本堆到一定数量后不再提供逐对对比（理由见 MAX_COMPARE_VERSIONS 注释） -->
      <div v-if="!canCompare && versionList.length" class="compare-off">
        版本较多（{{ versionList.length }} 个），逐对对比的组合过多、看不过来；
        请直接看各版本的徽章、推荐理由与改稿建议，或先删掉不再需要的版本。
      </div>
      <div v-if="canCompare && compareSel.length === 2" class="compare-bar">
        <el-button type="primary" size="small" @click="openReview">
          对比 v{{ compareSel[0]}} vs v{{ compareSel[1] }}</el-button>
      </div>
    </div>

    <VersionReview v-else :review="reviewView" />

    <div class="ver-tip">{{ tipText }}</div>
  </el-dialog>
</template>

<script setup>
// 统一版本弹窗（TECH-Agent用户化与版本化 §10.2/§11.3，阶段 1C）：
// - chapter：章节版本（质检分/改稿建议/申诉/删除），行为与 ProjectDetail 原内联弹窗一致；
// - outline/world：产物版本列表（每版字数 + 结算金额），切换前展示影响范围
//   （受影响章节 + 需重生成起始章 + 派生视图过期清单），确认后切换当前生效版本。
// 版本操作只保留「选择」一种语义：选择即切换当前生效版本（旧版与新版一视同仁），
// 不再提供单独的「回退」入口，避免"选择"与"回退"两套说法造成的语义歧义。
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import VersionReview from './VersionReview.vue'
import {
  getChapterVersions, selectChapterVersion, deleteChapterVersion,
  getProductVersions, selectProductVersion, getVersionSelectImpact, getVersionReview,
  rewriteChapterIssue, recheckChapterQuality,
} from '../api/novel'
import { evidenceInfo, fixInfo } from './issueText'
import { needsRecheck, noIssueText, recheckConfirmText, recheckResultText } from './versionQuality'
import { issueSummaryText } from '../utils/chapterQuality'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  pid: { type: String, required: true },
  kind: { type: String, default: 'chapter' },        // chapter | outline | world
  chapterNo: { type: Number, default: 0 },           // 仅 kind=chapter 使用
})
const emit = defineEmits(['update:modelValue', 'changed', 'appeal'])

// 候选版本数上限：超过则不提供逐对对比入口（也不显示勾选框）。
// 理由：对比是两两进行的，n 个候选版本有 n(n-1)/2 种组合 —— 5 个就有 10 种、
// 6 个 15 种，堆到这个量级后用户根本看不过来，此时版本卡片上的徽章/推荐理由/
// 改稿建议才是有效的决策信息（详见 docs/decisions.md 2026-10-30 条目）。
// 取值 5：保留" ≤5 个版本时可逐对对比"这一最常用的场景，不做成永远不显示。
const MAX_COMPARE_VERSIONS = 5

const versionList = ref([])
const compareSel = ref([])   // 勾选待对比的两个版本号
const reviewView = ref(null) // 评审接口响应（审校/评审两视角）
const expanded = reactive({})  // 各版本改稿建议的折叠态（按版本号 v 记录）
const rewritingKey = ref('')   // 正在「按此改」的问题键（`${v}-${index}`），防重复提交
const recheckingKey = ref(0)   // 正在重新质检的版本号（0=空闲），防重复提交
// 版本数超阈值时不提供逐对对比入口
const canCompare = computed(() => versionList.value.length <= MAX_COMPARE_VERSIONS)

// 改稿建议默认只露前 3 条，展开后显示全部（避免面板拥挤）
function visibleIssues(v) {
  const list = (v.quality && v.quality.issues) || []
  return expanded[v.v] ? list : list.slice(0, 3)
}
// 详情面唯一计数口径：与列表行「大改/中改/小改 + 条数」一致
const issueText = (q) => issueSummaryText(q && q.issues, q && q.issue_count)
const hasIssueDetail = (v) => !!issueText(v && v.quality)
function toggleIssues(no) {
  expanded[no] = !expanded[no]
}

const KIND_LABELS = { chapter: '章节', outline: '大纲', world: '世界观' }
const title = computed(() => props.kind === 'chapter'
  ? `章节版本 · 第${props.chapterNo}章`
  : `${KIND_LABELS[props.kind] || props.kind}版本`)
const emptyText = computed(() => props.kind === 'chapter'
  ? '暂无历史版本（重写/重生成会自动保留多版本）'
  : '暂无历史版本（重生成会自动保留旧版）')
const tipText = computed(() => props.kind === 'chapter'
  ? '每次重写/重生成都会追加新版本、保留旧版；首次生成默认展示质检评分最高的版本，手动选择/编辑后以您的操作为准。勾选两个版本可看【审校 + 评审】两视角评论（引用原文说明好在哪、问题在哪、具体怎么改）；版本较多时不再提供逐对对比，请直接看各版本的徽章与改稿建议。每条改稿建议都给出「证据（引用原文）」与「改法」，可直接点「按此改」只改写命中段落并新增一个版本（旧版保留，按新增版本字数计费；定位失败会明确报错，不会整章重写）。改写生成的版本可点「重新质检」补做质检：会调用模型、消耗模型 token，但不按字数计费；已有质检结果时不会重复调用。切换版本后如需与后续章节衔接，建议重新生成后续章节以同步记忆账本。重写/重生成按本次 AI 实际生成字数计费，候选版本同样计费；手动编辑不计费。'
  : '每次重生成都会追加新版本、保留旧版；带「快照」「导入前备份」标签的版本是历史保存点，同样可「采用此版本」还原。选择某个版本＝把它设为当前生效版本，不会删除已生成正文。勾选两个版本可看【审校 + 评审】两视角评论（本版多了什么、缺了什么、要不要从另一版补回）。若选择的版本比当前旧，基于更新版生成的章节可能与设定不一致，建议按提示重新生成受影响章节。重生成按本次 AI 实际生成字数计费。')

function sourceText(src) {
  return { ai: 'AI生成', user: '人工编辑', rewrite: '重写', regen: '重生成', legacy: '初始版' }[src] || src || '未知'
}
function fmtVerTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

async function load() {
  versionList.value = []
  compareSel.value = []
  reviewView.value = null
  try {
    if (props.kind === 'chapter') {
      const res = await getChapterVersions(props.pid, props.chapterNo)
      versionList.value = res.versions || []
    } else {
      const res = await getProductVersions(props.pid, props.kind)
      versionList.value = res.versions || []
    }
  } catch (e) {
    ElMessage.error(`加载${KIND_LABELS[props.kind] || ''}版本失败`)
    emit('update:modelValue', false)
  }
}

function toggleCompare(v) {
  const i = compareSel.value.indexOf(v)
  if (i >= 0) compareSel.value.splice(i, 1)
  else if (compareSel.value.length < 2) compareSel.value.push(v)
}

async function openReview() {
  const [v1, v2] = compareSel.value
  if (!v1 || !v2) return
  try {
    reviewView.value = await getVersionReview(
      props.pid, props.kind, v1, v2, props.kind === 'chapter' ? props.chapterNo : undefined)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '评审失败')
  }
}

async function onSelect(v) {
  if (props.kind === 'chapter') {
    try {
      await ElMessageBox.confirm(
        `确定选择第${props.chapterNo}章的 v${v} 版本作为当前生效版本？`, '选择版本', { type: 'warning' })
    } catch { return }
    try {
      const res = await selectChapterVersion(props.pid, props.chapterNo, v)
      ElMessage.success(`已选择 v${v}（${res.words}字）`)
      versionList.value = (await getChapterVersions(props.pid, props.chapterNo)).versions || []
      emit('changed')
    } catch (e) {
      ElMessage.error(e?.message || '选择版本失败')
    }
    return
  }
  // 产物版本：先取影响范围（哪些章基于更新版生成），确认后切换当前生效版本
  // （§11.3：只切 HEAD，不改写已生成正文）
  let impact
  try {
    impact = await getVersionSelectImpact(props.pid, props.kind, v)
  } catch {
    ElMessage.error('获取版本切换影响范围失败')
    return
  }
  const label = KIND_LABELS[props.kind] || props.kind
  const lines = [`确定选择${label}的 v${v} 版本作为当前生效版本？已生成正文不会被改动。`]
  if (impact.affected_chapters?.length) {
    lines.push(`有 ${impact.affected_chapters.length} 章基于更新的${label}版本生成（第 ${impact.affected_chapters.join('、')} 章），切换后建议从第 ${impact.regenerate_from} 章起重新生成以保持一致。`)
  } else {
    lines.push('未发现与更新版本强绑定的章节；存量章节可能缺少版本依赖记录，建议切换后抽查前文章节一致性。')
  }
  if (impact.stale_views?.length) {
    lines.push(`派生视图（记忆账本等）已有 ${impact.stale_views.length} 项落后，重新生成时会自动同步。`)
  }
  try {
    await ElMessageBox.confirm(lines.join('\n'), `选择${label}版本`,
      { type: 'warning', confirmButtonText: '确认选择', cancelButtonText: '取消' })
  } catch { return }
  try {
    const res = await selectProductVersion(props.pid, props.kind, v)
    ElMessage.success(`已选择 v${res.selected}`)
    versionList.value = res.versions || []
    emit('changed')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '选择版本失败')
  }
}

async function onDelete(v) {
  try {
    await ElMessageBox.confirm(`确定删除第${props.chapterNo}章的 v${v} 版本？删除后不可恢复。`, '删除版本', { type: 'warning' })
  } catch { return }
  try {
    const res = await deleteChapterVersion(props.pid, props.chapterNo, v)
    versionList.value = res.versions || []
    ElMessage.success(`已删除 v${v}`)
  } catch (e) {
    ElMessage.error(e?.message || '删除版本失败')
  }
}

// R1 按此改：只改命中段落 → 后端新增版本并设为当前，旧版保留；按新版本字数计费
async function onRewriteIssue(it, v, index) {
  if (rewritingKey.value) return
  try {
    await ElMessageBox.confirm(
      '将只改写这条建议命中的段落，其余正文保持不变；系统会新增一个版本并设为当前（旧版本全部保留）。'
      + '改写按新版本字数计费（自备 API Key 的项目不扣平台费），是否继续？',
      `按此改 · v${v.v}`, { type: 'warning', confirmButtonText: '开始改写', cancelButtonText: '取消' })
  } catch { return }
  rewritingKey.value = `${v.v}-${index}`
  try {
    const res = await rewriteChapterIssue(props.pid, props.chapterNo, {
      type: it.type || '',
      detail: it.detail || '',
      evidence: it.evidence || '',
      fix_suggestion: it.fix_suggestion || '',
      level: it.level || '',
      version: v.v,
    })
    if (res.versions) versionList.value = res.versions
    const cost = Number(res.due || 0) > 0 ? `，扣费 ¥${Number(res.due).toFixed(2)}` : ''
    ElMessage.success(`已按建议局部改写：新增 v${res.version}（改动 ${res.rewritten_chars} 字${cost}）`)
    emit('changed')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '局部改写失败')
  } finally {
    rewritingKey.value = ''
  }
}

// R14 单章重检：改写版本没有质检结果 → 手动补检；不改写正文、不新增版本
async function onRecheck(v) {
  if (recheckingKey.value) return
  try {
    await ElMessageBox.confirm(recheckConfirmText(v.v), `重新质检 · v${v.v}`,
      { type: 'info', confirmButtonText: '开始质检', cancelButtonText: '取消' })
  } catch { return }
  recheckingKey.value = v.v
  try {
    const res = await recheckChapterQuality(props.pid, props.chapterNo, { version: v.v })
    if (res.versions) versionList.value = res.versions
    ElMessage.success(recheckResultText(res))
    emit('changed')
  } catch (e) {
    // 失败不写「通过」，按钮保持可点，用户可直接重试
    ElMessage.error(`质检失败，可重试：${e?.response?.data?.detail || e?.message || '未知错误'}`)
  } finally {
    recheckingKey.value = 0
  }
}

watch(() => [props.modelValue, props.chapterNo, props.kind], ([vis]) => {
  if (vis) load()
})
</script>

<style scoped>
.ver-list { max-height: 55vh; overflow-y: auto; }
.ver-item {
  padding: 12px 14px;
  border: 1px solid var(--card-line);
  border-radius: 8px;
  margin-bottom: 10px;
  background: var(--card);
}
.ver-item.active { border-color: var(--ok); background: rgba(60, 122, 78, .06); }
/* 第一层：版本号 + 徽章 + 时间 */
.ver-head { display: flex; align-items: center; gap: 10px; }
.ver-check { margin-right: 2px; }
.ver-no { font-size: 16px; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }
.ver-badges { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ver-ts { margin-left: auto; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
/* 第二层：元信息 */
.ver-meta { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; margin-top: 8px; }
.ver-words { color: var(--ink-soft); font-size: 12px; font-variant-numeric: tabular-nums; }
.ver-charge { color: var(--gold-deep); font-size: 12px; font-variant-numeric: tabular-nums; }
.ver-issues { margin-top: 8px; }
.ver-issues-head { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.ver-issues-empty { font-size: 12px; color: var(--muted); line-height: 1.6; margin-bottom: 6px; }
.ver-issue-card { margin-bottom: 8px; }
.ver-issue { font-size: 12px; display: flex; gap: 6px; align-items: baseline; margin-bottom: 4px; }
.ver-issue-detail { flex: 1; min-width: 0; }
.lv-big { color: var(--danger); flex-shrink: 0; }
.lv-mid { color: var(--gold-deep); flex-shrink: 0; }
.lv-small { color: var(--muted); flex-shrink: 0; }
.ver-issue-type { color: var(--muted); flex-shrink: 0; }
/* R1：证据与改法双行说明，弱化底色以区别于问题标题行 */
.ver-issue-why {
  padding: 6px 8px;
  border-left: 2px solid var(--card-line);
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.032);
}
.why-line { display: flex; gap: 8px; font-size: 12px; line-height: 1.7; }
.why-line + .why-line { margin-top: 2px; }
.why-label { flex-shrink: 0; color: var(--gold-deep); }
.why-text { min-width: 0; color: var(--ink-soft); word-break: break-word; }
.why-empty { color: var(--muted); }
.ver-issue-toggle { padding-left: 0; margin-top: 2px; }
.ver-noq { font-size: 12px; color: var(--muted); margin-top: 8px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
/* 第四层：操作 */
.ver-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--card-line); }
.ver-tip { margin-top: 8px; font-size: 12px; color: var(--muted); line-height: 1.6; white-space: pre-line; }
.ver-reasons { margin-top: 6px; font-size: 12px; color: var(--gold-deep); }
.ver-reason { line-height: 1.6; }
.ver-fatal { margin-top: 6px; font-size: 12px; color: var(--muted); }
.compare-bar { margin-top: 8px; text-align: center; }
.compare-off {
  margin-top: 8px; font-size: 12px; color: var(--muted); line-height: 1.6;
  padding: 6px 8px; border: 1px dashed var(--card-line); border-radius: 6px;
}
.rv-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.rv-title { font-weight: 700; }

/* 窄屏：徽章与时间换行、操作铺满，避免横向挤压 */
@media (max-width: 700px) {
  .ver-head { flex-wrap: wrap; }
  .ver-ts { margin-left: 0; flex-basis: 100%; }
  .ver-meta { gap: 6px 10px; }
  .ver-actions { gap: 8px; }
}
</style>
