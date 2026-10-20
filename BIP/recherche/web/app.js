'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const norm = s => s.toLocaleLowerCase('de').replaceAll('ß','ss').normalize('NFKD').replace(/[\u0300-\u036f]/g,'');
const date = s => s ? new Date(s.slice(0,10)+'T12:00:00').toLocaleDateString('de-DE') : '–';
const num = n => new Intl.NumberFormat('de-DE').format(n);
const safeUrl = s => /^(https?:|mailto:)/i.test(s) ? s : '#';
let state, currentArticle, selected = new Set(), currentPage = 1, highlightTerms = [], requestId = 0, debounce, updatePoll;
const filterIds = ['scope','author','role','from','to','kind','sort'];

async function api(path, options={}) {
  const response = await fetch(path,options);
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || 'Die Anfrage ist fehlgeschlagen.');
  return data;
}
function post(path, body) {
  return api(path,{method:'POST',headers:{'Content-Type':'application/json','X-BIP-Token':state.csrf},body:JSON.stringify(body)});
}
function notice(message,error=false) {
  $('#notice').textContent=message; $('#notice').hidden=!message; $('#notice').classList.toggle('error',error);
}
function highlight(text,terms=highlightTerms) {
  if(!terms.length) return esc(text);
  let normalized='',mapping=[],offset=0;
  for(const char of text) {
    const n=norm(char); normalized+=n;
    for(let j=0;j<n.length;j++) mapping.push([offset,offset+char.length]);
    offset+=char.length;
  }
  let ranges=[];
  for(const term of terms) {
    if(!term) continue;
    let start=0,found;
    while((found=normalized.indexOf(norm(term),start))!==-1) {
      if(mapping[found] && mapping[found+norm(term).length-1])
        ranges.push([mapping[found][0],mapping[found+norm(term).length-1][1]]);
      start=found+Math.max(1,term.length);
    }
  }
  ranges.sort((a,b)=>a[0]-b[0]);
  const merged=[];
  for(const range of ranges) {
    const last=merged.at(-1);
    if(last && range[0]<=last[1]) last[1]=Math.max(last[1],range[1]); else merged.push(range);
  }
  let out='',last=0;
  for(const [start,end] of merged){out+=esc(text.slice(last,start))+'<mark>'+esc(text.slice(start,end))+'</mark>';last=end;}
  return out+esc(text.slice(last));
}
function credit(people) {
  const authors=people.filter(p=>p.role.startsWith('Autor'));
  if(authors.length) return authors.map(p=>p.name+(p.role==='Autor eines Teiltexts'?' (Teiltext)':'')).join(' · ');
  const editors=people.filter(p=>p.role==='Redaktion');
  if(editors.length) return 'Redaktion: '+editors.map(p=>p.name).join(' · ');
  const guests=people.filter(p=>p.role==='Gesprächsgast');
  if(guests.length) return 'Gesprächsgast: '+guests.map(p=>p.name).join(' · ');
  return 'Autor nicht angegeben';
}
function renderTagGroups() {
  const filter=norm($('#tag-search').value);
  const groups=new Map();
  for(const tag of state.tags) {
    if(filter && !norm(tag.label).includes(filter)) continue;
    if(!groups.has(tag.group)) groups.set(tag.group,[]);
    groups.get(tag.group).push(tag);
  }
  $('#tag-groups').innerHTML=[...groups].map(([group,tags],i)=>
    '<details'+(filter || tags.some(t=>selected.has(t.label)) || i===0?' open':'')+'><summary>'+esc(group)+'</summary>'+
    tags.map(t=>'<label class="tag-option"><input type="checkbox" value="'+esc(t.label)+'"'+(selected.has(t.label)?' checked':'')+'><span>'+esc(t.label)+'</span><small>'+num(t.count)+'</small></label>').join('')+'</details>').join('');
  $('#tag-groups').querySelectorAll('input').forEach(input=>input.addEventListener('change',()=>toggleTag(input.value,input.checked)));
  $('#tag-counter').textContent=selected.size+' gewählt';
  $('#selected').innerHTML=[...selected].map(t=>'<button data-remove="'+esc(t)+'">'+esc(t)+' <span aria-hidden="true">×</span></button>').join('');
  $('#selected').querySelectorAll('button').forEach(b=>b.onclick=()=>toggleTag(b.dataset.remove,false));
  document.querySelectorAll('[data-quick]').forEach(b=>b.classList.toggle('active',selected.has(b.dataset.quick)));
}
function toggleTag(tag,on=!selected.has(tag)) {
  if(on) selected.add(tag); else selected.delete(tag);
  renderTagGroups(); currentPage=1; search();
}
function queryParams() {
  const p=new URLSearchParams();
  if($('#q').value.trim()) p.set('q',$('#q').value.trim());
  for(const id of filterIds) if($('#'+id).value) p.set(id,$('#'+id).value);
  for(const t of selected) p.append('tag',t);
  p.set('page',currentPage); return p;
}
async function search() {
  const thisRequest=++requestId;
  const params=queryParams();
  $('#results').setAttribute('aria-busy','true');
  try {
    const result=await api('/api/search?'+params);
    if(thisRequest!==requestId) return;
    highlightTerms=result.highlight_terms;
    currentPage=result.page;
    history.replaceState(null,'','/?'+params);
    $('#result-count').textContent=num(result.total)+(result.total===1?' Treffer':' Treffer');
    const description=selected.size?'Alle gewählten Schlagwörter treffen zu':$('#q').value?'Treffer im vollständigen Artikeltext':'Neueste Veröffentlichungen zuerst';
    $('#result-hint').textContent=description;
    if(!result.items.length) {
      $('#results').innerHTML=state.total || state.archived
        ? '<div class="empty"><h3>Keine passenden Artikel gefunden.</h3><p>Entferne ein Schlagwort, erweitere den Zeitraum oder suche mit weniger Begriffen.</p><button class="button" id="empty-reset">Alle Filter zurücksetzen</button></div>'
        : '<div class="empty"><h3>Die BIP-Sammlung enthält noch keine Dokumente.</h3><p>Über „Jetzt aktualisieren“ werden die öffentlichen Artikel und Seiten abgerufen und für die lokale Suche aufbereitet.</p></div>';
      if($('#empty-reset')) $('#empty-reset').onclick=reset;
    } else {
      $('#results').innerHTML=result.items.map(a=>
        '<article class="article"><div class="article-top"><time datetime="'+esc(a.date.slice(0,10))+'">'+date(a.date)+'</time><span>·</span><span class="type">'+esc(a.kind)+'</span><span class="pages">'+a.pages+' S.</span></div>'+
        '<button class="title-button" data-open="'+a.id+'">'+highlight(a.title)+'</button>'+
        '<div class="credit">'+esc(credit(a.contributors))+'</div>'+
        a.snippets.map(s=>'<p class="snippet" data-open="'+a.id+'" data-paragraph="'+s.paragraph+'">'+(s.section?'<span class="section">'+esc(s.section)+'</span>':'')+highlight(s.text)+'</p>').join('')+
        '<div class="article-bottom"><div class="chips">'+a.tags.slice(0,6).map(t=>'<button class="chip" data-tag="'+esc(t)+'">'+esc(t)+'</button>').join('')+
        (a.tags.length>6?'<span class="more-tags">+'+(a.tags.length-6)+'</span>':'')+
        '</div><div class="article-links"><a href="/api/pdf?id='+a.id+'" target="_blank" rel="noopener">PDF öffnen ↗</a><a href="'+esc(safeUrl(a.url))+'" target="_blank" rel="noopener">Original ↗</a></div></div></article>').join('');
      $('#results').querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>openArticle(b.dataset.open,b.dataset.paragraph));
      $('#results').querySelectorAll('[data-tag]').forEach(b=>b.onclick=()=>toggleTag(b.dataset.tag,true));
    }
    $('#pagination').innerHTML=result.pages>1?'<button class="button" id="prev"'+(result.page===1?' disabled':'')+'>← Zurück</button><span>Seite '+result.page+' von '+result.pages+'</span><button class="button" id="next"'+(result.page===result.pages?' disabled':'')+'>Weiter →</button>':'';
    if($('#prev')) $('#prev').onclick=()=>{currentPage--;search();$('#result-count').scrollIntoView({behavior:'smooth'});};
    if($('#next')) $('#next').onclick=()=>{currentPage++;search();$('#result-count').scrollIntoView({behavior:'smooth'});};
  } catch(error) {notice(error.message,true);}
  finally {if(thisRequest===requestId) $('#results').setAttribute('aria-busy','false');}
}
function bodyMarkup(text,links) {
  return highlight(text).replace(/\[(\d+)\]/g,(all,n)=>links[Number(n)-1]?'<a href="'+esc(safeUrl(links[Number(n)-1]))+'" target="_blank" rel="noopener">'+all+'</a>':all);
}
async function openArticle(id,paragraph) {
  try {
    currentArticle=await api('/api/article?id='+encodeURIComponent(id));
    renderArticle();
    if(!$('#reader').open) $('#reader').showModal();
    $('#reader-scroll').scrollTop=0;
    if(paragraph!==undefined) {
      requestAnimationFrame(()=>{
        const element=$('#reader-content').querySelector('[data-p="'+paragraph+'"]');
        if(element){element.scrollIntoView({block:'center'});element.classList.add('focus');}
      });
    }
  } catch(error){notice(error.message,true);}
}
function renderArticle() {
  const a=currentArticle;
  $('#reader-pdf').href='/api/pdf?id='+a.id;
  const roles=a.contributors.map(p=>'<li><b>'+esc(p.name)+'</b> · '+esc(p.role)+'<br>'+esc(p.evidence)+
    (p.paragraph!==null?'<br><button class="text-button" data-jump="'+p.paragraph+'">Zur Quellenstelle im Text</button>':'')+'</li>').join('');
  $('#reader-content').innerHTML='<div class="reader-header"><div class="eyebrow">'+esc(a.kind)+' · '+a.pages+' PDF-Seiten</div><h2>'+highlight(a.title)+'</h2>'+
    '<div class="reader-meta"><b>Veröffentlicht: '+date(a.date)+'</b> · Geändert: '+date(a.modified)+'<br>'+esc(credit(a.contributors))+
    '<br><a href="'+esc(safeUrl(a.url))+'" target="_blank" rel="noopener">Original auf bip-jetzt.de ↗</a><span>Archiviert am '+date(a.fetched_at)+'</span></div>'+
    '<details class="credit-detail"><summary>Autorenangaben und Rollen prüfen</summary>'+(roles?'<ul>'+roles+'</ul>':'<p>Keine eindeutige Verfasserangabe im archivierten Artikel. Du kannst eine selbst geprüfte Angabe ergänzen.</p>')+'</details>'+
    (a.versions.length?'<details class="credit-detail"><summary>'+a.versions.length+' frühere Fassungen</summary><ul>'+a.versions.map(v=>'<li><a href="/api/pdf?id='+a.id+'&version='+v.fingerprint+'" target="_blank" rel="noopener">Fassung vom '+date(v.modified)+' öffnen</a></li>').join('')+'</ul></details>':'')+
    '</div><div class="reader-tags"><div class="chips">'+a.tags.map(t=>'<button class="chip" data-evidence="'+esc(t)+'">'+esc(t)+'</button>').join('')+'</div><button class="button" id="edit-open">Schlagwörter & eigene Angaben bearbeiten</button><p class="hint" id="evidence-hint">Klicke auf ein Schlagwort, um eine zugehörige Textstelle zu sehen.</p></div>'+
    '<div class="reader-body">'+a.paragraphs.map((p,i)=>'<'+(p.heading?'h3':'p')+' id="p-'+i+'" data-p="'+i+'">'+bodyMarkup(p.text,a.links)+'</'+(p.heading?'h3':'p')+'>').join('')+
    '<div class="references"><h3>Quellen und Links</h3><ol>'+a.links.map(url=>'<li><a href="'+esc(safeUrl(url))+'" target="_blank" rel="noopener">'+esc(url)+'</a></li>').join('')+'</ol></div></div>';
  $('#edit-open').onclick=openEditor;
  const jump=n=>{const el=$('#reader-content').querySelector('[data-p="'+n+'"]');if(el){$('#reader-content').querySelectorAll('.focus').forEach(p=>p.classList.remove('focus'));el.classList.add('focus');el.scrollIntoView({block:'center',behavior:'smooth'});}};
  $('#reader-content').querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>jump(b.dataset.jump));
  $('#reader-content').querySelectorAll('[data-evidence]').forEach(b=>b.onclick=()=>{
    const paragraphs=a.tag_evidence[b.dataset.evidence]||[];
    if(paragraphs.length) jump(paragraphs[0]);
    else $('#evidence-hint').textContent='Dieses Schlagwort wurde aus dem Titel oder als eigene Angabe zugeordnet.';
  });
}
function personRow(person={name:'',role:'Autor'}) {
  const div=document.createElement('div');div.className='person-row';
  div.innerHTML='<input aria-label="Name der Person" placeholder="Name" value="'+esc(person.name)+'"><select aria-label="Rolle">'+['Autor','Autor eines Teiltexts','Redaktion','Gesprächsgast'].map(r=>'<option'+(r===person.role?' selected':'')+'>'+esc(r)+'</option>').join('')+'</select><button type="button" class="icon-button" aria-label="Eigene Personenangabe entfernen">×</button>';
  div.querySelector('button').onclick=()=>div.remove();
  $('#edit-people').append(div);
}
function openEditor() {
  const known=new Set(state.tags.map(t=>t.label));
  $('#edit-tags').innerHTML=state.tags.map(t=>'<label><input type="checkbox" value="'+esc(t.label)+'"'+(currentArticle.tags.includes(t.label)?' checked':'')+'>'+esc(t.label)+'</label>').join('');
  $('#custom-tags').value=currentArticle.tags.filter(t=>!known.has(t)).join(', ');
  $('#edit-people').innerHTML='';
  for(const p of currentArticle.manual.contributors||[]) personRow(p);
  $('#edit-message').textContent='';
  $('#editor').showModal();
}
async function loadState() {
  const selections=Object.fromEntries(filterIds.map(id=>[id,$('#'+id).value]));
  state=await api('/api/state');
  $('#stats').innerHTML='<span><b>'+num(state.articles)+'</b> Beiträge</span><span><b>'+num(state.pages)+'</b> ergänzende Seiten</span><span><b>'+num(state.total)+'</b> PDFs</span><span class="stand">'+(state.total ? state.date_from.slice(0,4)+' — '+state.date_to.slice(0,4) : 'Noch keine Dokumente')+'</span>';
  $('#updated-at').textContent='Quellabgleich: '+date(state.source_checked_at)+' · Suche vollständig lokal';
  $('#author').innerHTML='<option value="">Alle Personen & Redaktionen</option><option value="__unknown__">Ohne eindeutige Autorenangabe</option>'+state.authors.map(a=>'<option value="'+esc(a.name)+'">'+esc(a.name)+' ('+a.count+')</option>').join('');
  $('#kind').innerHTML='<option value="">Alle Beitragstypen</option>'+Object.entries(state.kinds).sort().map(([k,v])=>'<option value="'+esc(k)+'">'+esc(k)+' ('+v+')</option>').join('');
  for(const [id,value] of Object.entries(selections)) if([...$('#'+id).options||[]].some(o=>o.value===value) || $('#'+id).tagName==='INPUT') $('#'+id).value=value;
  renderTagGroups();
}
async function checkUpdate() {
  try {
    const status=await api('/api/status');
    if(status.phase==='running') {
      $('#update').disabled=true;$('#update').textContent='↻ Wird aktualisiert …';notice(status.message);
      if(!updatePoll) updatePoll=setInterval(checkUpdate,2500);
    } else {
      $('#update').disabled=false;$('#update').textContent='↻ Jetzt aktualisieren';
      if(updatePoll){clearInterval(updatePoll);updatePoll=null;await loadState();await search();notice(status.message,status.phase==='error');}
      else if(status.phase==='error') notice('Letzte Aktualisierung: '+status.message,true);
    }
  } catch(error){notice(error.message,true);}
}
function reset() {
  $('#q').value='';selected.clear();$('#tag-search').value='';
  for(const id of filterIds) $('#'+id).value= id==='scope'?'posts':id==='sort'?'newest':'';
  currentPage=1;renderTagGroups();search();
}
$('#search-form').onsubmit=e=>{e.preventDefault();clearTimeout(debounce);currentPage=1;$('#sort').value=$('#q').value?'relevance':'newest';search();};
$('#q').addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{currentPage=1;$('#sort').value=$('#q').value?'relevance':'newest';search();},280);});
for(const id of filterIds) $('#'+id).onchange=()=>{currentPage=1;search();};
$('#tag-search').oninput=renderTagGroups;
$('#reset').onclick=reset;
$('#mobile-filters').onclick=()=>{
  const expanded=$('.workspace').classList.toggle('filter-visible');
  $('#mobile-filters').setAttribute('aria-expanded',String(expanded));
  $('#mobile-filters').textContent=expanded?'Filter ausblenden':'Filter & Schlagwörter anzeigen';
};
document.querySelectorAll('[data-quick]').forEach(b=>b.onclick=()=>toggleTag(b.dataset.quick));
$('#reader-close').onclick=()=>$('#reader').close();
$('#editor-close').onclick=()=>$('#editor').close();
$('#edit-cancel').onclick=()=>$('#editor').close();
$('#add-person').onclick=()=>personRow();
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();$('#q').focus();}});
$('#edit-form').onsubmit=async e=>{
  e.preventDefault();
  const tags=[...$('#edit-tags').querySelectorAll('input:checked')].map(i=>i.value);
  tags.push(...$('#custom-tags').value.split(',').map(s=>s.trim()).filter(Boolean));
  const contributors=[...$('#edit-people').querySelectorAll('.person-row')].map(r=>({name:r.querySelector('input').value.trim(),role:r.querySelector('select').value})).filter(p=>p.name);
  try {
    currentArticle=await post('/api/annotations',{id:currentArticle.id,tags:[...new Set(tags)],contributors});
    await loadState();await search();renderArticle();$('#editor').close();
  } catch(error){$('#edit-message').textContent=error.message;}
};
$('#update').onclick=async()=>{
  $('#update').disabled=true;
  try{await post('/api/update',{});await checkUpdate();}
  catch(error){notice(error.message,true);$('#update').disabled=false;}
};
(async()=>{
  try {
    await loadState();
    const initial=new URLSearchParams(location.search);
    if(initial.has('q')) $('#q').value=initial.get('q');
    for(const id of filterIds) if(initial.has(id)) $('#'+id).value=initial.get(id);
    selected=new Set(initial.getAll('tag'));currentPage=Number(initial.get('page'))||1;
    renderTagGroups();await search();await checkUpdate();
    if(initial.has('id')) await openArticle(initial.get('id'));
  }catch(error){notice('Die Sammlung konnte nicht geladen werden: '+error.message,true);}
})();
