<template>
  <el-dialog :model-value="modelValue" :title="title" width="860px" @update:model-value="$emit('update:modelValue', $event)">
    <!-- 对比模式 -->
    <div v-if="diffView" class="diff-bar">
      <el-button link type="primary" @click="diffView = false">← 返回版本列表</el-button>
      <span class="diff-title">v{{ diffView.v1 }} vs v{{ diffView.v2 }}</span>
      <span v-if="diffView.truncated" class="diff-trunc">（差异较多，已折叠中段）</span>
    </div>

    <div v-if="!diffView" class="ver-list">
      <div v-for="v in versionList" :key="v.v" class="ver-item" :class="{ active: v.selected }">
        <!-- 第一层：版本号 + 状态徽章 + 生成时间 -->
        <div class="ver-head">
          <el-checkbox class="ver-check" :model-value="compareSel.includes(v.v)"
                       :disabled="compareSel.length >= 2 && !compareSel.includes(v.v)"
                       @change="toggleCompare(v.v)" />
          <span class="ver-no">v{{ v.v }}</span>
          <div class="ver-badges">
            <el-tag v-if="v.badge === 'recommended'" size="small" type="warning" effect="dark">推荐</el-tag>
            <el-tag v-else-if="v.badge === 'user_managed'" size="small" type="primary">你的选择</el-tag>
            <!-- blocked_compliance 理论不入版本库（quality 合规命中不落盘），后端兜底不推荐，UI 不展示 -->
            <el-tag v-if="v.selected" size="small" type="success">当前</el-tag>
            <el-tag v-else size="small" type="info">{{ sourceText(v.source) }}</el-tag>
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
        <!-- 改稿建议：默认折叠，避免长列表刷屏（窄屏尤为拥挤） -->
        <div v-if="v.quality && (v.quality.issues || []).length" class="ver-issues">
          <div class="ver-issues-head">改稿建议（{{ v.quality.issue_count || v.quality.issues.length }} 处）</div>
          <div v-for="(it, i) in visibleIssues(v)" :key="i" class="ver-issue">
            <span :class="it.level === '大改' ? 'lv-big' : (it.level === '中改' ? 'lv-mid' : 'lv-small')">{{ it.level || '小改' }}</span>
            <span v-if="it.type" class="ver-issue-type">[{{ it.type }}]</span>
            <span class="ver-issue-detail">{{ it.detail }}</span>
            <el-button v-if="kind === 'chapter'" size="small" link type="warning" @click="$emit('appeal', it)">申诉</el-button>
          </div>
          <el-button v-if="(v.quality.issues || []).length > 3" size="small" link type="primary"
                     class="ver-issue-toggle" @click="toggleIssues(v.v)">
            {{ expanded[v.v] ? '收起' : `展开其余 ${(v.quality.issues || []).length - 3} 条` }}
          </el-button>
        </div>
        <div v-else-if="kind === 'chapter'" class="ver-noq">该版本无质检问题记录</div>
        <!-- 第四层：操作（切换 / 采用 / 删除） -->
        <div class="ver-actions">
          <el-button v-if="v.badge === 'recommended' && !v.selected" size="small" link type="warning"
                     @click="onSelect(v.v)">采用建议</el-button>
          <el-button size="small" link :type="v.selected ? '' : 'primary'" :disabled="v.selected"
                     @click="onSelect(v.v)">{{ v.selected ? '使用中' : '选择/回退' }}</el-button>
          <el-button v-if="kind === 'chapter' && !v.selected" size="small" link type="danger" @click="onDelete(v.v)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!versionList.length" :description="emptyText" />
      <div v-if="compareSel.length === 2" class="compare-bar">
        <el-button type="primary" size="small" @click="openDiff">对比 v{{ compareSel[0]}} vs v{{ compareSel[1] }}</el-button>
      </div>
    </div>

    <!-- 章节正文并排 diff -->
    <div v-else-if="diffView && diffView.blocks" class="diff-text">
      <div v-for="(b, i) in diffView.blocks" :key="i" class="diff-block" :class="'diff-' + b.type">
        <template v-if="b.type === 'equal'"><p>{{ b.text }}</p></template>
        <template v-else-if="b.type === 'add'"><p class="row-add">＋ {{ b.b_text }}</p></template>
        <template v-else-if="b.type === 'del'"><p class="row-del">－ {{ b.a_text }}</p></template>
        <template v-else-if="b.type === 'mod'">
          <p class="row-del">－ {{ b.a_text }}</p>
          <p class="row-add">＋ {{ b.b_text }}</p>
        </template>
        <template v-else-if="b.type === 'collapse'"><div class="diff-collapse">… 其余 {{ b.count }} 处差异已折叠 …</div></template>
      </div>
    </div>

    <!-- outline/world 字段级 diff -->
    <div v-else-if="diffView && diffView.groups" class="diff-struct">
      <div v-for="g in diffView.groups" :key="g.group" class="diff-group">
        <div class="diff-group-head">{{ g.group }}</div>
        <div v-for="(c, i) in g.changes" :key="i" class="diff-change" :class="'dc-' + c.type">
          <b>{{ c.name }}</b>
          <template v-if="c.type === 'added'"><span class="dc-new">新增</span></template>
          <template v-else-if="c.type === 'removed'"><span class="dc-old">删除</span></template>
          <template v-else><span class="dc-mod">改写</span></template>
          <div v-if="c.old !== null && c.old !== undefined" class="dc-val">旧：{{ String(c.old) }}</div>
          <div v-if="c.new !== null && c.new !== undefined" class="dc-val">新：{{ String(c.new) }}</div>
        </div>
      </div>
    </div>

    <div class="ver-tip">{{ tipText }}</div>
  </el-dialog>
