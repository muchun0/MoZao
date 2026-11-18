<template>
  <!-- 「大纲」页签：分卷折叠 + 章节列表，当前章高亮（只读，点击跳章） -->
  <div class="panel">
    <p v-if="loading" class="hint">加载中…</p>
    <p v-else-if="error" class="err">{{ error }}</p>
    <template v-else>
      <p v-if="!groups.length" class="empty">还没有大纲，先在大纲页生成或导入</p>
      <details v-for="g in groups" :key="g.no" class="group" open>
        <summary>{{ g.name }} <em>{{ g.chapters.length }}</em></summary>
        <button v-for="c in g.chapters" :key="c.no" class="item"
                :class="{ on: c.no === ch }" @click="$emit('jump', c.no)">
          <span class="item-name">{{ c.no }}. {{ c.title || `第${c.no}章` }}</span>
          <span v-if="c.tag" class="chip">{{ c.tag }}</span>
          <span class="item-sub">{{ c.core_event || c.goal || '' }}</span>
        </button>
      </details>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getOutline } from '../../../api/novel'
import { errorDetail } from '../../../utils/agentRun'

const props = defineProps({ pid: { type: String, required: true }, ch: { type: Number, default: 0 } })
defineEmits(['jump'])

const data = ref(null)
const loading = ref(false)
const error = ref('')

const groups = computed(() => {
  const volumes = data.value?.volumes || []
  const chapters = data.value?.chapters || []
  if (!chapters.length) return []
  if (!volumes.length) return [{ no: 0, name: '全部章节', chapters }]
  const list = volumes.map(v => ({
    no: v.no,
    name: v.name || `第${v.no}卷`,
    chapters: chapters.filter(c => c.volume_no === v.no),
  })).filter(g => g.chapters.length)
  const rest = chapters.filter(c => !volumes.some(v => v.no === c.volume_no))
  if (rest.length) list.push({ no: 0, name: '未分卷', chapters: rest })
  return list
})

onMounted(async () => {
  loading.value = true
  try {
    data.value = await getOutline(props.pid)
  } catch (e) {
    error.value = errorDetail(e, '大纲加载失败')
  } finally {
    loading.value = false
  }
})
</script>
