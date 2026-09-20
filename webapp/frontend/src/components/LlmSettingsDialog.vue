<template>
  <el-dialog v-model="visible" title="我的模型配置（BYOK）" width="520px">
    <el-alert type="success" :closable="false" style="margin-bottom: 16px">
      配置自己的 API Key 后，生成将使用你的 Key，<b>不再从余额扣费</b>（平台未产生 LLM 费用）。
      留空保存即回到平台默认 Key。
    </el-alert>
    <el-form :model="form" label-width="90px" size="default">
      <el-form-item label="服务商">
        <el-select v-model="form.provider" style="width:100%">
          <el-option label="DeepSeek" value="deepseek" />
          <el-option label="OpenAI" value="openai" />
          <el-option label="智谱 GLM" value="zhipu" />
          <el-option label="通义千问" value="qwen" />
          <el-option label="Moonshot" value="moonshot" />
          <el-option label="自定义 OpenAI 兼容" value="custom" />
        </el-select>
      </el-form-item>
      <el-form-item label="API Key">
        <el-input v-model="form.api_key" type="password" show-password placeholder="sk-..." />
      </el-form-item>
      <el-form-item label="Base URL">
        <el-input v-model="form.base_url" placeholder="留空用默认，如 https://api.deepseek.com/v1" />
      </el-form-item>
      <el-form-item label="模型名">
        <el-input v-model="form.model" placeholder="留空用默认，如 deepseek-chat" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getMyLlm, saveMyLlm } from '../api/project'

defineExpose({ open: () => { visible.value = true } })

const visible = ref(false)
const saving = ref(false)
const form = ref({ provider: 'deepseek', api_key: '', base_url: '', model: '' })

onMounted(async () => {
  try {
    const r = await getMyLlm()
    form.value.provider = r.provider || 'deepseek'
    form.value.api_key = r.api_key || ''
    form.value.base_url = r.base_url || ''
    form.value.model = r.model || ''
  } catch {}
})

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
