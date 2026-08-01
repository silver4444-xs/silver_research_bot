<template>
  <div class="ar-root">
    <!-- L1: Hero card -->
    <div class="ar-hero">
      <div class="ar-hero-main">
        <h2 class="ar-title">{{ analysis.title || '论文分析报告' }}</h2>
        <div v-if="analysis.venue" class="ar-venue">{{ analysis.venue }}</div>
        <div class="ar-meta-row">
          <span v-if="analysis.language" class="chip cd">{{ analysis.language === 'en' ? 'EN' : '中文' }}</span>
          <span v-if="analysis.page_count" class="chip cd">{{ analysis.page_count }} 页</span>
          <span v-if="analysis.formula_count" class="chip cd">{{ analysis.formula_count }} 个公式</span>
          <span v-if="analysis.figure_count" class="chip cd">{{ analysis.figure_count }} 张图表</span>
        </div>
        <div v-if="analysis.key_findings && analysis.key_findings.length" class="ar-findings">
          <div class="ar-findings-label">关键发现</div>
          <ul>
            <li v-for="(f, i) in analysis.key_findings" :key="i">{{ f }}</li>
          </ul>
        </div>
        <div class="ar-actions">
          <button v-if="hasArtifact('visualization')" class="btn ba bsm" @click="openArtifact('visualization')">📊 可视化</button>
          <button v-if="hasArtifact('citation_graph')" class="btn ba bsm" @click="openArtifact('citation_graph')">🔗 引文图谱</button>
          <button class="btn bg bsm" @click="$emit('export')">📥 导出</button>
          <button class="btn bg bsm" @click="$emit('askFollowUp')">💬 追问</button>
        </div>
      </div>
      <div v-if="analysis.scores && Object.keys(analysis.scores).length" class="ar-hero-score">
        <div class="ar-overall-score" :class="scoreColorClass(overallScore)">{{ overallScore }}</div>
        <div class="ar-overall-label">综合评分</div>
      </div>
    </div>

    <!-- L2: Dashboard chips — analysis status & actions only (metadata lives in Hero) -->
    <div class="ar-dashboard">
      <span class="ar-chip" :class="analysis.audit_passed ? 'ar-chip-ok' : 'ar-chip-warn'">
        {{ analysis.audit_passed ? '✅' : '⚠️' }} 审计{{ analysis.audit_passed ? '通过' : '未通过' }}
      </span>
      <span v-if="analysis.issue_count != null" class="ar-chip">📋 {{ analysis.issue_count }} 个待改进项</span>
      <span v-if="analysis.has_translation" class="ar-chip">🌐 已翻译</span>
      <span v-if="analysis.artifacts && analysis.artifacts.length" class="ar-chip">📦 {{ analysis.artifacts.length }} 个产物</span>
    </div>

    <!-- L3: Review scores (always expanded) -->
    <div v-if="analysis.scores && Object.keys(analysis.scores).length" class="ar-scores-box">
      <div class="ar-scores-title">📋 多维度评分</div>
      <ScoreBadge
        v-for="(score, key) in analysis.scores"
        :key="key"
        :label="scoreLabel(key)"
        :score="score"
      />
    </div>

    <!-- L4: Collapsible section cards -->
    <div v-if="streaming" class="ar-streaming-hint">
      <span class="ar-streaming-pulse"></span> 正在生成详细分析…
    </div>
    <div v-else-if="parsedCards.length" class="ar-sections">
      <CollapsibleCard
        v-for="card in parsedCards"
        :key="card.id"
        :title="card.title"
        :icon="card.icon"
        :preview="card.preview"
        :expanded="card.id === 'overview'"
      >
        <div v-html="card.html"></div>
      </CollapsibleCard>
    </div>
    <div v-else class="ar-sections ar-sections-empty">
      <div class="ar-empty-summary">
        <p>📋 分析报告已生成。查看上方评分和关键发现了解论文核心信息。</p>
        <p class="ar-empty-hint">详细分析内容将在对话完成后展示。</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import CollapsibleCard from './CollapsibleCard.vue'
import ScoreBadge from './ScoreBadge.vue'

const props = defineProps({
  analysis: { type: Object, required: true },
  content: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
})

defineEmits(['export', 'askFollowUp'])

const md = inject('markdown')

