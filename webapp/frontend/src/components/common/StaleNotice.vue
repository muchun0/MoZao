<template>
  <!-- R13：同一套「记录可能过期」提示，体检页与生成过程卡共用，文案全部来自后端 -->
  <div v-if="notice" class="stale-notice" :class="`is-${notice.level || 'warning'}`">
    <div class="stale-head">
      <span class="stale-badge">{{ notice.title }}</span>
    </div>
    <p class="stale-text">{{ notice.text }}</p>
    <p v-if="notice.advice" class="stale-advice">{{ notice.advice }}</p>
    <p v-if="notice.global_note" class="stale-extra">{{ notice.global_note }}</p>
  </div>
</template>

<script setup>
defineProps({
  notice: { type: Object, default: null },
})
</script>

<style scoped>
.stale-notice {
  margin: 8px 0 10px;
  padding: 10px 12px;
  border-radius: 6px;
  border: 1px solid #f3d19e;
  background: #fdf6ec;
  font-size: 13px;
  line-height: 1.7;
  color: #6b4a12;
}
.stale-notice.is-info {
  border-color: #c6e2ff;
  background: #f2f8fd;
  color: #33546e;
}
.stale-head {
  margin-bottom: 2px;
}
.stale-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-weight: 600;
  font-size: 12px;
  color: #a0521b;
  background: #fae4c2;
}
.is-info .stale-badge {
  color: #2c6a9c;
  background: #dcecfb;
}
.stale-text {
  margin: 4px 0 0;
}
.stale-advice {
  margin: 4px 0 0;
  font-weight: 600;
}
.stale-extra {
  margin: 4px 0 0;
  opacity: 0.85;
}
</style>
