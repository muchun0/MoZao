<template>
  <div class="login-stage">
    <div class="login-card">
      <div class="brand-seal">墨</div>
      <h1 class="login-title">墨造</h1>
      <p class="login-sub">AI 小 说 生 成</p>
      <el-tabs v-model="tab">
        <el-tab-pane label="登录" name="login">
          <el-form @submit.prevent="doLogin">
            <el-form-item>
              <el-input v-model="loginForm.username" placeholder="用户名或邮箱" />
            </el-form-item>
            <el-form-item>
              <el-input v-model="loginForm.password" type="password" placeholder="密码" show-password />
            </el-form-item>
            <el-button type="primary" native-type="submit" :loading="loading" class="btn-block">登录</el-button>
          </el-form>
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
            <el-button type="primary" native-type="submit" :loading="loading" class="btn-block">注册</el-button>
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
            <el-button type="primary" native-type="submit" :loading="loading" class="btn-block">重置密码</el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
      <p class="login-note">注册即送 <b>10 元</b> 体验金</p>
    </div>
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

const loginForm = ref({ username: '', password: '' })
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
.login-stage {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 20px;
  background:
    radial-gradient(1100px 520px at 18% -8%, rgba(60,76,130,.85) 0%, transparent 60%),
    radial-gradient(900px 500px at 85% 110%, rgba(201,162,39,.16) 0%, transparent 55%),
    linear-gradient(160deg, #25325c 0%, #1b2547 48%, #121a33 100%);
  position: relative;
  overflow: hidden;
}
.login-stage::before {
  content: "";
  position: absolute; inset: 0;
  background-image: radial-gradient(rgba(201,162,39,.16) 1px, transparent 1.6px);
  background-size: 30px 30px;
  opacity: .5;
}
.login-stage::after {
  content: "墨";
  position: absolute; right: -40px; bottom: -90px;
  font-family: var(--serif); font-size: 340px; line-height: 1;
  color: rgba(201,162,39,.07); pointer-events: none; transform: rotate(-8deg);
}
.login-card {
  position: relative; z-index: 2;
  width: min(430px, 94vw);
  background: var(--card);
  border: 1px solid rgba(201,162,39,.35);
  border-radius: 18px;
  box-shadow: 0 24px 60px rgba(0,0,0,.45), 0 0 0 1px rgba(201,162,39,.15) inset;
  padding: 38px 36px 30px;
}
.brand-seal {
  width: 56px; height: 56px; margin: 0 auto 12px;
  display: flex; align-items: center; justify-content: center;
  font-family: var(--serif); font-size: 30px; color: #f5efdc;
  background: linear-gradient(140deg, #c9a227, #8a6d1e);
  border-radius: 14px; box-shadow: 0 6px 18px rgba(201,162,39,.4);
}
.login-title {
  text-align: center;
  font-size: 30px;
  letter-spacing: 10px;
  color: var(--indigo-deep);
  text-indent: 10px;
  margin-bottom: 4px;
}
.login-sub {
  text-align: center;
  color: var(--muted);
  font-size: 13px;
  letter-spacing: 3px;
  margin-bottom: 20px;
}
.links { display: flex; justify-content: space-between; margin-top: 12px; }
.btn-block { width: 100%; }
.login-note {
  text-align: center;
  color: var(--muted);
  font-size: 12.5px;
  margin-top: 16px;
}
.login-note b { color: var(--gold); }
</style>
