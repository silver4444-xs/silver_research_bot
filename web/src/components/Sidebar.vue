<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="logo">
        <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
      </div>
      <div>
        <h1>Silver Research</h1>
        <p>论文研读工作台</p>
      </div>
    </div>
    <nav class="nav">
      <router-link v-for="it in nav" :key="it.id" :to="it.to" class="navi">
        <svg viewBox="0 0 24 24" v-html="it.icon"></svg>
        {{ it.label }}
      </router-link>
    </nav>
    <div class="mini-card">
      <span class="chip cg">论文研读</span>
      <span class="chip cb2">四维分析</span>
      <span class="chip ca">公式解读</span>
      <span class="chip cp">可视化</span>
    </div>
  </aside>
</template>

<script setup>
const icons = {
  chat: '<path d="M20 2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4l4 4 4-4h4a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/>',
  layers: '<path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>',
}
const nav = [
  { id: 'agent', label: 'Agent 对话', icon: icons.chat, to: '/agent' },
  { id: 'papers', label: '论文研读', icon: icons.file, to: '/papers' },
  { id: 'rag', label: '文献 RAG', icon: icons.layers, to: '/rag' },
]
</script>

<style scoped>
.sidebar {
  background: rgba(10, 11, 31, 0.9);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border-right: 1px solid rgba(99, 102, 241, 0.08);
  color: var(--c-text);
  padding: var(--s-6) var(--s-4);
  display: flex;
  flex-direction: column;
  gap: var(--s-4);
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  z-index: 10;
  box-shadow: inset -1px 0 0 rgba(99, 102, 241, 0.03);
}
.brand {
  display: flex; align-items: center; gap: var(--s-3);
  padding-bottom: var(--s-5); margin-bottom: var(--s-2);
  border-bottom: 1px solid rgba(99, 102, 241, 0.08);
}
.logo {
  width: 42px; height: 42px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, var(--c-accent), var(--c-accent-2));
  border-radius: var(--r-md);
  box-shadow: 0 0 22px rgba(139, 92, 246, 0.45);
  flex-shrink: 0;
}
.logo svg { width: 24px; height: 24px; fill: #fff }
.brand h1 { font-size: 17px; margin: 0; color: #fff; font-family: var(--ff-body); font-weight: 700; letter-spacing: -0.02em }
.brand p { font-size: 10px; margin: 2px 0 0; color: rgba(168, 133, 250, 0.55); text-transform: uppercase; letter-spacing: .1em; font-weight: 500 }
.nav { display: flex; flex-direction: column; gap: 4px; margin-top: var(--s-2) }
.mini-card {
  background: rgba(99, 102, 241, 0.04); border-radius: var(--r-lg); padding: var(--s-3);
  display: flex; flex-wrap: wrap; gap: 6px;
  border: 1px solid rgba(99, 102, 241, 0.06); margin-top: auto;
}
</style>
