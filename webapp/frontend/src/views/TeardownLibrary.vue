<template>
  <div>
    <div class="toolbar">
      <h2 class="title">爆款拆书库</h2>
      <el-input v-model="q" placeholder="搜索题材共性、套路…" clearable style="width:260px" @keyup.enter="reload" @clear="reload" />
      <el-button @click="reload">搜索</el-button>
      <el-button type="primary" style="margin-left:auto" @click="openAnalyze">+ 拆解爆款作品</el-button>
    </div>
    <p class="hint">
      项目调研的题材共性会自动沉淀（同题材合并更新）；你也可以粘贴/上传看到的爆款作品文本，由 AI 拆解入库。内容来自生成前的联网调研、内置知识库与手动拆解。
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
          <span class="tsrc">{{ srcLabel(t.source_type) }}</span>
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

    <el-dialog v-model="showAnalyze" title="拆解爆款作品" width="620px">
      <el-form :model="anaForm" label-width="90px">
        <el-form-item label="作品名"><el-input v-model="anaForm.title" placeholder="可选，如《大王饶命》第一章" /></el-form-item>
        <el-form-item label="题材">
          <el-select v-model="anaForm.genre" clearable filterable allow-create default-first-option
            placeholder="可选；不填则由 AI 判断">
            <el-option v-for="g in genreOptions" :key="g" :label="g" :value="g" />
          </el-select>
        </el-form-item>
        <el-form-item label="作品文本">
          <div style="width:100%">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px">
              <input type="file" ref="anaFileInput" accept=".txt,.md,.docx" @change="onAnaFile" />
              <span class="muted" style="font-size:12px">.txt / .md / .docx，≤5MB</span>
            </div>
            <el-input v-model="anaForm.text" type="textarea" :rows="9"
              placeholder="粘贴爆款作品的核心章节（≥50 字，≤20000 字）。AI 将拆解开篇钩子、情节套路、人设亮点、文风节奏、可借用手法并沉淀入库" />
            <div class="word-count">已输入 {{ anaWords }} 字（{{ anaWords < 50 ? '至少 50 字' : anaWords > 20000 ? '超过 20000 字上限' : '可提交' }}）</div>
            <div v-if="anaFileMsg" class="file-msg" :class="anaFileMsg.includes('识别') || anaFileMsg.includes('解析完成') ? 'file-ok' : 'file-err'">{{ anaFileMsg }}</div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAnalyze=false">取消</el-button>
        <el-button type="primary" :loading="analyzing" :disabled="!canAnalyze" @click="doAnalyze">拆解并入库</el-button>
      </template>
    </el-dialog>

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
        <template v-if="current?.source_type === 'manual'">
          <div v-if="obj.opening" class="dsec">
            <div class="dlabel">开篇钩子拆解</div>
            <div class="dtext">{{ obj.opening }}</div>
          </div>
          <div v-if="listOf(obj.hooks).length" class="dsec">
            <div class="dlabel">悬念 / 冲突钩子</div>
            <ul class="dlist"><li v-for="(v,i) in listOf(obj.hooks)" :key="i">{{ v }}</li></ul>
          </div>
          <div v-if="listOf(obj.characters).length" class="dsec">
            <div class="dlabel">人设亮点</div>
            <ul class="dlist"><li v-for="(v,i) in listOf(obj.characters)" :key="i">{{ v }}</li></ul>
          </div>
          <div v-if="listOf(obj.dialogue).length" class="dsec">
            <div class="dlabel">对话 / 描写技巧</div>
            <ul class="dlist"><li v-for="(v,i) in listOf(obj.dialogue)" :key="i">{{ v }}</li></ul>
          </div>
          <div v-if="listOf(obj.takeaways).length" class="dsec highlight">
            <div class="dlabel">可直接借用的手法（核心交付）</div>
            <ul class="dlist"><li v-for="(v,i) in listOf(obj.takeaways)" :key="i">{{ v }}</li></ul>
          </div>
          <div v-if="listOf(obj.excerpts).length" class="dsec">
            <div class="dlabel">原文关键片段（回溯核对）</div>
            <div v-for="(v,i) in listOf(obj.excerpts)" :key="i" class="dtext quote">{{ v }}</div>
          </div>
        </template>
        <div v-if="!Object.keys(obj).length" class="dtext">暂无详细内容</div>
        <div class="dmeta">来源：{{ srcLabel(current?.source_type) }} · 浏览 {{ current?.view_count }}</div>
        <el-button v-if="auth.isAdmin" type="danger" plain size="small" style="margin-top:10px" @click="doDelete(current)">
          删除该条目（管理员）
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { getTeardownGenres, getTeardowns, getTeardownDetail, deleteTeardown, analyzeTeardown } from '../api/teardown'
import { parseContent } from '../api/tools'

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

const showAnalyze = ref(false)
const analyzing = ref(false)
const anaFileMsg = ref('')
const anaFileInput = ref(null)
const anaForm = ref({ title: '', genre: '', text: '' })

const genreOptions = computed(() => {
  const set = new Set(['通用', ...genres.value.map(g => g.genre)])
  return [...set]
})
const anaWords = computed(() => (anaForm.value.text || '').length)
const canAnalyze = computed(() => anaWords.value >= 50 && anaWords.value <= 20000 && !analyzing.value)

function srcLabel(st) {
  if (st === 'seed') return '内置知识库'
  if (st === 'manual') return '手动拆解'
  return '调研沉淀'
}

function openAnalyze() {
  anaForm.value = { title: '', genre: '', text: '' }
  anaFileMsg.value = ''
  if (anaFileInput.value) anaFileInput.value.value = ''
  showAnalyze.value = true
}

function onAnaFile(e) {
  const f = e.target.files[0]
  if (!f) return
  if (f.size > 5 * 1024 * 1024) {
    anaFileMsg.value = '文件超过 5MB 限制，请精简后上传'
    e.target.value = ''
    return
  }
  const ext = ((f.name.match(/\.([^.]+)$/) || [])[1] || '').toLowerCase()
  if (!['txt', 'md', 'docx'].includes(ext)) {
    anaFileMsg.value = '仅支持 .txt / .md / .docx 格式'
    e.target.value = ''
    return
  }
  anaFileMsg.value = '正在解析…'
  const fd = new FormData()
  fd.append('file', f)
  parseContent(fd).then(res => {
    anaForm.value.text = res.full_text || ''
    anaFileMsg.value = res.full_text ? `解析完成，已填入（${res.full_text.length} 字）` : '解析结果为空，请检查文件内容'
  }).catch(err => {
    anaFileMsg.value = err.response?.data?.detail || '解析失败，请检查文件格式'
    e.target.value = ''
  })
}

async function doAnalyze() {
  analyzing.value = true
  try {
    const res = await analyzeTeardown(anaForm.value)
    const td = res.td || {}
    ElMessage.success(`拆解完成，已入库「${td.title || '作品拆解'}」`)
    showAnalyze.value = false
    reload()
    loadGenres()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '拆解失败，请稍后重试')
  } finally {
    analyzing.value = false
  }
}

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
.dsec.highlight { background: #f7f2e4; border: 1px solid #eadfbe; border-radius: 8px; padding: 10px 12px; }
.dtext.quote { border-left: 3px solid #e3c55d; padding: 6px 10px; margin-bottom: 8px; background: #faf8f1; color: #5f6b7a; }
.word-count { font-size: 12px; color: #909399; margin-top: 6px; }
.muted { color: #909399; }
.file-msg { font-size: 12px; line-height: 1.6; margin-top: 4px; }
.file-ok { color: #67c23a; }
.file-err { color: #f56c6c; }
</style>
