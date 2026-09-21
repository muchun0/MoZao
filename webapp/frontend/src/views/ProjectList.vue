<template>
  <div>
    <div class="toolbar">
      <el-input v-model="search" placeholder="搜索书名、题材…" clearable style="width:260px" />
      <el-radio-group v-model="filter" size="small">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="active">进行中</el-radio-button>
        <el-radio-button value="archived">已归档</el-radio-button>
      </el-radio-group>
      <el-select v-model="sort" style="width:140px;margin-left:auto">
        <el-option label="按创建时间" value="created" />
        <el-option label="按更新时间" value="updated" />
        <el-option label="按名称" value="name" />
      </el-select>
      <el-button type="primary" @click="showCreate = true">+ 新建小说</el-button>
    </div>

    <div v-loading="loading" class="grid">
      <el-card v-for="p in filtered" :key="p.id" class="pcard" @click="$router.push('/project/'+p.id)">
        <div class="pname">{{ p.title }}</div>
        <div class="pmeta">{{ p.genre }} · {{ p.total_words }}字</div>
        <div class="pmeta">已生成 {{ p.chapters_done || 0 }} 章</div>
        <el-progress :percentage="pct(p)" :stroke-width="6" style="margin-top:8px" />
        <div class="pactions" @click.stop>
          <el-button v-if="!p.archived" size="small" @click="doArchive(p)">归档</el-button>
          <el-button size="small" @click="doClone(p)">克隆</el-button>
          <el-button size="small" type="danger" @click="doDelete(p)">删除</el-button>
        </div>
      </el-card>
      <el-empty v-if="!loading && !filtered.length" description="还没有小说，点右上角新建" />
    </div>

    <el-dialog v-model="showCreate" title="新建小说" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="书名"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="题材"><el-input v-model="form.genre" placeholder="如：都市科幻、玄幻、言情" /></el-form-item>
        <el-form-item label="目标字数"><el-input-number v-model="form.total_words" :min="0" :step="1000" /></el-form-item>
        <el-form-item label="风格"><el-input v-model="form.style" placeholder="如：克制冷峻、轻松幽默" /></el-form-item>
        <el-form-item label="风格角色">
          <el-select v-model="form.style_role_id" clearable placeholder="可选：一键套用写作风格角色（人设+技能）">
            <el-option v-for="r in styleRoles" :key="r.id" :label="r.name" :value="r.id">
              <div style="display:flex;justify-content:space-between;gap:12px">
                <span>{{ r.name }}</span>
                <small class="muted">{{ r.user_id ? '我的' : '平台' }}</small>
              </div>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="核心设定"><el-input v-model="form.premise" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="auto">全自动生成</el-radio>
            <el-radio value="expand">AI扩写（我提供大纲）</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="form.mode === 'expand'">
          <el-form-item label="现有字数">
            <el-input-number v-model="form.existing_words" :min="0" :step="10000" />
            <span class="muted" style="margin-left:8px">按差值计费</span>
          </el-form-item>
          <el-form-item label="上传大纲">
            <input type="file" ref="fileInput" accept=".txt,.md,.doc,.docx" @change="onFilePick" />
          </el-form-item>
          <el-form-item label="手写内容">
            <el-input v-model="form.user_content" type="textarea" :rows="4" placeholder="粘贴你已写好的内容，AI将在此基础上续写" />
          </el-form-item>
        </template>
        <el-form-item label="高质量模式"><el-switch v-model="form.high_quality_mode" /></el-form-item>
        <el-form-item label="费用估算">
          <div class="price-hint">
            <span v-if="form.mode === 'expand'">
              目标 {{ form.total_words }} 字 - 已有 {{ form.existing_words || 0 }} 字 = AI生成约 {{ aiWords }} 字
            </span>
            <span v-else>AI生成约 {{ form.total_words }} 字</span>
            <br>
            单价：{{ form.high_quality_mode ? '3.00 元/万字（高质量）' : '2.00 元/万字' }}
            <b>预估费用：约 {{ estPrice }} 元</b>
            <small class="muted">（创建不扣费，生成完成后按实际字数结算）</small>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate=false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { listProjects, createProject, archiveProject, cloneProject, deleteProject } from '../api/project'
