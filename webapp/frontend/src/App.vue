<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
onMounted(() => {
  // 启动时从后端刷新用户信息，保证 is_admin 等字段是最新的
  auth.fetchMe()
})
</script>