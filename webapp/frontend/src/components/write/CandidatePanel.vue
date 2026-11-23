<template>
  <!-- 右侧 AI 面板：模式/长度/候选数/指令 → 生成 → 人工采纳；并展示 48h 内未采纳记录 -->
  <section class="panel">
    <div class="p-head">
      <span class="p-title">内联写作</span>
      <el-tooltip content="生成即按候选合计字数计费；未采纳也计费" placement="left">
        <span class="p-badge">计费说明</span>
      </el-tooltip>
    </div>

    <div class="block">
      <div class="seg">
        <button v-for="m in modes" :key="m.value" class="seg-item"
                :class="{ on: mode === m.value }" @click="$emit('update:mode', m.value)">
          {{ m.label }}
        </button>
      </div>
      <p class="hint">{{ modeHint }}</p>
    </div>

    <div class="block">
      <div class="row">
        <span class="label">每候选字数</span>
        <el-input-number :model-value="targetWords" :min="100" :max="800" :step="50" size="small"
                         controls-position="right"
                         @update:model-value="v => $emit('update:targetWords', v)" />
      </div>
      <div class="row">
        <span class="label">候选数</span>
        <el-radio-group :model-value="candidateCount" size="small"
                        @update:model-value="v => $emit('update:candidateCount', v)">
          <el-radio-button :value="2">2 个</el-radio-button>
          <el-radio-button :value="3">3 个</el-radio-button>
        </el-radio-group>
      </div>
      <el-input :model-value="instruction" type="textarea" :rows="2" maxlength="200" show-word-limit
                placeholder="可补充要求：更紧张一点 / 多写对话 / 收在悬念上"
                @update:model-value="v => $emit('update:instruction', v)" />
    </div>

    <div class="estimate" v-if="estimate">
      <span>约 {{ estimate.words }} 字</span>
      <span class="dot">·</span>
      <span class="price">¥{{ Number(estimate.price || 0).toFixed(2) }}</span>
      <span class="tag">未采纳也计费</span>
    </div>
    <el-button type="primary" class="gen-btn" :loading="generating" :disabled="!canGenerate"
               @click="$emit('generate')">
      {{ generating ? '生成中…' : generateLabel }}
    </el-button>
    <p v-if="blockedReason" class="warn">{{ blockedReason }}</p>

    <div v-if="genMeta" class="charged">
      本次已计费 ¥{{ Number(genMeta.charged || 0).toFixed(2) }}（{{ genMeta.words }} 字）
      <span v-if="genMeta.replayed" class="replay">重放，未重复扣费</span>
    </div>

    <div v-if="candidates.length" class="cands">
      <div v-for="(c, i) in candidates" :key="c.id" class="cand">
        <div class="cand-head">
          <span class="cand-no">候选 {{ i + 1 }}</span>
          <div class="cand-actions">
            <el-button size="small" text @click="$emit('copy', c)">复制</el-button>
            <el-button size="small" type="primary" :loading="adopting === c.id"
                       @click="$emit('adopt', c)">采纳</el-button>
          </div>
        </div>
        <p class="cand-text">{{ c.text }}</p>
      </div>
    </div>

    <div v-if="drafts.length" class="drafts">
      <p class="drafts-title">未采纳候选（48 小时内，已计费）</p>
      <div v-for="d in drafts" :key="d.gen_id" class="draft">
        <div class="draft-head">
          <span>{{ modeLabel(d.mode) }} · {{ d.words }} 字</span>
          <el-button size="small" text type="primary" @click="$emit('restore', d)">恢复</el-button>
        </div>
        <p class="draft-text">{{ (d.candidates?.[0]?.text || '').slice(0, 80) }}…</p>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  mode: { type: String, default: 'continue' },
  targetWords: { type: Number, default: 300 },
  candidateCount: { type: Number, default: 2 },
  instruction: { type: String, default: '' },
  estimate: { type: Object, default: null },
  generating: { type: Boolean, default: false },
  candidates: { type: Array, default: () => [] },
  genMeta: { type: Object, default: null },
  drafts: { type: Array, default: () => [] },
  canGenerate: { type: Boolean, default: true },
  blockedReason: { type: String, default: '' },
  adopting: { type: String, default: '' },
})
defineEmits(['update:mode', 'update:targetWords', 'update:candidateCount', 'update:instruction',
  'generate', 'adopt', 'copy', 'restore'])

