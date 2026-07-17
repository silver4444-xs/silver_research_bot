<template>
  <header class="hero">
    <div>
      <p class="eyebrow">{{ t('eyebrow') }}</p>
      <h2>{{ t('title') }}</h2>
      <p class="subtitle">{{ t('subtitle') }}</p>
      <div class="row" style="gap:8px;margin-top:8px">
        <button class="btn bg bsm" @click="$emit('toggle-lang')">
          {{ lang === 'zh' ? 'EN' : '中文' }}
        </button>
      </div>
    </div>
    <div class="sbar">
      <div class="si"><strong>{{ t('api') }}</strong><span><span class="dot" :class="apiOk ? 'dot-ok' : 'dot-warn'"></span>{{ apiStatus }}</span></div>
      <div class="si"><strong>{{ t('analyzed') }}</strong><span>{{ paperCount }} 篇</span></div>
      <div class="si"><strong>公式</strong><span>{{ totalFormulas }} 个</span></div>
      <div class="si"><strong>图表</strong><span>{{ totalFigures }} 张</span></div>
      <button class="btn bp bsm" @click="$emit('refresh')">{{ t('refresh') }}</button>
    </div>
  </header>
</template>

<script setup>
defineProps({
  lang: { type: String, required: true },
  t: { type: Function, required: true },
  apiOk: { type: Boolean, required: true },
  apiStatus: { type: String, required: true },
  paperCount: { type: Number, required: true },
  totalFormulas: { type: Number, required: true },
  totalFigures: { type: Number, required: true },
})
defineEmits(['toggle-lang', 'refresh'])
</script>

<style scoped>
.hero { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--s-6); margin-bottom: var(--s-6); flex-wrap: wrap }
.eyebrow { text-transform: uppercase; font-size: 10px; letter-spacing: .14em; color: var(--c-text-muted); margin: 0 0 var(--s-1); font-weight: 600 }
.hero h2 { margin: 0 0 var(--s-2); font-size: 24px; background: linear-gradient(135deg, #E2E8F0, #A78BFA); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text }
.subtitle { font-size: 14px; color: var(--c-text-secondary); max-width: 540px; margin: 0 }
.sbar {
  display: flex; flex-wrap: wrap; gap: var(--s-3); align-items: center;
  background: var(--c-bg-glass); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
  border: 1px solid var(--c-border); border-radius: var(--r-lg);
  padding: var(--s-3) var(--s-4); box-shadow: var(--glow-sm);
}
.si { display: flex; flex-direction: column; gap: 2px }
.si strong { font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--c-text-muted) }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 4px }
.dot-ok { background: var(--c-success); box-shadow: 0 0 6px rgba(34, 197, 94, 0.5) }
.dot-warn { background: var(--c-danger); box-shadow: 0 0 6px rgba(239, 68, 68, 0.5) }
@media (max-width: 768px) { .hero { flex-direction: column } }
</style>
