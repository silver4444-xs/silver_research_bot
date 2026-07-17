import { ref, computed } from 'vue'
import { useApi } from './useApi.js'

const STAGES = [{ id: 'parse', label: '文档解析' }, { id: 'translate', label: '全文翻译' }, { id: 'analyze', label: '四维分析' }, { id: 'formula_explain', label: '公式解读' }, { id: 'visualize', label: '可视化' }, { id: 'citation', label: '引用图谱' }, { id: 'review', label: '多视角审稿' }]

export function usePapers() {
  const { api } = useApi()

  // === Reactive State ===
  const papers = ref([])
  const pdet = ref(null)
  const selectedPapers = ref(new Set())
  const curPaperId = ref('')
  const uploading = ref(false)
  const upStatus = ref('')
  const progressMsg = ref('')
  const pStages = ref([])
  const upFile = ref(null)
  const upText = ref('')
  const upLang = ref('auto')
  const upMode = ref('file')
  const drag = ref(false)
  const apiOk = ref(true)
  const apiStatus = ref('未知')

  let _pollTimer = null

  // === Computed ===
  const totalFormulas = computed(() => papers.value.reduce((s, p) => s + (p.formula_count || 0), 0))
  const totalFigures = computed(() => papers.value.reduce((s, p) => s + (p.figure_count || 0), 0))
  const canSubmit = computed(() => (upMode.value === 'file' && upFile.value) || (upMode.value === 'text' && upText.value.trim().length > 50))
  const hasAnyResult = computed(() => { const p = pdet.value; if (!p) return false; return !!(p.translation || p.system_model || p.problem_formulation || p.optimization_algorithm || p.experiment_design || p.formula_explanations || p.visualization_html) })
  const cmpReadyPapers = computed(() => papers.value.filter(p => p.status === 'completed'))

  // === Methods ===
  async function loadPapers() { try { papers.value = await api('/api/paper/list') } catch (e) { console.error(e) } }
  async function doUpload() {
    uploading.value = true; upStatus.value = ''; progressMsg.value = '准备中…'
    pStages.value = STAGES.map(s => ({ ...s, s: 'pending' }))
    try {
      let r
      if (upMode.value === 'file' && upFile.value) { const fd = new FormData(); fd.append('file', upFile.value); fd.append('language', upLang.value); r = await api('/api/paper/upload', { method: 'POST', body: fd }); upFile.value = null }
      else if (upMode.value === 'text' && upText.value.trim()) { const fd = new FormData(); const blob = new Blob([upText.value], { type: 'text/plain' }); fd.append('file', blob, 'paper-input.txt'); fd.append('language', upLang.value); r = await api('/api/paper/upload', { method: 'POST', body: fd }); upText.value = '' }
      curPaperId.value = r.paper_id
      _pollTimer = setInterval(pollProgress, 2000)
    } catch (e) { upStatus.value = `失败: ${e.message}`; uploading.value = false }
  }
  async function pollProgress() {
    if (!curPaperId.value) return
    try {
      const p = await api(`/api/paper/${curPaperId.value}/progress`)
      progressMsg.value = p.message || p.stage
      pStages.value = STAGES.map(s => {
        if (s.id === p.stage) return { ...s, s: p.status === 'completed' ? 'done' : 'active' }
        const si = STAGES.findIndex(x => x.id === p.stage), ci = STAGES.findIndex(x => x.id === s.id)
        return { ...s, s: ci < si ? 'done' : 'pending' }
      })
      if (p.stage === 'review' && p.status === 'completed') {
        clearInterval(_pollTimer); _pollTimer = null; uploading.value = false
        upStatus.value = '分析完成！'; await loadPapers()
      }
    } catch (e) { progressMsg.value = '轮询失败: ' + e.message; console.error('pollProgress', e) }
  }
  function clearPoll() { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null } }
  async function openPaper(pid) {
    clearPoll()
    try {
      pdet.value = await api(`/api/paper/${pid}`); dt.value = pdet.value.translation ? 'translation' : 'system_model'; ps.value = 'detail'
      if (pdet.value.status === 'processing') { curPaperId.value = pid; pStages.value = STAGES.map(s => ({ ...s, s: 'pending' })); _pollTimer = setInterval(pollDetailProgress, 2000) }
      else { pStages.value = STAGES.map(s => ({ ...s, s: 'done' })); retypesetDeferred() }
    } catch (e) { console.error(e); alert('加载论文失败: ' + (e.message || '网络错误，请确认后端已启动')) }
  }
  async function pollDetailProgress() {
    if (!curPaperId.value) return
    try {
      const p = await api(`/api/paper/${curPaperId.value}/progress`)
      pStages.value = STAGES.map(s => { if (s.id === p.stage) return { ...s, s: p.status === 'completed' ? 'done' : 'active' }; const si = STAGES.findIndex(x => x.id === p.stage), ci = STAGES.findIndex(x => x.id === s.id); return { ...s, s: ci < si ? 'done' : 'pending' } })
      if (p.stage === 'review' && p.status === 'completed') { clearPoll(); pdet.value = await api(`/api/paper/${curPaperId.value}`); retypesetDeferred() }
    } catch (e) { /*ignore*/ }
  }
  async function doExport() { if (!pdet.value || !pdet.value.paper_id) return; const a = document.createElement('a'); a.href = `/api/paper/${pdet.value.paper_id}/export`; a.download = `${pdet.value.paper_id}_analysis.zip`; a.click() }
  function exportArtifact(pid, atype, label) { const a = document.createElement('a'); a.href = `/api/paper/${pid}/export/${atype}`; const ext = atype === 'visualization' || atype === 'citation_graph' ? '.html' : '.md'; a.download = (label || atype) + ext; a.click() }
  async function delPaper(pid) { if (!confirm('确认删除？')) return; try { await fetch(`/api/paper/${pid}`, { method: 'DELETE' }); await loadPapers(); pdet.value = null } catch (e) { console.error(e) } }
  function toggleSelectPaper(pid) { const s = new Set(selectedPapers.value); if (s.has(pid)) s.delete(pid); else s.add(pid); selectedPapers.value = s }
  function selectAllPapers() { if (selectedPapers.value.size === papers.value.length) { selectedPapers.value = new Set() } else { selectedPapers.value = new Set(papers.value.map(p => p.paper_id)) } }
  async function deleteSelected() { if (!selectedPapers.value.size) return; if (!confirm(`确认删除选中的 ${selectedPapers.value.size} 篇论文？`)) return; try { await api('/api/paper/batch-delete', { method: 'POST', body: JSON.stringify({ paper_ids: [...selectedPapers.value] }) }); selectedPapers.value = new Set(); await loadPapers(); pdet.value = null } catch (e) { console.error(e) } }
  async function deleteAllPapers() { if (!confirm('确认删除全部论文？此操作不可撤销！')) return; try { await api('/api/paper/batch-delete', { method: 'POST', body: JSON.stringify({ delete_all: true }) }); selectedPapers.value = new Set(); await loadPapers(); pdet.value = null } catch (e) { console.error(e) } }
  function onDrop(e) { drag.value = false; const f = e.dataTransfer?.files?.[0]; if (f?.name?.toLowerCase().endsWith('.pdf') || f?.name?.toLowerCase().endsWith('.txt')) upFile.value = f }
  function onFile(e) { const f = e.target?.files?.[0]; if (f) upFile.value = f }
  async function loadAll() { try { apiOk.value = true; apiStatus.value = '在线'; await Promise.all([loadRagPapers(), loadPapers()]) } catch { apiOk.value = false; apiStatus.value = '离线' } }

  return {
    papers, pdet, selectedPapers, curPaperId, uploading, upStatus, progressMsg,
    pStages, upFile, upText, upLang, upMode, drag, apiOk, apiStatus,
    totalFormulas, totalFigures, canSubmit, hasAnyResult, cmpReadyPapers,
    STAGES, _pollTimer,
    loadPapers, loadAll, doUpload, pollProgress, clearPoll, openPaper,
    pollDetailProgress, doExport, exportArtifact, delPaper,
    toggleSelectPaper, selectAllPapers, deleteSelected, deleteAllPapers,
    onDrop, onFile,
  }
}
