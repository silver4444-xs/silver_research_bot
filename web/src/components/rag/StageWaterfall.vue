<template>
  <div class="ptrack">
    <div v-if="!stages.length" class="empty">尚未检索。</div>
    <div v-else class="waterfall">
      <div v-for="s in stages" :key="s.name" class="pts stage" :class="s.status">
        <div class="stage-name">{{ s.name }}</div>
        <div class="stage-counts">{{ s.in }}→{{ s.out }}</div>
        <div class="stage-ms">{{ fmtMs(s.ms) }}</div>
        <div class="stage-status">
          <span v-if="s.status === 'ok'" class="ok">ok</span>
          <span v-else-if="s.status === 'skipped'" class="skip" :title="s.reason">跳过</span>
          <span v-else class="fail" :title="s.reason">失败</span>
        </div>
        <div v-if="s.status !== 'ok' && s.reason" class="stage-reason">{{ s.reason }}</div>
      </div>
    </div>
    <div v-if="timings && timings.total != null" class="total">
      总耗时 {{ fmtMs(timings.total) }}
    </div>
  </div>
</template>

<script setup>
import { fmtMs } from '../../composables/useRagLab.js'

defineProps({
  stages: { type: Array, default: () => [] },
  timings: { type: Object, default: () => ({}) },
})
</script>

<style scoped>
.waterfall { display: flex; gap: var(--s-2); flex-wrap: wrap; margin-bottom: var(--s-2) }
.stage { flex: 1; min-width: 120px; flex-direction: column; align-items: flex-start }
.stage-name { font-family: var(--ff-mono); font-weight: 600; color: var(--c-text) }
.stage-counts { font-size: 11px; color: var(--c-text-secondary) }
.stage-ms { font-size: 11px; color: var(--c-text-muted) }
.stage-status { font-size: 10px; font-weight: 600 }
.stage-status .ok { color: var(--c-success) }
.stage-status .skip { color: var(--c-warn) }
.stage-status .fail { color: var(--c-danger) }
.stage-reason { font-size: 10px; color: var(--c-text-muted); line-height: 1.3; margin-top: 2px }
.total { font-size: 11px; color: var(--c-text-secondary) }
.empty { padding: 0 }
</style>
