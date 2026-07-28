<template>
  <div class="score-badge" :class="colorClass">
    <span class="score-label">{{ label }}</span>
    <span class="score-bar-track">
      <span class="score-bar-fill" :style="{ width: pct + '%' }"></span>
    </span>
    <span class="score-value">{{ score }}/{{ maxScore }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  score: { type: Number, required: true },
  maxScore: { type: Number, default: 10 },
})

const pct = computed(() => Math.min(100, Math.round((props.score / props.maxScore) * 100)))

const colorClass = computed(() => {
  if (props.score >= 8) return 'score-green'
  if (props.score >= 6) return 'score-amber'
  return 'score-red'
})
</script>

<style scoped>
.score-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 4px 0;
}
.score-label {
  width: 60px;
  flex-shrink: 0;
  color: var(--c-text-muted);
  font-size: 11px;
}
.score-bar-track {
  flex: 1;
  height: 4px;
  background: rgba(255,255,255,0.06);
  border-radius: 2px;
  overflow: hidden;
}
.score-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.4s ease;
}
.score-value {
  width: 36px;
  text-align: right;
  font-weight: 600;
  font-size: 13px;
  font-family: var(--ff-mono);
}
.score-green .score-bar-fill { background: var(--c-success); }
.score-green .score-value { color: var(--c-success); }
.score-amber .score-bar-fill { background: var(--c-warn); }
.score-amber .score-value { color: var(--c-warn); }
.score-red .score-bar-fill { background: var(--c-danger); }
.score-red .score-value { color: var(--c-danger); }
</style>
