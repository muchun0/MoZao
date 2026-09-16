<template>
  <el-dialog v-model="visible" title="充值" width="480px">
    <div class="recharge-wrap">
      <p class="recharge-tip">充值请联系站长</p>
      <div class="qr-box">
        <img v-if="!imgError" src="/payment-qr.png" alt="收款码" @error="imgError = true" />
        <div v-if="imgError" class="qr-placeholder">收款码图片</div>
      </div>
      <p class="recharge-note">扫码转账后，备注你的用户名，联系站长人工确认到账。</p>
      <el-button type="primary" plain style="width:100%;margin-top:10px" @click="refundDialog = true">
        申请退款
      </el-button>
    </div>
  </el-dialog>

  <el-dialog v-model="refundDialog" title="申请退款" width="480px">
    <el-form :model="refundForm" label-width="80px">
      <el-form-item label="退款金额">
        <el-input-number v-model="refundForm.amount" :min="1" />
      </el-form-item>
      <el-form-item label="退款原因">
        <el-input v-model="refundForm.reason" type="textarea" :rows="3" placeholder="请说明退款原因" />
      </el-form-item>
      <el-alert type="warning" :closable="false" style="margin-bottom:10px">
        注册赠送金额不支持退款
      </el-alert>
    </el-form>
    <template #footer>
      <el-button @click="refundDialog = false">取消</el-button>
      <el-button type="primary" @click="submitRefund">提交申请</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import request from '../api/request'
import { ElMessage } from 'element-plus'

const visible = ref(false)
const imgError = ref(false)
const refundDialog = ref(false)
const refundForm = ref({ amount: 0, reason: '' })

function open() {
  visible.value = true
  imgError.value = false
}

async function submitRefund() {
  try {
    await request.post('/refund/request', refundForm.value)
    ElMessage.success('退款申请已提交，请等待站长处理')
    refundDialog.value = false
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '提交失败')
  }
}

defineExpose({ open })
</script>

<style scoped>
.recharge-wrap { text-align: center; }
.recharge-tip { font-size: 16px; margin-bottom: 16px; }
.qr-box { display: flex; justify-content: center; margin-bottom: 12px; }
.qr-box img { max-width: 260px; border-radius: 8px; }
.qr-placeholder { width: 260px; height: 260px; background: #f5f7fa; border: 2px dashed #dcdfe6; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #909399; }
.recharge-note { font-size: 13px; color: #909399; }
</style>
