<template>
<div class="app">
<aside class="sidebar">
  <div class="brand"><div class="logo"><svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg></div><div><h1>Silver Research</h1><p>论文研读工作台</p></div></div>
  <nav class="nav">
    <button v-for="it in nav" :key="it.id" :class="['navi',tab===it.id&&'act']" @click="tab=it.id"><svg viewBox="0 0 24 24" v-html="it.icon"></svg>{{ it.label }}</button>
  </nav>
  <div class="mini-card"><span class="chip cg">论文研读</span><span class="chip cb2">四维分析</span><span class="chip ca">公式解读</span><span class="chip cp">可视化</span></div>
</aside>
<main class="main">
  <!-- ═══ Papers ═══ -->
  <section v-if="tab==='papers'">
    <div class="subnav"><button v-for="s in psub" :key="s.id" :class="['st',ps===s.id&&'act']" @click="ps=s.id">{{ s.label }}</button></div>

    <!-- Upload -->
    <div v-if="ps==='upload'" style="margin-top:var(--s-5)">
      <div class="card"><div class="ch"><h3>提交论文</h3></div>
        <div class="utabs">
          <button :class="['ut',upMode==='file'&&'act']" @click="upMode='file'"><svg viewBox="0 0 24 24" fill="none"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>PDF 文件上传</button>
          <button :class="['ut',upMode==='text'&&'act']" @click="upMode='text'"><svg viewBox="0 0 24 24" fill="none"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>文本粘贴输入</button>
        </div>

        <!-- File mode -->
        <div v-if="upMode==='file'" class="upload-card" :class="{drag}" @dragover.prevent="drag=true" @dragleave="drag=false" @drop.prevent="onDrop">
          <div class="drop-zone">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 12 15 15"/></svg>
            <p>拖拽 PDF 文件到此处或点击按钮选择</p>
            <input ref="fi" type="file" accept=".pdf" hidden @change="onFile" />
            <button class="btn ba" @click="$refs.fi.click()">选择 PDF 文件</button>
            <div v-if="upFile" class="file-card">
              <div class="file-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><polyline points="9 15 12 12 15 15"/></svg></div>
              <div class="file-card-info">
                <div class="file-card-name">{{ upFile.name }}</div>
                <div class="file-card-meta">{{ fmtSize(upFile.size) }} · PDF 文档</div>
              </div>
              <button class="file-card-remove" @click="upFile=null" title="移除文件"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>
          </div>
        </div>

        <!-- Text mode — Rich Markdown Editor -->
        <div v-if="upMode==='text'" class="editor-area">
          <div class="ep">
            <div class="eh"><svg viewBox="0 0 24 24" fill="none"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg> Markdown 编辑</div>
            <div class="tb">
              <button class="tb-btn" title="粗体 **text**" @click="insMd('**','**')">B</button>
              <button class="tb-btn" title="斜体 *text*" @click="insMd('*','*')"><em>I</em></button>
              <button class="tb-btn" title="标题 ##" @click="insMd('\n## ','')">H</button>
              <button class="tb-btn" title="公式块 $$" @click="insMd('\n$$\n','\n$$\n')">$$</button>
              <button class="tb-btn" title="行内公式 $" @click="insMd('$','$')">$</button>
              <button class="tb-btn" title="代码块 ```" @click="insMd('\n```\n','\n```\n')">&lt;/&gt;</button>
              <button class="tb-btn" title="引用 >" @click="insMd('\n> ','')">"</button>
              <button class="tb-btn" title="列表 -" @click="insMd('\n- ','')">-</button>
            </div>
            <textarea ref="editorArea" class="editor-textarea" v-model="upText"
              placeholder="在此粘贴论文全文内容…&#10;&#10;支持 Markdown 语法：&#10;  # 标题  ## 二级标题  **粗体**  *斜体*&#10;  $$ 公式块 $$  $ 行内公式 $&#10;  > 引用  - 列表  ``` 代码块 ```"
              @scroll="syncScroll()"></textarea>
          </div>
          <div class="pp">
            <div class="ph"><svg viewBox="0 0 24 24" fill="none"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> 实时预览</div>
            <div class="pc" ref="previewEl" v-html="renderMd(upText)" @scroll="syncScroll(true)"></div>
          </div>
        </div>

        <div class="row" style="margin-top:var(--s-4)">
          <div><label for="ulang">论文语言</label><select id="ulang" v-model="upLang"><option value="auto">自动检测</option><option value="en">英文</option><option value="zh">中文</option></select></div>
          <div style="align-self:flex-end"><button class="btn bp" :disabled="!canSubmit||uploading" @click="doUpload">{{ uploading?'分析中…':'开始分析' }}</button></div>
        </div>

        <div v-if="uploading" class="ptrack" style="margin-top:var(--s-4)">
          <div class="pt-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> {{ progressMsg }}</div>
          <div class="pt-stages"><template v-if="pStages.length"><div v-for="s in pStages" :key="s.id" :class="['pts',s.s]">
            <div class="ptd"><svg v-if="s.s==='done'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="14" height="14"><polyline points="20 6 9 17 4 12"/></svg><div v-else-if="s.s==='active'" class="pts-spin"></div><div v-else class="ptd-empty"></div></div>
            <span>{{ s.label }}</span>
          </div></template></div>
        </div>
        <div v-if="upStatus" class="ibox" :class="{'ibox-ok':!uploading}" style="margin-top:var(--s-3)"><strong>{{ upStatus }}</strong></div>
      </div>
    </div>

    <!-- List -->
    <div v-if="ps==='list'">
      <div class="pstats-bar">
        <div class="pstat"><span class="pstat-num">{{papers.length}}</span><span class="pstat-label">论文</span></div>
        <div class="pstat"><span class="pstat-num cp">{{papers.filter(p=>p.status==='completed').length}}</span><span class="pstat-label">已完成</span></div>
        <div class="pstat"><span class="pstat-num ca">{{papers.filter(p=>p.status==='processing').length}}</span><span class="pstat-label">分析中</span></div>
      </div>
      <div class="ptoolbar">
        <label class="row" style="gap:6px;font-size:13px;cursor:pointer;user-select:none" @click.stop>
          <div :class="['sel-circle',selectedPapers.size===papers.length&&papers.length>0?'on':'']" @click="selectAllPapers"></div>
          <span style="color:var(--c-text-secondary)">全选</span>
        </label>
        <div class="ptoolbar-spacer"></div>
        <button v-if="selectedPapers.size" class="btn btn-del-sel bsm" @click="deleteSelected">删除选中 ({{selectedPapers.size}})</button>
        <button class="btn btn-del-all bsm" @click="deleteAllPapers">全部删除</button>
        <button class="btn bs bsm" @click="loadPapers">刷新</button>
        <button class="btn ba bsm" @click="ps='upload'">上传新论文</button>
      </div>
      <div class="pgrid" v-if="papers.length">
        <article v-for="p in papers" :key="p.paper_id" :class="['pcard',{sel:selectedPapers.has(p.paper_id)}]" @click="openPaper(p.paper_id)">
          <div class="pcard-top">
            <div class="pcard-sel" @click.stop>
              <div :class="['sel-circle',selectedPapers.has(p.paper_id)?'on':'']" @click="toggleSelectPaper(p.paper_id)"></div>
            </div>
            <button class="pcard-del" @click.stop="delPaper(p.paper_id)" title="删除">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
          <div class="pcard-body">
            <h4 class="pcard-title">{{ p.title }}</h4>
            <div class="pcard-meta">
              <span class="chip" :class="p.status==='completed'?'cp':p.status==='processing'?'ca':'cg'">{{ p.status==='completed'?'已完成':p.status==='processing'?'分析中':'未知' }}</span>
              <span class="chip" :class="p.language==='en'?'cb2':'cg'">{{ p.language==='en'?'EN':'ZH' }}</span>
              <span>{{ p.page_count||'?' }} 页</span>
              <span>{{ p.formula_count||'?' }} 公式</span>
              <span class="pcard-date">{{ (p.uploaded_at||'').slice(0,10) }}</span>
            </div>
          </div>
        </article>
      </div>
      <div v-if="!papers.length" class="pempty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" width="56" height="56" style="opacity:0.15;margin-bottom:var(--s-4)"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        <p style="color:var(--c-text-muted);font-size:14px">暂无已分析的论文</p>
        <button class="btn ba bsm" @click="ps='upload'" style="margin-top:var(--s-3)">上传第一篇论文</button>
      </div>
    </div>

    <!-- Detail -->
    <div v-if="ps==='detail'&&pdet" class="pd" style="margin-top:var(--s-5)">
      <div class="card"><div class="ch"><h3>{{ pdet.title }}</h3><div class="row rt"><button class="btn bg bsm" @click="ps='list';clearPoll()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="16" height="16"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>返回</button><button class="btn ba bsm" @click="doExport">导出全部</button></div></div><div class="pdm"><span class="chip" :class="pdet.language==='en'?'cb2':'cg'">{{ pdet.language==='en'?'英文':'中文' }}</span><span style="font-size:13px;color:var(--c-text-secondary)">{{ pdet.page_count||'?' }}页 · {{ pdet.formula_count||'?' }}公式</span><span v-if="pdet.status==='processing'" class="chip ca">分析中</span><span v-if="pdet.has_translation" class="chip cb2">已翻译</span><span v-if="pdet.status==='completed'" class="chip cp">已审计</span></div></div>

      <div v-if="pdet.status==='processing'&&!hasAnyResult" class="card"><div class="pt-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> 分析进行中…</div><div class="pt-stages"><template v-if="pStages.length"><div v-for="s in pStages" :key="s.id" :class="['pts',s.s]"><div class="ptd"><div v-if="s.s==='active'" class="pts-spin"></div></div><span>{{ s.label }}</span></div></template></div></div>

      <div class="dtabs"><button v-for="t in dtabs" :key="t.id" :class="['st',dt===t.id&&'act']" @click="dt=t.id">{{ t.label }}</button></div>
      <div class="card" v-if="dt==='translation'&&pdet.translation"><div class="ch"><h3>全文翻译</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'translation','全文翻译')" title="下载全文翻译"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><div class="md" v-html="renderAll(pdet.translation)"></div></div>
      <div v-if="dt==='translation'&&!pdet.translation" class="card"><p class="empty">该论文暂无翻译结果（可能为中文论文或翻译尚未完成）</p></div>
      <template v-for="d in dims" :key="d.id"><div class="card" v-if="dt===d.id&&pdet[d.id]"><div class="ch"><h3>{{ d.label }}</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,d.id,d.label)" :title="'下载'+d.label"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><div class="md" v-html="renderAll(pdet[d.id])"></div></div></template>
      <div class="card" v-if="dt==='formulas'&&pdet.formula_explanations"><div class="ch"><h3>公式解读</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'formulas','公式解读')" title="下载公式解读"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><div class="md" v-html="renderFormula(pdet.formula_explanations)"></div></div>
      <div class="card" v-if="dt==='visualization'&&pdet.visualization_html"><div class="ch"><h3>可视化分析</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'visualization','可视化分析')" title="下载可视化分析"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><iframe :srcdoc="pdet.visualization_html" class="vis-frame" sandbox="allow-scripts allow-same-origin" title="可视化分析"></iframe></div>
      <div class="card" v-if="dt==='audit'&&pdet.audit"><div class="ch"><h3>审计报告</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'audit','审计报告')" title="下载审计报告"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><div v-html="renderAudit(pdet.audit)"></div></div>
      <div class="card" v-if="dt==='citation_graph'&&pdet.citation_graph_html"><div class="ch"><h3>引用图谱</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'citation_graph','引用图谱')" title="下载引用图谱"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><iframe :srcdoc="pdet.citation_graph_html" class="vis-frame" sandbox="allow-scripts"></iframe></div>
      <div class="card" v-if="dt==='review'"><div class="ch"><h3>审稿意见</h3></div><div v-if="pdet.review_theory" style="margin-bottom:16px"><div style="display:flex;align-items:center;gap:8px"><h4 style="margin:0;font-size:14px;color:var(--c-text-secondary)">理论视角</h4><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'review_theory','理论审稿意见')" title="下载理论审稿意见"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><div class="md" v-html="renderAll(pdet.review_theory)"></div></div><div v-if="pdet.review_engineering" style="margin-bottom:16px"><div style="display:flex;align-items:center;gap:8px"><h4 style="margin:0;font-size:14px;color:var(--c-text-secondary)">工程视角</h4><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'review_engineering','工程审稿意见')" title="下载工程审稿意见"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><div class="md" v-html="renderAll(pdet.review_engineering)"></div></div><div v-if="pdet.review_domain" style="margin-bottom:16px"><div style="display:flex;align-items:center;gap:8px"><h4 style="margin:0;font-size:14px;color:var(--c-text-secondary)">领域视角</h4><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id,'review_domain','领域审稿意见')" title="下载领域审稿意见"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div><div class="md" v-html="renderAll(pdet.review_domain)"></div></div><p v-if="!pdet.review_theory&&!pdet.review_engineering&&!pdet.review_domain" class="empty">审稿意见尚未生成。</p></div>
      <div class="card" v-if="dt==='ask'"><div class="ch"><h3>提问</h3></div><div class="row" style="gap:8px"><input v-model="askQ" placeholder="基于分析结果提问…" style="flex:1" @keydown.enter="doAsk" /><button class="btn bp bsm" @click="doAsk" :disabled="!askQ.trim()">提问</button></div><div v-if="askA" class="md" style="margin-top:12px" v-html="renderMd(askA)"></div></div>
    </div>

    <!-- Compare -->
    <div v-if="ps==='compare'" class="cmp-area"><div class="card"><div class="ch"><h3>横向对比工作台 <span style="font-size:12px;color:var(--c-text-muted);font-weight:400">— 已选 {{cmpIds.length}} 篇</span></h3></div>
    <div class="cmp-pick-grid"><div v-for="p in cmpReadyPapers" :key="p.paper_id" :class="['cmp-pick-card',{sel:cmpIds.includes(p.paper_id)}]" :style="cmpIds.includes(p.paper_id)?'--clr:'+PAPER_COLORS[cmpIds.indexOf(p.paper_id)%12]:''" @click="toggleCmpPaper(p.paper_id)"><div class="cmp-pick-chk">{{ cmpIds.includes(p.paper_id) ? '✓' : '' }}</div><div class="cmp-pick-info"><div class="cmp-pick-title">{{ p.title?.slice(0,70) || p.paper_id }}</div><div class="cmp-pick-meta">{{ p.page_count||'?' }} 页 · {{ p.formula_count||'?' }} 公式 · {{ p.status==='completed'?'已完成':'分析中' }}</div></div></div></div>
    <p v-if="!cmpReadyPapers.length" class="empty">暂无已完成分析的论文，请先上传并分析论文。</p>
    <div class="row" style="gap:8px;margin-top:var(--s-3)"><button class="btn ba" :disabled="cmpIds.length<2" @click="doCompare">{{ comparing?'对比中…':'开始对比（已选'+cmpIds.length+'篇）' }}</button><button v-if="cmpIds.length" class="btn bg bsm" @click="cmpIds=[]">清空选择</button><button class="btn bs bsm" @click="loadCompareHistory" style="margin-left:auto">历史对比</button></div><p v-if="cmpError" class="empty" style="color:var(--c-danger)">{{ cmpError }}</p></div>
    <!-- History list -->
    <div class="card" v-if="showCmpHistory"><div class="ch"><h3>历史对比列表 <button class="btn bg bsm" @click="showCmpHistory=false" style="float:right">×</button></h3></div><div v-if="!cmpHistory.length" class="empty">暂无历史对比记录。</div><article v-for="h in cmpHistory" :key="h.id" class="ri cmp-hist-row"><div><strong>{{ h.created_at?.slice(0,19) }}</strong><p>{{ h.paper_count }} 篇论文</p></div><div style="text-align:right"><button class="btn bp bsm" @click="loadCompareResult(h.id)">查看</button><button class="btn bg bsm" style="color:var(--c-danger);margin-left:4px" @click="deleteCompareResult(h.id)">删除</button></div></article></div>
    <!-- Comparison result -->
    <template v-if="cmpResult">
    <!-- Dashboard cards -->
    <div class="cmp-dash"><div class="cmp-pcard" v-for="(p,i) in cmpPapers" :key="p.paper_id" :style="'--clr:'+PAPER_COLORS[i]"><div class="cmp-pclr"></div><div class="cmp-ptitle">{{ p.title?.slice(0,60) || p.paper_id }}</div><div class="cmp-pmeta">页数: {{ p.page_count||'?' }} | 公式: {{ p.formula_count||'?' }}</div><div class="cmp-pscore" v-if="cmpStructured?.scores?.[p.paper_id]">综合: {{ cmpAvgScore(p.paper_id)?.toFixed(1) ?? '—' }}/10</div></div></div>
    <!-- Tab bar -->
    <div class="card card-tabs"><div class="ctabs"><button v-for="t in cmpTabs" :key="t.id" :class="['ctab',{on:cmpTab===t.id}]" @click="switchCmpTab(t.id)">{{ t.label }}</button></div>
    <!-- Overview -->
    <div v-if="cmpTab==='overview'" class="cmp-overview"><div class="cmp-ov-row" v-if="cmpStructured?.similarity_matrix?.length"><strong>论文相似度矩阵</strong><div class="cmp-mini-heat" v-html="cmpMiniHeatmap()"></div></div><div class="cmp-ov-row" v-if="cmpStructured?.formula_overlap && Object.keys(cmpStructured.formula_overlap).length"><strong>公式重叠度</strong><table class="cmp-tbl"><thead><tr><th>论文对</th><th>Jaccard 相似度</th></tr></thead><tbody><tr v-for="(v,k) in cmpStructured.formula_overlap" :key="k"><td>{{ cmpFormatPair(k) }}</td><td>{{ (v*100).toFixed(1) }}%</td></tr></tbody></table></div></div>
    <!-- Dimensions matrix -->
    <div v-if="cmpTab==='dimensions'" class="cmp-matrix"><div class="cmp-dim-filter"><label>维度筛选: </label><select v-model="cmpDimFilter"><option value="all">全部维度</option><option v-for="d in cmpDimNames" :key="d" :value="d">{{ d }}</option></select></div><table class="cmp-tbl cmp-mtbl"><thead><tr><th>维度</th><th v-for="(p,i) in cmpPapers" :key="p.paper_id" :style="'color:'+PAPER_COLORS[i]">{{ p.title?.slice(0,20) }}</th></tr></thead><tbody><tr v-for="d in filteredCmpDims" :key="d"><td><strong>{{ d }}</strong></td><td v-for="(p,i) in cmpPapers" :key="p.paper_id"><div class="cmp-score-badge" :style="'--clr:'+PAPER_COLORS[i]">{{ (cmpStructured?.scores?.[p.paper_id]?.[d]||'-') }}</div></td></tr></tbody></table></div>
    <!-- Charts -->
    <div v-if="cmpTab==='charts'" class="cmp-charts"><div class="cmp-chart-box" id="cmp-radar-container"><strong>维度评分雷达图</strong><div class="cmp-cv" ref="cmpRadarCv"></div></div><div class="cmp-chart-box" id="cmp-heatmap-container"><strong>相似度热力图</strong><div class="cmp-cv" ref="cmpHeatCv"></div></div><div class="cmp-chart-box" id="cmp-bars-container"><strong>维度评分柱状图</strong><div class="cmp-cv" ref="cmpBarsCv"></div></div><div class="cmp-chart-box" id="cmp-stack-container"><strong>评分堆叠图</strong><div class="cmp-cv" ref="cmpStackCv"></div></div></div>
    <!-- Metrics -->
    <div v-if="cmpTab==='metrics'"><table class="cmp-tbl" v-if="cmpResult.metrics?.length"><thead><tr><th>指标</th><th v-for="(p,i) in cmpPapers" :key="p.paper_id">{{ p.title?.slice(0,20) }}</th><th>偏好</th></tr></thead><tbody><tr v-for="m in cmpResult.metrics" :key="m.metric_name"><td><strong>{{ m.metric_name }}</strong><div style="font-size:11px;color:var(--c-text-secondary)">{{ m.dataset }}</div></td><td v-for="(p,i) in cmpPapers" :key="p.paper_id" :style="'color:'+PAPER_COLORS[i]">{{ m.paper_values?.[p.paper_id] ?? '-' }}{{ m.unit||'' }}</td><td>{{ m.higher_is_better?'↑ 越高越好':'↓ 越低越好' }}</td></tr></tbody></table><p v-else class="empty">暂无非结构化指标数据。指标数据依赖 LLM 从实验设计中自动提取。</p></div>
    <!-- Synthesis -->
    <div v-if="cmpTab==='synthesis'" class="md" v-html="renderMd(cmpResult.synthesis||'综合分析生成中…')"></div>
    </div>
    <!-- Bottom bar -->
    <div class="card" v-if="cmpResult" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><button class="btn bs bsm" @click="exportCompareMD">导出 Markdown</button><button class="btn bs bsm" @click="exportCompareCSV">导出 CSV</button><button class="btn bs bsm" @click="exportCompareHTML">导出 HTML</button><span style="font-size:12px;color:var(--c-text-secondary);margin-left:auto">对比 ID: {{ cmpResult.structured?.created_at || '-' }}</span></div></template></div>
  </section>

  <!-- ═══ Original ═══ -->
  <header class="hero"><div><p class="eyebrow">{{ t('eyebrow') }}</p><h2>{{ t('title') }}</h2><p class="subtitle">{{ t('subtitle') }}</p><div class="row" style="gap:8px;margin-top:8px"><button class="btn bg bsm" @click="lang=lang==='zh'?'en':'zh'">{{ lang==='zh'?'EN':'中文' }}</button></div></div><div class="sbar"><div class="si"><strong>{{ t('api') }}</strong><span><span class="dot" :class="apiOk?'dot-ok':'dot-warn'"></span>{{ apiStatus }}</span></div><div class="si"><strong>{{ t('analyzed') }}</strong><span>{{ papers.length }} 篇</span></div><div class="si"><strong>公式</strong><span>{{ totalFormulas }} 个</span></div><div class="si"><strong>图表</strong><span>{{ totalFigures }} 张</span></div><button class="btn bp bsm" @click="loadAll">{{ t('refresh') }}</button></div></header>


  <section v-if="tab==='agent'" class="chat-container">
      <div class="thread"><div v-for="(m,i) in chatMsgs" :key="i" :class="['cbubble',m.role]"><strong>{{ m.role==='user'?'你':'Agent' }}</strong><p>{{ m.content }}</p></div></div>
      <div class="chat-input-box">
        <button class="chat-attach-btn" @click="$refs.chatFileInput.click()" title="上传文件">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        </button>
        <input ref="chatFileInput" type="file" accept=".pdf,.txt,.md" hidden @change="onChatFile" />
        <textarea ref="chatTextarea" v-model="chatIn" rows="1" placeholder="输入消息…（Enter 发送，Shift+Enter 换行）" @keydown="onChatKey" @input="autoResize"></textarea>
        <button class="chat-send-btn" :class="{active:chatIn.trim()}" @click="doChat" :disabled="!chatIn.trim()" title="发送">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
        </button>
      </div>
      <div v-if="chatAttach" class="chat-attach-tag"><span class="chip cb2">{{ chatAttach.name }}</span><button class="btn bg bsm" @click="chatAttach=null" style="color:var(--c-text-muted)">×</button></div>
    </section>



  <section v-else-if="tab==='rag'" class="grid gr"><div class="card"><div class="ch"><h3>文献检索</h3></div><label for="rq">查询</label><input id="rq" v-model="ragQ" /><div class="row"><div><label for="rk">Top K</label><input id="rk" v-model.number="ragK" type="number" min="1" /></div><div><label for="rt">Tag</label><input id="rt" v-model="ragTag" /></div><div><label for="rm">模态</label><select id="rm" v-model="ragModality"><option value="">全部</option><option value="text">文本</option><option value="formula">公式</option><option value="figure">图表</option><option value="table">表格</option></select></div></div><div class="row" style="margin-top:var(--s-2)"><button class="btn bp bsm" @click="doRagSearch">检索</button><button class="btn bs bsm" @click="doRagCtx">上下文</button><button class="btn bs bsm" @click="doRagSuggest">建议</button></div><div v-if="ragResults.length" class="rlist" style="margin-top:var(--s-3)"><article v-for="r in ragResults" :key="r.chunk_id" class="ri"><div><strong>{{ r.title }}</strong><span class="chip cs" style="font-size:10px;margin-left:6px">{{ r.chunk_type }}</span><p style="font-size:12px;color:var(--c-text-secondary)">{{ r.text?.slice(0,200) }}...</p></div><div style="text-align:right;min-width:60px"><span style="font-size:13px;color:var(--c-accent)">{{ (r.score*100).toFixed(0) }}%</span></div></article></div></div><div class="card"><div class="ch"><h3>文献入库</h3></div><label for="pt">标题</label><input id="pt" v-model="pf.title" /><label for="pa">摘要</label><textarea id="pa" v-model="pf.abstract" rows="2"></textarea><label for="pc2">内容</label><textarea id="pc2" v-model="pf.content" rows="4"></textarea><label for="pz">标签</label><input id="pz" v-model="pfTags" /><button class="btn bp bf" @click="doIngest">入库</button></div><div class="card sp2"><div class="ch"><h3>文献列表</h3><button class="btn bs bsm" @click="loadRagPapers">刷新</button></div><div class="rlist"><article v-for="p in ragPapers" :key="p.paper_id" class="ri"><div><strong>{{ p.title }}</strong><p>{{ p.paper_id }}</p></div><div style="text-align:right;font-size:12px"><span class="chip cp">{{ (p.tags||[]).join(',') }}</span><div style="color:var(--c-text-secondary)">{{ p.created_at }}</div><button class="btn bg bsm" style="color:var(--c-danger);margin-top:4px" @click="doDeletePaper(p.paper_id)">删除</button></div></article><p v-if="!ragPapers.length" class="empty">暂无文献。</p></div></div></section>
