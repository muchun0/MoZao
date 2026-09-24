<template>
  <div>
    <div class="toolbar">
      <h2 class="title">爆款拆书库</h2>
      <el-input v-model="q" placeholder="搜索题材共性、套路…" clearable style="width:260px" @keyup.enter="reload" @clear="reload" />
      <el-button type="primary" @click="reload">搜索</el-button>
    </div>
    <p class="hint">
      每次项目调研的题材共性会自动沉淀到这里（同题材合并更新）；浏览后可复用为写作参考。库中内容来自生成前的联网调研与内置知识库。
    </p>

    <el-radio-group v-model="genre" size="small" class="genres" @change="reload">
      <el-radio-button value="">全部</el-radio-button>
      <el-radio-button v-for="g in genres" :key="g.genre" :value="g.genre">
        {{ g.genre }}（{{ g.count }}）
      </el-radio-button>
    </el-radio-group>

    <div v-loading="loading" class="grid">
      <el-card v-for="t in items" :key="t.id" class="tcard" @click="openDetail(t)">
        <div class="thead">
          <span class="tgenre">{{ t.genre }}</span>
          <span class="tsrc">{{ t.source_type === 'seed' ? '内置知识库' : '调研沉淀' }}</span>
        </div>
        <div class="ttitle">{{ t.title }}</div>
        <div class="tsummary">{{ t.summary || '暂无摘要' }}</div>
        <div class="tfoot">
          <span>浏览 {{ t.view_count }}</span>
          <span>{{ fmtTime(t.updated_at) }}</span>
        </div>
      </el-card>
      <el-empty v-if="!loading && !items.length" description="暂无拆书条目，生成项目完成调研后会自动沉淀" />
    </div>

    <el-pagination
      v-if="total > pageSize"
      style="margin-top:14px;justify-content:flex-end"
      layout="prev, pager, next"
      :total="total"
      :page-size="pageSize"
      v-model:current-page="page"
      @current-change="reload"
    />

    <el-drawer v-model="drawer" size="520px" :title="current?.title || '拆书详情'">
      <div v-if="obj" class="detail">
        <div class="dsec">
          <div class="dlabel">共性综述</div>
          <div class="dtext">{{ obj.summary || '—' }}</div>
        </div>
        <div v-if="listOf(obj.trends).length" class="dsec">
          <div class="dlabel">流行方向 / 热梗</div>
          <ul class="dlist"><li v-for="(v,i) in listOf(obj.trends)" :key="i">{{ v }}</li></ul>
        </div>
        <div v-if="listOf(obj.plot_patterns).length" class="dsec">
          <div class="dlabel">情节套路</div>
          <ul class="dlist"><li v-for="(v,i) in listOf(obj.plot_patterns)" :key="i">{{ v }}</li></ul>
        </div>
        <div v-if="obj.pacing" class="dsec">
          <div class="dlabel">节奏</div>
          <div class="dtext">{{ obj.pacing }}</div>
        </div>
        <div v-if="obj.opening_strategy" class="dsec">
          <div class="dlabel">开篇策略</div>
          <div class="dtext">{{ obj.opening_strategy }}</div>
        </div>
        <div v-if="listOf(obj.avoid).length" class="dsec">
          <div class="dlabel">雷区（避免）</div>
          <ul class="dlist avoid"><li v-for="(v,i) in listOf(obj.avoid)" :key="i">{{ v }}</li></ul>
        </div>
        <div v-if="listOf(obj.styles).length" class="dsec">
          <div class="dlabel">文风</div>
          <div class="chips"><span v-for="(v,i) in listOf(obj.styles)" :key="i" class="chip">{{ v }}</span></div>
        </div>
        <div v-if="listOf(obj.references).length" class="dsec">
          <div class="dlabel">参考作品/信源</div>
          <ul class="dlist"><li v-for="(v,i) in listOf(obj.references)" :key="i">{{ v }}</li></ul>
        </div>
        <div v-if="listOf(obj.platform_notes).length" class="dsec">
          <div class="dlabel">平台要点</div>
          <ul class="dlist"><li v-for="(v,i) in listOf(obj.platform_notes)" :key="i">{{ v }}</li></ul>
        </div>
        <div v-if="!Object.keys(obj).length" class="dtext">暂无详细内容</div>
        <div class="dmeta">来源：{{ current?.source_type === 'seed' ? '内置知识库' : '项目调研沉淀' }} · 浏览 {{ current?.view_count }}</div>
        <el-button v-if="auth.isAdmin" type="danger" plain size="small" style="margin-top:10px" @click="doDelete(current)">
          删除该条目（管理员）
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { getTeardownGenres, getTeardowns, getTeardownDetail, deleteTeardown } from '../api/teardown'

const auth = useAuthStore()
const loading = ref(false)
const genres = ref([])
const genre = ref('')
const q = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 12

const drawer = ref(false)
const current = ref(null)
const obj = ref({})

function listOf(v) {
  if (Array.isArray(v)) return v
  if (typeof v === 'string' && v.trim()) return v.split(/[；;]/).map(s => s.trim()).filter(Boolean)
  return []
}

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function reload() {
  loading.value = true
  try {
    const r = await getTeardowns({ genre: genre.value, q: q.value, page: page.value, page_size: pageSize })
    items.value = r.items || []
    total.value = r.total || 0
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadGenres() {
  try {
    const r = await getTeardownGenres()
    genres.value = r.genres || []
  } catch {}
}

async function openDetail(t) {
  try {
    const d = await getTeardownDetail(t.id)
    current.value = d
    obj.value = d.content_obj || {}
    drawer.value = true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  }
}

async function doDelete(t) {
  try {
    await ElMessageBox.confirm(`确认删除「${t.title}」？删除后不可恢复。`, '删除拆书条目', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteTeardown(t.id)
    ElMessage.success('已删除')
    drawer.value = false
    reload()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

onMounted(() => {
  loadGenres()
  reload()
})
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; }
.title { margin: 0; font-size: 20px; font-weight: 700; color: #1f2d3d; }
.hint { color: #7a8699; font-size: 13px; margin: 6px 0 12px; }
.genres { margin-bottom: 14px; flex-wrap: wrap; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.tcard { cursor: pointer; transition: transform .15s, box-shadow .15s; }
.tcard:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0,0,0,.1); }
.thead { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.tgenre { background: #f2e8c9; color: #8a6d1e; font-size: 12px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.tsrc { font-size: 12px; color: #9aa5b5; }
.ttitle { font-size: 15px; font-weight: 700; color: #1f2d3d; margin-bottom: 6px; }
.tsummary { font-size: 13px; color: #5f6b7a; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 39px; }
.tfoot { display: flex; justify-content: space-between; font-size: 12px; color: #9aa5b5; margin-top: 10px; }
.detail { font-size: 14px; }
.dsec { margin-bottom: 16px; }
.dlabel { font-weight: 700; color: #8a6d1e; margin-bottom: 6px; font-size: 13px; }
.dtext { color: #3d4a5c; line-height: 1.7; white-space: pre-wrap; }
.dlist { margin: 0; padding-left: 18px; color: #3d4a5c; line-height: 1.8; }
.dlist.avoid li { color: #a03a3a; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { background: #eef2f8; color: #3d5a86; font-size: 12px; padding: 3px 10px; border-radius: 10px; }
.dmeta { color: #9aa5b5; font-size: 12px; border-top: 1px solid #eef1f5; padding-top: 10px; }
</style>
