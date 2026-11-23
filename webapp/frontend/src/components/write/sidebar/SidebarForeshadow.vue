<template>
  <!-- 「伏笔」页签：五态筛选 + 条目跳章（状态与距离全部由后端计算，前端只渲染） -->
  <div class="panel">
    <p v-if="loading" class="hint">加载中…</p>
    <p v-else-if="error" class="err">{{ error }}</p>
    <template v-else>
      <div class="filters">
        <button v-for="f in filters" :key="f.key" class="filter"
                :class="{ on: state === f.key }" @click="state = f.key">
          {{ f.label }}<em v-if="counts[f.key]">{{ counts[f.key] }}</em>
        </button>
      </div>

      <p v-if="!data" class="empty">还没有伏笔台账</p>
      <template v-else>
        <button v-for="it in visible" :key="it.index" class="item" @click="jump(it)">
          <span class="item-name">{{ it.name }}</span>
          <span class="chip" :class="sevClass(it.state)">{{ it.state_label || it.state }}</span>
          <span class="item-sub">
            {{ it.distance_label || '' }}
            <template v-if="it.reveal_ch"> · 计划第{{ it.reveal_ch }}章回收</template>
          </span>
        </button>
        <p v-if="!visible.length" class="empty-s">该状态下没有伏笔</p>

        <section v-if="unstructured.length" class="sec">
          <h5>未登记章号 <em>{{ unstructured.length }}</em></h5>
          <div v-for="u in unstructured" :key="u.name" class="card">
            <p class="card-title">{{ u.name }}</p>
            <p class="txt muted">未登记章号，补全后可纳入跟踪</p>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { listForeshadowing } from '../../../api/tools'
import { errorDetail } from '../../../utils/agentRun'

const props = defineProps({ pid: { type: String, required: true } })
const emit = defineEmits(['jump'])

const filters = [
  { key: '', label: '全部' },
  { key: 'overdue', label: '逾期' },
  { key: 'due_soon', label: '临期' },
  { key: 'stalled', label: '停滞' },
  { key: 'planted', label: '已埋设' },
  { key: 'revealed', label: '已回收' },
]

const data = ref(null)
const state = ref('')
const loading = ref(false)
const error = ref('')

const items = computed(() => data.value?.items || [])
const unstructured = computed(() => data.value?.unstructured || [])
const counts = computed(() => {
  const summary = data.value?.summary || {}
  return {
    overdue: summary.overdue || 0,
    due_soon: summary.due_soon || 0,
    stalled: summary.stalled || 0,
    planted: summary.planted || 0,
    revealed: summary.revealed || 0,
  }
})
const visible = computed(() => (state.value ? items.value.filter(i => i.state === state.value) : items.value))

function sevClass(s) {
  if (s === 'overdue') return 'bad'
  if (s === 'due_soon') return 'warn'
  return ''
}

function jump(it) {
  const target = Number(it.reveal_ch) || Number(it.buried_ch) || 0
  if (target > 0) emit('jump', target)
}

onMounted(async () => {
  loading.value = true
  try {
    data.value = await listForeshadowing(props.pid)
  } catch (e) {
    error.value = errorDetail(e, '伏笔台账加载失败')
  } finally {
    loading.value = false
  }
})
</script>