</main>
</div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'

const icons={
  chat:'<path d="M20 2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4l4 4 4-4h4a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/>',
  file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/>',
  layers:'<path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>',
}
const nav=[{id:'agent',label:'Agent 对话',icon:icons.chat},{id:'papers',label:'论文研读',icon:icons.file},{id:'rag',label:'文献 RAG',icon:icons.layers}]
const psub=[{id:'upload',label:'上传论文'},{id:'list',label:'论文列表'},{id:'compare',label:'横向对比'}]
const dtabs=[{id:'translation',label:'全文翻译'},{id:'system_model',label:'系统模型'},{id:'problem_formulation',label:'问题表述'},{id:'optimization_algorithm',label:'优化算法'},{id:'experiment_design',label:'实验设计'},{id:'formulas',label:'公式解读'},{id:'visualization',label:'可视化'},{id:'citation_graph',label:'引用图谱'},{id:'review',label:'审稿意见'},{id:'audit',label:'审计报告'},{id:'ask',label:'提问'}]
const dims=[{id:'system_model',label:'系统模型分析'},{id:'problem_formulation',label:'问题表述分析'},{id:'optimization_algorithm',label:'优化算法分析'},{id:'experiment_design',label:'实验设计分析'}]

const tab=ref('papers');const ps=ref('upload');const dt=ref('translation');const upMode=ref('file')
const upFile=ref(null);const upText=ref('');const upLang=ref('auto');const uploading=ref(false);const drag=ref(false);const upStatus=ref('');const progressMsg=ref('');const curPaperId=ref('');const pStages=ref([]);let _pollTimer=null
const STAGES=[{id:'parse',label:'文档解析'},{id:'translate',label:'全文翻译'},{id:'analyze',label:'四维分析'},{id:'formula_explain',label:'公式解读'},{id:'visualize',label:'可视化'},{id:'citation',label:'引用图谱'},{id:'review',label:'多视角审稿'},{id:'audit',label:'质量审计'}]
const papers=ref([]);const pdet=ref(null);const selectedPapers=ref(new Set());const cmpIds=ref([]);const cmpResult=ref(null);const comparing=ref(false);const cmpError=ref('')
const cmpTab=ref('overview');const cmpTabs=[{id:'overview',label:'对比仪表板'},{id:'dimensions',label:'多维对比'},{id:'charts',label:'可视化图表'},{id:'metrics',label:'定量分析'},{id:'synthesis',label:'综合分析'}]
const cmpReadyPapers=computed(()=>papers.value.filter(p=>p.status==='completed'))
function toggleCmpPaper(pid){const i=cmpIds.value.indexOf(pid);if(i>=0)cmpIds.value.splice(i,1);else cmpIds.value.push(pid)}
const cmpHistory=ref([]);const showCmpHistory=ref(false);const cmpStructured=ref(null);const cmpDimFilter=ref('all')
const cmpDimNames=ref([]);const cmpPapers=ref([])
const cmpRadarCv=ref(null);const cmpHeatCv=ref(null);const cmpBarsCv=ref(null);const cmpStackCv=ref(null)
const PAPER_COLORS=['#a78bfa','#60a5fa','#34d399','#fbbf24','#f472b6','#fb923c','#94a3b8','#f87171','#4ade80','#c084fc','#38bdf8','#a3e635']
const apiOk=ref(true);const apiStatus=ref('未知');const ragPapers=ref([])
const chatMsgs=ref([{role:'agent',content:'你好！上传 PDF 或粘贴论文文本即可自动完成翻译、四维分析、公式解读。'}])
const chatIn=ref('');const chatReply=ref(null);const chatAttach=ref(null)

