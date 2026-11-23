<template>
  <el-dialog v-model="visible" title="充值" width="480px">
    <div class="recharge-wrap">
      <p class="recharge-tip">充值请联系站长</p>
      <div class="qr-box">
        <img v-if="!imgError" src="/payment-qr.png" alt="站长联系方式二维码" @error="imgError = true" />
        <div v-if="imgError" class="qr-placeholder">二维码图片缺失</div>
      </div>
      <p class="recharge-note">扫码添加站长微信，说明充值金额与用户名；站长确认收款后为你入账。</p>
      <p class="recharge-note">充值到账后在「个人中心 → 账单」查看余额与申请退款。</p>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'

const visible = ref(false)
const imgError = ref(false)

function open() {
  visible.value = true
  imgError.value = false
}

defineExpose({ open })
</script>

<style scoped>
.recharge-wrap { text-align: center; }
.recharge-tip { font-size: 16px; margin-bottom: 16px; }
.qr-box { display: flex; justify-content: center; margin-bottom: 12px; }
.qr-box img { max-width: 260px; border-radius: 8px; }
.qr-placeholder { width: 260px; height: 260px; background: #f5f7fa; border: 2px dashed #dcdfe6; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #909399; }
.recharge-note { font-size: 13px; color: #909399; line-height: 1.7; }
</style>