</template>

<script setup>
// 统一版本弹窗（TECH-Agent用户化与版本化 §10.2/§11.3，阶段 1C）：
// - chapter：章节版本（质检分/改稿建议/申诉/删除），行为与 ProjectDetail 原内联弹窗一致；
// - outline/world：产物版本列表（每版字数 + 结算金额），回滚前展示影响范围
//   （受影响章节 + 需重生成起始章 + 派生视图过期清单），确认后受控回滚。
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getChapterVersions, selectChapterVersion, deleteChapterVersion,
  getProductVersions, selectProductVersion, getRollbackImpact, getProductDiff,
} from '../api/novel'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  pid: { type: String, required: true },
  kind: { type: String, default: 'chapter' },        // chapter | outline | world
  chapterNo: { type: Number, default: 0 },           // 仅 kind=chapter 使用
})
const emit = defineEmits(['update:modelValue', 'changed', 'appeal'])

const versionList = ref([])
const compareSel = ref([])   // 勾选待对比的两个版本号
const diffView = ref(null)   // diff 接口响应
const expanded = reactive({})  // 各版本改稿建议的折叠态（按版本号 v 记录）

// 改稿建议默认只露前 3 条，展开后显示全部（避免面板拥挤）
function visibleIssues(v) {
  const list = (v.quality && v.quality.issues) || []
  return expanded[v.v] ? list : list.slice(0, 3)
}
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
  ? '每次重写/重生成都会追加新版本、保留旧版；首次生成默认展示质检评分最高的版本，手动选择/编辑后以您的操作为准。切换版本后如需与后续章节衔接，建议重新生成后续章节以同步记忆账本。重写/重生成按本次 AI 实际生成字数计费，候选版本同样计费；手动编辑不计费。'
  : '每次重生成都会追加新版本、保留旧版；回退只切换当前生效版本，不删除已生成正文。回退后基于更新版生成的章节可能与设定不一致，建议按提示重新生成受影响章节。重生成按本次 AI 实际生成字数计费。')

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
  diffView.value = null
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

async function openDiff() {
  const [v1, v2] = compareSel.value
  if (!v1 || !v2) return
  try {
    diffView.value = await getProductDiff(props.pid, props.kind, v1, v2, props.kind === 'chapter' ? props.chapterNo : undefined)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '对比失败')
  }
}