const ragQ=ref('retrieval augmented');const ragK=ref(5);const ragTag=ref('');const ragModality=ref('');const ragResults=ref([]);const ragCtx=ref('');const ragSuggest=ref('');const ragSnap=ref({})
const pfTags=ref('rag,llm');const pf=ref({title:'',abstract:'',content:''})
const lang=ref('zh');const LOCALE={zh:{eyebrow:'论文分析平台',title:'研究论文深度分析工作台',subtitle:'上传 PDF 即可自动翻译、四维分析、公式解读与可视化审计。',api:'API',analyzed:'已分析',refresh:'刷新'},en:{eyebrow:'Paper Analysis Platform',title:'Research Paper Analysis Workbench',subtitle:'Upload PDF for auto translation, 4D analysis, formula explanation & audit.',api:'API',analyzed:'Analyzed',refresh:'Refresh'}};function t(k){return (LOCALE[lang.value]||LOCALE.zh)[k]||k}
const askQ=ref('');const askA=ref('')
const totalFormulas=computed(()=>papers.value.reduce((s,p)=>s+(p.formula_count||0),0))
const totalFigures=computed(()=>papers.value.reduce((s,p)=>s+(p.figure_count||0),0))
const canSubmit=computed(()=>(upMode.value==='file'&&upFile.value)||(upMode.value==='text'&&upText.value.trim().length>50))
const hasAnyResult=computed(()=>{const p=pdet.value;if(!p)return false;return !!(p.translation||p.system_model||p.problem_formulation||p.optimization_algorithm||p.experiment_design||p.formula_explanations||p.visualization_html||p.audit)})

