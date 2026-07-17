<template>
  <HeroHeader
    :lang="lang" :t="t" :apiOk="apiOk" :apiStatus="apiStatus"
    :paperCount="papers.length" :totalFormulas="totalFormulas" :totalFigures="totalFigures"
    @toggle-lang="lang = lang === 'zh' ? 'en' : 'zh'"
    @refresh="loadAll"
  />
</template>

<script setup>
import { inject } from 'vue'
import HeroHeader from '../components/HeroHeader.vue'

const { lang, t } = inject('i18n')
const { papers, loadAll, apiOk, apiStatus, totalFormulas, totalFigures } = inject('papers')
</script>