async function onSelect(v) {
  if (props.kind === 'chapter') {
    try {
      await ElMessageBox.confirm(`确定回退到第${props.chapterNo}章的 v${v} 版本？当前选中将切换为该版本。`, '切换版本', { type: 'warning' })
    } catch { return }
    try {
      const res = await selectChapterVersion(props.pid, props.chapterNo, v)
      ElMessage.success(`已切换到 v${v}（${res.words}字）`)
      versionList.value = (await getChapterVersions(props.pid, props.chapterNo)).versions || []
      emit('changed')
    } catch (e) {
      ElMessage.error(e?.message || '切换版本失败')
    }
    return
  }
  // 产物回滚：先取影响范围，确认后受控回滚（§11.3：只切 HEAD，不改写已生成正文）
  let impact
  try {
    impact = await getRollbackImpact(props.pid, props.kind, v)
  } catch {
    ElMessage.error('获取回滚影响范围失败')
    return
  }
  const label = KIND_LABELS[props.kind] || props.kind
  const lines = [`确定回退${label}到 v${v} 版本？当前生效版本将切换为该版本，已生成正文不会被改动。`]
  if (impact.affected_chapters?.length) {
    lines.push(`有 ${impact.affected_chapters.length} 章基于更新版${label}生成（第 ${impact.affected_chapters.join('、')} 章），回退后建议从第 ${impact.regenerate_from} 章起重新生成以保持一致。`)
  } else {
    lines.push('未发现与更新版强绑定的章节；存量章节可能缺少版本依赖记录，建议回退后抽查前文章节一致性。')
  }
  if (impact.stale_views?.length) {
    lines.push(`派生视图（记忆账本等）已有 ${impact.stale_views.length} 项落后，重新生成时会自动同步。`)
  }
  try {
    await ElMessageBox.confirm(lines.join('\n'), `回退${label}`, { type: 'warning', confirmButtonText: '确认回退', cancelButtonText: '取消' })
  } catch { return }
  try {
    const res = await selectProductVersion(props.pid, props.kind, v)
    ElMessage.success(`已回退到 v${res.selected}`)
    versionList.value = res.versions || []
    emit('changed')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '回退失败')
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
.ver-issue { font-size: 12px; display: flex; gap: 6px; align-items: baseline; margin-bottom: 3px; }
.ver-issue-detail { flex: 1; min-width: 0; }
.lv-big { color: var(--danger); flex-shrink: 0; }
.lv-mid { color: var(--gold-deep); flex-shrink: 0; }
.lv-small { color: var(--muted); flex-shrink: 0; }
.ver-issue-type { color: var(--muted); flex-shrink: 0; }
.ver-issue-toggle { padding-left: 0; margin-top: 2px; }
.ver-noq { font-size: 12px; color: var(--muted); margin-top: 8px; }
/* 第四层：操作 */
.ver-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--card-line); }
.ver-tip { margin-top: 8px; font-size: 12px; color: var(--muted); line-height: 1.6; white-space: pre-line; }
.ver-reasons { margin-top: 6px; font-size: 12px; color: var(--gold-deep); }
.ver-reason { line-height: 1.6; }
.ver-fatal { margin-top: 6px; font-size: 12px; color: var(--muted); }
.compare-bar { margin-top: 8px; text-align: center; }

/* 窄屏：徽章与时间换行、操作铺满，避免横向挤压 */
@media (max-width: 700px) {
  .ver-head { flex-wrap: wrap; }
  .ver-ts { margin-left: 0; flex-basis: 100%; }
  .ver-meta { gap: 6px 10px; }
  .ver-actions { gap: 8px; }
}
.diff-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.diff-title { font-weight: 700; }
.diff-trunc { font-size: 12px; color: var(--el-text-color-secondary); }
.diff-text { max-height: 60vh; overflow-y: auto; font-size: 13px; line-height: 1.7; }
.diff-block p { margin: 2px 0; padding: 2px 6px; border-radius: 3px; }
.row-add { background: var(--el-color-success-light-9); color: var(--el-color-success-dark-2); }
.row-del { background: var(--el-color-danger-light-9); color: var(--el-color-danger-dark-2); text-decoration: line-through; }
.diff-collapse { text-align: center; color: var(--el-text-color-secondary); font-size: 12px; padding: 6px; }
.diff-struct { max-height: 60vh; overflow-y: auto; }
.diff-group { margin-bottom: 10px; }
.diff-group-head { font-weight: 700; border-bottom: 1px solid var(--el-border-color); padding-bottom: 2px; margin-bottom: 4px; }
.diff-change { font-size: 13px; padding: 4px 6px; margin-bottom: 3px; border-radius: 3px; }
.dc-added { background: var(--el-color-success-light-9); }
.dc-removed { background: var(--el-color-danger-light-9); }
.dc-modified { background: var(--el-color-warning-light-9); }
.dc-new { color: var(--el-color-success); margin-left: 6px; font-size: 12px; }
.dc-old { color: var(--el-color-danger); margin-left: 6px; font-size: 12px; }
.dc-mod { color: var(--el-color-warning); margin-left: 6px; font-size: 12px; }
.dc-val { font-size: 12px; color: var(--el-text-color-regular); margin-top: 2px; word-break: break-all; }
</style>
