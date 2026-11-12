<template>
  <div class="tl">
    <FactsPanelHead
      title="时间线"
      sub="只读投影：按章看「已经发生的事件」，以及「发展方向的事件」（埋线、伏笔推进这类指向后文的事件）。人物、地点、状态在「世界事实」，伏笔与冲突的检查结论在「全书体检」。"
      :stats="stats"
      :loading="loading"
      @refresh="reload"
    />

    <el-empty v-if="off" description="时间线未启用（后台开关 timeline_view 关闭）">
      <p class="tl-tip">后台「开关管理」打开 timeline_view 后回到这里刷新即可，无需重新生成。</p>
    </el-empty>

    <template v-else>
      <div v-if="meta" class="tl-meta">
        <span class="tl-tag mono">版本 {{ meta.version }}</span>
        <span v-if="meta.sources?.stale" class="tl-tag warn">上游设定已更新，视图可能落后</span>
      </div>

      <nav class="tl-tabs">
        <button type="button" class="tl-tab" :class="{ on: tab === 'happened' }" @click="tab = 'happened'">
          已经发生的事件<span class="tl-tab-n">{{ happened.length }}</span>
        </button>
        <button type="button" class="tl-tab" :class="{ on: tab === 'direction' }" @click="tab = 'direction'">
          发展方向的事件<span class="tl-tab-n">{{ direction.length }}</span>
        </button>
        <el-select v-model="participant" size="small" clearable placeholder="按参与人筛选" class="tl-select" @change="reload">
          <el-option v-for="(n, name) in participants" :key="name" :value="name" :label="`${name}（${n}）`" />
        </el-select>
      </nav>

      <section class="tl-card">
        <header class="tl-card-head">
          <h4 class="tl-h4">{{ tab === 'happened' ? '已经发生的事件' : '发展方向的事件' }}</h4>
          <span class="tl-count">
            <template v-if="tab === 'happened'">按章顺序，写到的第 {{ atCh }} 章为止</template>
            <template v-else>埋线 / 伏笔推进这类指向后文的事件，不当作已发生的事实</template>
          </span>
        </header>

        <div v-if="groups.length" class="tl-groups">
          <div v-for="g in groups" :key="g.ch" class="tl-group">
            <div class="tl-ch">第 {{ g.ch }} 章 <span class="tl-dim">{{ g.items.length }} 条</span></div>
            <ul class="tl-list">
              <li v-for="(e, i) in g.items" :key="i">
                <el-tag size="small" effect="plain" :type="kindTag(e.kind)">{{ kindText(e.kind) }}</el-tag>
                <span class="tl-desc">{{ e.desc }}</span>
                <span v-if="e.participants?.length" class="tl-dim">{{ e.participants.join('、') }}</span>
              </li>
            </ul>
          </div>
        </div>

        <p v-else-if="loading" class="tl-hint">读取中…</p>
        <p v-else class="tl-hint">
          <template v-if="tab === 'direction'">
            还没有登记「发展方向」类事件。写新章时，埋线、伏笔推进会被标成这个类型；已经埋下的伏笔清单可去「全书体检 → 伏笔回收」看。
          </template>
          <template v-else>
            还没有事件记录。事件在写完一章后自动登记；这个项目前 {{ atCh }} 章如果是在事件流修复前生成的，就不会有记录——从下一章开始才会出现。
          </template>
        </p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getFactsGraph } from '../../api/facts'
import FactsPanelHead from './FactsPanelHead.vue'

const props = defineProps({ pid: { type: String, required: true } })

const loading = ref(false)
const off = ref(false)
const graph = ref(null)
const tab = ref('happened')
const participant = ref('')
const events = ref([])
const participants = ref({})

const KIND_TEXT = { state: '状态', object: '物品', relationship: '关系', foreshadow: '伏笔' }
const KIND_TAG = { state: '', object: 'success', relationship: 'info', foreshadow: 'warning' }
const kindText = (k) => KIND_TEXT[k] || '状态'
const kindTag = (k) => KIND_TAG[k] || ''

const meta = computed(() => graph.value)
const atCh = computed(() => meta.value?.sources?.at_ch ?? graph.value?.data?.at_ch ?? 0)

// 事件分两类：已经发生的（状态/物品/关系）与指向后文的（伏笔类，即发展方向）
const happened = computed(() => events.value.filter(e => e.kind !== 'foreshadow'))
const direction = computed(() => events.value.filter(e => e.kind === 'foreshadow'))

const groups = computed(() => {
  const src = tab.value === 'happened' ? happened.value : direction.value
  const map = new Map()
  for (const e of src) {
    const ch = Number(e.ch) || 0
    if (!map.has(ch)) map.set(ch, [])
    map.get(ch).push(e)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([ch, items]) => ({ ch, items }))
})

const stats = computed(() => [
  { label: '写到第几章', value: atCh.value },
  { label: '已发生', value: happened.value.length },
  { label: '发展方向', value: direction.value.length },
])

async function reload() {
  loading.value = true
  off.value = false
  try {
    graph.value = await getFactsGraph(props.pid, { view: 'timeline', participant: participant.value })
    events.value = graph.value?.data?.events || []
    participants.value = graph.value?.data?.participants || {}
  } catch (e) {
    if (e?.response?.status === 404) off.value = true
    graph.value = null
    events.value = []
    participants.value = {}
  } finally {
    loading.value = false
  }
}

onMounted(reload)
watch(() => props.pid, reload)
</script>

<style scoped>
.tl { display: flex; flex-direction: column; gap: 12px; }
.tl-tip { font-size: 13px; color: var(--muted); }
.tl-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.tl-tag {
  font-size: 12px; color: var(--ink-soft); background: var(--chip, rgba(0, 0, 0, .04));
  border-radius: 6px; padding: 2px 8px;
}
.tl-tag.mono { font-family: var(--mono, ui-monospace, monospace); }
.tl-tag.warn { color: var(--gold-deep); }
.tl-tabs { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.tl-tab {
  border: 1px solid var(--card-line); background: transparent; color: var(--ink-soft);
  border-radius: 999px; padding: 4px 12px; font-size: 13px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
}
.tl-tab.on { border-color: var(--indigo, #5b5bd6); color: var(--indigo, #5b5bd6); font-weight: 600; }
.tl-tab-n { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tl-select { width: 160px; margin-left: auto; }
.tl-card {
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 12px 14px;
}
.tl-card-head {
  display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; margin-bottom: 10px;
}
.tl-h4 { font-size: 14px; margin: 0; }
.tl-count { font-size: 12px; color: var(--muted); }
.tl-groups { display: flex; flex-direction: column; gap: 14px; }
.tl-group { border-left: 2px solid var(--indigo-soft, rgba(91, 91, 214, .18)); padding-left: 12px; }
.tl-ch { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.tl-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.tl-list li { display: flex; align-items: flex-start; gap: 8px; font-size: 13px; line-height: 1.7; }
.tl-desc { flex: 1; color: var(--ink-soft); }
.tl-dim { color: var(--muted); font-size: 12px; }
.tl-hint { font-size: 12px; color: var(--muted); line-height: 1.8; margin: 4px 0 0; }
</style>
