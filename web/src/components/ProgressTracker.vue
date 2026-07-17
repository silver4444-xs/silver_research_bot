<template>
  <div class="ptrack">
    <div class="pt-head">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16" class="spin">
        <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
      </svg>
      {{ message }}
    </div>
    <div class="pt-stages">
      <div v-for="s in stages" :key="s.id" :class="['pts', s.s]">
        <div class="ptd">
          <svg v-if="s.s === 'done'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="14" height="14">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          <div v-else-if="s.s === 'active'" class="pts-spin"></div>
          <div v-else class="ptd-empty"></div>
        </div>
        <span>{{ s.label }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  message: { type: String, default: '' },
  stages: { type: Array, required: true },
})
</script>