async function api(url,opts={}){const res=await fetch(url,{...opts,...(opts.body instanceof FormData?{}:{headers:{'Content-Type':'application/json',...(opts.headers||{})}})});if(!res.ok)throw new Error(await res.text());return res.json()}

// Paper
async function loadPapers(){try{papers.value=await api('/api/paper/list')}catch(e){console.error(e)}}
async function doUpload(){
  uploading.value=true;upStatus.value='';progressMsg.value='准备中…'
  pStages.value=STAGES.map(s=>({...s,s:'pending'}))
  try{
    let r
    if(upMode.value==='file'&&upFile.value){const fd=new FormData();fd.append('file',upFile.value);fd.append('language',upLang.value);r=await api('/api/paper/upload',{method:'POST',body:fd});upFile.value=null}
    else if(upMode.value==='text'&&upText.value.trim()){const fd=new FormData();const blob=new Blob([upText.value],{type:'text/plain'});fd.append('file',blob,'paper-input.txt');fd.append('language',upLang.value);r=await api('/api/paper/upload',{method:'POST',body:fd});upText.value=''}
    curPaperId.value=r.paper_id
    _pollTimer=setInterval(pollProgress,2000)
  }catch(e){upStatus.value=`失败: ${e.message}`;uploading.value=false}
}
async function pollProgress(){
  if(!curPaperId.value)return
  try{
    const p=await api(`/api/paper/${curPaperId.value}/progress`)
    progressMsg.value=p.message||p.stage
    pStages.value=STAGES.map(s=>{
      if(s.id===p.stage)return{...s,s:p.status==='completed'?'done':'active'}
      const si=STAGES.findIndex(x=>x.id===p.stage),ci=STAGES.findIndex(x=>x.id===s.id)
      return{...s,s:ci<si?'done':'pending'}
    })
    if(p.stage==='audit'&&p.status==='completed'){
      clearInterval(_pollTimer);_pollTimer=null;uploading.value=false
      upStatus.value='分析完成！';await loadPapers()
    }
  }catch(e){progressMsg.value='轮询失败: '+e.message;console.error('pollProgress',e)}}
