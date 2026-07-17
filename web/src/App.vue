<template>
  <div class="app">
    <Sidebar />
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { provide } from 'vue'
import Sidebar from './components/Sidebar.vue'
import { useApi } from './composables/useApi.js'
import { useI18n } from './composables/useI18n.js'
import { useMarkdown } from './composables/useMarkdown.js'
import { usePapers } from './composables/usePapers.js'
import { useCompare } from './composables/useCompare.js'

const { api } = useApi()
const i18n = useI18n()
const papers = usePapers()
const md = useMarkdown(papers.upText)
const compare = useCompare(papers.papers)

provide('api', { api })
provide('i18n', i18n)
provide('markdown', md)
provide('papers', papers)
provide('compare', compare)
</script>
