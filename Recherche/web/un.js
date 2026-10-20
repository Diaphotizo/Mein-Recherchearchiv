'use strict';
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const norm=s=>String(s).toLocaleLowerCase('de').replaceAll('ß','ss').normalize('NFKD').replace(/[\u0300-\u036f]/g,'');
const date=(s,year)=>s && /^\d{4}-\d{2}-\d{2}/.test(s) && s.slice(0,4)>'1800' ? new Date(s.slice(0,10)+'T12:00:00').toLocaleDateString('de-DE'):/^[12]\d{3}$/.test(String(year||'')) ? String(year):'Datum unbekannt';
const num=n=>new Intl.NumberFormat('de-DE').format(n||0);
const safeUrl=s=>/^https?:\/\//i.test(s||'')?s:'#';
let state,current,selected=new Set(),page=1,terms=[],request=0,readerRequest=0,debounce,poll,updateStarted=false,activePage=1;
const filterIds=['relation','body','kind','language','from','to','sort'];
const relationNames={germany:'Deutschland',israel:'Israel',both:'Deutschland UND Israel',either:'Deutschland ODER Israel',palestine:'Palästina, Gaza, Westbank & UNRWA'};
const languageName=s=>({en:'Englisch',eng:'Englisch',english:'Englisch',de:'Deutsch',deu:'Deutsch',ger:'Deutsch',german:'Deutsch','en-de':'Englisch / Deutsch'}[String(s||'').toLowerCase()]||s||'Sprache unbekannt');
function sourceCheckLabel(value){
  if(value.source_check_partial){
    const latest=value.last_source_attempt_at||value.source_checked_at;
    return latest?'Teilabgleich: '+date(latest):'Teilabgleich: Datum noch nicht erfasst';
  }
  if(value.source_checked_at) return 'Quellstand: '+date(value.source_checked_at);
  if(value.last_source_attempt_at) return 'Letzter Prüfversuch: '+date(value.last_source_attempt_at);
  return 'Quellstand: noch kein abgeschlossener Abgleich';
}
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
  document.querySelectorAll('[data-relation]').forEach(b=>b.classList.toggle('active',$('#relation').value===b.dataset.relation));
}
function toggle(t,on=!selected.has(t)){on?selected.add(t):selected.delete(t);page=1;renderTags();search();}
function params(){
  const p=new URLSearchParams();
  if($('#q').value.trim()) p.set('q',$('#q').value.trim());
  for(const key of filterIds) if($('#'+key).value) p.set(key,$('#'+key).value);
  for(const t of selected) p.append('tag',t);
  p.set('page',page);return p;
}
function textBadge(a){
  if(!a.text_pages) return '<span class="text-state">Ohne Textschicht</span>';
  if(a.text_pages<a.pages) return '<span class="text-state">'+num(a.text_pages)+' / '+num(a.pages)+' Seiten mit Text</span>';
  return '';
}
function pageLink(id,p=1){return '/api/un/pdf?id='+encodeURIComponent(id)+'#page='+Math.max(1,Number(p)||1);}
function relationBadges(a){
  return ['germany','israel','palestine'].filter(k=>a.relations?.[k]).map(k=>'<span>'+esc(relationNames[k])+'</span>').join('');
}
function renderCard(a){
  const credits=[a.body,a.author].filter(Boolean).filter((v,i,all)=>all.indexOf(v)===i);
  return '<article class="article"><div class="article-top"><time>'+date(a.date,a.publication_year)+'</time><span>·</span><span class="type">'+esc(a.kind||'UN-Dokument')+'</span><span class="pages">'+num(a.pages)+' S. · '+esc(languageName(a.language))+'</span></div>'+
    '<button class="title-button" data-open="'+esc(a.id)+'">'+highlight(a.title||'Titel nicht angegeben')+'</button>'+
    (a.symbol?'<div class="case-label"><span class="case-number">'+highlight(a.symbol)+'</span></div>':'')+
    (credits.length?'<div class="credit">'+credits.map(esc).join(' · ')+'</div>':'')+
    '<div class="un-relations">'+relationBadges(a)+'</div>'+
    (a.snippets||[]).map(s=>'<p class="snippet" data-open="'+esc(a.id)+'" data-page="'+Number(s.page||1)+'"><span class="section">Fundstelle · PDF-Seite '+num(s.page||1)+'</span>'+highlight(s.text)+'</p>').join('')+
    textBadge(a)+'<div class="article-bottom"><div class="chips">'+(a.tags||[]).slice(0,6).map(t=>'<button class="chip" data-tag="'+esc(t)+'">'+esc(t)+'</button>').join('')+((a.tags||[]).length>6?'<span class="more-tags">+'+(a.tags.length-6)+'</span>':'')+
    '</div><div class="article-links">'+(a.pages?'<a href="'+pageLink(a.id)+'" target="_blank" rel="noopener">PDF öffnen ↗</a>':'<span class="un-availability">PDF noch nicht verfügbar</span>')+(safeUrl(a.url)!=='#'?'<a href="'+esc(safeUrl(a.url))+'" target="_blank" rel="noopener">Quelle ↗</a>':'')+'</div></div></article>';
}
async function search(){
  const serial=++request,p=params();
  history.replaceState(null,'','/un/?'+p);
  $('#results').setAttribute('aria-busy','true');
  try{
    const result=await api('/api/un/search?'+p);
    if(serial!==request) return;
    terms=result.highlight_terms||[];page=result.page||1;
    $('#result-count').textContent=num(result.total)+' Treffer';
    $('#result-hint').textContent=$('#q').value?'Fundstellen im Dokumenttext und in den Quellenangaben':selected.size?'Alle gewählten Schlagwörter treffen zu':$('#sort').value==='oldest'?'Älteste Dokumente zuerst':'Dokumente nach gewählter Sortierung';
    $('#results').innerHTML=(result.items||[]).length?result.items.map(renderCard).join(''):Number(state.total)===0?
      '<div class="empty"><h3>Die UN-Sammlung enthält noch keine Dokumente.</h3><p>Über „Jetzt aktualisieren“ werden die eingerichteten öffentlichen UN-Quellen geprüft. Hier erscheinen nur tatsächlich erfasste Dokumente; der Umfang steht oben unter „Umfang, Sprache und Stand der Sammlung“.</p></div>':
      '<div class="empty"><h3>Keine passenden Dokumente gefunden.</h3><p>Entferne ein Schlagwort, erweitere den Zeitraum oder wähle einen anderen Länderbezug. Seiten ohne Textschicht sind nur über die Quellenangaben auffindbar.</p><button id="empty-reset" class="button">Alle Filter zurücksetzen</button></div>';
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
  state=await api('/api/un/state');
  $('#stats').innerHTML='<span><b>'+num(state.total)+'</b> PDF-Fassungen</span><span><b>'+num(state.countries?.germany)+'</b> Deutschland-Bezug</span><span><b>'+num(state.countries?.israel)+'</b> Israel-Bezug</span><span><b>'+num(state.countries?.palestine)+'</b> Palästina & UNRWA</span>';
  for(const o of $('#relation').options) o.textContent=o.value==='all'?'Gesamte UN-Sammlung ('+num(state.total)+')':relationNames[o.value]+' ('+num(state.countries?.[o.value])+')';
  $('#body').innerHTML='<option value="">Alle Stellen</option>'+(state.bodies||[]).map(b=>'<option value="'+esc(b.name)+'">'+esc(b.name)+' ('+num(b.count)+')</option>').join('');
  $('#kind').innerHTML='<option value="">Alle Dokumenttypen</option>'+Object.entries(state.kinds||{}).sort().map(([k,n])=>'<option value="'+esc(k)+'">'+esc(k)+' ('+num(n)+')</option>').join('');
  $('#language').innerHTML='<option value="">Alle vorhandenen Fassungen</option>'+Object.entries(state.languages||{}).map(([k,n])=>'<option value="'+esc(k)+'">'+esc(languageName(k))+' ('+num(n)+')</option>').join('');
  for(const [k,v] of Object.entries(saved)) if(['from','to'].includes(k)||[...($('#'+k).options||[])].some(o=>o.value===v)) $('#'+k).value=v;
  const languages=Object.entries(state.languages||{}).filter(([,n])=>n>0).map(([k,n])=>esc(languageName(k))+' ('+num(n)+')').join(', ')||'Noch keine Fassungen erfasst';
  $('#coverage-content').innerHTML='<p>Eigenständige Sammlung öffentlich zugänglicher UN-Dokumente mit Deutschland- oder Israel-Bezug sowie dem zusätzlichen Themenbereich Palästina, Gaza, Westbank und UNRWA. Dokumente können mehreren Bereichen zugeordnet sein; die Länderzahlen werden daher nicht addiert.</p><p>Aufgenommen werden englische und vorhandene deutsche Fassungen. Eine deutsche Fassung wird nur angezeigt, wenn sie tatsächlich vorliegt. Eigene Übersetzungen werden nicht erzeugt.</p><dl><dt>Vorhandene Sprachen</dt><dd>'+languages+'</dd><dt>Letzter Quellenabgleich</dt><dd>'+esc(sourceCheckLabel(state))+'</dd><dt>PDF-Seiten</dt><dd>'+num(state.total_pages)+'</dd><dt>Seiten mit Textschicht</dt><dd>'+num(state.text_pages)+'</dd><dt>Ohne erkannten Text</dt><dd>'+num(state.no_text_pages)+'</dd></dl>';
  if((state.coverage_notes||[]).length) $('#coverage-content').innerHTML+='<ul>'+state.coverage_notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul>';
  $('#text-coverage').textContent=state.total?num(state.text_pages)+' von '+num(state.total_pages)+' PDF-Seiten mit auslesbarem Text. Seiten ohne Textschicht bleiben im Original-PDF verfügbar.':'Englische Originale und vorhandene deutsche Fassungen · Die Sammlung wird schrittweise aufgebaut.';
  $('#updated-at').textContent=sourceCheckLabel(state)+' · Suche vollständig lokal';
  renderTags();
}
async function openDocument(id,p=1){
  const serial=++readerRequest;
  try{
    const result=await api('/api/un/document?id='+encodeURIComponent(id)+'&page='+p+'&q='+encodeURIComponent($('#q').value));
    if(serial!==readerRequest) return;
    current=result.document;activePage=result.page.number;
    const a=current;
    $('#reader-pdf').href=pageLink(a.id,activePage);
    const evid=a.tag_evidence||{};
    const reasons=Object.entries(a.relation_reasons||{}).map(([key,values])=>{
      const list=Array.isArray(values)?values:[values];
      return list.filter(Boolean).map(reason=>'<li><b>'+esc(relationNames[key]||key)+':</b> '+esc(typeof reason==='object'?(reason.reason||reason.text||''):reason)+'</li>').join('');
    }).join('');
    const credits=[['Dokumentnummer',a.symbol],['UN-Organ / Stelle',a.body],['Verfasser / Einreicher',a.author],['Sprache',languageName(a.language)]];
    $('#reader-header').innerHTML='<div class="reader-header"><div class="eyebrow">UN · '+esc(a.kind||'Dokument')+' · '+num(a.pages)+' PDF-Seiten</div><h2>'+highlight(a.title)+'</h2><div class="reader-meta source-meta"><b>'+date(a.date,a.publication_year)+'</b><dl>'+credits.filter(([,v])=>v).map(([k,v])=>'<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>').join('')+'</dl>'+(safeUrl(a.url)!=='#'?'<a href="'+esc(safeUrl(a.url))+'" target="_blank" rel="noopener">Originalquelle öffnen ↗</a>':'')+'</div>'+
      '<div class="un-relations">'+relationBadges(a)+'</div>'+(reasons?'<details class="un-reasons"><summary>Warum ist dieses Dokument zugeordnet?</summary><ul>'+reasons+'</ul></details>':'')+
      (a.verification_notes?'<p class="hint">'+esc(a.verification_notes)+'</p>':'')+
      (a.acquisition_note?'<p class="hint"><b>Archivhinweis:</b> '+esc(a.acquisition_note)+'</p>':'')+
      ((result.matches||[]).length?'<p class="hint">Fundstellen der aktuellen Suche</p><div class="match-list">'+result.matches.map(m=>'<button data-jump="'+Number(m.page||1)+'">Seite '+num(m.page||1)+'</button>').join('')+'</div>':'')+
      '</div><div class="reader-tags"><div class="chips">'+(a.tags||[]).map(t=>'<button class="chip" data-evidence="'+esc(t)+'">'+esc(t)+'</button>').join('')+'</div><button id="edit-open" class="button">Schlagwörter & Notizen bearbeiten</button><p class="hint" id="evidence-hint">Schlagwörter öffnen eine zugehörige PDF-Textseite, sofern eine Fundstelle vorliegt.</p>'+(a.notes?'<div class="court-notes"><b>Meine Notizen</b><br>'+esc(a.notes)+'</div>':'')+'</div>';
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
  $('#edit-tags').innerHTML=(state.tags||[]).map(t=>'<label><input type="checkbox" value="'+esc(t.label)+'"'+((current.tags||[]).includes(t.label)?' checked':'')+'>'+esc(t.label)+'</label>').join('');
  $('#custom-tags').value=(current.tags||[]).filter(t=>!known.has(t)).join(', ');
  $('#notes').value=current.notes||current.manual?.notes||'';
  $('#edit-message').textContent='';$('#editor').showModal();
}
async function checkUpdate(){
  try{
    const s=await api('/api/un/status');
    const errors=Array.isArray(s.errors)?s.errors:[];
    $('#update-details').hidden=!s.message && !errors.length;
    $('#update-summary').textContent=(s.finished_at||s.time?date(s.finished_at||s.time)+' · ':'')+(s.message||'');
    $('#update-errors').innerHTML=errors.slice(0,30).map(e=>'<li><b>'+esc(e.source||'Quelle')+'</b>: '+esc(e.error||e.message||String(e))+(e.url?' <a href="'+esc(safeUrl(e.url))+'" target="_blank" rel="noopener">Quelle öffnen ↗</a> · <a href="/un/?q='+encodeURIComponent(e.url)+'">Im Archiv suchen</a>':'')+'</li>').join('')+(errors.length>30?'<li>Weitere '+(errors.length-30)+' Hinweise im lokalen Aktualisierungsprotokoll.</li>':'');
    const running=['running','validating','indexing'].includes(s.phase);
    $('#update').disabled=running;
    $('#update').textContent=running?'↻ Wird aktualisiert …':'↻ Jetzt aktualisieren';
    if(running){
      notice(s.message||'Öffentliche UN-Quellen werden geprüft …');
      if(!poll) poll=setInterval(checkUpdate,2500);
    }else{
      const wasRunning=Boolean(poll);
      if(poll){clearInterval(poll);poll=null;}
      if(updateStarted || wasRunning){await loadState();await search();}
      if(updateStarted || wasRunning){
        const errs=Array.isArray(s.errors)?s.errors.length:0;
        notice((s.message||'Abgleich beendet.')+(errs?' Einzelheiten stehen unter „Umfang, Sprache und Stand der Sammlung“.':''),s.phase==='error',s.phase==='partial');
      }
      updateStarted=false;
    }
  }catch(e){notice(e.message,true);}
}
function reset(){
  $('#q').value='';selected.clear();$('#tag-search').value='';
  for(const k of filterIds) $('#'+k).value=k==='sort'?'newest':k==='relation'?'all':'';
  page=1;renderTags();search();
}
$('#search-form').onsubmit=e=>{e.preventDefault();clearTimeout(debounce);page=1;$('#sort').value=$('#q').value?'relevance':'newest';search();};
$('#q').oninput=()=>{clearTimeout(debounce);debounce=setTimeout(()=>{page=1;$('#sort').value=$('#q').value?'relevance':'newest';search();},350);};
for(const k of filterIds) $('#'+k).onchange=()=>{page=1;renderTags();search();};
$('#tag-search').oninput=renderTags;$('#reset').onclick=reset;
$('#notice-dismiss').onclick=()=>notice('');
document.querySelectorAll('[data-relation]').forEach(b=>b.onclick=()=>{$('#relation').value=b.dataset.relation;page=1;renderTags();search();});
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
    await post('/api/un/annotations',{id:current.id,tags:[...new Set(tags)],notes:$('#notes').value});
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
    await post('/api/un/update',{});
    await checkUpdate();
    if(!poll && updateStarted) poll=setInterval(checkUpdate,2500);
  }catch(e){notice(e.message,true);$('#update').disabled=false;updateStarted=false;}
};
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();$('#q').focus();}});
(async()=>{
  try{
    await loadState();
    const initial=new URLSearchParams(location.search);
    $('#q').value=initial.get('q')||'';
    for(const k of filterIds) if(initial.has(k)) $('#'+k).value=initial.get(k);
    selected=new Set(initial.getAll('tag'));page=Math.max(1,Number(initial.get('page'))||1);
    renderTags();await search();await checkUpdate();
  }catch(e){notice('Die Sammlung konnte nicht geladen werden: '+e.message,true);}
})();
