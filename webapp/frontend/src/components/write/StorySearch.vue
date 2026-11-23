<template>
  <!-- 只读全文检索：正文 / 设定；点击结果跳章节并定位到命中位置 -->
  <section class="search">
    <div class="s-row">
      <el-input v-model="q" size="small" placeholder="搜索正文或设定（2–40 字）"
                maxlength="40" @keyup.enter="run" clearable />
      <el-button size="small" type="primary" :loading="loading" @click="run">搜索</el-button>
    </div>
    <div class="s-row">
      <el-radio-group v-model="scope" size="small" @change="run">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="body">正文</el-radio-button>
        <el-radio-button value="settings">设定</el-radio-button>
      </el-radio-group>
      <span v-if="meta" class="s-meta">{{ meta.items.length }} 条 · {{ meta.elapsed_ms }}ms</span>
    </div>
    <p v-if="error" class="s-err">{{ error }}</p>
    <p v-if="meta?.truncated" class="s-warn">扫描已到上限，结果可能不完整</p>
    <div class="s-list">
      <button v-for="(it, i) in (meta?.items || [])" :key="i" class="s-item"
              @click="$emit('open', { ...it, q: q.trim() })">
        <span class="s-ch">{{ it.kind === 'settings' ? it.ch_title : `第${it.ch}章` }}</span>
        <span class="s-snip">{{ it.snippet }}</span>
      </button>
      <p v-if="searched && !error && !meta?.items?.length" class="s-empty">没有找到匹配内容</p>
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'
import { searchProject } from '../../api/inline'
import { errorDetail } from '../../utils/agentRun'

const props = defineProps({ pid: { type: String, required: true } })
defineEmits(['open'])

const q = ref('')
const scope = ref('all')
const loading = ref(false)
const meta = ref(null)
const error = ref('')
const searched = ref(false)

async function run() {
  const text = q.value.trim()
  if (text.length < 2) {
    error.value = '请输入至少 2 个字'
    return
  }
  loading.value = true
  error.value = ''
  try {
    meta.value = await searchProject(props.pid, { q: text, scope: scope.value, limit: 20 })
    searched.value = true
  } catch (e) {
    error.value = errorDetail(e, '搜索失败')
    meta.value = null
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.search { padding: 16px; height: 100%; box-sizing: border-box; overflow-y: auto; }
.s-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.s-meta { margin-left: auto; font-size: 11px; color: var(--muted); }
.s-err { color: var(--danger); font-size: 12px; }
.s-warn { color: var(--gold-deep); font-size: 11px; }
.s-list { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.s-item {
  text-align: left; border: 1px solid var(--card-line); border-radius: 10px;
  background: var(--paper); padding: 8px 10px; cursor: pointer;
  display: flex; flex-direction: column; gap: 4px;
}
.s-item:hover { border-color: var(--gold-line); }
.s-ch { font-size: 11px; color: var(--indigo); }
.s-snip { font-family: var(--serif); font-size: 12px; color: var(--ink-soft); line-height: 1.7; }
.s-empty { color: var(--muted); font-size: 12px; text-align: center; padding: 16px 0; }
</style>