// Card section patterns — ordered by appearance in report
// Matches emoji headings AND Chinese numbered/descriptive headings
const CARD_PATTERNS = [
  { id: 'overview',     re: /📄\s*(?:论文总览|论文.*分析报告|论文基本信息)|论文总览|论文.*(?:分析报告|基本信息)/, icon: '📄' },
  { id: 'motivation',   re: /🧠\s*研究问题|研究问题与动机|一[、.].*(?:研究|动机|核心问题)|核心问题与研究动机/, icon: '🧠' },
  { id: 'system_model', re: /🏗️\s*系统模型|二[、.].*(?:系统|模型|架构)|系统模型(?:与|架构)/, icon: '🏗️' },
  { id: 'algorithm',    re: /🔬\s*.*算法|三[、.].*算法|算法设计/, icon: '🔬' },
  { id: 'experiment',   re: /📊\s*实验|四[、.].*实验|实验(?:设计|结果|评估)/, icon: '📊' },
  { id: 'review',       re: /📋\s*.*审稿|五[、.].*(?:审稿|维度|评价)|多维.*审稿|审稿评价/, icon: '📋' },
  { id: 'suggestions',  re: /💡\s*.*建议|📈\s*可视化|📋.*建议|六[、.].*(?:贡献|总结|建议|改进|未来)|研究贡献|主要贡献/, icon: '💡' },
  { id: 'audit',        re: /🔍\s*.*审计|质量审计|.*改进建议/, icon: '🔍' },
]

const overallScore = computed(() => {
  const scores = props.analysis.scores || {}
  const vals = Object.values(scores).filter(v => typeof v === 'number')
  if (vals.length === 0) return null
  return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length)
})

function scoreColorClass(s) {
  if (s == null) return ''
  if (s >= 8) return 'score-green'
  if (s >= 6) return 'score-amber'
  return 'score-red'
}

function scoreLabel(key) {
  const labels = { theory: '理论视角', engineering: '工程视角', domain: '领域视角', overall: '综合', overall_score: '综合' }
  return labels[key] || key
}

function hasArtifact(type) {
  return (props.analysis.artifacts || []).includes(type)
}

function openArtifact(type) {
  const paperId = props.analysis.paper_id
  if (paperId) {
    window.open('/api/paper/' + paperId + '/' + type, '_blank')
  }
}

