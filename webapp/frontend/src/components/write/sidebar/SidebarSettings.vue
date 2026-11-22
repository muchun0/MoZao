<template>
  <!-- 「设定」页签：世界观/术语/机制/地点/势力/物品速查（只读，数据来自 GET /world） -->
  <div class="panel">
    <p v-if="loading" class="hint">加载中…</p>
    <p v-else-if="error" class="err">{{ error }}</p>
    <template v-else>
      <p v-if="isEmpty" class="empty">还没有世界观设定，先在世界观页生成</p>
      <template v-else>
        <details v-if="world?.world_view" class="group" open>
          <summary>世界观</summary>
          <p class="txt">{{ world.world_view }}</p>
        </details>

        <details v-if="terms.length" class="group">
          <summary>术语统一 <em>{{ terms.length }}</em></summary>
          <div v-for="t in terms" :key="t.name" class="card">
            <p class="card-title">{{ t.name }}</p>
            <p v-if="t.aliases?.length" class="txt muted">别名：{{ t.aliases.join('、') }}</p>
            <p v-if="t.definition" class="txt">{{ t.definition }}</p>
          </div>
        </details>

        <details v-if="world?.mechanism_rules" class="group">
          <summary>机制规则</summary>
          <p class="txt">{{ world.mechanism_rules }}</p>
        </details>

        <details v-for="sec in listSections" :key="sec.key" class="group">
          <summary>{{ sec.label }} <em>{{ sec.items.length }}</em></summary>
          <div v-for="(row, i) in sec.items" :key="i" class="card">
            <p class="card-title">{{ row.name }}</p>
            <p v-if="rowDesc(row)" class="txt muted clamp">{{ rowDesc(row) }}</p>
          </div>
        </details>

        <details v-if="taboos.length" class="group">
          <summary>语言禁忌 <em>{{ taboos.length }}</em></summary>
          <p class="chips"><span v-for="t in taboos" :key="t" class="chip">{{ t }}</span></p>
        </details>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getWorld } from '../../../api/novel'
import { errorDetail } from '../../../utils/agentRun'

const props = defineProps({ pid: { type: String, required: true } })
const world = ref(null)
const loading = ref(false)
const error = ref('')

const terms = computed(() => world.value?.terminology || [])
const taboos = computed(() => world.value?.style_taboos || [])
const listSections = computed(() => [
  { key: 'locations', label: '地点', items: world.value?.locations || [] },
  { key: 'factions', label: '势力', items: world.value?.factions || [] },
  { key: 'enemies', label: '生物', items: world.value?.enemies || [] },
  { key: 'items', label: '物品', items: world.value?.items || [] },
].filter(s => s.items.length))

const isEmpty = computed(() => {
  const w = world.value
  if (!w) return true
  return !w.world_view && !terms.value.length && !w.mechanism_rules && !listSections.value.length
})

// 设定卡的描述字段不统一：按优先级取第一个非空文本，不猜含义
function rowDesc(row) {
  return row.desc || row.description || row.summary || row.effect || row.goal || row.relation_to_mc || ''
}

onMounted(async () => {
  loading.value = true
  try {
    world.value = await getWorld(props.pid)
  } catch (e) {
    error.value = errorDetail(e, '设定加载失败')
  } finally {
    loading.value = false
  }
})
</script>
