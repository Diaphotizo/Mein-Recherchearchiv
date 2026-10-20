'use strict';
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const norm=s=>String(s).toLocaleLowerCase('de').replaceAll('ß','ss').normalize('NFKD').replace(/[\u0300-\u036f]/g,'');
const date=s=>s && /^\d{4}-\d{2}-\d{2}/.test(s) && s.slice(0,4)>'1800' ? new Date(s.slice(0,10)+'T12:00:00').toLocaleDateString('de-DE'):'Datum unbekannt';
const num=n=>new Intl.NumberFormat('de-DE').format(n||0);
const safeUrl=s=>/^https?:\/\//i.test(s||'')?s:'#';
let state,current,selected=new Set(),page=1,terms=[],request=0,readerRequest=0,debounce,poll,updateStarted=false,activePage=1;
const filterIds=['court','case','kind','source','from','to','text_status','sort'];
async function api(path,options={}){
  const response=await fetch(path,options);
  const data=await response.json();
  if(!response.ok) throw new Error(data.error||'Die Anfrage ist fehlgeschlagen.');
  return data;
}
const post=(path,body)=>api(path,{method:'POST',headers:{'Content-Type':'application/json','X-BIP-Token':state.csrf},body:JSON.stringify(body)});
function notice(text,error=false,partial=false){
  $('#notice-message').textContent=text;$('#notice').hidden=!text;
  $('#notice').classList.toggle('error',error);$('#notice').classList.toggle('partial',partial);
}
function highlight(text){
  text=String(text||'');
  let normalized='',positions=[],offset=0;
  for(const char of text){
    const n=norm(char);normalized+=n;
    for(let i=0;i<n.length;i++) positions.push([offset,offset+char.length]);
    offset+=char.length;
  }
  const ranges=[];
  for(const raw of terms){
    const term=norm(raw);if(!term) continue;
    let start=0,pos;
    while((pos=normalized.indexOf(term,start))!==-1){
      if(positions[pos]&&positions[pos+term.length-1]) ranges.push([positions[pos][0],positions[pos+term.length-1][1]]);
      start=pos+term.length;
    }
  }
  ranges.sort((a,b)=>a[0]-b[0]);const merged=[];
  for(const pair of ranges){const last=merged.at(-1);if(last&&pair[0]<=last[1]) last[1]=Math.max(last[1],pair[1]);else merged.push(pair);}
  let out='',last=0;
  for(const [start,end] of merged){out+=esc(text.slice(last,start))+'<mark>'+esc(text.slice(start,end))+'</mark>';last=end;}
  return out+esc(text.slice(last));
}
function renderTags(){
  const filter=norm($('#tag-search').value),groups=new Map();
  for(const t of state.tags||[]){
    if(filter&&!norm(t.label).includes(filter)) continue;
    if(!groups.has(t.group)) groups.set(t.group,[]);
    groups.get(t.group).push(t);
  }
  $('#tag-groups').innerHTML=[...groups].map(([group,tags],i)=>'<details'+(filter||i===0||tags.some(t=>selected.has(t.label))?' open':'')+'><summary>'+esc(group)+'</summary>'+tags.map(t=>'<label class="tag-option"><input type="checkbox" value="'+esc(t.label)+'"'+(selected.has(t.label)?' checked':'')+'><span>'+esc(t.label)+'</span><small>'+num(t.count)+'</small></label>').join('')+'</details>').join('');
  $('#tag-groups').querySelectorAll('input').forEach(e=>e.onchange=()=>toggle(e.value,e.checked));
  $('#tag-counter').textContent=selected.size+' gewählt';
  $('#selected').innerHTML=[...selected].map(t=>'<button data-remove="'+esc(t)+'">'+esc(t)+' ×</button>').join('');
  $('#selected').querySelectorAll('button').forEach(b=>b.onclick=()=>toggle(b.dataset.remove,false));
  document.querySelectorAll('[data-quick]').forEach(b=>b.classList.toggle('active',selected.has(b.dataset.quick)));
}
function toggle(t,on=!selected.has(t)){on?selected.add(t):selected.delete(t);page=1;renderTags();search();}
function params(){
  const p=new URLSearchParams();
  if($('#q').value.trim()) p.set('q',$('#q').value.trim());
  for(const key of filterIds) if($('#'+key).value) p.set(key,$('#'+key).value);
  if($('#germany').checked) p.set('germany','1');
  for(const t of selected) p.append('tag',t);
  p.set('page',page);return p;
}
function textBadge(a){
  if(!a.text_pages) return '<span class="text-state">Ohne Textschicht</span>';
  if(a.text_pages<a.pages) return '<span class="text-state">'+num(a.text_pages)+' / '+num(a.pages)+' Seiten mit Text</span>';
  return '';
}
function pageLink(id,p=1){return '/api/courts/pdf?id='+encodeURIComponent(id)+'#page='+Math.max(1,Number(p)||1);}
async function search(){
  const serial=++request,p=params();
  history.replaceState(null,'','/icc-icj/?'+p);
  $('#results').setAttribute('aria-busy','true');
  try{
    const result=await api('/api/courts/search?'+p);
    if(serial!==request) return;
    terms=result.highlight_terms||[];page=result.page;
    $('#result-count').textContent=num(result.total)+' Treffer';
    $('#result-hint').textContent=$('#q').value?'Fundstellen im Dokumenttext und in den Quellenangaben':selected.size?'Alle gewählten Schlagwörter treffen zu':$('#sort').value==='oldest'?'Älteste Dokumente zuerst':'Dokumente nach gewählter Sortierung';
    $('#results').innerHTML=result.items.length?result.items.map(a=>
      '<article class="article"><div class="article-top"><time>'+date(a.date)+'</time><span>·</span><span class="type">'+esc(a.court)+' · '+esc(a.kind||'Dokument')+'</span><span class="pages">'+num(a.pages)+' S.</span></div>'+
      '<button class="title-button" data-open="'+esc(a.id)+'">'+highlight(a.title)+'</button>'+
      '<div class="case-label"><span class="case-number">'+esc(a.document_id||a.case_id)+'</span> · '+esc(a.case_title||'')+'</div>'+
      (a.source?'<div class="credit">'+esc(a.source)+'</div>':'')+
      (a.snippets||[]).map(s=>'<p class="snippet" data-open="'+esc(a.id)+'" data-page="'+s.page+'"><span class="section">Fundstelle · PDF-Seite '+s.page+'</span>'+highlight(s.text)+'</p>').join('')+
      textBadge(a)+'<div class="article-bottom"><div class="chips">'+(a.tags||[]).slice(0,6).map(t=>'<button class="chip" data-tag="'+esc(t)+'">'+esc(t)+'</button>').join('')+((a.tags||[]).length>6?'<span class="more-tags">+'+(a.tags.length-6)+'</span>':'')+
      '</div><div class="article-links"><a href="'+pageLink(a.id)+'" target="_blank" rel="noopener">PDF öffnen ↗</a><a href="'+esc(safeUrl(a.url))+'" target="_blank" rel="noopener">Quelle ↗</a></div></div></article>').join(''):
      (state.total ? '<div class="empty"><h3>Keine passenden Dokumente gefunden.</h3><p>Entferne ein Schlagwort, erweitere den Zeitraum oder suche mit weniger Begriffen. Seiten ohne Textschicht sind nur über die Quellenangaben auffindbar.</p><button id="empty-reset" class="button">Alle Filter zurücksetzen</button></div>'
      : '<div class="empty"><h3>Die ICC/ICJ-Sammlung enthält noch keine Dokumente.</h3><p>Über „Jetzt aktualisieren“ werden die eingerichteten öffentlichen Gerichtsquellen geprüft und verfügbare PDFs in die lokale Sammlung aufgenommen.</p></div>');
    $('#results').querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>openDocument(b.dataset.open,Number(b.dataset.page)||1));
    $('#results').querySelectorAll('[data-tag]').forEach(b=>b.onclick=()=>toggle(b.dataset.tag,true));
    if($('#empty-reset')) $('#empty-reset').onclick=reset;
    $('#pagination').innerHTML=result.pages>1?'<button id="prev" class="button"'+(page===1?' disabled':'')+'>← Zurück</button><span>Seite '+page+' von '+result.pages+'</span><button id="next" class="button"'+(page===result.pages?' disabled':'')+'>Weiter →</button>':'';
    if($('#prev')) $('#prev').onclick=()=>{page--;search();$('#result-count').scrollIntoView({behavior:'smooth'});};
    if($('#next')) $('#next').onclick=()=>{page++;search();$('#result-count').scrollIntoView({behavior:'smooth'});};
  }catch(e){notice(e.message,true);}
  finally{if(serial===request) $('#results').setAttribute('aria-busy','false');}
}
async function loadState(){
  const saved=Object.fromEntries(filterIds.map(k=>[k,$('#'+k).value]));
  state=await api('/api/courts/state');
  $('#stats').innerHTML='<span><b>'+num(state.total)+'</b> PDFs</span><span><b>'+num(state.courts?.ICC)+'</b> ICC</span><span><b>'+num(state.courts?.ICJ)+'</b> ICJ</span><span><b>'+num((state.cases||[]).length)+'</b> Verfahren</span><span class="stand">Englische Originale</span>';
  $('#case').innerHTML='<option value="">Alle Verfahren</option>'+(state.cases||[]).map(c=>'<option value="'+esc(c.id)+'">'+esc(c.court+' '+c.id+' · '+c.title)+' ('+num(c.count)+')</option>').join('');
  $('#kind').innerHTML='<option value="">Alle Dokumenttypen</option>'+Object.entries(state.kinds||{}).sort().map(([k,n])=>'<option value="'+esc(k)+'">'+esc(k)+' ('+num(n)+')</option>').join('');
  $('#source').innerHTML='<option value="">Alle Stellen</option>'+(state.sources||[]).map(s=>'<option value="'+esc(s.name)+'">'+esc(s.name)+' ('+num(s.count)+')</option>').join('');
  for(const [k,v] of Object.entries(saved)) $('#'+k).value=v;
  const coverage=state.coverage||{};
  $('#coverage-content').innerHTML='<p>'+esc(coverage.scope||'Öffentliche englische Verfahrensdokumente mit Bezug zu Israel, Palästina und Deutschland.')+'</p><p>'+esc(coverage.language||'')+'</p><p>'+esc(coverage.icc||'')+'</p><p>'+esc(coverage.known_scope_limit||'')+'</p><p>Fehlende Datumsangaben werden nicht durch Ersatzdaten ergänzt. Ein Verfahrensdatum bezeichnet nicht automatisch den Zeitpunkt des geschilderten Geschehens.</p>'+
    '<dl><dt>Öffentliche Quellen</dt><dd><a href="https://www.icj-cij.org/" target="_blank" rel="noopener">ICJ</a> · <a href="https://www.legal-tools.org/" target="_blank" rel="noopener">Legal Tools / ICC</a></dd><dt>Quellstand</dt><dd>'+date(state.source_checked_at||coverage.checked_at)+'</dd><dt>PDF-Seiten</dt><dd>'+num(state.total_pages)+'</dd><dt>Seiten mit Textschicht</dt><dd>'+num(state.text_pages)+'</dd><dt>Ohne erkannten Text</dt><dd>'+num(state.no_text_pages)+'</dd></dl>';
  if((state.coverage_notes||[]).length) $('#coverage-content').innerHTML+='<ul>'+state.coverage_notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul>';
  $('#text-coverage').textContent=num(state.text_pages)+' von '+num(state.total_pages)+' PDF-Seiten mit auslesbarem Text. Seiten ohne Textschicht bleiben im Original-PDF verfügbar.';
  $('#updated-at').textContent='Quellstand: '+date(state.source_checked_at||coverage.checked_at)+' · Suche vollständig lokal';
  renderTags();
}
async function openDocument(id,p=1){
  const serial=++readerRequest;
  try{
    const result=await api('/api/courts/document?id='+encodeURIComponent(id)+'&page='+p+'&q='+encodeURIComponent($('#q').value));
    if(serial!==readerRequest) return;
    current=result.document;activePage=result.page.number;
    const a=current;
    $('#reader-pdf').href=pageLink(a.id,activePage);
    const evid=a.tag_evidence||{};
    $('#reader-header').innerHTML='<div class="reader-header"><div class="eyebrow">'+esc(a.court)+' · '+esc(a.kind||'Dokument')+' · '+num(a.pages)+' PDF-Seiten</div><h2>'+highlight(a.title)+'</h2><div class="reader-meta source-meta"><b>'+date(a.date)+'</b><br>'+esc(a.document_id||a.case_id)+' · '+esc(a.case_title||'')+(a.source?'<br>'+esc(a.source):'')+'<br><a href="'+esc(safeUrl(a.url))+'" target="_blank" rel="noopener">Originalquelle öffnen ↗</a></div>'+
      (a.verification_notes?'<p class="hint">'+esc(a.verification_notes)+'</p>':'')+
      ((result.matches||[]).length?'<p class="hint">Fundstellen der aktuellen Suche</p><div class="match-list">'+result.matches.map(m=>'<button data-jump="'+m.page+'">Seite '+m.page+'</button>').join('')+'</div>':'')+
      '</div><div class="reader-tags"><div class="chips">'+(a.tags||[]).map(t=>'<button class="chip" data-evidence="'+esc(t)+'">'+esc(t)+'</button>').join('')+'</div><button id="edit-open" class="button">Schlagwörter & Notizen bearbeiten</button><p class="hint" id="evidence-hint">Schlagwörter öffnen eine zugehörige PDF-Textseite.</p>'+(a.notes?'<div class="court-notes"><b>Meine Notizen</b><br>'+esc(a.notes)+'</div>':'')+'</div>';
    $('#page-number').value=activePage;$('#page-number').max=a.pages;
    $('#page-total').textContent='von '+num(a.pages);
    $('#page-prev').disabled=activePage<=1;$('#page-next').disabled=activePage>=a.pages;
    $('#page-text').innerHTML=highlight(result.page.text||'');
    $('#page-message').textContent=result.page.text?.trim()?'Ausgelesene Textschicht. Für das verbindliche Layout und Bilder das Original-PDF öffnen.':'Für diese Seite wurde kein auslesbarer Text gefunden. Bitte das Original-PDF öffnen; die Seite kann ein Scan oder eine Bildtafel sein.';
    $('#edit-open').onclick=openEditor;
    $('#reader-header').querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>openDocument(a.id,Number(b.dataset.jump)));
    $('#reader-header').querySelectorAll('[data-evidence]').forEach(b=>b.onclick=()=>{
      const evidence=evid[b.dataset.evidence]||[];
      const n=Array.isArray(evidence)?evidence[0]:evidence.page;
      if(n) openDocument(a.id,typeof n==='object'?n.page:Number(n));
      else $('#evidence-hint').textContent='Dieses Schlagwort stammt aus den Quellenangaben oder wurde selbst ergänzt.';
    });
    if(!$('#reader').open) $('#reader').showModal();
    if(p!==1) $('.page-controls').scrollIntoView({block:'start'});else $('#reader-scroll').scrollTop=0;
  }catch(e){notice(e.message,true);}
}
function openEditor(){
  const known=new Set((state.tags||[]).map(t=>t.label));
  $('#edit-tags').innerHTML=(state.tags||[]).map(t=>'<label><input type="checkbox" value="'+esc(t.label)+'"'+(current.tags.includes(t.label)?' checked':'')+'>'+esc(t.label)+'</label>').join('');
  $('#custom-tags').value=current.tags.filter(t=>!known.has(t)).join(', ');
  $('#notes').value=current.notes||current.manual?.notes||'';
  $('#edit-message').textContent='';$('#editor').showModal();
}
async function checkUpdate(){
  try{
    const s=await api('/api/courts/status');
    const errors=Array.isArray(s.errors)?s.errors:[];
    $('#update-details').hidden=!s.message && !errors.length;
    $('#update-summary').textContent=(s.finished_at||s.time?date(s.finished_at||s.time)+' · ':'')+(s.message||'');
    $('#update-errors').innerHTML=errors.slice(0,30).map(e=>'<li><b>'+esc(e.source||'Quelle')+'</b>: '+esc(e.error||e.message||String(e))+(e.url?' <a href="'+esc(safeUrl(e.url))+'" target="_blank" rel="noopener">Quelle öffnen ↗</a> · <a href="/icc-icj/?q='+encodeURIComponent(e.url)+'">Im Archiv suchen</a>':'')+'</li>').join('')+(errors.length>30?'<li>Weitere '+(errors.length-30)+' Hinweise im lokalen Aktualisierungsprotokoll.</li>':'');
    const running=s.phase==='running';
    $('#update').disabled=running;
    $('#update').textContent=running?'↻ Wird aktualisiert …':'↻ Jetzt aktualisieren';
    if(running){
      notice(s.message||'Öffentliche Register werden geprüft …');
      if(!poll) poll=setInterval(checkUpdate,2500);
    }else{
      const wasRunning=Boolean(poll);
      if(poll){clearInterval(poll);poll=null;await loadState();await search();}
      if(updateStarted || wasRunning){
        const errs=Array.isArray(s.errors)?s.errors.length:0;
        notice((s.message||'Abgleich beendet.')+(errs?' Einzelheiten stehen unter „Umfang, Sprache und Stand der Sammlung“.':''),s.phase==='error',s.phase==='partial');
      }
      updateStarted=false;
    }
  }catch(e){notice(e.message,true);}
}
function reset(){
  $('#q').value='';selected.clear();$('#tag-search').value='';$('#germany').checked=false;
  for(const k of filterIds) $('#'+k).value=k==='sort'?'newest':'';
  page=1;renderTags();search();
}
$('#search-form').onsubmit=e=>{e.preventDefault();clearTimeout(debounce);page=1;$('#sort').value=$('#q').value?'relevance':'newest';search();};
$('#q').oninput=()=>{clearTimeout(debounce);debounce=setTimeout(()=>{page=1;$('#sort').value=$('#q').value?'relevance':'newest';search();},350);};
for(const k of [...filterIds,'germany']) $('#'+k).onchange=()=>{page=1;search();};
$('#tag-search').oninput=renderTags;$('#reset').onclick=reset;
$('#notice-dismiss').onclick=()=>notice('');
document.querySelectorAll('[data-quick]').forEach(b=>b.onclick=()=>toggle(b.dataset.quick));
$('#reader-close').onclick=()=>$('#reader').close();
$('#editor-close').onclick=()=>$('#editor').close();$('#edit-cancel').onclick=()=>$('#editor').close();
$('#page-prev').onclick=()=>openDocument(current.id,activePage-1);
$('#page-next').onclick=()=>openDocument(current.id,activePage+1);
$('#page-form').onsubmit=e=>{e.preventDefault();openDocument(current.id,Number($('#page-number').value));};
$('#page-number').onchange=()=>openDocument(current.id,Number($('#page-number').value));
$('#edit-form').onsubmit=async e=>{
  e.preventDefault();
  const tags=[...$('#edit-tags').querySelectorAll('input:checked')].map(i=>i.value);
  tags.push(...$('#custom-tags').value.split(',').map(x=>x.trim()).filter(Boolean));
  try{
    await post('/api/courts/annotations',{id:current.id,tags:[...new Set(tags)],notes:$('#notes').value});
    await loadState();await search();await openDocument(current.id,activePage);$('#editor').close();
  }catch(e){$('#edit-message').textContent=e.message;}
};
$('#mobile-filters').onclick=()=>{
  const open=$('.workspace').classList.toggle('filter-visible');
  $('#mobile-filters').setAttribute('aria-expanded',String(open));
  $('#mobile-filters').textContent=open?'Filter ausblenden':'Filter & Schlagwörter anzeigen';
};
$('#update').onclick=async()=>{
  $('#update').disabled=true;updateStarted=true;
  notice('Aktualisierung wird gestartet …');
  try{
    await post('/api/courts/update',{});
    if(!poll) poll=setInterval(checkUpdate,2500);
  }catch(e){notice(e.message,true);$('#update').disabled=false;updateStarted=false;}
};
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();$('#q').focus();}});
(async()=>{
  try{
    await loadState();
    const initial=new URLSearchParams(location.search);
    $('#q').value=initial.get('q')||'';
    for(const k of filterIds) if(initial.has(k)) $('#'+k).value=initial.get(k);
    $('#germany').checked=initial.get('germany')==='1';selected=new Set(initial.getAll('tag'));page=Number(initial.get('page'))||1;
    renderTags();await search();await checkUpdate();
  }catch(e){notice('Die Sammlung konnte nicht geladen werden: '+e.message,true);}
})();
