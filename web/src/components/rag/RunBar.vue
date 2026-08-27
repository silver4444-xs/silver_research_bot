<template>
  <div class="card">
    <div class="runbar">
      <input
        class="runbar-q"
        v-model="q"
        placeholder="输入检索 query…"
        @keyup.enter="emit('search', q)"
      />
      <button class="btn bp" :disabled="busy || !q.trim()" @click="emit('search', q)">
        {{ loading.search ? '检索中…' : '检索' }}
      </button>
      <button class="btn bs" :disabled="busy || !q.trim()" @click="emit('ask', q)">
        {{ loading.ask ? '问答中…' : '问答' }}
      </button>

      <span v-if="currentRun" class="chip cs runbar-id" :title="currentRun.run_id">
        {{ currentRun.label || currentRun.run_id }}
      </span>
      <span v-else-if="loading.save" class="chip cs runbar-id">留档中…</span>
      <span v-else class="chip cp runbar-id">未留档</span>

      <span class="runbar-spacer"></span>
      <button class="btn ba bsm" @click="emit('compare')">对比</button>
      <button class="btn bs bsm" @click="emit('export')">导出</button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  loading: { type: Object, default: () => ({}) },
  currentRun: { type: Object, default: null },
})
const emit = defineEmits(['search', 'ask', 'compare', 'export'])

const q = ref('retrieval augmented')
const busy = () => props.loading.search || props.loading.ask
</script>

<style scoped>
.runbar { display: flex; gap: var(--s-2); align-items: center; flex-wrap: wrap }
.runbar-q { flex: 1; min-width: 220px; margin: 0 }
.runbar-id { font-size: 11px; max-width: 200px; overflow: hidden; text-overflow: ellipsis }
.runbar-spacer { flex: 1 }
</style>
