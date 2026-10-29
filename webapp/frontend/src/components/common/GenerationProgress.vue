<template>
  <!-- 全项目唯一的进度指示组件：顶部横幅 + 进度条（修复双进度条）。
       错误态与进行态共用同一容器，阶段切换/进度/文案三者由 useGenerationProgress 统一驱动。 -->
  <div v-if="visible" class="gen-banner" :class="{ 'is-error': isError }">
    <!-- 出错：仅显示错误信息 + 操作位（继续生成/关闭由父级 slot 提供） -->
    <div v-if="isError" class="gen-error-wrap">
      <span class="gen-error">{{ errorText }}</span>
      <slot name="actions" />
    </div>
    <!-- 运行中：脉冲点 + 阶段文案 + 百分比 + 进度条 + 子进度/已用时间 -->
    <template v-else>
      <div class="gen-banner-top">
        <span class="pulse" />
        <span class="gen-banner-status">{{ statusText }}</span>
        <span v-if="chapterText" class="gen-banner-ch">{{ chapterText }}</span>
        <span class="gen-banner-pct">{{ percent }}%</span>
        <slot name="actions" />
      </div>
      <el-progress
        :percentage="percent"
        :stroke-width="6"
        :show-text="false"
        :indeterminate="false"
        :duration="2"
      />
      <div class="gen-banner-meta">
        <span>已用 {{ elapsedText }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
// 仅渲染：所有状态来自 useGenerationProgress 单例 store
import {
  isVisible, percent, statusText, chapterText, elapsedText,
  errorText, isError,
} from '../../composables/useGenerationProgress'
defineProps({
  // 预留：未来可注入自定义顶部样式，当前无外部 prop 依赖
})
const visible = isVisible
</script>

<style scoped>
/* ===== 生成中横幅：全宽，仅生成/出错时出现 ===== */
.gen-banner {
  margin-bottom: 16px;
  padding: 10px 16px;
  background: rgba(201, 162, 39, .06);
  border: 1px solid rgba(201, 162, 39, .25);
  border-radius: 8px;
}
.gen-banner.is-error { border-color: #fbc4c4; background: #fef7f5; }
.gen-banner-top { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.gen-banner-status { flex: 1; font-size: 13px; color: var(--ink-soft); }
.gen-banner-ch { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.gen-banner-pct { font-size: 12px; color: var(--gold-deep); font-variant-numeric: tabular-nums; font-weight: 500; }
.gen-banner-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.pulse { width: 6px; height: 6px; border-radius: 50%; background: var(--gold); animation: gp-pulse 1.4s ease-in-out infinite; flex-shrink: 0; }
@keyframes gp-pulse {
  0%, 100% { opacity: .35; transform: scale(.85); }
  50% { opacity: 1; transform: scale(1.15); }
}
.gen-error { display: inline-block; font-size: 13px; color: #f56c6c; word-break: break-all; flex: 1; }
.gen-error-wrap { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

/* 窄屏：横幅内元素换行，进度条仍占满宽度 */
@media (max-width: 640px) {
  .gen-banner-top { flex-wrap: wrap; }
  .gen-banner-status { flex-basis: 100%; order: 1; }
  .pulse { order: 0; }
  .gen-banner-pct { order: 2; margin-left: auto; }
  .gen-banner-ch { order: 3; flex-basis: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .pulse { animation: none; }
}
</style>
