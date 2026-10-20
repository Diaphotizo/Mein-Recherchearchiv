'use strict';
(async()=>{
  const nav=document.querySelector('#collections');
  if(!nav) return;
  const collectionForPath=path=>/^\/icc-icj\/?$/.test(path)?'courts':/^\/un\/?$/.test(path)?'un':path==='/'?'bip':null;
  const active=collectionForPath(location.pathname);
  try {
    const response=await fetch('/api/collections');
    if(!response.ok) return;
    const {collections}=await response.json();
    for(const collection of collections){
      const element=document.createElement(collection.enabled?'a':'span');
      element.className='collection-tab'+(collection.id===active?' current':'')+(!collection.enabled?' future':'');
      element.textContent=collection.label+(!collection.enabled?' · später':'');
      if(collection.enabled){
        element.href=collection.href;
        if(collection.id===active) element.setAttribute('aria-current','page');
        element.addEventListener('click',()=>{
          try {
            if(active) sessionStorage.setItem('research-last-'+active,location.pathname+location.search);
            const last=sessionStorage.getItem('research-last-'+collection.id);
            if(last && last.startsWith('/') && !last.startsWith('//')) {
              const saved=new URL(last,location.origin);
              if(saved.origin===location.origin && collectionForPath(saved.pathname)===collection.id)
                element.href=saved.pathname+saved.search;
            }
          } catch(e) { /* Navigation also works when browser storage is unavailable. */ }
        });
      } else {
        element.setAttribute('aria-disabled','true');
        element.title='Als eigene Sammlung vorbereitet. Noch keine UN-Dokumente eingebunden.';
      }
      nav.append(element);
    }
  }catch(e){ console.warn('Sammlungsnavigation nicht verfügbar.'); }
})();
