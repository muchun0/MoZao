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
        <div class="pmeta">已生成 {{ p.generated_chapters || 0 }} 章</div>
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
        <el-form-item label="目标字数"><el-input-number v-model="form.total_words" :min="10000" :step="10000" /></el-form-item>
        <el-form-item label="风格"><el-input v-model="form.style" placeholder="如：克制冷峻、轻松幽默" /></el-form-item>
        <el-form-item label="核心设定"><el-input v-model="form.premise" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="auto">全自动生成</el-radio>
            <el-radio value="expand">AI扩写（我提供大纲）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="高质量模式"><el-switch v-model="form.high_quality_mode" /></el-form-item>
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
import { ElMessage, ElMessageBox } from 'element-plus'

const list = ref([])
const loading = ref(false)
const search = ref('')
const filter = ref('')
const sort = ref('created')
const showCreate = ref(false)
const creating = ref(false)

const form = ref({
  title: '', genre: '', total_words: 100000, style: '', premise: '',
  mode: 'auto', high_quality_mode: false,
})

async function load() {
  loading.value = true
  try {
    const res = await listProjects()
    list.value = res.projects || res || []
  } catch (e) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

const filtered = computed(() => {
  let arr = list.value
  if (filter.value === 'archived') arr = arr.filter(p => p.archived)
  else if (filter.value === 'active') arr = arr.filter(p => !p.archived)
  if (search.value) {
    const q = search.value.toLowerCase()
    arr = arr.filter(p => p.title?.toLowerCase().includes(q) || p.genre?.toLowerCase().includes(q))
  }
  return arr
})

function pct(p) {
  if (!p.total_words) return 0
  return Math.round((p.generated_words || 0) / p.total_words * 100)
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

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.pcard { cursor: pointer; transition: box-shadow .2s; }
.pcard:hover { box-shadow: 0 4px 12px rgba(0,0,0,.1); }
.pname { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.pmeta { font-size: 13px; color: #909399; }
.pactions { display: flex; gap: 8px; margin-top: 10px; }
</style>
