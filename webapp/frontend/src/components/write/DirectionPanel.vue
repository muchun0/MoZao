<template>
  <!-- R26 剧情走向候选：卡点 → 3~5 个方向 → 按此写 / 存便签；并展示本章便签 -->
  <section class="panel">
    <div class="p-head">
      <span class="p-title">剧情走向</span>
      <el-tooltip content="候选只是方向，由你判断要不要用；生成按候选合计字数计费" placement="left">
        <span class="p-badge">仅供参考</span>
      </el-tooltip>
    </div>

    <div class="block">
      <div class="row">
        <span class="label">卡点</span>
        <el-select :model-value="stuckType" size="small" style="width: 150px"
                   @update:model-value="v => $emit('update:stuckType', v || '')">
          <el-option v-for="s in stuckTypes" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
      </div>
      <el-input v-if="stuckType === 'custom'" :model-value="stuckText" type="textarea" :rows="2"
                maxlength="200" show-word-limit placeholder="说说你卡在哪：例如主角动机接不上"
                @update:model-value="v => $emit('update:stuckText', v)" />
      <div class="row">
        <span class="label">方向数</span>
        <el-radio-group :model-value="count" size="small"
                        @update:model-value="v => $emit('update:count', v)">
          <el-radio-button v-for="n in [3, 4, 5]" :key="n" :value="n">{{ n }} 个</el-radio-button>
        </el-radio-group>
      </div>
    </div>

    <el-button type="primary" class="gen-btn" :loading="generating" :disabled="!canGenerate"
               @click="$emit('generate')">
      {{ generating ? '生成中…' : (batch?.items?.length ? '换一批' : '生成走向') }}
    </el-button>
    <p v-if="blockedReason" class="warn">{{ blockedReason }}</p>
    <p v-if="billingHint" class="hint">生成即按候选合计字数计费；换一批会再次计费。</p>

    <div v-if="batch" class="charged">
      本批 {{ batch.items?.length || 0 }} 个方向
      <template v-if="batch.charged > 0">· 已计费 ¥{{ Number(batch.charged).toFixed(2) }}（{{ batch.words }} 字）</template>
      <span v-if="batch.replayed" class="replay">重放，未重复扣费</span>
    </div>

    <div v-if="batch?.items?.length" class="cards">
      <p v-if="generic" class="hint">本批偏通用：没有收伏笔/兑承诺的方向，可换个卡点再试。</p>
      <div v-for="(item, i) in batch.items" :key="item.id" class="card">
        <div class="card-head">
          <span class="card-no">走向 {{ i + 1 }}</span>
          <span v-if="sourceTagLabel(item.source_tag)" class="src-tag">
            {{ sourceTagLabel(item.source_tag) }}
          </span>
        </div>
        <p class="card-title">{{ item.direction }}</p>
        <p class="card-text">{{ item.summary }}</p>
        <div class="card-foot">
          <div class="tags">
            <el-tag v-for="t in item.tags" :key="t" size="small" type="info">{{ t }}</el-tag>
          </div>
          <div class="acts">
            <el-button size="small" text @click="$emit('adopt', item)"
                       :loading="adopting === item.id">存便签</el-button>
            <el-button size="small" type="primary" @click="$emit('use', item)">按此写</el-button>
          </div>
        </div>
      </div>
    </div>

    <div class="notes">
      <p class="notes-title">写作便签 · {{ notes.length }}（不改正文、不计费）</p>
      <div v-for="n in notes" :key="n.draft_id" class="note">
        <div class="note-head">
          <span class="note-title">{{ n.direction || '便签' }}</span>
          <el-button size="small" text @click="$emit('delete-note', n)">删除</el-button>
        </div>
        <p class="note-text">{{ n.summary }}</p>
      </div>
      <p v-if="!notes.length" class="hint">还没有便签：选一个走向点「存便签」即可保存。</p>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { STUCK_TYPES, sourceTagLabel, isGenericBatch } from '../../utils/direction'

const props = defineProps({
  stuckType: { type: String, default: '' },
  stuckText: { type: String, default: '' },
  count: { type: Number, default: 4 },
  generating: { type: Boolean, default: false },
  batch: { type: Object, default: null },
  notes: { type: Array, default: () => [] },
  adopting: { type: String, default: '' },
  canGenerate: { type: Boolean, default: true },
  blockedReason: { type: String, default: '' },
})
defineEmits(['update:stuckType', 'update:stuckText', 'update:count',
  'generate', 'adopt', 'use', 'delete-note'])

const stuckTypes = STUCK_TYPES
const generic = computed(() => isGenericBatch(props.batch?.items || []))
// 计费开关由服务端随批次返回；未生成过时先不提示，避免开关关闭时误导
const billingHint = computed(() => !!props.batch?.billing)
</script>

<style scoped>
.panel {
  width: 340px; flex: 0 0 340px; height: 100%; overflow-y: auto; box-sizing: border-box;
  padding: 16px; background: var(--card); border-left: 1px solid var(--card-line);
}
.p-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.p-title { font-family: var(--serif); font-size: 15px; letter-spacing: 2px; color: var(--ink); }
.p-badge { font-size: 11px; color: var(--gold-deep); border-bottom: 1px dashed var(--gold-line); cursor: help; }
.block { margin-bottom: 10px; }
.row { display: flex; align-items: center; justify-content: space-between; margin: 8px 0; }
.label { font-size: 12px; color: var(--ink-soft); }
.hint { margin: 6px 2px 0; font-size: 11px; color: var(--muted); line-height: 1.6; }
.gen-btn { width: 100%; margin-top: 4px; }
.warn { color: var(--danger); font-size: 12px; margin: 8px 2px 0; line-height: 1.6; }
.charged { margin-top: 10px; font-size: 12px; color: var(--ok); }
.replay { margin-left: 6px; color: var(--muted); }
.cards { margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.card { border: 1px solid var(--card-line); border-radius: 12px; background: var(--paper); padding: 10px 12px; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-no { font-size: 11px; color: var(--muted); }
.src-tag {
  font-size: 11px; color: var(--gold-deep); background: var(--gold-soft);
  padding: 1px 6px; border-radius: 6px;
}
.card-title { margin: 6px 0 4px; font-family: var(--serif); font-size: 14px; color: var(--ink); font-weight: 600; }
.card-text { margin: 0; font-size: 12px; line-height: 1.75; color: var(--ink-soft); white-space: pre-wrap; }
.card-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; gap: 8px; }
.tags { display: flex; gap: 4px; flex-wrap: wrap; min-width: 0; }
.acts { display: flex; gap: 4px; flex-shrink: 0; }
.notes { margin-top: 18px; border-top: 1px dashed var(--card-line); padding-top: 10px; }
.notes-title { font-size: 11px; color: var(--muted); margin: 0 0 6px; }
.note { padding: 8px 10px; border-radius: 10px; background: var(--indigo-soft); margin-bottom: 6px; }
.note-head { display: flex; align-items: center; justify-content: space-between; }
.note-title { font-size: 13px; color: var(--ink); font-weight: 600; }
.note-text { margin: 4px 0 0; font-size: 12px; color: var(--ink-soft); line-height: 1.6; }
</style>
