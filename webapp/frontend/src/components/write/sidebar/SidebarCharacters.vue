<template>
  <!-- 「人物」页签：世界观人物卡速览（只读，编辑入口保留在世界观 tab） -->
  <div class="panel">
    <p v-if="loading" class="hint">加载中…</p>
    <p v-else-if="error" class="err">{{ error }}</p>
    <template v-else>
      <p v-if="!characters.length" class="empty">还没有人物卡，先在世界观页生成</p>
      <div v-for="c in characters" :key="c.name" class="card">
        <p class="card-title">
          {{ c.name }}
          <span v-if="c.role" class="chip">{{ c.role }}</span>
        </p>
        <p v-if="c.personality" class="txt">性格：{{ c.personality }}</p>
        <p v-if="c.desire" class="txt">欲望：{{ c.desire }}</p>
        <p v-if="c.speech_style" class="txt muted">说话方式：{{ c.speech_style }}</p>
        <p v-if="c.desc" class="txt muted clamp">{{ c.desc }}</p>
      </div>
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
const characters = computed(() => world.value?.characters || [])

onMounted(async () => {
  loading.value = true
  try {
    world.value = await getWorld(props.pid)
  } catch (e) {
    error.value = errorDetail(e, '人物卡加载失败')
  } finally {
    loading.value = false
  }
})
</script>
