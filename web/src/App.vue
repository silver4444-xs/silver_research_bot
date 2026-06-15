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
    <div v-if="ps==='list'" class="card" style="margin-top:var(--s-5)"><div class="ch"><h3>已分析论文</h3><div class="row rt"><button class="btn bs bsm" @click="loadPapers">刷新</button><button class="btn ba bsm" @click="ps='upload'">上传新论文</button></div></div>
      <div class="plist"><article v-for="p in papers" :key="p.paper_id" class="pi" @click="openPaper(p.paper_id)"><div><strong>{{ p.title }}</strong><div class="pmeta"><span class="chip" :class="p.language==='en'?'cb2':'cg'">{{ p.language==='en'?'EN':'ZH' }}</span><span>{{ p.page_count||'?' }}页 · {{ p.formula_count||'?' }}公式</span><span>{{ (p.uploaded_at||'').slice(0,10) }}</span></div></div><button class="btn bg bsm" style="color:var(--c-danger)" @click.stop="delPaper(p.paper_id)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="15" height="15"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button></article><p v-if="!papers.length" class="empty">暂无已分析的论文。</p></div>
    </div>

    <!-- Detail -->
    <div v-if="ps==='detail'&&pdet" class="pd" style="margin-top:var(--s-5)">
      <div class="card"><div class="ch"><h3>{{ pdet.title }}</h3><div class="row rt"><button class="btn bg bsm" @click="ps='list';clearPoll()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="16" height="16"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>返回</button><button class="btn ba bsm" @click="doExport">导出全部</button></div></div><div class="pdm"><span class="chip" :class="pdet.language==='en'?'cb2':'cg'">{{ pdet.language==='en'?'英文':'中文' }}</span><span style="font-size:13px;color:var(--c-text-secondary)">{{ pdet.page_count||'?' }}页 · {{ pdet.formula_count||'?' }}公式</span><span v-if="pdet.status==='processing'" class="chip ca">分析中</span><span v-if="pdet.has_translation" class="chip cb2">已翻译</span><span v-if="pdet.status==='completed'" class="chip cp">已审计</span></div></div>

      <div v-if="pdet.status==='processing'&&!hasAnyResult" class="card"><div class="pt-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> 分析进行中…</div><div class="pt-stages"><template v-if="pStages.length"><div v-for="s in pStages" :key="s.id" :class="['pts',s.s]"><div class="ptd"><div v-if="s.s==='active'" class="pts-spin"></div></div><span>{{ s.label }}</span></div></template></div></div>

      <div class="dtabs"><button v-for="t in dtabs" :key="t.id" :class="['st',dt===t.id&&'act']" @click="dt=t.id">{{ t.label }}</button></div>
      <div class="card" v-if="dt==='translation'&&pdet.translation"><div class="ch"><h3>全文翻译</h3></div><div class="md" v-html="renderAll(pdet.translation)"></div></div>
      <div v-if="dt==='translation'&&!pdet.translation" class="card"><p class="empty">该论文暂无翻译结果（可能为中文论文或翻译尚未完成）</p></div>
      <template v-for="d in dims" :key="d.id"><div class="card" v-if="dt===d.id&&pdet[d.id]"><div class="ch"><h3>{{ d.label }}</h3></div><div class="md" v-html="renderAll(pdet[d.id])"></div></div></template>
      <div class="card" v-if="dt==='formulas'&&pdet.formula_explanations"><div class="ch"><h3>公式解读</h3></div><div class="md" v-html="renderFormula(pdet.formula_explanations)"></div></div>
      <div class="card" v-if="dt==='visualization'&&pdet.visualization_html"><div class="ch"><h3>可视化分析</h3></div><iframe :srcdoc="pdet.visualization_html" class="vis-frame" sandbox="allow-scripts allow-same-origin" title="可视化分析"></iframe></div>
      <div class="card" v-if="dt==='audit'&&pdet.audit"><div class="ch"><h3>审计报告</h3></div><pre class="ap">{{ fmtJson(pdet.audit) }}</pre></div>
    </div>

    <!-- Compare -->
    <div v-if="ps==='compare'" class="card" style="margin-top:var(--s-5)"><div class="ch"><h3>横向对比</h3></div><label for="cs">选择论文（Ctrl/Cmd 多选）</label><select id="cs" v-model="cmpIds" multiple class="cmp-sel"><option v-for="p in papers" :key="p.paper_id" :value="p.paper_id">{{ p.title?.slice(0,80) }}</option></select><button class="btn ba" :disabled="cmpIds.length<2" @click="doCompare" style="margin-top:var(--s-3)">开始对比（已选{{cmpIds.length}}篇）</button><div v-if="cmpResult" class="md" style="margin-top:var(--s-4)" v-html="renderMd(cmpResult.synthesis||'对比完成')"></div></div>
  </section>

  <!-- ═══ Original ═══ -->
  <header class="hero"><div><p class="eyebrow">Paper Analysis Platform</p><h2>研究论文深度分析工作台</h2><p class="subtitle">上传 PDF 或粘贴文本即可自动翻译、四维系统分析、公式解读与可视化审计。</p></div><div class="sbar"><div class="si"><strong>API</strong><span><span class="dot" :class="apiOk?'dot-ok':'dot-warn'"></span>{{ apiStatus }}</span></div><div class="si"><strong>已分析</strong><span>{{ papers.length }} 篇</span></div><button class="btn bp bsm" @click="loadAll">刷新</button></div></header>


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



  <section v-else-if="tab==='rag'" class="grid gr"><div class="card"><div class="ch"><h3>文献检索</h3></div><label for="rq">查询</label><input id="rq" v-model="ragQ" /><div class="row"><div><label for="rk">Top K</label><input id="rk" v-model.number="ragK" type="number" min="1" /></div><div><label for="rt">Tag</label><input id="rt" v-model="ragTag" /></div></div><div class="row" style="margin-top:var(--s-2)"><button class="btn bp bsm" @click="doRagSearch">检索</button><button class="btn bs bsm" @click="doRagCtx">上下文</button><button class="btn bs bsm" @click="doRagSuggest">建议</button></div></div><div class="card"><div class="ch"><h3>文献入库</h3></div><label for="pt">标题</label><input id="pt" v-model="pf.title" /><label for="pa">摘要</label><textarea id="pa" v-model="pf.abstract" rows="2"></textarea><label for="pc2">内容</label><textarea id="pc2" v-model="pf.content" rows="4"></textarea><label for="pz">标签</label><input id="pz" v-model="pfTags" /><button class="btn bp bf" @click="doIngest">入库</button></div><div class="card sp2"><div class="ch"><h3>文献列表</h3><button class="btn bs bsm" @click="loadRagPapers">刷新</button></div><div class="rlist"><article v-for="p in ragPapers" :key="p.paper_id" class="ri"><div><strong>{{ p.title }}</strong><p>{{ p.paper_id }}</p></div><div style="text-align:right;font-size:12px"><span class="chip cp">{{ (p.tags||[]).join(',') }}</span><div style="color:var(--c-text-secondary)">{{ p.created_at }}</div></div></article><p v-if="!ragPapers.length" class="empty">暂无文献。</p></div></div></section>
