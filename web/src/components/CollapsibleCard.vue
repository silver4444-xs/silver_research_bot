<template>
  <div class="cc-card" :class="{ expanded: isOpen }">
    <div class="cc-header" @click="toggle">
      <span class="cc-arrow">{{ isOpen ? '▼' : '▶' }}</span>
      <span class="cc-icon">{{ icon }}</span>
      <span class="cc-title">{{ title }}</span>
      <span v-if="!isOpen && preview" class="cc-preview">{{ preview }}</span>
    </div>
    <div v-if="isOpen" class="cc-body">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  icon: { type: String, default: '📄' },
  preview: { type: String, default: '' },
  expanded: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])

const isOpen = ref(props.expanded)

watch(() => props.expanded, (v) => { isOpen.value = v })

function toggle() {
  isOpen.value = !isOpen.value
  emit('toggle', isOpen.value)
}
</script>

<style scoped>
.cc-card {
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  margin-bottom: var(--s-2);
  background: rgba(0, 0, 0, 0.18);
  transition: border-color var(--t-fast), background var(--t-fast);
}
.cc-card:hover {
  border-color: rgba(99, 102, 241, 0.2);
}
.cc-card.expanded {
  border-color: var(--c-border-active);
  background: rgba(99, 102, 241, 0.04);
}
.cc-header {
  display: flex;
  align-items: center;
  gap: var(--s-2);
  padding: var(--s-3) var(--s-4);
  cursor: pointer;
  user-select: none;
  transition: background var(--t-fast);
}
.cc-header:hover {
  background: rgba(99, 102, 241, 0.04);
}
.cc-arrow {
  font-size: 10px;
  color: var(--c-text-muted);
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  transition: transform var(--t-fast);
}
.cc-icon { flex-shrink: 0; font-size: 14px; }
.cc-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--c-text);
  white-space: nowrap;
}
.cc-preview {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--c-text-muted);
  margin-left: var(--s-2);
}
.cc-body {
  padding: 0 var(--s-4) var(--s-4);
  font-size: 14px;
  line-height: 1.7;
  animation: cc-fadein 0.2s ease;
}
.cc-body :deep(h2), .cc-body :deep(h3), .cc-body :deep(h4) {
  margin: 0.6em 0 0.3em;
  line-height: 1.3;
}
.cc-body :deep(table) {
  border-collapse: collapse; margin: var(--s-2) 0; font-size: 13px;
}
.cc-body :deep(th), .cc-body :deep(td) {
  border: 1px solid var(--c-border); padding: 4px 10px; text-align: left;
}
.cc-body :deep(pre) {
  background: rgba(0,0,0,0.25); padding: var(--s-3);
  border-radius: var(--r-md); overflow-x: auto; font-size: 13px; margin: var(--s-2) 0;
}
.cc-body :deep(code) {
  font-family: var(--ff-mono); font-size: 13px;
  background: rgba(0,0,0,0.2); padding: 2px 6px; border-radius: 4px;
}
.cc-body :deep(pre code) { background: none; padding: 0; }
.cc-body :deep(a) { color: var(--c-accent); text-decoration: underline; }
.cc-body :deep(blockquote) {
  border-left: 3px solid var(--c-accent); padding-left: var(--s-3);
  margin: var(--s-2) 0; color: var(--c-text-muted);
}
@keyframes cc-fadein {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 480px) {
  .cc-header { padding: var(--s-2) var(--s-3); }
  .cc-body { padding: 0 var(--s-3) var(--s-3); }
  .cc-preview { display: none; }
}
</style>