// Split markdown content into card sections by heading boundaries
const parsedCards = computed(() => {
  const raw = props.content || ''
  if (!raw) return []

  // Find all # / ## / ### / #### headings with position
  const headingRe = /^(#{1,4})\s+([^\n]+)$/gm
  const headings = []
  let m
  while ((m = headingRe.exec(raw)) !== null) {
    const level = m[1].length
    headings.push({ index: m.index, level, text: m[2].trim() })
  }

  if (headings.length === 0) {
    // Fallback: entire content as one overview card
    const preview = raw.replace(/[#*`|>\-]/g, '').replace(/\s+/g, ' ').trim().slice(0, 80)
    return [{ id: 'overview', title: '分析报告', icon: '📄', preview, html: md.renderFormula(raw) }]
  }

  // Icon palette for unmatched sections (cycles when exhausted)
  const FALLBACK_ICONS = ['📌', '📝', '🔎', '📖', '💭', '📑']

  const cards = []
  let fallbackIdx = 0
  for (let i = 0; i < headings.length; i++) {
    const h = headings[i]
    const nextIdx = i + 1 < headings.length ? headings[i + 1].index : raw.length
    const sectionText = raw.slice(h.index, nextIdx).trim()
    if (!sectionText) continue

    let matched = null
    for (const pat of CARD_PATTERNS) {
      if (pat.re.test(h.text)) { matched = pat; break }
    }

    const bodyStart = sectionText.indexOf('\n')
    const body = bodyStart >= 0 ? sectionText.slice(bodyStart + 1).trim() : ''
    const preview = body.replace(/[#*`|>\-]/g, '').replace(/\s+/g, ' ').trim().slice(0, 80)

    if (matched) {
      cards.push({
        id: matched.id,
        title: h.text.replace(/^[📄🧠🏗️🎯🔬📊📋🔍💡📈]\s*/, '').replace(/^[一二三四五六七八九十]+[、.，]\s*/, '').trim(),
        icon: matched.icon,
        preview,
        html: md.renderFormula(sectionText),
      })
    } else {
      // Fallback: unmatched sections still get a card (prevents silent data loss)
      const cleanTitle = h.text.replace(/^[📄🧠🏗️🎯🔬📊📋🔍💡📈]\s*/, '').replace(/^[一二三四五六七八九十]+[、.，]\s*/, '').trim()
      cards.push({
        id: 'section_' + i,
        title: cleanTitle,
        icon: FALLBACK_ICONS[fallbackIdx % FALLBACK_ICONS.length],
        preview,
        html: md.renderFormula(sectionText),
      })
      fallbackIdx++
    }
  }
  return cards
})
</script>

<style scoped>
.ar-root {
  width: 100%; max-width: 100%;
  animation: ar-fadein 0.35s ease;
}

@keyframes ar-fadein {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

/* L1: Hero */
.ar-hero {
  display: flex; gap: var(--s-4);
  padding: var(--s-4);
  background: rgba(99, 102, 241, 0.06);
  border-left: 3px solid var(--c-accent);
  border-radius: var(--r-lg);
  margin-bottom: var(--s-3);
}
.ar-hero-main { flex: 1; min-width: 0; }
.ar-title {
  font-size: 16px; font-weight: 700; color: #fff; margin: 0 0 4px;
  font-family: var(--ff-heading); line-height: 1.4;
}
.ar-venue { font-size: 12px; color: var(--c-text-muted); margin-bottom: var(--s-2); }
.ar-meta-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: var(--s-3); }
.ar-findings {
  margin-bottom: var(--s-3); padding: var(--s-2) var(--s-3);
  background: rgba(0,0,0,0.15); border-radius: var(--r-md);
}
.ar-findings-label {
  font-size: 11px; font-weight: 600; color: var(--c-text-muted);
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;
}
.ar-findings ul {
  margin: 0; padding-left: var(--s-4); font-size: 13px; color: var(--c-text-secondary);
}
.ar-findings li { margin-bottom: 2px; }
.ar-actions { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.ar-hero-score {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  flex-shrink: 0; padding: var(--s-2) var(--s-3);
}
.ar-overall-score {
  font-size: 32px; font-weight: 700; font-family: var(--ff-mono); color: var(--c-accent);
}
.ar-overall-score.score-green { color: var(--c-success); }
.ar-overall-score.score-amber { color: var(--c-warn); }
.ar-overall-score.score-red { color: var(--c-danger); }
.ar-overall-label { font-size: 11px; color: var(--c-text-muted); margin-top: 2px; }

/* L2: Dashboard */
.ar-dashboard { display: flex; flex-wrap: wrap; gap: 8px; padding: var(--s-2) 0; margin-bottom: var(--s-3); }
.ar-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 12px; border-radius: 20px; font-size: 12px;
  background: rgba(99, 102, 241, 0.08); color: var(--c-text-secondary);
}
.ar-chip-ok { background: rgba(34, 197, 94, 0.08); color: var(--c-success); }
.ar-chip-warn { background: rgba(245, 158, 11, 0.08); color: var(--c-warn); }

/* L3: Scores */
.ar-scores-box {
  padding: var(--s-3) var(--s-4);
  background: rgba(99, 102, 241, 0.04);
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  margin-bottom: var(--s-3);
}
.ar-scores-title {
  font-size: 12px; font-weight: 600; color: var(--c-text-muted);
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: var(--s-2);
}

/* L4: Sections */
.ar-sections { display: flex; flex-direction: column; }

/* Streaming hint */
.ar-streaming-hint {
  display: flex; align-items: center; gap: var(--s-2);
  padding: var(--s-4); font-size: 13px; color: var(--c-text-muted);
  background: rgba(99, 102, 241, 0.03);
  border: 1px dashed var(--c-border);
  border-radius: var(--r-md);
  animation: ar-fadein 0.3s ease;
}
.ar-streaming-pulse {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--c-accent);
  animation: ar-pulse 1.2s ease-in-out infinite;
}
@keyframes ar-pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}

/* Empty summary fallback */
.ar-sections-empty {
  padding: var(--s-4);
  background: rgba(99, 102, 241, 0.03);
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
}
.ar-empty-summary p {
  margin: 0; font-size: 14px; color: var(--c-text-secondary); line-height: 1.6;
}
.ar-empty-hint {
  margin-top: var(--s-2) !important;
  font-size: 12px !important; color: var(--c-text-muted);
}

/* Responsive */
@media (max-width: 860px) {
  .ar-hero { flex-direction: column; }
  .ar-hero-score { flex-direction: row; gap: var(--s-2); }
  .ar-overall-score { font-size: 24px; }
}
</style>