</main>
</div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'

const icons={
  chat:'<path d="M20 2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4l4 4 4-4h4a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/>',
  file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/>',
  layers:'<path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>',
}
const nav=[{id:'agent',label:'Agent 对话',icon:icons.chat},{id:'papers',label:'论文研读',icon:icons.file},{id:'rag',label:'文献 RAG',icon:icons.layers}]
const psub=[{id:'upload',label:'上传论文'},{id:'list',label:'论文列表'},{id:'compare',label:'横向对比'}]
const dtabs=[{id:'translation',label:'全文翻译'},{id:'system_model',label:'系统模型'},{id:'problem_formulation',label:'问题表述'},{id:'optimization_algorithm',label:'优化算法'},{id:'experiment_design',label:'实验设计'},{id:'formulas',label:'公式解读'},{id:'visualization',label:'可视化'},{id:'audit',label:'审计报告'}]
const dims=[{id:'system_model',label:'系统模型分析'},{id:'problem_formulation',label:'问题表述分析'},{id:'optimization_algorithm',label:'优化算法分析'},{id:'experiment_design',label:'实验设计分析'}]

const tab=ref('papers');const ps=ref('upload');const dt=ref('translation');const upMode=ref('file')
const upFile=ref(null);const upText=ref('');const upLang=ref('auto');const uploading=ref(false);const drag=ref(false);const upStatus=ref('');const progressMsg=ref('');const curPaperId=ref('');const pStages=ref([]);let _pollTimer=null
const STAGES=[{id:'parse',label:'文档解析'},{id:'translate',label:'全文翻译'},{id:'analyze',label:'四维分析'},{id:'formula_explain',label:'公式解读'},{id:'visualize',label:'可视化'},{id:'audit',label:'质量审计'}]
const papers=ref([]);const pdet=ref(null);const cmpIds=ref([]);const cmpResult=ref(null)
const apiOk=ref(true);const apiStatus=ref('未知');const ragPapers=ref([])
const chatMsgs=ref([{role:'agent',content:'你好！上传 PDF 或粘贴论文文本即可自动完成翻译、四维分析、公式解读。'}])
const chatIn=ref('');const chatReply=ref(null);const chatAttach=ref(null)

const ragQ=ref('retrieval augmented');const ragK=ref(5);const ragTag=ref('');const ragResults=ref([]);const ragCtx=ref('');const ragSuggest=ref('');const ragSnap=ref({})
const pfTags=ref('rag,llm');const pf=ref({title:'',abstract:'',content:''})
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
function renderFormula(t){if(!t)return'';if(t.indexOf('<div class="frow"')>-1||t.indexOf('<style>')>-1){setTimeout(retypeset,100);return t}return renderAll(t)}
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
async function delPaper(pid){if(!confirm('确认删除？'))return;try{await fetch(`/api/paper/${pid}`,{method:'DELETE'});await loadPapers();pdet.value=null}catch(e){console.error(e)}}
async function doCompare(){try{cmpResult.value=await api('/api/paper/compare',{method:'POST',body:JSON.stringify({paper_ids:cmpIds.value})})}catch(e){console.error(e)}}
function onDrop(e){drag.value=false;const f=e.dataTransfer?.files?.[0];if(f?.name?.toLowerCase().endsWith('.pdf')||f?.name?.toLowerCase().endsWith('.txt'))upFile.value=f}
function onFile(e){const f=e.target?.files?.[0];if(f)upFile.value=f}

