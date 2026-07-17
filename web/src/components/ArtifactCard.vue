<template>
  <div class="card">
    <div class="ch">
      <h3>{{ title }}</h3>
      <button class="btn bg bsm" @click="$emit('download')" :title="'下载' + title">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
      </button>
    </div>
    <slot />
  </div>
</template>

<script setup>
defineProps({ title: { type: String, required: true } })
defineEmits(['download'])
</script>
