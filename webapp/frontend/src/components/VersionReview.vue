<template>
  <!-- 版本对比结果：审校 agent + 评审 agent 的评论视角（不再是 git 式增删） -->
  <div class="rv">
    <!-- 顶部：只摆差异事实，不做行级增删展示 -->
    <div v-if="summary" class="rv-summary">
      <span v-if="isChapter">
        两版共 {{ summary.changed_blocks }} 处不同：改写 {{ summary.rewritten_paragraphs }} 段、
        新增 {{ summary.added_paragraphs }} 段、删除 {{ summary.deleted_paragraphs }} 段
      </span>
      <span v-else>
        两版共 {{ summary.changed_blocks }} 处字段差异：新增 {{ summary.added_paragraphs }} 项、
        改写 {{ summary.rewritten_paragraphs }} 项、删除 {{ summary.deleted_paragraphs }} 项
      </span>
      <span v-if="summary.truncated" class="rv-trunc">（差异较多，概览仅统计不逐条展开）</span>
    </div>

    <div v-for="p in panels" :key="p.v" class="rv-panel">
      <div class="rv-head">
        <span class="rv-ver">v{{ p.v }}</span>
        <span class="rv-meta">
          {{ p.words }}字<template v-if="p.score !== null && p.score !== undefined">
            ｜质检 {{ p.score }} 分{{ p.passed ? ' ✓' : ' ✗' }}</template>
        </span>
      </div>

      <div v-for="r in p.reviews" :key="p.v + '-' + r.agent" class="rv-agent">
        <div class="rv-agent-head">
          <el-tag size="small" :type="r.agent === 'proofread' ? 'success' : 'warning'" effect="dark">
            {{ r.agent_label }}
          </el-tag>
          <span class="rv-focus">侧重：{{ r.focus }}</span>
        </div>

        <!-- 写得好的地方：引用原文 + 好在哪 -->
        <div class="rv-sec">
          <div class="rv-sec-head ok">写得好的地方<template v-if="r.strengths.length">
            （{{ r.strengths.length }}）</template></div>
          <div v-for="(s, i) in r.strengths" :key="'s' + i" class="rv-item">
            <div class="rv-quote">「{{ s.quote }}」</div>
            <div class="rv-why"><span class="rv-aspect">{{ s.aspect }}</span>{{ s.reason }}</div>
          </div>
          <div v-if="!r.strengths.length" class="rv-empty">{{ r.empty_hint }}</div>
        </div>

        <!-- 需要改进的地方：引用原文 + 问题定性 + 具体改法 -->
        <div class="rv-sec">
          <div class="rv-sec-head bad">需要改进的地方<template v-if="r.improvements.length">
            （{{ r.improvements.length }}）</template></div>
          <div v-for="(it, i) in r.improvements" :key="'i' + i" class="rv-item">
            <div v-if="it.quote" class="rv-quote">「{{ it.quote }}」</div>
            <div v-else class="rv-nolocate">（本版未记录该问题的正文位置，只能给出问题与改法）</div>
            <div class="rv-problem">
              <span :class="levelClass(it.level)">{{ it.level || '问题' }}</span>
              <span v-if="it.type" class="rv-type">[{{ it.type }}]</span>{{ it.problem }}
            </div>
            <div v-if="it.suggestion" class="rv-fix">
              <b>改法：</b>{{ it.suggestion }}<span v-if="it.expected_gain">
                （预计提升 {{ it.expected_gain }} 分）</span>
            </div>
          </div>
          <div v-if="!r.improvements.length" class="rv-empty">本视角未发现该版的问题条目</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
export default { name: 'VersionReview' }
</script>

<script setup>
import { computed } from 'vue'
// 版本对比结果渲染（REQ-版本对比与推荐）。
// 只负责展示后端 version_review 的产出：审校视角（文字/表达/一致性/冗余）
// 与评审视角（结构/剧情/人物/节奏）各自的「写得好的地方」「需要改进的地方」。
// 不做任何本地推断与二次计算 —— 口径单一权威源在后端（AGENTS.md §11.2/§11.3）。
const props = defineProps({
  review: { type: Object, default: () => ({}) },
})

const panels = computed(() => props.review.panels || [])
const summary = computed(() => props.review.summary || null)
const isChapter = computed(() => props.review.kind === 'chapter')

function levelClass(level) {
  return level === '大改' ? 'lv-big' : (level === '中改' ? 'lv-mid' : 'lv-small')
}
</script>

<style scoped>
.rv { max-height: 60vh; overflow-y: auto; padding-right: 4px; }
.rv-summary { font-size: 12px; color: var(--muted); margin-bottom: 10px; line-height: 1.6; }
.rv-trunc { color: var(--gold-deep); }
.rv-panel {
  border: 1px solid var(--card-line);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: var(--card);
}
.rv-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.rv-ver { font-size: 15px; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }
.rv-meta { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.rv-agent { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--card-line); }
.rv-agent-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rv-focus { font-size: 12px; color: var(--muted); }
.rv-sec { margin-top: 8px; }
.rv-sec-head { font-size: 12px; font-weight: 700; margin-bottom: 4px; }
.rv-sec-head.ok { color: var(--ok); }
.rv-sec-head.bad { color: var(--danger); }
.rv-item { padding: 6px 8px; margin-bottom: 6px; border-radius: 4px; background: rgba(0, 0, 0, .02); }
.rv-quote {
  font-size: 13px; line-height: 1.7; color: var(--ink);
  border-left: 3px solid var(--card-line); padding-left: 8px; word-break: break-all;
}
.rv-nolocate { font-size: 12px; color: var(--muted); font-style: italic; }
.rv-why { font-size: 12px; color: var(--ink-soft); margin-top: 4px; line-height: 1.6; }
.rv-aspect {
  display: inline-block; margin-right: 6px; padding: 0 5px; border-radius: 3px;
  background: var(--el-color-success-light-9); color: var(--el-color-success-dark-2);
}
.rv-problem { font-size: 12px; margin-top: 4px; line-height: 1.6; word-break: break-all; }
.rv-type { color: var(--muted); margin-right: 4px; }
.lv-big { color: var(--danger); margin-right: 6px; }
.lv-mid { color: var(--gold-deep); margin-right: 6px; }
.lv-small { color: var(--muted); margin-right: 6px; }
.rv-fix { font-size: 12px; color: var(--ink-soft); margin-top: 3px; line-height: 1.6; }
.rv-empty { font-size: 12px; color: var(--muted); padding: 2px 0; }
</style>
