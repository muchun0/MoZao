<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="logo" @click="$router.push('/')">
        <span class="seal">墨</span>
        墨造 · AI小说生成
      </div>
      <div class="right">
        <span v-if="auth.user" class="balance">余额 ¥{{ (balance || 0).toFixed(2) }}</span>
        <el-button v-if="auth.user" text @click="rechargeRef.open()">充值</el-button>
        <el-button v-if="auth.user" text @click="$router.push('/workspace')">工作台</el-button>
        <el-button v-if="auth.user" text @click="$router.push('/teardowns')">拆书库</el-button>
        <el-button v-if="auth.isAdmin" text @click="$router.push('/admin')">管理后台</el-button>
        <el-dropdown v-if="auth.user" @command="onCommand">
          <span class="username">{{ auth.user.username }}<el-icon><arrow-down /></el-icon></span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="workspace">工作台</el-dropdown-item>
              <el-dropdown-item command="llm">模型配置</el-dropdown-item>
              <el-dropdown-item command="logout">退出</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
    <el-footer class="footer">
      <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">冀ICP备2026038211号</a>
    </el-footer>
    <RechargeDialog ref="rechargeRef" />
    <LlmSettingsDialog ref="llmRef" />
  </el-container>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { getBalance } from '../api/project'
import { ArrowDown } from '@element-plus/icons-vue'
import RechargeDialog from '../components/RechargeDialog.vue'
import LlmSettingsDialog from '../components/LlmSettingsDialog.vue'

const auth = useAuthStore()
const router = useRouter()
const balance = ref(0)
const rechargeRef = ref(null)
const llmRef = ref(null)

onMounted(async () => {
  try {
    const r = await getBalance()
    balance.value = r.user?.balance ?? 0
  } catch {}
})

function onCommand(cmd) {
  if (cmd === 'workspace') { router.push('/workspace'); return; }
  if (cmd === 'llm') { llmRef.value.open(); return; }
  if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.layout { min-height: 100vh; flex-direction: column; }
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 60px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--serif);
  font-size: 20px;
  letter-spacing: 4px;
  color: #f0e2b6;
  cursor: pointer;
}
.seal {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  font-size: 15px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(140deg, #c9a227, #8a6d1e);
  color: #f5efdc;
  box-shadow: 0 3px 8px rgba(201,162,39,.4);
}
.right { display: flex; align-items: center; gap: 16px; }
.balance {
  font-weight: 700;
  color: #f0e2b6;
  font-size: 14px;
  padding: 5px 12px;
  background: rgba(0,0,0,.22);
  border: 1px solid rgba(201,162,39,.4);
  border-radius: 999px;
}
.footer {
  padding: 14px 0 20px;
  text-align: center;
  font-size: 12px;
}
.footer a {
  color: #9aa0a6;
  text-decoration: none;
}
.footer a:hover { color: #c9a227; }
.username {
  cursor: pointer;
  font-size: 14px;
  color: rgba(233,228,210,.75);
}
</style>
