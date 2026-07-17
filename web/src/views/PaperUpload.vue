<template>
  <div style="margin-top:var(--s-5)">
    <div class="card">
      <div class="ch"><h3>提交论文</h3></div>
      <div class="utabs">
        <button :class="['ut', upMode==='file' && 'act']" @click="upMode='file'">
          <svg viewBox="0 0 24 24" fill="none"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          PDF 文件上传
        </button>
        <button :class="['ut', upMode==='text' && 'act']" @click="upMode='text'">
          <svg viewBox="0 0 24 24" fill="none"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>
          文本粘贴输入
        </button>
      </div>

      <!-- File mode -->
      <div v-if="upMode==='file'" class="upload-card" :class="{ drag }" @dragover.prevent="drag=true" @dragleave="drag=false" @drop.prevent="onDrop">
        <div class="drop-zone">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 12 15 15"/></svg>
          <p>拖拽 PDF 文件到此处或点击按钮选择</p>
          <input ref="fi" type="file" accept=".pdf" hidden @change="onFile" />
          <button class="btn ba" @click="$refs.fi.click()">选择 PDF 文件</button>
          <div v-if="upFile" class="file-card">
            <div class="file-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><polyline points="9 15 12 12 15 15"/></svg></div>
            <div class="file-card-info">
              <div class="file-card-name">{{ upFile.name }}</div>
              <div class="file-card-meta">{{ fmtSize(upFile.size) }} &middot; PDF 文档</div>
            </div>
            <button class="file-card-remove" @click="upFile=null" title="移除文件"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
        </div>
      </div>

      <!-- Text mode -- Rich Markdown Editor -->
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
        <div style="align-self:flex-end"><button class="btn bp" :disabled="!canSubmit || uploading" @click="doUpload">{{ uploading ? '分析中…' : '开始分析' }}</button></div>
      </div>

      <div v-if="uploading" class="ptrack" style="margin-top:var(--s-4)">
        <div class="pt-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> {{ progressMsg }}</div>
        <div class="pt-stages"><template v-if="pStages.length"><div v-for="s in pStages" :key="s.id" :class="['pts', s.s]">
          <div class="ptd"><svg v-if="s.s==='done'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="14" height="14"><polyline points="20 6 9 17 4 12"/></svg><div v-else-if="s.s==='active'" class="pts-spin"></div><div v-else class="ptd-empty"></div></div>
          <span>{{ s.label }}</span>
        </div></template></div>
      </div>
      <div v-if="upStatus" class="ibox" :class="{ 'ibox-ok': !uploading }" style="margin-top:var(--s-3)"><strong>{{ upStatus }}</strong></div>
    </div>
  </div>
</template>

<script setup>
import { inject } from 'vue'

const papers = inject('papers')
const md = inject('markdown')

const {
  upFile, upText, upLang, upMode, drag,
  canSubmit, uploading, upStatus, progressMsg,
  pStages, STAGES,
  onDrop, onFile, doUpload,
} = papers

const { insMd, syncScroll, renderMd, fmtSize } = md
</script>
