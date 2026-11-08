<template>
  <el-dialog v-model="visible" title="我的模型配置（BYOK）" width="560px">
    <el-alert type="success" :closable="false" style="margin-bottom: 16px">
      配置自备 Key 后，生成走你的 Key，<b>平台不扣费</b>；
      不配置则使用平台默认 Key，<b>按 AI 实际生成字数从余额扣费</b>（统一 3 元/万字）。
      留空保存即回到平台默认 Key。
    </el-alert>
    <el-form :model="form" label-width="90px" size="default">
      <el-form-item label="服务商">
        <el-select v-model="form.provider" style="width:100%" @change="onProviderChange">
          <el-option v-for="p in providers" :key="p.value" :label="p.label" :value="p.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="API Key">
        <el-input v-model="form.api_key" type="password" show-password placeholder="sk-..." />
      </el-form-item>
      <el-form-item label="Base URL">
        <el-input v-model="form.base_url" :placeholder="baseUrlPlaceholder" />
      </el-form-item>
      <el-form-item label="模型名">
        <el-input v-model="form.model" placeholder="留空用默认（自定义兼容端点必须填）" />
      </el-form-item>
      <el-form-item label="思考模式" v-if="current.providers_supports_thinking">
        <el-select v-model="form.thinking" style="width:100%">
          <el-option label="自动（推荐：已知模型自动关闭）" value="auto" />
          <el-option label="关闭思考（省 token，正文更稳）" value="off" />
          <el-option label="开启思考" value="on" />
        </el-select>
      </el-form-item>
    </el-form>
    <el-alert v-if="testResult" :type="testResult.ok ? 'success' : 'error'"
              :closable="true" style="margin-top: 8px" @close="testResult = null">
      <template v-if="testResult.ok">
        连接成功：{{ testResult.response_model }}，{{ testResult.latency_ms }}ms，
        用量 {{ testResult.usage?.total_tokens ?? '-' }} tokens
      </template>
      <template v-else>
        [{{ testResult.error_code }}] {{ testResult.detail }}
      </template>
    </el-alert>
    <template #footer>
      <el-button :loading="testing" @click="onTest">测试连接</el-button>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getMyLlm, saveMyLlm, testMyLlm } from '../api/project'

// 兜底列表：接口未返回 providers 时仍可用（含火山方舟 Ark 接入点）
const FALLBACK_PROVIDERS = [
  { value: 'deepseek', label: 'DeepSeek', supports_thinking: true },
  { value: 'doubao', label: '火山方舟（豆包/Ark 接入点）', supports_thinking: true },
  { value: 'openai', label: 'OpenAI', supports_thinking: false },
  { value: 'zhipu', label: '智谱 GLM', supports_thinking: true },
  { value: 'qwen', label: '通义千问', supports_thinking: true },
  { value: 'moonshot', label: 'Moonshot', supports_thinking: false },
  { value: 'custom', label: '自定义 OpenAI 兼容', supports_thinking: true },
]

defineExpose({ open: () => { visible.value = true } })

const visible = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref(null)
const providers = ref(FALLBACK_PROVIDERS)
const form = ref({ provider: 'deepseek', api_key: '', base_url: '', model: '', thinking: 'auto' })

const current = computed(() => {
  const p = providers.value.find((x) => x.value === form.value.provider) || {}
  return { ...p, providers_supports_thinking: p.supports_thinking ?? true }
})
const baseUrlPlaceholder = computed(() => {
  const d = current.value.default_base_url
  return d ? `留空用默认：${d}` : '自定义兼容端点必须填写（如 https://ark.cn-beijing.volces.com/api/v3）'
})

onMounted(async () => {
  try {
    const r = await getMyLlm()
    if (r.providers?.length) providers.value = r.providers
    form.value.provider = r.provider || 'deepseek'
    form.value.api_key = r.api_key || ''
    form.value.base_url = r.base_url || ''
    form.value.model = r.model || ''
    form.value.thinking = r.thinking || 'auto'
  } catch {}
})

function onProviderChange() {
  testResult.value = null
}

async function onTest() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testMyLlm(form.value)
  } catch (e) {
    testResult.value = {
      ok: false, error_code: 'REQUEST_FAILED',
      detail: e?.response?.data?.detail || '测试请求失败',
    }
  } finally {
    testing.value = false
  }
}

async function onSave() {
  saving.value = true
  try {
    await saveMyLlm(form.value)
    ElMessage.success(form.value.api_key ? '已启用你的模型，生成不再扣费' : '已清空，回到平台默认模型')
    visible.value = false
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>
