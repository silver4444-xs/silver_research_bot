<template>
  <div class="card">
    <div class="ch">
      <h3>运行历史 <span v-if="runs.length" class="chip cs">{{ runs.length }}</span></h3>
      <div class="actions">
        <button class="btn ba bsm" :disabled="selectedIds.length < 2 || selectedIds.length > 4"
          @click="emit('compare', selectedIds)">
          对比选中（{{ selectedIds.length }}）
        </button>
        <button class="btn bs bsm" @click="emit('export', selectedIds)">导出</button>
        <button class="btn bg bsm" @click="emit('refresh')">刷新</button>
      </div>
    </div>

    <p v-if="!runs.length" class="empty">尚无留存 run。执行一次检索会自动留档。</p>
    <div v-else class="rlist">
      <div v-for="r in runs" :key="r.run_id" class="ri" :class="{ active: r.run_id === currentRunId }">
        <label class="pick">
          <input type="checkbox" :checked="selectedIds.includes(r.run_id)" @change="togglePick(r.run_id)" />
        </label>
        <button class="ri-main" @click="emit('load', r.run_id)">
          <strong>{{ r.label || r.run_id }}</strong>
          <span class="ri-sub">
            <span class="chip cs">{{ r.result_count }} 条</span>
            <span v-if="r.metrics && r.metrics.p_at_k != null" class="chip cp">P@K {{ r.metrics.p_at_k.toFixed(2) }}</span>
            <span class="dim">{{ (r.created_at || '').slice(0, 16).replace('T', ' ') }}</span>
          </span>
          <span class="ri-query">{{ r.query }}</span>
        </button>
        <div class="ri-ops">
          <button class="btn bg bsm" @click="rename(r)">改名</button>
          <button class="btn bg bsm" style="color: var(--c-danger)" @click="emit('delete', r.run_id)">删</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  runs: { type: Array, default: () => [] },
  currentRunId: { type: String, default: null },
})
const emit = defineEmits(['load', 'rename', 'delete', 'compare', 'export', 'refresh'])

const selectedIds = ref([])

function togglePick(id) {
  const i = selectedIds.value.indexOf(id)
  if (i >= 0) selectedIds.value = selectedIds.value.filter((x) => x !== id)
  else if (selectedIds.value.length < 4) selectedIds.value = selectedIds.value.concat(id)
}

function rename(r) {
  const label = prompt('run 名称', r.label || r.run_id)
  if (label == null) return
  const tags = prompt('标签（逗号分隔）', (r.tags || []).join(','))
  emit('rename', r.run_id, label.trim(), (tags == null ? [] : tags.split(',').map((s) => s.trim()).filter(Boolean)))
}
</script>

<style scoped>
.actions { display: flex; gap: var(--s-2) }
.rlist { display: flex; flex-direction: column; gap: var(--s-2) }
.ri { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-bg-card) }
.ri.active { border-color: var(--c-border-active); background: rgba(99,102,241,0.08) }
.pick { margin: 0; display: flex; align-items: center }
.ri-main { flex: 1; min-width: 0; text-align: left; background: none; border: none; cursor: pointer; padding: 0 }
.ri-main strong { display: block; font-size: 13px; color: #fff }
.ri-sub { display: flex; gap: 6px; align-items: center; margin-top: 2px; font-size: 11px }
.dim { color: var(--c-text-muted) }
.ri-query { display: block; font-size: 11px; color: var(--c-text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px }
.ri-ops { display: flex; gap: var(--s-1); flex-shrink: 0 }
.empty { color: var(--c-text-muted); font-style: italic; text-align: center; padding: var(--s-6) }
</style>
