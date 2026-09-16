<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">墨造 · AI小说生成</h2>
      <el-tabs v-model="tab">
        <el-tab-pane label="登录" name="login">
          <el-form @submit.prevent="doLogin">
            <el-form-item>
              <el-input v-model="loginForm.email" placeholder="邮箱" />
            </el-form-item>
            <el-form-item>
              <el-input v-model="loginForm.password" type="password" placeholder="密码" show-password />
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="loading" style="width:100%">登录</el-button>
          </el-form>
          <div class="links">
            <el-link @click="tab='register'">注册账号</el-link>
            <el-link @click="tab='forgot'">忘记密码</el-link>
          </div>
        </el-tab-pane>

        <el-tab-pane label="注册" name="register">
          <el-form @submit.prevent="doRegister">
            <el-form-item>
              <el-input v-model="regForm.username" placeholder="用户名" />
            </el-form-item>
            <el-form-item>
              <el-input v-model="regForm.email" placeholder="邮箱" />
            </el-form-item>
            <el-form-item>
              <el-input v-model="regForm.password" type="password" placeholder="密码" show-password />
            </el-form-item>
            <el-form-item>
              <div style="display:flex;gap:8px;width:100%">
                <el-input v-model="regForm.code" placeholder="验证码" />
                <el-button :disabled="cooldown>0" @click="sendCode">{{ cooldown>0?cooldown+'s':'发送验证码' }}</el-button>
              </div>
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="loading" style="width:100%">注册</el-button>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="忘记密码" name="forgot">
          <el-form @submit.prevent="doReset">
            <el-form-item>
              <el-input v-model="resetForm.email" placeholder="注册邮箱" />
            </el-form-item>
            <el-form-item>
              <div style="display:flex;gap:8px;width:100%">
                <el-input v-model="resetForm.code" placeholder="验证码" />
                <el-button :disabled="cooldown>0" @click="sendResetCode">{{ cooldown>0?cooldown+'s':'发送验证码' }}</el-button>
              </div>
            </el-form-item>
            <el-form-item>
              <el-input v-model="resetForm.password" type="password" placeholder="新密码" show-password />
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="loading" style="width:100%">重置密码</el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { sendCode as sendCodeApi, resetPassword, register } from '../api/auth'
import { ElMessage } from 'element-plus'

const router = useRouter()
const auth = useAuthStore()
const tab = ref('login')
const loading = ref(false)
const cooldown = ref(0)
let timer = null

const loginForm = ref({ email: '', password: '' })
const regForm = ref({ username: '', email: '', password: '', code: '' })
const resetForm = ref({ email: '', code: '', password: '' })

function startCooldown() {
  cooldown.value = 60
  timer = setInterval(() => {
    cooldown.value--
    if (cooldown.value <= 0) clearInterval(timer)
  }, 1000)
}

onUnmounted(() => clearInterval(timer))

async function doLogin() {
  loading.value = true
  try {
    await auth.login(loginForm.value)
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally { loading.value = false }
}

async function doRegister() {
  loading.value = true
  try {
    await register(regForm.value)
    ElMessage.success('注册成功，请登录')
    tab.value = 'login'
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '注册失败')
  } finally { loading.value = false }
}

async function sendCode() {
  if (!regForm.value.email) { ElMessage.warning('请先输入邮箱'); return }
  try {
    await sendCodeApi({ email: regForm.value.email, type: 'register' })
    ElMessage.success('验证码已发送')
    startCooldown()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  }
}

async function sendResetCode() {
  if (!resetForm.value.email) { ElMessage.warning('请先输入邮箱'); return }
  try {
    await sendCodeApi({ email: resetForm.value.email, type: 'reset' })
    ElMessage.success('验证码已发送')
    startCooldown()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  }
}

async function doReset() {
  loading.value = true
  try {
    await resetPassword(resetForm.value)
    ElMessage.success('密码已重置，请登录')
    tab.value = 'login'
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重置失败')
  } finally { loading.value = false }
}
</script>

<style scoped>
.login-wrap { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
.login-card { width: 420px; }
.title { text-align: center; margin-bottom: 20px; color: #303133; }
.links { display: flex; justify-content: space-between; margin-top: 12px; }
</style>