function retypeset(){if(window.MathJax)MathJax.typesetPromise();if(window.mermaid)try{mermaid.run({querySelector:'.mermaid'})}catch(e){}}function renderAll(t){let h=renderMd(t);setTimeout(retypeset,100);return h}
function sanitizeLatex(s){return s.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,"").replace(/#/g,'\\#').replace(/%/g,'\\%').replace(/~/g,'\\textasciitilde{}').replace(/[“”]/g,'"').replace(/[‘’]/g,"'").replace(/ /g,' ')}
function renderFormula(t){if(!t)return'';if(t.indexOf('<div class="frow"')>-1||t.indexOf('<style>')>-1){
  // Sanitize LaTeX special chars
  t=t.replace(/(<div class="fexpr">)([\s\S]*?)(<\/div>)/g,function(_,o,c,e){c=sanitizeLatex(c);if(/^\s*\$/.test(c))return o+c+e;c=c.replace(/^\s+|\s+$/g,'');return o+'$$'+c+'$$'+e})
  // Auto-wrap unwrapped LaTeX in .fmean: \cmd{...} patterns
  t=t.replace(/(<div class="fmean">)([\s\S]*?)(<\/div>)/g,function(_,o,c,e){(function(sc){var mb=[];sc=sc.replace(/\$\$[\s\S]*?\$\$|\$[\s\S]*?\$/g,function(m){mb.push(m);return'￰M'+(mb.length-1)+'￰'});sc=sc.replace(/(\\[a-zA-Z]+(?:\{[^}]*\})+(?!\$))/g,'$$$1$');sc=sc.replace(/￰M(\d+)￰/g,function(_,i){return mb[parseInt(i)]});return sc})(c);return o+c+e})
  setTimeout(retypeset,100);return t}return renderAll(t)}
function clearPoll(){if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}}
async function openPaper(pid){clearPoll()
  try{pdet.value=await api(`/api/paper/${pid}`);dt.value=pdet.value.translation?'translation':'system_model';ps.value='detail'
    if(pdet.value.status==='processing'){curPaperId.value=pid;pStages.value=STAGES.map(s=>({...s,s:'pending'}));_pollTimer=setInterval(pollDetailProgress,2000)}
    else{pStages.value=STAGES.map(s=>({...s,s:'done'}));setTimeout(retypeset,300)}}catch(e){console.error(e);alert('加载论文失败: '+(e.message||'网络错误，请确认后端已启动'))}}
async function pollDetailProgress(){if(!curPaperId.value)return
  try{const p=await api(`/api/paper/${curPaperId.value}/progress`)
    pStages.value=STAGES.map(s=>{if(s.id===p.stage)return{...s,s:p.status==='completed'?'done':'active'};const si=STAGES.findIndex(x=>x.id===p.stage),ci=STAGES.findIndex(x=>x.id===s.id);return{...s,s:ci<si?'done':'pending'}})
    if(p.stage==='audit'&&p.status==='completed'){clearPoll();pdet.value=await api(`/api/paper/${curPaperId.value}`);setTimeout(retypeset,300)}}catch(e){/*ignore*/}}
async function doExport(){if(!pdet.value||!pdet.value.paper_id)return;const a=document.createElement('a');a.href=`/api/paper/${pdet.value.paper_id}/export`;a.download=`${pdet.value.paper_id}_analysis.zip`;a.click()}
function exportArtifact(pid, atype, label){const a=document.createElement('a');a.href=`/api/paper/${pid}/export/${atype}`;const ext=atype==='visualization'||atype==='citation_graph'?'.html':atype==='audit'?'.json':'.md';a.download=(label||atype)+ext;a.click()}
async function delPaper(pid){if(!confirm('确认删除？'))return;try{await fetch(`/api/paper/${pid}`,{method:'DELETE'});await loadPapers();pdet.value=null}catch(e){console.error(e)}}
function toggleSelectPaper(pid){const s=new Set(selectedPapers.value);if(s.has(pid))s.delete(pid);else s.add(pid);selectedPapers.value=s}
function selectAllPapers(){if(selectedPapers.value.size===papers.value.length){selectedPapers.value=new Set()}else{selectedPapers.value=new Set(papers.value.map(p=>p.paper_id))}}
async function deleteSelected(){if(!selectedPapers.value.size)return;if(!confirm(`确认删除选中的 ${selectedPapers.value.size} 篇论文？`))return;try{await api('/api/paper/batch-delete',{method:'POST',body:JSON.stringify({paper_ids:[...selectedPapers.value]})});selectedPapers.value=new Set();await loadPapers();pdet.value=null}catch(e){console.error(e)}}
async function deleteAllPapers(){if(!confirm('确认删除全部论文？此操作不可撤销！'))return;try{await api('/api/paper/batch-delete',{method:'POST',body:JSON.stringify({delete_all:true})});selectedPapers.value=new Set();await loadPapers();pdet.value=null}catch(e){console.error(e)}}
async function doCompare(){comparing.value=true;cmpError.value='';cmpResult.value=null;cmpStructured.value=null
  try{const r=await api('/api/paper/compare',{method:'POST',body:JSON.stringify({paper_ids:cmpIds.value,structured:true})})
    cmpResult.value=r;cmpStructured.value=r.structured||null
    cmpDimNames.value=r.structured?.scores?Object.keys(r.structured.scores[Object.keys(r.structured.scores)[0]]||{}):Object.keys(r.dimensions||{})
    cmpPapers.value=cmpIds.value.map(id=>papers.value.find(p=>p.paper_id===id)||{paper_id:id,title:id})
    await nextTick();if(cmpTab.value==='charts')renderAllCmpCharts()
  }catch(e){cmpError.value='对比失败: '+(e.message||'网络错误')}finally{comparing.value=false}}
function cmpAvgScore(pid){const sc=cmpStructured.value?.scores?.[pid];if(!sc)return null;const v=Object.values(sc).filter(x=>typeof x==='number');return v.length?v.reduce((a,b)=>a+b,0)/v.length:null}
function cmpPaperTitle(pid){const p=cmpPapers.value.find(x=>x.paper_id===pid);return p?(p.title?.slice(0,30)||pid):pid}
function cmpFormatPair(key){const parts=key.split('|');return parts.map(p=>cmpPaperTitle(p)).join(' ↔ ')}
function switchCmpTab(tid){cmpTab.value=tid;if(tid==='charts')nextTick().then(renderAllCmpCharts)}
const filteredCmpDims=computed(()=>cmpDimFilter.value==='all'?cmpDimNames.value:[cmpDimFilter.value])
function cmpMiniHeatmap(){const m=cmpStructured.value?.similarity_matrix;if(!m||!m.length)return'';const n=m.length;let h=`<div style="display:grid;grid-template-columns:repeat(${n},1fr);gap:3px;width:100%;max-width:420px;margin:0 auto">`;for(let i=0;i<n;i++)for(let j=0;j<n;j++){const v=m[i]?.[j]||0;const a=Math.round(v*255);h+=`<div style="aspect-ratio:1;background:rgb(${a},${Math.round(a*0.4)},${Math.round(255-a*0.6)});font-size:${n>4?9:12}px;display:flex;align-items:center;justify-content:center;border-radius:3px;color:${v>0.55?'#000':'#fff'}" title="相似度:${v}">${v.toFixed(2)}</div>`}h+='</div>';return h}

// D3 chart rendering
function renderAllCmpCharts(){renderCmpRadar();renderCmpHeat();renderCmpBars();renderCmpStack()}
function renderCmpRadar(){const el=cmpRadarCv.value;if(!el||!window.d3)return;el.innerHTML='';const cd=cmpStructured.value?.chart_data?.radar;if(!cd||!cd.labels?.length)return
  const W=el.clientWidth||400,H=el.clientHeight||350,R=Math.min(W,H)/2-40,cx=W/2,cy=H/2,n=cd.labels.length
  const svg=d3.select(el).append('svg').attr('viewBox',`0 0 ${W} ${H}`)
  const g=svg.append('g').attr('transform',`translate(${cx},${cy})`)
  const angScale=(2*Math.PI)/n
  // Grid circles
  for(let l=1;l<=5;l++){const r=(R/5)*l;g.append('circle').attr('r',r).attr('fill','none').attr('stroke','#333').attr('stroke-width',0.5)}
  // Axis lines
  for(let i=0;i<n;i++){const a=angScale*i-Math.PI/2;g.append('line').attr('x1',0).attr('y1',0).attr('x2',R*Math.cos(a)).attr('y2',R*Math.sin(a)).attr('stroke','#444').attr('stroke-width',0.5)
    g.append('text').attr('x',(R+14)*Math.cos(a)).attr('y',(R+14)*Math.sin(a)).attr('text-anchor','middle').attr('dominant-baseline','middle').attr('fill','#999').attr('font-size',11).text(cd.labels[i].slice(0,6))}
  // Dataset polygons
  cd.datasets.forEach((ds,j)=>{const pts=ds.data.map((v,i)=>{const a=angScale*i-Math.PI/2;const r=(v/10)*R;return [r*Math.cos(a),r*Math.sin(a)]})
    const line=d3.line().x(d=>d[0]).y(d=>d[1]);g.append('path').attr('d',line(pts)+'Z').attr('fill',PAPER_COLORS[j]).attr('fill-opacity',0.15).attr('stroke',PAPER_COLORS[j]).attr('stroke-width',1.5)
    pts.forEach(([x,y],i)=>{g.append('circle').attr('cx',x).attr('cy',y).attr('r',4).attr('fill',PAPER_COLORS[j]).attr('stroke','#0f0f1a').attr('stroke-width',1)})})}
