'use strict';
const $=id=>document.getElementById(id);
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl=url=>{try{const parsed=new URL(url);return parsed.protocol==='https:'?escapeHtml(parsed.href):'#';}catch{return '#';}};
const label=value=>String(value).replaceAll('_',' ');
const stamp=value=>new Date(value).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})+' IST';
let metadata, alertCursor={demo:0,live:0}, firstAlerts={demo:true,live:true};
let alertItems={demo:[],live:[]};
async function api(path,options={}){
 const token=sessionStorage.getItem('finn-token');
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),...options.headers}});
 const body=await response.json();
 if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail||body));
 return body;
}
function notify(message){$('notice').textContent=message;}
function busy(button,action){button.disabled=true;return Promise.resolve().then(action).catch(e=>notify(e.message)).finally(()=>button.disabled=false);}
function card(item){
 const p=item.payload;
 const links=(p.sources||[]).filter(s=>s.startsWith('https://')).map((url,i)=>`<a target="_blank" rel="noopener noreferrer" href="${safeUrl(url)}">${p.kind==='eod'?'Price source':`Open filing${i?' '+(i+1):''}`} ↗</a>`).join('');
 let table='';
 if(p.results?.metrics?.length){
  const pct=v=>v==null?'—':escapeHtml(v.toFixed(1))+'%';
  table=`<div class="table-wrap"><table><thead><tr><th>Metric</th><th>Reported</th><th>QoQ</th><th>YoY</th></tr></thead><tbody>${p.results.metrics.map(m=>`<tr><td>${escapeHtml(m.metric)}</td><td>${m.value==null?'Not reported':escapeHtml(m.value.toLocaleString('en-IN'))+' '+escapeHtml(m.unit)+'<br>'+escapeHtml(m.period_end)+' · '+escapeHtml(m.scope)}</td><td>${pct(m.qoq_pct)}</td><td>${pct(m.yoy_pct)}</td></tr>`).join('')}</tbody></table></div><p class="muted small">${escapeHtml(p.results.status)}</p>`;
 }
 return `<article class="card ${item.priority>=70?'attention':''}"><div class="card-meta"><div><span class="symbol">${escapeHtml(item.ticker)}</span><span class="badge">${escapeHtml(label(item.category))}</span></div><time datetime="${escapeHtml(item.published)}">${stamp(item.published)}</time></div><h3>${escapeHtml(item.title)}</h3><p class="reason">${escapeHtml(item.reason)}</p>${table}${p.attachment_error?'<p class="small muted">Attachment unavailable; headline only. '+escapeHtml(p.attachment_error)+'</p>':''}<div class="card-actions">${links}${p.demo?'<span class="muted small">Illustrative event · not a real disclosure</span>':''}</div>${p.body?`<details><summary>Source text / context</summary><p>${escapeHtml(p.body.slice(0,12000))}</p></details>`:''}</article>`;
}
async function inbox(){
 const params=new URLSearchParams({mode:$('mode').value});
 if($('window').value==='custom'){
  if(!$('start').value||!$('end').value)return;
  params.set('start',new Date($('start').value).toISOString());params.set('end',new Date($('end').value).toISOString());
 }else params.set('hours',$('window').value);
 const data=await api('/api/digest?'+params);
 $('count').textContent=data.attention.length;
 let html=`<div class="section-label"><span>WORTH YOUR ATTENTION</span><b>${data.attention.length.toString().padStart(2,'0')}</b></div>`;
 html+=data.attention.map(card).join('')||'<div class="empty">Nothing above your materiality bar in this window.</div>';
 if(data.review.length)html+=`<div class="section-label"><span>FOR REVIEW</span><span>${data.review.length} UPDATE${data.review.length===1?'':'S'}</span></div>`+data.review.map(card).join('');
 html+=`<details class="routine"><summary><span>${data.routine.length} routine updates</span><span>Show all +</span></summary>${data.routine.map(card).join('')||'<p class="muted small">No routine updates in this window.</p>'}</details>`;
 $('digest').innerHTML=html;
 if($('mode').value==='demo')$('source-status').innerHTML='<strong>Illustration mode.</strong> These events demonstrate filtering. They are not company disclosures.';
 else{
  metadata=await api('/api/meta');
  const f=metadata.filings_status, p=metadata.prices_status;
  const errors=f?.errors?.length||0;
  const sessions=Object.values(p?.sessions||{}).sort();
  $('source-status').textContent=f?`Filings checked ${stamp(f.finished)} · ${errors} source errors. `:'No filing sync completed. Start the ingestion worker. ';
  if(sessions.length)$('source-status').textContent+=`EOD sessions: ${sessions[0]} to ${sessions.at(-1)}. `;
  else $('source-status').textContent+='No EOD data loaded. ';
  if(errors)$('source-status').innerHTML+=`<details><summary>Coverage gaps (${errors})</summary>${f.errors.map(x=>'<p>'+escapeHtml(x)+'</p>').join('')}</details>`;
 }
 await alerts();
}
async function alerts(){
 const mode=$('mode').value;
 const rows=await api(`/api/alerts?mode=${mode}&after=${alertCursor[mode]}`);
 if(rows.length){
  alertCursor[mode]=rows.at(-1).seq;
  alertItems[mode]=[...alertItems[mode],...rows].slice(-200);
  if(!firstAlerts[mode]){notify(`${rows.length} new alert${rows.length>1?'s':''}: ${rows.at(-1).ticker} — ${rows.at(-1).title}`);}
 }
 firstAlerts[mode]=false;
 $('alert-count').textContent=alertItems[mode].length;
 $('alerts').innerHTML=[...alertItems[mode]].reverse().map(r=>`<div class="alert-item"><strong>${escapeHtml(r.ticker)} · ${escapeHtml(r.title)}</strong>${escapeHtml(r.rule)} threshold · ${stamp(r.created)}${r.demo?' · illustration':''}</div>`).join('')||'<p class="muted small">No alerts yet.</p>';
}
async function waitJob(id){
 for(let i=0;i<600;i++){
  const job=await api('/api/jobs/'+id);
  if(job.status==='done')return job.result;
  if(job.status==='failed')throw new Error(job.error);
  await new Promise(resolve=>setTimeout(resolve,1500));
 }
 throw new Error('The job is taking longer than expected. Check the server logs.');
}
function renderResearch(data){
 let html=`<div class="section-label"><span>${escapeHtml(data.ticker)} / ${data.mode==='cited_analysis'?'EVIDENCE-LED ANALYSIS':'SOURCE PASSAGES'}</span></div>`;
 if(data.answer.length){html+=data.answer.map(p=>`<article class="point"><h3>${escapeHtml(p.claim)}</h3><p class="implication">Inference: ${escapeHtml(p.implication)}</p><p class="small">${p.evidence_ids.map(id=>`<a href="#evidence-${escapeHtml(id)}">[${escapeHtml(id)}]</a>`).join(' ')}</p></article>`).join('');}
 html+=data.evidence.map(e=>`<div class="evidence" id="evidence-${escapeHtml(e.id)}"><span class="eyebrow">${escapeHtml(e.id)} · ${escapeHtml(e.kind)}</span><blockquote>“${escapeHtml(e.quote)}”</blockquote><a target="_blank" rel="noopener noreferrer" href="${safeUrl(e.url+'#page='+e.page)}">${escapeHtml(e.title)} · PDF page ${e.page} ↗</a></div>`).join('');
 if(!data.answer.length){html+=data.passages.map(p=>`<details class="evidence"><summary>${escapeHtml(p.title)} · PDF page ${p.page}</summary><p>${escapeHtml(p.text)}</p><a target="_blank" rel="noopener noreferrer" href="${safeUrl(p.url+'#page='+p.page)}">Open primary source ↗</a></details>`).join('');}
 html+=`<div class="inset"><strong>What this answer cannot establish</strong>${data.gaps.map(g=>`<p>${escapeHtml(g)}</p>`).join('')}</div>`;
 html+=`<details class="coverage"><summary>Source coverage · ${data.sources.length} documents</summary>${data.sources.map(s=>`<a target="_blank" rel="noopener noreferrer" href="${safeUrl(s.url)}">${escapeHtml(s.title)} ↗</a><span class="muted">Loaded ${stamp(s.fetched)} · publication date ${escapeHtml(s.published||'unverified')}</span>`).join('')}</details>`;
 $('answer').innerHTML=html;
}
async function boot(){
 try{
  metadata=await api('/api/meta');
  $('date').textContent=new Date().toLocaleDateString('en-IN',{timeZone:'Asia/Kolkata',weekday:'long',day:'numeric',month:'long'}).toUpperCase()+' / 18 COMPANIES';
  $('holdings').innerHTML=metadata.portfolio.map(([t,s])=>`<div class="holding"><strong>${escapeHtml(t)}</strong><span>${escapeHtml(s)}</span></div>`).join('');
  $('picks').innerHTML=metadata.research_picks.map(t=>`<button type="button" data-ticker="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join('');
  const r=metadata.rules;$('price').value=r.price_pct;$('volume').value=r.volume_multiple;$('lookback').value=r.lookback;$('priority').value=r.min_priority;
  $('categories').innerHTML=metadata.categories.map(c=>`<label><input type="checkbox" name="category" value="${escapeHtml(c)}" ${r.categories.includes(c)?'checked':''}>${escapeHtml(label(c))}</label>`).join('');
  const usage=await api('/api/usage');
  $('cost').textContent=`Model calls today: ${usage.reduce((s,r)=>s+r.calls,0)}. Extraction ${metadata.models.extract?'configured':'off'} · reasoning ${metadata.models.reason?'configured':'off'}. Cached answers do not call the model again.`;
  await inbox();
 }catch(e){notify(e.message);}
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{
 document.querySelectorAll('.view').forEach(view=>view.classList.toggle('hidden',view.id!==button.dataset.view));
 document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab===button));
}));
$('access').addEventListener('click',()=>{const token=prompt('Access token for this private FINN instance. Leave blank for local mode.');if(token!==null){sessionStorage.setItem('finn-token',token);$('notice').textContent='';boot();}});
$('mode').addEventListener('change',()=>inbox().catch(e=>notify(e.message)));
$('window').addEventListener('change',()=>{$('custom-window').classList.toggle('hidden',$('window').value!=='custom');inbox().catch(e=>notify(e.message));});
$('custom-window').addEventListener('submit',event=>{event.preventDefault();inbox().catch(e=>notify(e.message));});
$('refresh').addEventListener('click',()=>busy($('refresh'),inbox));
$('picks').addEventListener('click',event=>{if(event.target.dataset.ticker)$('ticker').value=event.target.dataset.ticker;});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{$('question').value=button.dataset.question;}));
$('load').addEventListener('click',()=>busy($('load'),async()=>{
 $('research-status').textContent='Finding and parsing annual reports and transcripts…';
 const job=await api('/api/company/sync',{method:'POST',body:JSON.stringify({ticker:$('ticker').value})});
 const result=await waitJob(job.job_id);
 $('research-status').textContent=`${result.loaded.length} documents ready. ${result.errors.length} source gaps.`;
 if(result.errors.length)$('answer').innerHTML='<div class="inset">'+result.errors.map(e=>'<p>'+escapeHtml((e.title||'Source')+': '+e.error)+'</p>').join('')+'</div>';
}));
$('research-form').addEventListener('submit',event=>{event.preventDefault();busy($('ask'),async()=>{
 $('research-status').textContent='Loading documents if needed, then reading the evidence…';
 const job=await api('/api/research',{method:'POST',body:JSON.stringify({ticker:$('ticker').value,question:$('question').value,deep:$('deep').checked})});
 const result=await waitJob(job.job_id);renderResearch(result);
 $('research-status').textContent=result.mode==='cited_analysis'?'Analysis ready. Open the evidence before relying on a claim.':'Source passages ready. No synthetic research data is used.';
});});
$('rules-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{
 const data={price_pct:+$('price').value,volume_multiple:+$('volume').value,lookback:+$('lookback').value,min_priority:+$('priority').value,categories:[...document.querySelectorAll('[name="category"]:checked')].map(e=>e.value)};
 const result=await api('/api/rules',{method:'PUT',body:JSON.stringify(data)});notify('Rules saved. '+result.applies);
});});
if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
setInterval(()=>{if(!document.hidden)alerts().catch(()=>{});},15000);
boot();
