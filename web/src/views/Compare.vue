<template>
  <div class="cmp-area">
    <div class="card">
      <div class="ch"><h3>横向对比工作台 <span style="font-size:12px;color:var(--c-text-muted);font-weight:400">— 已选 {{ cmpIds.length }} 篇</span></h3></div>
      <div class="cmp-pick-grid">
        <div v-for="p in cmpReadyPapers" :key="p.paper_id"
          :class="['cmp-pick-card', { sel: cmpIds.includes(p.paper_id) }]"
          :style="cmpIds.includes(p.paper_id) ? '--clr:' + PAPER_COLORS[cmpIds.indexOf(p.paper_id) % 12] : ''"
          @click="toggleCmpPaper(p.paper_id)">
          <div class="cmp-pick-chk">{{ cmpIds.includes(p.paper_id) ? '✓' : '' }}</div>
          <div class="cmp-pick-info">
            <div class="cmp-pick-title">{{ p.title?.slice(0, 70) || p.paper_id }}</div>
            <div class="cmp-pick-meta">{{ p.page_count || '?' }} 页 &middot; {{ p.formula_count || '?' }} 公式 &middot; {{ p.status === 'completed' ? '已完成' : '分析中' }}</div>
          </div>
        </div>
      </div>
      <p v-if="!cmpReadyPapers.length" class="empty">暂无已完成分析的论文，请先上传并分析论文。</p>
      <div class="row" style="gap:8px;margin-top:var(--s-3)">
        <button class="btn ba" :disabled="cmpIds.length < 2" @click="doCompare">{{ comparing ? '对比中…' : '开始对比（已选' + cmpIds.length + '篇）' }}</button>
        <button v-if="cmpIds.length" class="btn bg bsm" @click="cmpIds = []">清空选择</button>
        <button class="btn bs bsm" @click="loadCompareHistory" style="margin-left:auto">历史对比</button>
      </div>
      <p v-if="cmpError" class="empty" style="color:var(--c-danger)">{{ cmpError }}</p>
    </div>

    <!-- History list -->
    <div class="card" v-if="showCmpHistory">
      <div class="ch"><h3>历史对比列表 <button class="btn bg bsm" @click="showCmpHistory = false" style="float:right">×</button></h3></div>
      <div v-if="!cmpHistory.length" class="empty">暂无历史对比记录。</div>
      <article v-for="h in cmpHistory" :key="h.id" class="ri cmp-hist-row">
        <div><strong>{{ h.created_at?.slice(0, 19) }}</strong><p>{{ h.paper_count }} 篇论文</p></div>
        <div style="text-align:right"><button class="btn bp bsm" @click="loadCompareResult(h.id)">查看</button><button class="btn bg bsm" style="color:var(--c-danger);margin-left:4px" @click="deleteCompareResult(h.id)">删除</button></div>
      </article>
    </div>

    <!-- Comparison result -->
    <template v-if="cmpResult">
      <!-- Dashboard cards -->
      <div class="cmp-dash">
        <div class="cmp-pcard" v-for="(p, i) in cmpPapers" :key="p.paper_id" :style="'--clr:' + PAPER_COLORS[i]">
          <div class="cmp-pclr"></div>
          <div class="cmp-ptitle">{{ p.title?.slice(0, 60) || p.paper_id }}</div>
          <div class="cmp-pmeta">页数: {{ p.page_count || '?' }} | 公式: {{ p.formula_count || '?' }}</div>
          <div class="cmp-pscore" v-if="cmpStructured?.scores?.[p.paper_id]">综合: {{ cmpAvgScore(p.paper_id)?.toFixed(1) ?? '—' }}/10</div>
        </div>
      </div>

      <!-- Tab bar -->
      <div class="card card-tabs">
        <div class="ctabs">
          <button v-for="t in cmpTabs" :key="t.id" :class="['ctab', { on: cmpTab === t.id }]" @click="switchCmpTab(t.id)">{{ t.label }}</button>
        </div>

        <!-- Overview -->
        <div v-if="cmpTab === 'overview'" class="cmp-overview">
          <div class="cmp-ov-row" v-if="cmpStructured?.similarity_matrix?.length">
            <strong>论文相似度矩阵</strong>
            <div class="cmp-mini-heat" v-html="cmpMiniHeatmap()"></div>
          </div>
          <div class="cmp-ov-row" v-if="cmpStructured?.formula_overlap && Object.keys(cmpStructured.formula_overlap).length">
            <strong>公式重叠度</strong>
            <table class="cmp-tbl">
              <thead><tr><th>论文对</th><th>Jaccard 相似度</th></tr></thead>
              <tbody><tr v-for="(v, k) in cmpStructured.formula_overlap" :key="k"><td>{{ cmpFormatPair(k) }}</td><td>{{ (v * 100).toFixed(1) }}%</td></tr></tbody>
            </table>
          </div>
        </div>

        <!-- Dimensions matrix -->
        <div v-if="cmpTab === 'dimensions'" class="cmp-matrix">
          <div class="cmp-dim-filter">
            <label>维度筛选: </label>
            <select v-model="cmpDimFilter">
              <option value="all">全部维度</option>
              <option v-for="d in cmpDimNames" :key="d" :value="d">{{ d }}</option>
            </select>
          </div>
          <table class="cmp-tbl cmp-mtbl">
            <thead>
              <tr>
                <th>维度</th>
                <th v-for="(p, i) in cmpPapers" :key="p.paper_id" :style="'color:' + PAPER_COLORS[i]">{{ p.title?.slice(0, 20) }}</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="d in filteredCmpDims" :key="d">
                <tr @click="cmpExpandedDim = cmpExpandedDim === d ? null : d" style="cursor:pointer" :style="cmpExpandedDim === d ? 'background:rgba(255,255,255,0.04)' : ''">
                  <td><strong>{{ d }}</strong> <span style="font-size:10px;color:var(--c-text-muted)">{{ cmpExpandedDim === d ? '▲' : '▶' }}</span></td>
                  <td v-for="(p, i) in cmpPapers" :key="p.paper_id">
                    <div class="cmp-score-badge" :style="'--clr:' + PAPER_COLORS[i]">{{ (cmpStructured?.scores?.[p.paper_id]?.[d] || '-') }}</div>
                  </td>
                </tr>
                <tr v-if="cmpExpandedDim === d">
                  <td :colspan="cmpPapers.length + 1" style="padding:16px 12px;background:rgba(255,255,255,0.02)">
                    <div v-for="(p, i) in cmpPapers" :key="p.paper_id" style="margin-bottom:12px">
                      <div :style="'color:' + PAPER_COLORS[i] + ';font-weight:600;margin-bottom:4px'">{{ p.title?.slice(0, 50) || p.paper_id }}</div>
                      <div v-if="cmpStructured?.dimensions?.[d]?.score_reasons?.[p.paper_id]" style="font-size:13px;color:var(--c-text-secondary);margin-bottom:8px;padding:6px 10px;background:rgba(255,255,255,0.03);border-radius:4px;border-left:3px solid" :style="'border-left-color:' + PAPER_COLORS[i]">
                        <strong>评分依据:</strong> {{ cmpStructured.dimensions[d].score_reasons[p.paper_id] }}
                      </div>
                      <div v-if="cmpStructured?.dimensions?.[d]?.extracted_items?.[p.paper_id]?.length">
                        <div style="font-size:12px;color:var(--c-text-muted);margin-bottom:4px">关键发现:</div>
                        <div v-for="(it, k) in cmpStructured.dimensions[d].extracted_items[p.paper_id]" :key="k" style="margin-bottom:6px;padding:6px 10px;background:rgba(255,255,255,0.03);border-radius:4px">
                          <div style="color:var(--c-accent);font-size:13px">{{ it.name }}</div>
                          <div style="font-size:12px;color:var(--c-text-secondary)">{{ it.description }}</div>
                          <div v-if="it.comparative_note" style="font-size:11px;color:var(--c-warning);margin-top:2px"><em>跨论文对比:</em> {{ it.comparative_note }}</div>
                        </div>
                      </div>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>

        <!-- Charts -->
        <div v-if="cmpTab === 'charts'" class="cmp-charts">
          <div class="cmp-chart-box" id="cmp-radar-container">
            <strong>维度评分雷达图</strong>
            <div class="cmp-cv" ref="cmpRadarCv"></div>
          </div>
          <div class="cmp-chart-box" id="cmp-heatmap-container">
            <strong>相似度热力图</strong>
            <div class="cmp-cv" ref="cmpHeatCv"></div>
          </div>
          <div class="cmp-chart-box" id="cmp-bars-container">
            <strong>维度评分柱状图</strong>
            <div class="cmp-cv" ref="cmpBarsCv"></div>
          </div>
          <div class="cmp-chart-box" id="cmp-stack-container">
            <strong>评分堆叠图</strong>
            <div class="cmp-cv" ref="cmpStackCv"></div>
          </div>
        </div>

        <!-- Gap Analysis -->
        <div v-if="cmpTab === 'gap'" class="cmp-gap">
          <div v-if="cmpPairwiseGaps.length" style="margin-bottom:20px">
            <h4 style="margin-bottom:8px">最大差异维度</h4>
            <div v-for="(pair, k) in cmpPairwiseGaps" :key="k" style="margin-bottom:10px;padding:10px;background:rgba(255,255,255,0.03);border-radius:6px">
              <div style="font-size:13px;color:var(--c-text-muted);margin-bottom:6px">{{ cmpPaperTitle(pair.pidA) }} <span style="color:var(--c-text-secondary)">vs</span> {{ cmpPaperTitle(pair.pidB) }}</div>
              <div v-for="g in pair.topGaps" :key="g.dim" style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:13px">
                <span style="min-width:80px;color:var(--c-accent)">{{ g.dim }}</span>
                <span :style="'color:' + PAPER_COLORS[cmpPapers.findIndex(x => x.paper_id === g.pidA)]">{{ g.scoreA }}</span>
                <span style="font-size:11px;color:var(--c-text-muted)">Δ{{ g.delta.toFixed(1) }}</span>
                <span :style="'color:' + PAPER_COLORS[cmpPapers.findIndex(x => x.paper_id === g.pidB)]">{{ g.scoreB }}</span>
              </div>
            </div>
          </div>
          <div style="margin-bottom:16px">
            <h4 style="margin-bottom:8px">各维度差异详情</h4>
            <div v-for="(dim, dn) in cmpStructured?.dimensions || {}" :key="dn" style="margin-bottom:14px;padding:12px;background:rgba(255,255,255,0.02);border-radius:6px;border-left:3px solid var(--c-accent)">
              <div style="font-weight:600;margin-bottom:8px;color:var(--c-accent)">{{ dn }}</div>
              <div v-for="(p, i) in cmpPapers" :key="p.paper_id" style="margin-bottom:8px;padding:8px;background:rgba(255,255,255,0.02);border-radius:4px">
                <div :style="'color:' + PAPER_COLORS[i] + ';font-weight:600;font-size:13px'">
                  {{ p.title?.slice(0, 50) || p.paper_id }}
                  <span class="cmp-score-badge" :style="'--clr:' + PAPER_COLORS[i] + ';margin-left:6px'">{{ dim.extracted_scores?.[p.paper_id] ?? '—' }}</span>
                </div>
                <div v-if="dim.score_reasons?.[p.paper_id]" style="font-size:12px;color:var(--c-text-secondary);margin:4px 0">{{ dim.score_reasons[p.paper_id] }}</div>
                <div v-if="dim.extracted_items?.[p.paper_id]?.length">
                  <div v-for="(it, k) in dim.extracted_items[p.paper_id]" :key="k" style="margin-top:4px;padding:4px 8px;background:rgba(255,255,255,0.03);border-radius:3px;font-size:12px">
                    <span style="color:var(--c-accent)">{{ it.name }}</span>
                    <span v-if="it.comparative_note" style="color:var(--c-warning);margin-left:6px;font-size:11px">↔ {{ it.comparative_note }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <p v-if="cmpResult.structured?.error" class="empty" style="color:var(--c-warning)">注意: 本次对比在降级模式下完成 — {{ cmpResult.structured.error }}</p>

        <!-- Synthesis -->
        <div v-if="cmpTab === 'synthesis'" class="md" v-html="md.renderMd(cmpResult.synthesis || '综合分析生成中…')"></div>
      </div>

      <!-- Bottom bar -->
      <div class="card" v-if="cmpResult" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <button class="btn bs bsm" @click="exportCompareMD">导出 Markdown</button>
        <button class="btn bs bsm" @click="exportCompareCSV">导出 CSV</button>
        <button class="btn bs bsm" @click="exportCompareHTML">导出 HTML</button>
        <span style="font-size:12px;color:var(--c-text-secondary);margin-left:auto">对比 ID: {{ cmpResult.structured?.created_at || '-' }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { inject } from 'vue'

const papersApi = inject('papers')
const cmp = inject('compare')
const md = inject('markdown')

const { cmpReadyPapers } = papersApi

const {
  cmpIds, cmpResult, cmpStructured, cmpTab, cmpTabs, cmpPapers,
  comparing, cmpError,
  cmpHistory, showCmpHistory, cmpDimFilter, cmpDimNames, cmpExpandedDim,
  cmpRadarCv, cmpHeatCv, cmpBarsCv, cmpStackCv,
  filteredCmpDims, cmpPairwiseGaps, PAPER_COLORS,
  toggleCmpPaper, cmpAvgScore, cmpPaperTitle, cmpFormatPair,
  switchCmpTab, cmpMiniHeatmap,
  doCompare, loadCompareHistory, loadCompareResult, deleteCompareResult,
  exportCompareMD, exportCompareCSV, exportCompareHTML,
} = cmp
</script>
