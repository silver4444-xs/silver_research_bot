import { nextTick } from 'vue'

let _mjRetries = 0

export function useMarkdown(upText) {
	function retypeset(){if(window.MathJax&&window.MathJax.typesetPromise){try{MathJax.typesetClear();MathJax.typesetPromise()}catch(e){console.error('MathJax typeset error:',e)}_mjRetries=0}
	else if(_mjRetries<30){_mjRetries++;setTimeout(retypeset,500)}
	if(window.mermaid)try{mermaid.run({querySelector:'.mermaid'})}catch(e){}}
	document.addEventListener('MathJax:ready',()=>{_mjRetries=0;retypeset()})
	function retypesetDeferred(){nextTick(()=>{setTimeout(retypeset,0)})}
	function _stripBackendWarnings(t){return t.replace(/(\n\n> ⚠[^\n]*)+$/,'')}
	function renderAll(t){t=_stripBackendWarnings(t);let h=renderMd(t);retypesetDeferred();return h}
	function sanitizeLatex(s){return s.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,"").replace(/#/g,'\\#').replace(/%/g,'\\%').replace(/~/g,'\\textasciitilde{}').replace(/[""]/g,'"').replace(/['']/g,"'").replace(/ /g,' ')}
	function renderFormula(t){if(!t)return'';if(t.indexOf('<div class="frow"')>-1||t.indexOf('<style>')>-1){
	  // Sanitize LaTeX special chars
	  t=t.replace(/(<div class="fexpr">)([\s\S]*?)(<\/div>)/g,function(_,o,c,e){c=sanitizeLatex(c);if(!/\\begin\{(?:align|aligned|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|cases|array|gather|gathered|split|eqnarray)\}/.test(c))c=c.replace(/&/g,'\\&');if(/^\s*\$/.test(c))return o+c+e;c=c.replace(/^\s+|\s+$/g,'');return o+'$$'+c+'$$'+e})
	  // Auto-wrap unwrapped LaTeX in .fmean: \cmd{...} patterns
	  t=t.replace(/(<div class="fmean">)([\s\S]*?)(<\/div>)/g,function(_,o,c,e){(function(sc){var mb=[];sc=sc.replace(/\$\$[\s\S]*?\$\$|\$[\s\S]*?\$/g,function(m){mb.push(m);return'￰M'+(mb.length-1)+'￰'});sc=sc.replace(/(\\[a-zA-Z]+(?:\{[^}]*\})+(?!\$))/g,'$$$1$');sc=sc.replace(/￰M(\d+)￰/g,function(_,i){var m=mb[parseInt(i)];if(m[0]==='$'&&m[1]!=='$')m=m.replace(/&/g,'\\&');return m});return sc})(c);return o+c+e})
	  t=t.replace(/\n{3,}/g,'\n\n');retypesetDeferred();return t}return renderAll(t)}
	function renderMd(t){if(!t)return''
	  let m=[],mb=[],ht=[]
	  t=t.replace(/\$\$([\s\S]*?)\$\$/g,(_,c)=>{mb.push('$$'+c+'$$');return '◈M'+mb.length+'◈'})
	  t=t.replace(/(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)/g,(_,c)=>{mb.push('$'+c+'$');return '◈M'+mb.length+'◈'})
	  t=t.replace(/\\\[([\s\S]*?)\\\]/g,(_,c)=>{mb.push('\\['+c+'\\]');return '◈M'+mb.length+'◈'})
	  t=t.replace(/\\\(([\s\S]*?)\\\)/g,(_,c)=>{mb.push('\\('+c+'\\)');return '◈M'+mb.length+'◈'})
	  t=t.replace(/```mermaid\s*\n([\s\S]*?)```/g,(_,c)=>{m.push(c);return'%%M'+m.length+'%%'})
	  t=t.replace(/<(img|div|span|strong)\b[^>]*>|<\/(div|span|strong)>/gi,m=>{ht.push(m);return '◈H'+ht.length+'◈'})
	  t=t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
	  t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
	    .replace(/^#### (.+)$/gm,'<h5>$1</h5>').replace(/^### (.+)$/gm,'<h4>$1</h4>')
	    .replace(/^## (.+)$/gm,'<h3>$1</h3>').replace(/^# (.+)$/gm,'<h2>$1</h2>')
	    .replace(/^(\d+)\. (.+)$/gm,'<!--OLI-->$2<!--/OLI-->').replace(/^- (.+)$/gm,'<!--ULI-->$1<!--/ULI-->')
	    .replace(/(<!--OLI-->.*?<!--\/OLI-->\n?)+/g,'<ol>$&</ol>').replace(/(<!--ULI-->.*?<!--\/ULI-->\n?)+/g,'<ul>$&</ul>')
	    .replace(/<!--OLI-->/g,'<li>').replace(/<!--\/OLI-->/g,'</li>')
	    .replace(/<!--ULI-->/g,'<li>').replace(/<!--\/ULI-->/g,'</li>')
	    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\*(.+?)\*/g,'<em>$1</em>')
	    .replace(/`([^`]+)`/g,'<code>$1</code>')
	    .replace(/^> (.+)$/gm,'<blockquote><p>$1</p></blockquote>')
	    .replace(/\|(.+)\|/g,m=>'<tr>'+m.slice(1,-1).split('|').map(c=>/\-{3,}/.test(c)?'<th>'+c+'</th>':'<td>'+c.trim()+'</td>').join('')+'</tr>')
	    .replace(/(<tr>.*<\/tr>\n?)+/g,'<table>$&</table>')
	    .replace(/^---$/gm,'<hr>')
	  let tbl=[]
	  t=t.replace(/(<table>[\s\S]*?<\/table>)/g,(_,c)=>{tbl.push(c);return '◈T'+tbl.length+'◈'})
	  t=t.replace(/\n\n/g,'</p><p>')
	    .replace(/\n/g,'<br>').replace(/^(?!<)/,'<p>').replace(/(?<!>)$/,'</p>')
	    .replace(/%%M(\d+)%%/g,(_,i)=>'<pre class="mermaid">'+m[parseInt(i)-1]+'</pre>')
	    .replace(/◈T(\d+)◈/g,(_,i)=>tbl[parseInt(i)-1])
	    .replace(/◈H(\d+)◈/g,(_,i)=>ht[parseInt(i)-1])
	    .replace(/◈M(\d+)◈/g,(_,i)=>sanitizeLatex(mb[parseInt(i)-1]))
	  return t}
	function insMd(before,after){const ta=document.querySelector('.editor-textarea');if(!ta)return;const s=ta.selectionStart,e=ta.selectionEnd,v=upText.value;if(s!==e){upText.value=v.slice(0,s)+before+v.slice(s,e)+after+v.slice(e);ta.focus();ta.setSelectionRange(s+before.length,e+before.length)}else{const nl=s>0&&v[s-1]!=='\n'?'\n':'';upText.value=v.slice(0,s)+nl+before+after+v.slice(e);ta.focus();ta.setSelectionRange(s+nl.length+before.length,s+nl.length+before.length)}}
	function syncScroll(fromPreview){const ed=document.querySelector('.editor-textarea'),pv=document.querySelector('.pc');if(!ed||!pv)return;if(fromPreview){ed.scrollTop=(pv.scrollTop/(pv.scrollHeight-pv.clientHeight))*(ed.scrollHeight-ed.clientHeight)}else{pv.scrollTop=(ed.scrollTop/(ed.scrollHeight-ed.clientHeight))*(pv.scrollHeight-pv.clientHeight)}}
	function fmtSize(bytes){if(!bytes)return'0 B';const u=['B','KB','MB','GB'];let i=0,s=bytes;while(s>=1024&&i<u.length-1){s/=1024;i++}return s.toFixed(i===0?0:1)+' '+u[i]}

	return { renderMd, renderFormula, renderAll, sanitizeLatex, insMd, syncScroll, fmtSize, retypeset, retypesetDeferred }
}