function renderCmpHeat(){const el=cmpHeatCv.value;if(!el||!window.d3)return;el.innerHTML='';const cd=cmpStructured.value?.chart_data?.heatmap;if(!cd||!cd.labels?.length)return
  const W=el.clientWidth||400,n=cd.labels.length,cs=Math.min(60,Math.floor((W-80)/n)),H=n*cs+60
  const svg=d3.select(el).append('svg').attr('viewBox',`0 0 ${W} ${H}`)
  const m=cd.matrix;if(!m)return
  for(let i=0;i<n;i++){svg.append('text').attr('x',80).attr('y',40+i*cs+cs/2).attr('fill','#999').attr('font-size',10).text(cd.labels[i].slice(0,10))
    for(let j=0;j<n;j++){const v=m[i]?.[j]||0;const a=Math.round(v*255)
      svg.append('rect').attr('x',90+j*cs).attr('y',28+i*cs).attr('width',cs-2).attr('height',cs-2).attr('fill',`rgb(${a},${Math.round(a*0.4)},${Math.round(255-a*0.6)})`).attr('rx',3)
      svg.append('text').attr('x',90+j*cs+cs/2).attr('y',28+i*cs+cs/2).attr('text-anchor','middle').attr('dominant-baseline','middle').attr('fill',v>0.5?'#000':'#fff').attr('font-size',9).text(v.toFixed(2))}}
  for(let j=0;j<n;j++){svg.append('text').attr('x',90+j*cs+cs/2).attr('y',22).attr('text-anchor','middle').attr('fill','#999').attr('font-size',10).text(cd.labels[j].slice(0,10))}}
function renderCmpBars(){const el=cmpBarsCv.value;if(!el||!window.d3)return;el.innerHTML='';const cd=cmpStructured.value?.chart_data?.bars;if(!cd||!cd.labels?.length)return
  const W=el.clientWidth||500,H=320,n=cd.labels.length,m=cd.datasets?.length||0,barW=Math.max(8,Math.floor((W-80)/n/(m+1)))
  const svg=d3.select(el).append('svg').attr('viewBox',`0 0 ${W} ${H}`)
  const maxY=10;const y=d3.scaleLinear().domain([0,maxY]).range([H-40,20])
  svg.append('g').attr('transform','translate(60,0)').call(d3.axisLeft(y).ticks(5))
  cd.datasets.forEach((ds,j)=>{cd.labels.forEach((l,i)=>{const v=ds.data[i]||0;const bx=70+i*(m+1)*barW+j*barW
    svg.append('rect').attr('x',bx).attr('y',y(v)).attr('width',barW-2).attr('height',H-40-y(v)).attr('fill',PAPER_COLORS[j]).attr('rx',2)
    svg.append('text').attr('x',bx+barW/2).attr('y',y(v)-4).attr('text-anchor','middle').attr('fill','#ccc').attr('font-size',9).text(v.toFixed(1))})})}
function renderCmpStack(){const el=cmpStackCv.value;if(!el||!window.d3)return;el.innerHTML='';const cd=cmpStructured.value?.chart_data?.stacked;if(!cd||!cd.labels?.length)return
  const W=el.clientWidth||500,H=320,n=cd.labels?.length||0,barW=Math.max(20,Math.floor((W-80)/n))
  const svg=d3.select(el).append('svg').attr('viewBox',`0 0 ${W} ${H}`)
  const dims=cd.dimensions||[];const data=cd.data||[]
  const maxTotal=d3.max(data.map(row=>row.reduce((a,b)=>a+b,0)))||10
  const y=d3.scaleLinear().domain([0,maxTotal]).range([H-40,20])
  svg.append('g').attr('transform','translate(60,0)').call(d3.axisLeft(y).ticks(5))
  for(let i=0;i<n;i++){let acc=0;for(let j=0;j<dims.length;j++){const v=data[i]?.[j]||0
    svg.append('rect').attr('x',70+i*barW).attr('y',y(acc+v)).attr('width',barW-3).attr('height',y(acc)-y(acc+v)).attr('fill',PAPER_COLORS[j]).attr('rx',1);acc+=v}}}