const modes = [
  { value: 'continue', label: '续写' },
  { value: 'rewrite', label: '改写' },
  { value: 'expand', label: '扩写' },
]
const modeHint = computed(() => ({
  continue: '从光标处往下写，不需要选中。',
  rewrite: '选中一段文字后改写，保持原意。',
  expand: '选中一段文字后扩写，细节更足。',
}[props.mode]))
const generateLabel = computed(() => ({ continue: '从光标处续写', rewrite: '改写选中', expand: '扩写选中' }[props.mode]))
function modeLabel(m) {
  return ({ continue: '续写', rewrite: '改写', expand: '扩写' }[m] || m || '写作')
}
</script>

<style scoped>
.panel {
  /* 宽度跟随右栏（父级 300–340px），不再写死 340 撑破容器 */
  width: 100%; min-width: 0; height: 100%; overflow-y: auto; box-sizing: border-box;
  padding: 16px; background: var(--card);
}
.p-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.p-title { font-family: var(--serif); font-size: 15px; letter-spacing: 2px; color: var(--ink); }
.p-badge { font-size: 11px; color: var(--gold-deep); border-bottom: 1px dashed var(--gold-line); cursor: help; }
.block { margin-bottom: 12px; }
.seg { display: flex; background: var(--indigo-soft); border-radius: 10px; padding: 3px; }
.seg-item {
  flex: 1; border: 0; background: transparent; padding: 7px 0; border-radius: 8px;
  font-family: var(--sans); font-size: 13px; color: var(--ink-soft); cursor: pointer;
}
.seg-item.on { background: var(--card); color: var(--indigo); box-shadow: var(--shadow); font-weight: 600; }
.hint { margin: 6px 2px 0; font-size: 11px; color: var(--muted); line-height: 1.6; }
.row { display: flex; align-items: center; justify-content: space-between; margin: 8px 0; }
.label { font-size: 12px; color: var(--ink-soft); }
.estimate { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-soft); margin: 6px 2px; }
.price { color: var(--indigo); font-weight: 600; }
.dot { color: var(--muted); }
.tag { margin-left: auto; font-size: 11px; color: var(--gold-deep); background: var(--gold-soft); padding: 2px 6px; border-radius: 6px; }
.gen-btn { width: 100%; margin-top: 4px; }
.warn { color: var(--danger); font-size: 12px; margin: 8px 2px 0; line-height: 1.6; }
.charged { margin-top: 10px; font-size: 12px; color: var(--ok); }
.replay { margin-left: 6px; color: var(--muted); }
.cands { margin-top: 14px; display: flex; flex-direction: column; gap: 10px; }
.cand { border: 1px solid var(--card-line); border-radius: 12px; background: var(--paper); padding: 10px 12px; }
.cand-head { display: flex; align-items: center; justify-content: space-between; }
.cand-no { font-size: 11px; color: var(--muted); }
.cand-text { margin: 8px 0 0; font-family: var(--serif); font-size: 13px; line-height: 1.8; color: var(--ink); white-space: pre-wrap; }
.drafts { margin-top: 18px; border-top: 1px dashed var(--card-line); padding-top: 10px; }
.drafts-title { font-size: 11px; color: var(--muted); margin: 0 0 6px; }
.draft { padding: 8px 10px; border-radius: 10px; background: var(--indigo-soft); margin-bottom: 6px; }
.draft-head { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--ink); }
.draft-text { margin: 4px 0 0; font-size: 12px; color: var(--ink-soft); line-height: 1.6; }
</style>