import { listStyleRoles } from '../api/tools'
import { ElMessage, ElMessageBox } from 'element-plus'

const list = ref([])
const loading = ref(false)
const search = ref('')
const filter = ref('')
const sort = ref('created')
const showCreate = ref(false)
const creating = ref(false)
const styleRoles = ref([])

const form = ref({
  title: '', genre: '', total_words: 25000, style: '', premise: '',
  mode: 'auto', high_quality_mode: false, style_role_id: 0,
  existing_words: 0, user_content: '', expand_input: '',
})
const fileInput = ref(null)

const aiWords = computed(() => {
  const target = form.value.total_words || 0
  const existing = form.value.existing_words || 0
  return Math.max(0, target - existing)
})
const estPrice = computed(() => {
  const rate = form.value.high_quality_mode ? 3 : 2
  return ((aiWords.value / 10000) * rate).toFixed(2)
})

function onFilePick(e) {
  const f = e.target.files[0]
  if (!f) return
  const reader = new FileReader()
  reader.onload = () => { form.value.expand_input = reader.result; ElMessage.success('大纲已读取') }
  reader.readAsText(f)
}

async function load() {
  loading.value = true
  try {
    const res = await listProjects()
    list.value = res.projects || res || []
  } catch (e) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

async function loadStyleRoles() {
  try {
    const res = await listStyleRoles()
    styleRoles.value = res.roles || []
  } catch (e) { /* 风格角色加载失败不阻塞建书 */ }
}

const filtered = computed(() => {
  let arr = list.value
  if (filter.value === 'archived') arr = arr.filter(p => p.archived)
  else if (filter.value === 'active') arr = arr.filter(p => !p.archived)
  if (search.value) {
    const q = search.value.toLowerCase()
    arr = arr.filter(p => p.title?.toLowerCase().includes(q) || p.genre?.toLowerCase().includes(q))
  }
  arr = [...arr]
  if (sort.value === 'name') arr.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'))
  else if (sort.value === 'updated') arr.sort((a, b) => (b.updated || 0) - (a.updated || 0))
  else arr.sort((a, b) => (b.created || 0) - (a.created || 0))
  return arr
})

function pct(p) {
  if (!p.total_words) return 0
  return Math.min(100, Math.round((p.generated_words || 0) / p.total_words * 100))
}

async function doCreate() {
  if (!form.value.title.trim()) { ElMessage.warning('请输入书名'); return }
  creating.value = true
  try {
    await createProject(form.value)
    ElMessage.success('创建成功')
    showCreate.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  } finally { creating.value = false }
}

async function doArchive(p) {
  await archiveProject(p.id)
  ElMessage.success('已归档')
  load()
}
async function doClone(p) {
  await cloneProject(p.id)
  ElMessage.success('已克隆')
  load()
}
async function doDelete(p) {
  await ElMessageBox.confirm(`确定删除《${p.title}》？`, '确认', { type: 'warning' })
  await deleteProject(p.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => { load(); loadStyleRoles() })
</script>

<style scoped>
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.pcard { cursor: pointer; transition: box-shadow .2s; }
.pcard:hover { box-shadow: 0 4px 12px rgba(0,0,0,.1); }
.pname { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.pmeta { font-size: 13px; color: #909399; }
.pactions { display: flex; gap: 8px; margin-top: 10px; }
.price-hint { background: #f6f8fa; padding: 8px 12px; border-radius: 6px; font-size: 13px; line-height: 1.8; color: #606266; }
.price-hint b { color: #c9a227; }
.price-hint .muted { color: #909399; font-size: 12px; }
</style>
