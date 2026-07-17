import { ref, computed, nextTick } from 'vue'
import { useApi } from './useApi.js'

const PAPER_COLORS = ['#a78bfa', '#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#fb923c', '#94a3b8', '#f87171', '#4ade80', '#c084fc', '#38bdf8', '#a3e635']
const DIM_COLORS = ['#a78bfa', '#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#fb923c', '#94a3b8', '#f87171']

export function useCompare(papers) {
  const { api } = useApi()

  // === State ===
  const cmpIds = ref([])
  const cmpResult = ref(null)
  const comparing = ref(false)
  const cmpError = ref('')
  const cmpTab = ref('overview')
  const cmpTabs = [{ id: 'overview', label: '对比仪表板' }, { id: 'dimensions', label: '多维对比' }, { id: 'charts', label: '可视化图表' }, { id: 'gap', label: '差异分析' }, { id: 'synthesis', label: '综合分析' }]
  const cmpHistory = ref([])
  const showCmpHistory = ref(false)
  const cmpStructured = ref(null)
  const cmpDimFilter = ref('all')
  const cmpDimNames = ref([])
  const cmpPapers = ref([])
  const cmpExpandedDim = ref(null)
  const cmpRadarCv = ref(null)
  const cmpHeatCv = ref(null)
  const cmpBarsCv = ref(null)
  const cmpStackCv = ref(null)

  // === Computed ===
  const cmpReadyPapers = computed(() => papers.value.filter(p => p.status === 'completed'))
  const filteredCmpDims = computed(() => cmpDimFilter.value === 'all' ? cmpDimNames.value : [cmpDimFilter.value])
  const cmpPairwiseGaps = computed(() => {
    const dims = cmpStructured.value?.dimensions; const pids = cmpStructured.value?.paper_ids || []
    if (!dims || pids.length < 2) return []
    const gaps = []
    for (let i = 0; i < pids.length; i++) {
      for (let j = i + 1; j < pids.length; j++) {
        const pairGaps = []
        for (const [dn, dim] of Object.entries(dims)) {
          const sA = dim.extracted_scores?.[pids[i]], sB = dim.extracted_scores?.[pids[j]]
          if (sA != null && sB != null) pairGaps.push({ dim: dn, delta: Math.abs(sA - sB), scoreA: sA, scoreB: sB, pidA: pids[i], pidB: pids[j] })
        }
        pairGaps.sort((a, b) => b.delta - a.delta)
        gaps.push({ pidA: pids[i], pidB: pids[j], topGaps: pairGaps.slice(0, 3) })
      }
    }
    return gaps
  })

  // === Helper Methods ===
  function toggleCmpPaper(pid) { const i = cmpIds.value.indexOf(pid); if (i >= 0) cmpIds.value.splice(i, 1); else cmpIds.value.push(pid) }
  function cmpAvgScore(pid) { const sc = cmpStructured.value?.scores?.[pid]; if (!sc) return null; const v = Object.values(sc).filter(x => typeof x === 'number'); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null }
  function cmpPaperTitle(pid) { const p = cmpPapers.value.find(x => x.paper_id === pid); return p ? (p.title?.slice(0, 30) || pid) : pid }
  function cmpFormatPair(key) { const parts = key.split('|'); return parts.map(p => cmpPaperTitle(p)).join(' ↔ ') }
  function switchCmpTab(tid) { cmpTab.value = tid; if (tid === 'charts') nextTick().then(renderAllCmpCharts) }

  // === D3 Chart Methods ===
  function cmpMiniHeatmap() { const m = cmpStructured.value?.similarity_matrix; if (!m || !m.length) return ''; const n = m.length; let h = `<div style="display:grid;grid-template-columns:repeat(${n},1fr);gap:3px;width:100%;max-width:420px;margin:0 auto">`; for (let i = 0; i < n; i++)for (let j = 0; j < n; j++) { const v = m[i]?.[j] || 0; const a = Math.round(v * 255); h += `<div style="aspect-ratio:1;background:rgb(${a},${Math.round(a * 0.4)},${Math.round(255 - a * 0.6)});font-size:${n > 4 ? 9 : 12}px;display:flex;align-items:center;justify-content:center;border-radius:3px;color:${v > 0.55 ? '#000' : '#fff'}" title="相似度:${v}">${v.toFixed(2)}</div>` } h += '</div>'; return h }

  function renderAllCmpCharts() { renderCmpRadar(); renderCmpHeat(); renderCmpBars(); renderCmpStack() }

  function renderCmpRadar() {
    const el = cmpRadarCv.value; if (!el || !window.d3) return; el.innerHTML = ''; const cd = cmpStructured.value?.chart_data?.radar; if (!cd || !cd.labels?.length) return
    const W = el.clientWidth || 400, H = el.clientHeight || 350, R = Math.min(W, H) / 2 - 40, cx = W / 2, cy = H / 2, n = cd.labels.length
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${H}`)
    const g = svg.append('g').attr('transform', `translate(${cx},${cy})`)
    const angScale = (2 * Math.PI) / n
    // Grid circles
    for (let l = 1; l <= 5; l++) { const r = (R / 5) * l; g.append('circle').attr('r', r).attr('fill', 'none').attr('stroke', '#333').attr('stroke-width', 0.5) }
    // Axis lines
    for (let i = 0; i < n; i++) {
      const a = angScale * i - Math.PI / 2; g.append('line').attr('x1', 0).attr('y1', 0).attr('x2', R * Math.cos(a)).attr('y2', R * Math.sin(a)).attr('stroke', '#444').attr('stroke-width', 0.5)
      g.append('text').attr('x', (R + 14) * Math.cos(a)).attr('y', (R + 14) * Math.sin(a)).attr('text-anchor', 'middle').attr('dominant-baseline', 'middle').attr('fill', '#999').attr('font-size', 11).text(cd.labels[i].slice(0, 6))
    }
    // Dataset polygons
    cd.datasets.forEach((ds, j) => {
      const pts = ds.data.map((v, i) => { const a = angScale * i - Math.PI / 2; const r = (v / 10) * R; return [r * Math.cos(a), r * Math.sin(a)] })
      const line = d3.line().x(d => d[0]).y(d => d[1]); g.append('path').attr('d', line(pts) + 'Z').attr('fill', PAPER_COLORS[j]).attr('fill-opacity', 0.15).attr('stroke', PAPER_COLORS[j]).attr('stroke-width', 1.5)
      pts.forEach(([x, y], i) => { g.append('circle').attr('cx', x).attr('cy', y).attr('r', 4).attr('fill', PAPER_COLORS[j]).attr('stroke', '#0f0f1a').attr('stroke-width', 1) })
    })
  }

  function renderCmpHeat() {
    const el = cmpHeatCv.value; if (!el || !window.d3) return; el.innerHTML = ''; const cd = cmpStructured.value?.chart_data?.heatmap; if (!cd || !cd.labels?.length) return
    const W = el.clientWidth || 400, n = cd.labels.length, cs = Math.min(60, Math.floor((W - 80) / n)), H = n * cs + 60
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${H}`)
    const m = cd.matrix; if (!m) return
    for (let i = 0; i < n; i++) {
      svg.append('text').attr('x', 80).attr('y', 40 + i * cs + cs / 2).attr('fill', '#999').attr('font-size', 10).text(cd.labels[i].slice(0, 10))
      for (let j = 0; j < n; j++) {
        const v = m[i]?.[j] || 0; const a = Math.round(v * 255)
        svg.append('rect').attr('x', 90 + j * cs).attr('y', 28 + i * cs).attr('width', cs - 2).attr('height', cs - 2).attr('fill', `rgb(${a},${Math.round(a * 0.4)},${Math.round(255 - a * 0.6)})`).attr('rx', 3)
        svg.append('text').attr('x', 90 + j * cs + cs / 2).attr('y', 28 + i * cs + cs / 2).attr('text-anchor', 'middle').attr('dominant-baseline', 'middle').attr('fill', v > 0.5 ? '#000' : '#fff').attr('font-size', 9).text(v.toFixed(2))
      }
    }
    for (let j = 0; j < n; j++) { svg.append('text').attr('x', 90 + j * cs + cs / 2).attr('y', 22).attr('text-anchor', 'middle').attr('fill', '#999').attr('font-size', 10).text(cd.labels[j].slice(0, 10)) }
  }

  function renderCmpBars() {
    const el = cmpBarsCv.value; if (!el || !window.d3) return; el.innerHTML = ''; const cd = cmpStructured.value?.chart_data?.bars; if (!cd || !cd.labels?.length) return
    const W = el.clientWidth || 500, H = 320, n = cd.labels.length, m = cd.datasets?.length || 0, barW = Math.max(8, Math.floor((W - 80) / n / (m + 1)))
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${H}`)
    const maxY = 10; const y = d3.scaleLinear().domain([0, maxY]).range([H - 40, 20])
    svg.append('g').attr('transform', 'translate(60,0)').call(d3.axisLeft(y).ticks(5))
    cd.datasets.forEach((ds, j) => {
      cd.labels.forEach((l, i) => {
        const v = ds.data[i] || 0; const bx = 70 + i * (m + 1) * barW + j * barW
        svg.append('rect').attr('x', bx).attr('y', y(v)).attr('width', barW - 2).attr('height', H - 40 - y(v)).attr('fill', DIM_COLORS[j % DIM_COLORS.length]).attr('rx', 2)
        svg.append('text').attr('x', bx + barW / 2).attr('y', y(v) - 4).attr('text-anchor', 'middle').attr('fill', '#ccc').attr('font-size', 9).text(v.toFixed(1))
      })
    })
  }

  function renderCmpStack() {
    const el = cmpStackCv.value; if (!el || !window.d3) return; el.innerHTML = ''; const cd = cmpStructured.value?.chart_data?.stacked; if (!cd || !cd.labels?.length) return
    const W = el.clientWidth || 500, H = 320, n = cd.labels?.length || 0, barW = Math.max(20, Math.floor((W - 80) / n))
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${H}`)
    const dims = cd.dimensions || []; const data = cd.data || []
    const maxTotal = d3.max(data.map(row => row.reduce((a, b) => a + b, 0))) || 10
    const y = d3.scaleLinear().domain([0, maxTotal]).range([H - 40, 20])
    svg.append('g').attr('transform', 'translate(60,0)').call(d3.axisLeft(y).ticks(5))
    for (let i = 0; i < n; i++) {
      let acc = 0; for (let j = 0; j < dims.length; j++) {
        const v = data[i]?.[j] || 0
        svg.append('rect').attr('x', 70 + i * barW).attr('y', y(acc + v)).attr('width', barW - 3).attr('height', y(acc) - y(acc + v)).attr('fill', DIM_COLORS[j % DIM_COLORS.length]).attr('rx', 1); acc += v
      }
    }
  }

  // === API Methods ===
  async function doCompare() {
    comparing.value = true; cmpError.value = ''; cmpResult.value = null; cmpStructured.value = null
    try {
      const r = await api('/api/paper/compare', { method: 'POST', body: JSON.stringify({ paper_ids: cmpIds.value, structured: true }) })
      cmpResult.value = r; cmpStructured.value = r.structured || null
      cmpDimNames.value = r.structured?.scores ? Object.keys(r.structured.scores[Object.keys(r.structured.scores)[0]] || {}) : Object.keys(r.dimensions || {})
      cmpPapers.value = cmpIds.value.map(id => papers.value.find(p => p.paper_id === id) || { paper_id: id, title: id })
      await nextTick(); if (cmpTab.value === 'charts') renderAllCmpCharts()
    } catch (e) { cmpError.value = '对比失败: ' + (e.message || '网络错误') } finally { comparing.value = false }
  }

  async function loadCompareHistory() {
    showCmpHistory.value = !showCmpHistory.value; if (showCmpHistory.value) try { cmpHistory.value = (await api('/api/paper/compare/history')).comparisons || [] } catch (e) { console.error(e) }
  }

  async function loadCompareResult(cid) {
    showCmpHistory.value = false; try {
      cmpResult.value = await api(`/api/paper/compare/${cid}`); cmpStructured.value = cmpResult.value.structured || null; cmpDimNames.value = cmpResult.value.structured?.scores ? Object.keys(cmpResult.value.structured.scores[Object.keys(cmpResult.value.structured.scores)[0]] || {}) : []; cmpPapers.value = (cmpResult.value.paper_ids || []).map(id => papers.value.find(p => p.paper_id === id) || { paper_id: id, title: id }); cmpTab.value = 'overview'
    } catch (e) { console.error(e) }
  }

  async function deleteCompareResult(cid) {
    if (!confirm('确认删除此对比记录？')) return; try { await fetch(`/api/paper/compare/${cid}`, { method: 'DELETE' }); loadCompareHistory() } catch (e) { console.error(e) }
  }

  // === Export Methods ===
  function downloadBlob(content, filename, mime) { const b = new Blob(['﻿' + content], { type: mime + ';charset=utf-8' }); const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = filename; a.click(); URL.revokeObjectURL(a.href) }

  function exportCompareMD() { const s = cmpResult.value?.synthesis || ''; const t = cmpResult.value?.structured; let md = '# 论文横向对比报告\n\n'; if (t?.scores) { md += '## 评分矩阵\n\n'; for (const [pid, sc] of Object.entries(t.scores)) { md += `- ${pid}: ` + Object.entries(sc).map(([k, v]) => `${k}=${v}`).join(', ') + '\n' } } md += '\n## 综合分析\n\n' + s; downloadBlob(md, 'comparison_report.md', 'text/markdown') }

  function exportCompareCSV() { const t = cmpStructured.value; if (!t || !t.scores) { alert('无结构化数据可导出'); return } const dims = cmpDimNames.value; let csv = 'paper_id,' + dims.join(',') + '\n'; for (const [pid, sc] of Object.entries(t.scores)) { csv += pid + ',' + dims.map(d => sc[d] || '').join(',') + '\n' } downloadBlob(csv, 'comparison_scores.csv', 'text/csv') }

  function exportCompareHTML() { const s = cmpResult.value?.synthesis || ''; const t = cmpResult.value?.structured; let h = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>对比报告</title><style>body{font-family:sans-serif;max-width:960px;margin:0 auto;padding:20px;background:#0f0f1a;color:#e0e0e0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #333;padding:8px}th{background:#1a1a2e}h1,h2{color:#a78bfa}</style></head><body><h1>论文横向对比报告</h1>'; if (t?.scores) { h += '<h2>评分矩阵</h2><table><tr><th>论文</th>' + cmpDimNames.value.map(d => `<th>${d}</th>`).join('') + '</tr>'; for (const [pid, sc] of Object.entries(t.scores)) { h += `<tr><td>${pid}</td>` + cmpDimNames.value.map(d => `<td>${sc[d] || '-'}</td>`).join('') + '</tr>' } h += '</table>' } h += '<h2>综合分析</h2><pre style="white-space:pre-wrap">' + s + '</pre></body></html>'; downloadBlob(h, 'comparison_report.html', 'text/html') }

  return {
    cmpIds, cmpResult, cmpStructured, cmpTab, cmpTabs, cmpPapers, comparing, cmpError,
    cmpHistory, showCmpHistory, cmpDimFilter, cmpDimNames, cmpExpandedDim,
    cmpRadarCv, cmpHeatCv, cmpBarsCv, cmpStackCv,
    cmpReadyPapers, filteredCmpDims, cmpPairwiseGaps, PAPER_COLORS, DIM_COLORS,
    toggleCmpPaper, cmpAvgScore, cmpPaperTitle, cmpFormatPair,
    switchCmpTab, cmpMiniHeatmap, renderAllCmpCharts,
    doCompare, loadCompareHistory, loadCompareResult, deleteCompareResult,
    exportCompareMD, exportCompareCSV, exportCompareHTML, downloadBlob,
  }
}
