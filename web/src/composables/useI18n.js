import { ref } from 'vue'

const LOCALE = {
  zh: {
    eyebrow: '论文分析平台',
    title: '研究论文深度分析工作台',
    subtitle: '上传 PDF 即可自动翻译、四维分析、公式解读与可视化。',
    api: 'API',
    analyzed: '已分析',
    refresh: '刷新',
  },
  en: {
    eyebrow: 'Paper Analysis Platform',
    title: 'Research Paper Analysis Workbench',
    subtitle: 'Upload PDF for auto translation, 4D analysis, formula explanation & visualization.',
    api: 'API',
    analyzed: 'Analyzed',
    refresh: 'Refresh',
  },
}

export function useI18n() {
  const lang = ref('zh')

  function t(k) {
    return (LOCALE[lang.value] || LOCALE.zh)[k] || k
  }

  return { lang, t, LOCALE }
}
