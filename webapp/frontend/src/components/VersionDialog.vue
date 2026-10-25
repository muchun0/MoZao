<template>
  <el-dialog :model-value="modelValue" :title="title" width="560px" @update:model-value="$emit('update:modelValue', $event)">
    <div class="ver-list">
      <div v-for="v in versionList" :key="v.v" class="ver-item" :class="{ active: v.selected }">
        <div class="ver-main">
          <div class="ver-info">
            <span class="ver-no">v{{ v.v }}</span>
            <el-tag size="small" :type="v.selected ? 'success' : 'info'">{{ v.selected ? '当前' : sourceText(v.source) }}</el-tag>
            <el-tag v-if="v.quality && v.quality.score !== undefined && v.quality.score !== null"
                    size="small" :type="v.quality.passed ? 'success' : 'danger'">
              质检 {{ v.quality.score }}{{ v.quality.passed ? ' ✓' : ' ✗' }}
            </el-tag>
            <el-tag v-if="v.quality && v.quality.auto_revised" size="small" type="warning">自动改</el-tag>
            <span class="ver-words">{{ v.words }}字</span>
            <span v-if="v.charged !== undefined" class="ver-charge">扣费 ¥{{ Number(v.charged).toFixed(2) }}</span>
            <span class="ver-ts">{{ fmtVerTime(v.ts) }}</span>
          </div>
          <div v-if="v.quality && (v.quality.issues || []).length" class="ver-issues">
            <div class="ver-issues-head">改稿建议（{{ v.quality.issue_count || v.quality.issues.length }} 处）</div>
            <div v-for="(it, i) in v.quality.issues.slice(0, 8)" :key="i" class="ver-issue">
              <span :class="it.level === '大改' ? 'lv-big' : (it.level === '中改' ? 'lv-mid' : 'lv-small')">{{ it.level || '小改' }}</span>
              <span v-if="it.type" class="ver-issue-type">[{{ it.type }}]</span>
              <span>{{ it.detail }}</span>
              <el-button v-if="kind === 'chapter'" size="small" link type="warning" @click="$emit('appeal', it)">申诉</el-button>
            </div>
            <div v-if="(v.quality.issues || []).length > 8" class="ver-issue-more">… 其余 {{ v.quality.issues.length - 8 }} 条见版本质检记录</div>
          </div>
          <div v-else-if="kind === 'chapter'" class="ver-noq">该版本无质检问题记录</div>
        </div>
        <div class="ver-actions">
          <el-button size="small" link :type="v.selected ? '' : 'primary'" :disabled="v.selected"
                     @click="onSelect(v.v)">{{ v.selected ? '使用中' : '选择/回退' }}</el-button>
          <el-button v-if="kind === 'chapter' && !v.selected" size="small" link type="danger" @click="onDelete(v.v)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!versionList.length" :description="emptyText" />
    </div>
    <div class="ver-tip">{{ tipText }}</div>
  </el-dialog>
</template>

<script setup>
// 统一版本弹窗（TECH-Agent用户化与版本化 §10.2/§11.3，阶段 1C）：
// - chapter：章节版本（质检分/改稿建议/申诉/删除），行为与 ProjectDetail 原内联弹窗一致；
// - outline/world：产物版本列表（每版字数 + 结算金额），回滚前展示影响范围
//   （受影响章节 + 需重生成起始章 + 派生视图过期清单），确认后受控回滚。
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getChapterVersions, selectChapterVersion, deleteChapterVersion,
  getProductVersions, selectProductVersion, getRollbackImpact,
} from '../api/novel'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  pid: { type: String, required: true },
  kind: { type: String, default: 'chapter' },        // chapter | outline | world
  chapterNo: { type: Number, default: 0 },           // 仅 kind=chapter 使用
})
const emit = defineEmits(['update:modelValue', 'changed', 'appeal'])

const versionList = ref([])

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
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  margin-bottom: 8px;
}
.ver-item.active { border-color: var(--el-color-success); background: var(--el-color-success-light-9); }
.ver-main { flex: 1; min-width: 0; }
.ver-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ver-no { font-weight: 700; }
.ver-words { color: var(--el-text-color-regular); font-size: 12px; }
.ver-charge { color: var(--el-color-warning); font-size: 12px; }
.ver-ts { color: var(--el-text-color-secondary); font-size: 12px; }
.ver-issues { margin-top: 6px; }
.ver-issues-head { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.ver-issue { font-size: 12px; display: flex; gap: 6px; align-items: baseline; margin-bottom: 2px; }
.lv-big { color: var(--el-color-danger); }
.lv-mid { color: var(--el-color-warning); }
.lv-small { color: var(--el-text-color-secondary); }
.ver-issue-type { color: var(--el-text-color-secondary); }
.ver-issue-more { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; }
.ver-noq { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 6px; }
.ver-actions { display: flex; flex-direction: column; justify-content: center; gap: 4px; }
.ver-tip { margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; white-space: pre-line; }
</style>
