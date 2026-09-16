<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="logo" @click="$router.push('/')">墨造 · AI小说生成</div>
      <div class="right">
        <span v-if="auth.user" class="balance">余额 ¥{{ balance.toFixed(2) }}</span>
        <el-button v-if="auth.user" text @click="rechargeRef.open()">充值</el-button>
        <el-button v-if="auth.isAdmin" text @click="$router.push('/admin')">管理后台</el-button>
        <el-dropdown v-if="auth.user" @command="onCommand">
          <span class="username">{{ auth.user.username }}<el-icon><arrow-down /></el-icon></span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout">退出</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
    <RechargeDialog ref="rechargeRef" />
  </el-container>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { getBalance } from '../api/project'
import { ArrowDown } from '@element-plus/icons-vue'
import RechargeDialog from '../components/RechargeDialog.vue'

const auth = useAuthStore()
const router = useRouter()
const balance = ref(0)
const rechargeRef = ref(null)

onMounted(async () => {
  try { balance.value = (await getBalance()).balance } catch {}
})

function onCommand(cmd) {
  if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.layout { min-height: 100vh; }
.header { display: flex; align-items: center; justify-content: space-between; background: #fff; border-bottom: 1px solid #e4e7ed; padding: 0 24px; }
.logo { font-size: 18px; font-weight: 600; color: #303133; cursor: pointer; }
.right { display: flex; align-items: center; gap: 16px; }
.balance { font-size: 14px; color: #606266; }
.username { cursor: pointer; font-size: 14px; color: #303133; }
</style>