// Markdown editor helpers
function renderMd(t){if(!t)return''
  let m=[];t=t.replace(/```mermaid\s*\n([\s\S]*?)```/g,(_,c)=>{m.push(c);return'%%M'+m.length+'%%'})
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
    .replace(/%%M(\d+)%%/g,(_,i)=>'<pre class="mermaid">'+m[parseInt(i)-1]+'</pre>')}
function insMd(before,after){const ta=document.querySelector('.editor-textarea');if(!ta)return;const s=ta.selectionStart,e=ta.selectionEnd,v=upText.value;if(s!==e){upText.value=v.slice(0,s)+before+v.slice(s,e)+after+v.slice(e);ta.focus();ta.setSelectionRange(s+before.length,e+before.length)}else{const nl=s>0&&v[s-1]!=='\n'?'\n':'';upText.value=v.slice(0,s)+nl+before+after+v.slice(e);ta.focus();ta.setSelectionRange(s+nl.length+before.length,s+nl.length+before.length)}}
function syncScroll(fromPreview){const ed=document.querySelector('.editor-textarea'),pv=document.querySelector('.pc');if(!ed||!pv)return;if(fromPreview){ed.scrollTop=(pv.scrollTop/(pv.scrollHeight-pv.clientHeight))*(ed.scrollHeight-ed.clientHeight)}else{pv.scrollTop=(ed.scrollTop/(ed.scrollHeight-ed.clientHeight))*(pv.scrollHeight-pv.clientHeight)}}
function fmtSize(bytes){if(!bytes)return'0 B';const u=['B','KB','MB','GB'];let i=0,s=bytes;while(s>=1024&&i<u.length-1){s/=1024;i++}return s.toFixed(i===0?0:1)+' '+u[i]}
function fmtJson(a){try{return JSON.stringify(typeof a==='string'?JSON.parse(a):a,null,2)}catch{return String(a)}}

// Existing
async function loadAll(){try{apiOk.value=true;apiStatus.value='在线';await Promise.all([loadRagPapers(),loadPapers()])}catch{apiOk.value=false;apiStatus.value='离线'}}
async function loadRagPapers(){ragPapers.value=await api('/api/rag/papers')}
function onChatKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();doChat()}}
function autoResize(){const t=chatTextarea.value;t.style.height='auto';t.style.height=Math.min(t.scrollHeight,160)+'px'}
function onChatFile(e){const f=e.target?.files?.[0];if(f){chatAttach.value=f;chatMsgs.value.push({role:'user',content:'[上传文件: '+f.name+']'});doUploadChatFile()}}
async function doUploadChatFile(){const f=chatAttach.value;if(!f)return;try{const fd=new FormData();fd.append('file',f);const r=await api('/api/paper/upload',{method:'POST',body:fd});chatMsgs.value.push({role:'agent',content:'已开始分析论文: **'+r.title+'**\n\n语言: '+(r.language==='en'?'英文':'中文')+'\n状态: '+r.status+'\n\n分析完成后可在「论文研读」中查看。'});chatAttach.value=null}catch(e){chatMsgs.value.push({role:'agent',content:'文件上传失败: '+e.message});chatAttach.value=null}}
async function doChat(){const m=chatIn.value.trim();if(!m)return;chatMsgs.value.push({role:'user',content:m});chatReply.value=await api('/api/agent/chat',{method:'POST',body:JSON.stringify({message:m})});chatMsgs.value.push({role:'agent',content:chatReply.value.reply});chatIn.value='';const t=chatTextarea.value;if(t){t.style.height='auto'}}
async function doRagSearch(){const r=await api('/api/rag/search',{method:'POST',body:JSON.stringify({query:ragQ.value,top_k:ragK.value,tag:ragTag.value||null})});ragResults.value=r.results||[]}
async function doRagCtx(){const r=await api('/api/rag/context',{method:'POST',body:JSON.stringify({query:ragQ.value,top_k:ragK.value})});ragCtx.value=r.context;ragResults.value=r.results||[]}
async function doRagSuggest(){ragSuggest.value=JSON.stringify(await api('/api/rag/suggest',{method:'POST',body:JSON.stringify({query:ragQ.value,top_k:ragK.value})}),null,2)}
async function doIngest(){const tags=pfTags.value.split(',').map(s=>s.trim()).filter(Boolean);await api('/api/rag/papers',{method:'POST',body:JSON.stringify({...pf.value,tags})});await loadRagPapers()}

watch(tab,t=>{if(t==='papers')loadPapers();if(t==='rag')loadRagPapers()})
onMounted(loadAll)
</script>