// Export functions
function exportCompareMD(){const s=cmpResult.value?.synthesis||'';const t=cmpResult.value?.structured;let md='# 论文横向对比报告\n\n';if(t?.scores){md+='## 评分矩阵\n\n';for(const[pid,sc] of Object.entries(t.scores)){md+=`- ${pid}: `+Object.entries(sc).map(([k,v])=>`${k}=${v}`).join(', ')+'\n'}}md+='\n## 综合分析\n\n'+s;downloadBlob(md,'comparison_report.md','text/markdown')}
function exportCompareCSV(){const t=cmpStructured.value;if(!t||!t.scores){alert('无结构化数据可导出');return}const dims=cmpDimNames.value;let csv='paper_id,'+dims.join(',')+'\n';for(const[pid,sc] of Object.entries(t.scores)){csv+=pid+','+dims.map(d=>sc[d]||'').join(',')+'\n'}downloadBlob(csv,'comparison_scores.csv','text/csv')}
function exportCompareHTML(){const s=cmpResult.value?.synthesis||'';const t=cmpResult.value?.structured;let h='<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>对比报告</title><style>body{font-family:sans-serif;max-width:960px;margin:0 auto;padding:20px;background:#0f0f1a;color:#e0e0e0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #333;padding:8px}th{background:#1a1a2e}h1,h2{color:#a78bfa}</style></head><body><h1>论文横向对比报告</h1>';if(t?.scores){h+='<h2>评分矩阵</h2><table><tr><th>论文</th>'+cmpDimNames.value.map(d=>`<th>${d}</th>`).join('')+'</tr>';for(const[pid,sc] of Object.entries(t.scores)){h+=`<tr><td>${pid}</td>`+cmpDimNames.value.map(d=>`<td>${sc[d]||'-'}</td>`).join('')+'</tr>'}h+='</table>'}h+='<h2>综合分析</h2><pre style="white-space:pre-wrap">'+s+'</pre></body></html>';downloadBlob(h,'comparison_report.html','text/html')}
function downloadBlob(content,filename,mime){const b=new Blob(['﻿'+content],{type:mime+';charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=filename;a.click();URL.revokeObjectURL(a.href)}
async function loadCompareHistory(){showCmpHistory.value=!showCmpHistory.value;if(showCmpHistory.value)try{cmpHistory.value=(await api('/api/paper/compare/history')).comparisons||[]}catch(e){console.error(e)}}
async function loadCompareResult(cid){showCmpHistory.value=false;try{cmpResult.value=await api(`/api/paper/compare/${cid}`);cmpStructured.value=cmpResult.value.structured||null;cmpDimNames.value=cmpResult.value.structured?.scores?Object.keys(cmpResult.value.structured.scores[Object.keys(cmpResult.value.structured.scores)[0]]||{}):[];cmpPapers.value=(cmpResult.value.paper_ids||[]).map(id=>papers.value.find(p=>p.paper_id===id)||{paper_id:id,title:id});cmpTab.value='overview'}catch(e){console.error(e)}}
async function deleteCompareResult(cid){if(!confirm('确认删除此对比记录？'))return;try{await fetch(`/api/paper/compare/${cid}`,{method:'DELETE'});loadCompareHistory()}catch(e){console.error(e)}}
function onDrop(e){drag.value=false;const f=e.dataTransfer?.files?.[0];if(f?.name?.toLowerCase().endsWith('.pdf')||f?.name?.toLowerCase().endsWith('.txt'))upFile.value=f}
function onFile(e){const f=e.target?.files?.[0];if(f)upFile.value=f}

// Markdown editor helpers
function renderMd(t){if(!t)return''
  let m=[],mb=[]
  t=t.replace(/\$\$([\s\S]*?)\$\$/g,(_,c)=>{mb.push('$$'+c+'$$');return'◈M'+mb.length+'◈'})
  t=t.replace(/(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)/g,(_,c)=>{mb.push('$'+c+'$');return'◈M'+mb.length+'◈'})
  t=t.replace(/```mermaid\s*\n([\s\S]*?)```/g,(_,c)=>{m.push(c);return'%%M'+m.length+'%%'})
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/^#### (.+)$/gm,'<h5>$1</h5>').replace(/^### (.+)$/gm,'<h4>$1</h4>')
    .replace(/^## (.+)$/gm,'<h3>$1</h3>').replace(/^# (.+)$/gm,'<h2>$1</h2>')
    .replace(/^- (.+)$/gm,'<li>$1</li>').replace(/(<li>.*<\/li>\n?)+/g,'<ul>$&</ul>')
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\*(.+?)\*/g,'<em>$1</em>')
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/^> (.+)$/gm,'<blockquote><p>$1</p></blockquote>')
    .replace(/\|(.+)\|/g,m=>'<tr>'+m.slice(1,-1).split('|').map(c=>/\-{3,}/.test(c)?'<th>'+c+'</th>':'<td>'+c.trim()+'</td>').join('')+'</tr>')
    .replace(/(<tr>.*<\/tr>\n?)+/g,'<table>$&</table>')
    .replace(/^---$/gm,'<hr>')
    .replace(/\n\n/g,'</p><p>')
    .replace(/\n/g,'<br>').replace(/^(?!<)/,'<p>').replace(/(?<!>)$/,'</p>')
    .replace(/%%M(\d+)%%/g,(_,i)=>'<pre class="mermaid">'+m[parseInt(i)-1]+'</pre>')
    .replace(/◈M(\d+)◈/g,(_,i)=>mb[parseInt(i)-1])}
function insMd(before,after){const ta=document.querySelector('.editor-textarea');if(!ta)return;const s=ta.selectionStart,e=ta.selectionEnd,v=upText.value;if(s!==e){upText.value=v.slice(0,s)+before+v.slice(s,e)+after+v.slice(e);ta.focus();ta.setSelectionRange(s+before.length,e+before.length)}else{const nl=s>0&&v[s-1]!=='\n'?'\n':'';upText.value=v.slice(0,s)+nl+before+after+v.slice(e);ta.focus();ta.setSelectionRange(s+nl.length+before.length,s+nl.length+before.length)}}
function syncScroll(fromPreview){const ed=document.querySelector('.editor-textarea'),pv=document.querySelector('.pc');if(!ed||!pv)return;if(fromPreview){ed.scrollTop=(pv.scrollTop/(pv.scrollHeight-pv.clientHeight))*(ed.scrollHeight-ed.clientHeight)}else{pv.scrollTop=(ed.scrollTop/(ed.scrollHeight-ed.clientHeight))*(pv.scrollHeight-pv.clientHeight)}}
function fmtSize(bytes){if(!bytes)return'0 B';const u=['B','KB','MB','GB'];let i=0,s=bytes;while(s>=1024&&i<u.length-1){s/=1024;i++}return s.toFixed(i===0?0:1)+' '+u[i]}
function fmtJson(a){try{return JSON.stringify(typeof a==='string'?JSON.parse(a):a,null,2)}catch{return String(a)}}
	function renderAudit(raw){
		if(!raw) return '<div class="empty">暂无审计数据。</div>';
		var d;
		try { d = typeof raw === 'string' ? JSON.parse(raw) : raw; }
		catch(e) { return '<div class="empty">审计数据格式错误。</div>'; }
		if(!d || !Array.isArray(d.issues)) return '<div class="empty">暂无审计数据。</div>';

		if(d.overall_score !== undefined && d.dimension_scores) {
			return _renderAuditNew(d);
		}
		return _renderAuditLegacy(d);
	}
	function _renderAuditLegacy(d){
		var issues = d.issues;
		var passed = d.passed !== false;
		var SEV = {
			'严重': { border: '#ff4757', bg: 'rgba(255,71,87,.04)' },
			'一般': { border: '#e8950a', bg: 'rgba(255,165,2,.04)' },
			'建议': { border: '#5b66fa', bg: 'rgba(55,66,250,.04)' }
		};

		var counts = { '严重': 0, '一般': 0, '建议': 0 };
		for(var i = 0; i < issues.length; i++) {
			var s = issues[i].severity;
			counts[SEV[s] ? s : '建议']++;
		}

		var bc = passed ? 'audit-pass' : 'audit-fail';
		var icon = passed ? '&#10003;' : '&#10007;';
		var tt = passed ? (issues.length ? '审计通过（有建议）' : '审计通过') : '审计未通过';
		var sub = passed
			? ('共 ' + issues.length + ' 项：<span class="asev-chip asev-crit">' + counts['严重'] + '</span><span class="asev-chip asev-warn">' + counts['一般'] + '</span><span class="asev-chip asev-info">' + counts['建议'] + '</span>')
			: (counts['严重'] + ' 项严重问题需立即修复，共 ' + issues.length + ' 项问题待处理');

		var h = '<div class="audit-banner ' + bc + '"><div class="audit-banner-icon">' + icon + '</div><div><div class="audit-banner-title">' + tt + '</div><div class="audit-banner-sub">' + sub + '</div></div></div>';

		if(!issues.length) {
			h += '<div style="text-align:center;padding:var(--s-8)"><div style="width:48px;height:48px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:22px;font-weight:700;background:rgba(0,200,117,.18);color:#00c875;margin-bottom:var(--s-3)">&#10003;</div><p style="font-size:16px;color:var(--c-text);margin:0 0 4px">一切正常</p><p style="font-size:13px;color:var(--c-text-muted);margin:0">未发现任何质量问题，全部检查均已通过。</p></div>';
			return h;
		}

		var gr = { '严重': [], '一般': [], '建议': [] };
		for(var i = 0; i < issues.length; i++) {
			var iss = issues[i];
			var sv = iss.severity;
			(gr[SEV[sv] ? sv : '建议']).push(iss);
		}

		var sevs = ['严重', '一般', '建议'];
		for(var gi = 0; gi < sevs.length; gi++) {
			var sev = sevs[gi];
			var list = gr[sev];
			if(!list.length) continue;
			var m = SEV[sev];
			h += '<div class="asev-group"><div class="asev-head" style="color:' + m.border + '">' + sev + ' &middot; ' + list.length + ' 项</div>';
			for(var j = 0; j < list.length; j++) {
				var iss = list[j];
				var isLLM = iss.dimension === 'LLM审计';
				var dim = String(iss.dimension || '未知维度').replace(/&/g, '&amp;').replace(/</g, '&lt;');
				var dh = isLLM
					? renderMd(iss.detail || '')
					: String(iss.detail || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
				h += '<div class="audit-issue" style="border-left:3px solid ' + m.border + ';background:' + m.bg + '"><div class="audit-issue-dim">' + dim + '</div><div class="audit-issue-detail">' + dh + '</div>';
				if(iss.fix) h += '<div class="audit-issue-fix">&#9888; 修复建议：' + String(iss.fix).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>';
				h += '</div>';
			}
			h += '</div>';
		}
		setTimeout(retypeset, 100);
		return h;
	}
	function _renderAuditNew(d){
		var issues = d.issues;
		var passed = d.passed !== false;
		var SEV = {
			'严重': { border: '#ff4757', bg: 'rgba(255,71,87,.04)' },
			'一般': { border: '#e8950a', bg: 'rgba(255,165,2,.04)' },
			'建议': { border: '#5b66fa', bg: 'rgba(55,66,250,.04)' }
		};
		var GRADES = {
			'A': { bg: 'rgba(34,197,94,.12)', border: '#22c55e', text: '#4ade80' },
			'B': { bg: 'rgba(99,102,241,.12)', border: '#6366f1', text: '#818cf8' },
			'C': { bg: 'rgba(245,158,11,.12)', border: '#f59e0b', text: '#fbbf24' },
			'D': { bg: 'rgba(249,115,22,.12)', border: '#f97316', text: '#fb923c' },
			'F': { bg: 'rgba(239,68,68,.12)', border: '#ef4444', text: '#f87171' },
		};

		var h = '';

		// ── Grade Badge ──
		var grade = d.overall_grade || '?';
		var g = GRADES[grade] || { bg: 'rgba(255,255,255,.06)', border: 'rgba(255,255,255,.15)', text: 'var(--c-text-muted)' };
		h += '<div class="audit-grade" style="background:' + g.bg + ';border-color:' + g.border + ';color:' + g.text + '">' + grade + '</div>';

		// ── Score Bar ──
		var score = d.overall_score || 0;
		var sc = score >= 90 ? '#4ade80' : score >= 70 ? '#818cf8' : score >= 50 ? '#fbbf24' : '#f87171';
		h += '<div class="audit-scorebar">';
		h += '<div class="audit-scorebar-track"><div class="audit-scorebar-fill" style="width:' + score + '%;background:' + sc + '"></div></div>';
		h += '<span class="audit-scorebar-label" style="color:' + sc + '">' + score + '/100</span>';
		h += '</div>';

		// ── Pass/Fail Banner ──
		var bc = passed ? 'audit-pass' : 'audit-fail';
		var icon = passed ? '&#10003;' : '&#10007;';
		var tt = passed ? (issues.length ? '审计通过（有建议）' : '审计通过') : '审计未通过';
		h += '<div class="audit-banner ' + bc + '" style="margin-bottom:var(--s-4)"><div class="audit-banner-icon">' + icon + '</div><div><div class="audit-banner-title">' + tt + '</div><div class="audit-banner-sub">' + (d.summary ? d.summary.replace(/&/g,'&amp;').replace(/</g,'&lt;').slice(0,120) : '') + '</div></div></div>';

		// ── Dimension Score Cards ──
		var ds = d.dimension_scores || {};
		var dimMap = [
			{ key: 'translation_completeness', label: '翻译完整性' },
			{ key: 'formula_completeness', label: '公式完整性' },
			{ key: 'analysis_coverage', label: '分析覆盖度' },
			{ key: 'consistency', label: '一致性' },
			{ key: 'overall_quality', label: '综合质量' },
		];
		h += '<div class="audit-dimcards">';
		for(var i = 0; i < dimMap.length; i++) {
			var dm = dimMap[i];
			var dv = typeof ds[dm.key] === 'number' ? ds[dm.key] : 0;
			var dc = dv >= 90 ? '#4ade80' : dv >= 70 ? '#818cf8' : dv >= 50 ? '#fbbf24' : '#f87171';
			h += '<div class="audit-dimcard">';
			h += '<div class="audit-dimcard-label">' + dm.label + '</div>';
			h += '<div class="audit-dimcard-bar"><div class="audit-dimcard-fill" style="width:' + dv + '%;background:' + dc + '"></div></div>';
			h += '<div class="audit-dimcard-score" style="color:' + dc + '">' + dv + '</div>';
			h += '</div>';
		}
		h += '</div>';

		// ── Issue Statistics ──
		var counts = { '严重': 0, '一般': 0, '建议': 0 };
		for(var i2 = 0; i2 < issues.length; i2++) {
			var s2 = issues[i2].severity;
			counts[SEV[s2] ? s2 : '建议']++;
		}
		h += '<div class="audit-stats">';
		h += '<span class="asev-chip asev-crit">严重 ' + counts['严重'] + '</span>';
		h += '<span class="asev-chip asev-warn">一般 ' + counts['一般'] + '</span>';
		h += '<span class="asev-chip asev-info">建议 ' + counts['建议'] + '</span>';
		h += '<span style="font-size:12px;color:var(--c-text-muted);margin-left:auto">共 ' + issues.length + ' 项问题</span>';
		h += '</div>';

		// ── Issue List ──
		if(issues.length) {
			var gr = { '严重': [], '一般': [], '建议': [] };
			for(var i3 = 0; i3 < issues.length; i3++) {
				var iss3 = issues[i3];
				var sv3 = iss3.severity;
				(gr[SEV[sv3] ? sv3 : '建议']).push(iss3);
			}
			var sevs = ['严重', '一般', '建议'];
			for(var gi = 0; gi < sevs.length; gi++) {
				var sev = sevs[gi];
				var list = gr[sev];
				if(!list.length) continue;
				var m = SEV[sev];
				h += '<div class="asev-group"><div class="asev-head" style="color:' + m.border + '">' + sev + ' &middot; ' + list.length + ' 项</div>';
				for(var j = 0; j < list.length; j++) {
					var iss = list[j];
					var dim = String(iss.dimension || '未知维度').replace(/&/g, '&amp;').replace(/</g, '&lt;');
					h += '<div class="audit-issue" style="border-left:3px solid ' + m.border + ';background:' + m.bg + '">';
					h += '<div class="audit-issue-dim">' + dim + '</div>';
					h += '<div class="audit-issue-detail">' + renderMd(iss.detail || '') + '</div>';
					if(iss.fix) h += '<div class="audit-issue-fix">&#9888; 修复建议：' + String(iss.fix).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>';
					h += '</div>';
				}
				h += '</div>';
			}
		} else {
			h += '<div style="text-align:center;padding:var(--s-6)"><div style="width:48px;height:48px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:22px;font-weight:700;background:rgba(0,200,117,.18);color:#00c875;margin-bottom:var(--s-3)">&#10003;</div><p style="font-size:16px;color:var(--c-text);margin:0 0 4px">一切正常</p><p style="font-size:13px;color:var(--c-text-muted);margin:0">未发现任何质量问题，全部检查均已通过。</p></div>';
		}

		// ── Summary ──
		if(d.summary) {
			h += '<div class="audit-summary">' + renderMd(d.summary) + '</div>';
		}

		setTimeout(retypeset, 100);
		return h;
	}


// Existing
async function loadAll(){try{apiOk.value=true;apiStatus.value='在线';await Promise.all([loadRagPapers(),loadPapers()])}catch{apiOk.value=false;apiStatus.value='离线'}}
async function loadRagPapers(){ragPapers.value=await api('/api/rag/papers')}
function onChatKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();doChat()}}
function autoResize(){const t=chatTextarea.value;t.style.height='auto';t.style.height=Math.min(t.scrollHeight,160)+'px'}
function onChatFile(e){const f=e.target?.files?.[0];if(f){chatAttach.value=f;chatMsgs.value.push({role:'user',content:'[上传文件: '+f.name+']'});doUploadChatFile()}}
async function doUploadChatFile(){const f=chatAttach.value;if(!f)return;try{const fd=new FormData();fd.append('file',f);const r=await api('/api/paper/upload',{method:'POST',body:fd});chatMsgs.value.push({role:'agent',content:'已开始分析论文: **'+r.title+'**\n\n语言: '+(r.language==='en'?'英文':'中文')+'\n状态: '+r.status+'\n\n分析完成后可在「论文研读」中查看。'});chatAttach.value=null}catch(e){chatMsgs.value.push({role:'agent',content:'文件上传失败: '+e.message});chatAttach.value=null}}
async function doChat(){const m=chatIn.value.trim();if(!m)return;chatMsgs.value.push({role:'user',content:m});chatReply.value=await api('/api/agent/chat',{method:'POST',body:JSON.stringify({message:m})});chatMsgs.value.push({role:'agent',content:chatReply.value.reply});chatIn.value='';const t=chatTextarea.value;if(t){t.style.height='auto'}}
async function doRagSearch(){const r=await api('/api/rag/search',{method:'POST',body:JSON.stringify({query:ragQ.value,top_k:ragK.value,tag:ragTag.value||null,modality:ragModality.value||null,rerank:true})});ragResults.value=r.results||[]}
async function doRagCtx(){const r=await api('/api/rag/context',{method:'POST',body:JSON.stringify({query:ragQ.value,top_k:ragK.value})});ragCtx.value=r.context;ragResults.value=r.results||[]}
async function doRagSuggest(){ragSuggest.value=JSON.stringify(await api('/api/rag/suggest',{method:'POST',body:JSON.stringify({query:ragQ.value,top_k:ragK.value})}),null,2)}
async function doIngest(){const tags=pfTags.value.split(',').map(s=>s.trim()).filter(Boolean);await api('/api/rag/papers',{method:'POST',body:JSON.stringify({...pf.value,tags})});await loadRagPapers()}
async function doDeletePaper(pid){await api('/api/rag/papers/'+pid,{method:'DELETE'});await loadRagPapers()}
async function doAsk(){const q=askQ.value.trim();if(!q||!curPaperId.value)return;askA.value='思考中…';try{const r=await api('/api/paper/'+curPaperId.value+'/ask',{method:'POST',body:JSON.stringify({question:q})});askA.value=r.answer||'无回答'}catch(e){askA.value='提问失败: '+e.message}}

watch(tab,t=>{if(t==='papers')loadPapers();if(t==='rag')loadRagPapers()})
onMounted(loadAll)
</script>
