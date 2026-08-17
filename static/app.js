const state={page:1,status:'all',year:'',q:''};
const $=s=>document.querySelector(s), esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const day=s=>s?s.slice(0,10):'—';
async function load(){
  $('#list').innerHTML='<div class="loading">正在载入公开招标数据…</div>';
  const p=new URLSearchParams({...state,size:12});
  try{const r=await fetch('/api/tenders?'+p),d=await r.json(); if(!r.ok)throw Error(d.error||'载入失败');
    $('#total').textContent=d.total; render(d.items); pages(d.total,d.size);
    const y=$('#year'); if(y.options.length===1)d.years.forEach(v=>y.add(new Option(v+' 年',v)));
  }catch(e){$('#list').innerHTML=`<div class="empty">${esc(e.message)}，请稍后重试。</div>`}
}
function render(items){if(!items.length){$('#list').innerHTML='<div class="empty">没有符合条件的项目</div>';return}
  const now=new Date(); $('#list').innerHTML=items.map(x=>{const active=x.contest_date&&new Date(x.contest_date)>=now;return `<article class="card" data-no="${encodeURIComponent(x.no)}"><div class="card-top"><span class="badge ${active?'':'closed'}">${active?'正在招标':'已截止'}</span><span class="number">${esc(x.no)}</span></div><h3>${esc(x.name_zh||x.name_cn)}</h3><p class="pt">${esc(x.name_pt)}</p><div class="dates"><span>公告日期<b>${day(x.announcement_date)}</b></span><span>开标日期<b>${day(x.contest_date)}</b></span></div><span class="card-link">查看项目详情 →</span></article>`}).join('');
  document.querySelectorAll('.card').forEach(c=>c.onclick=()=>detail(decodeURIComponent(c.dataset.no)));
}
function pages(total,size){const n=Math.ceil(total/size),box=$('#pagination');box.innerHTML='';if(n<2)return;let start=Math.max(1,state.page-2),end=Math.min(n,start+4);start=Math.max(1,end-4);for(let i=start;i<=end;i++){const b=document.createElement('button');b.textContent=i;b.className=i===state.page?'active':'';b.onclick=()=>{state.page=i;load();scrollTo({top:520,behavior:'smooth'})};box.append(b)}}
async function detail(no){const r=await fetch('/api/tenders/'+encodeURIComponent(no)),x=await r.json();const links=[['中文公告',x.notice_zh],['简体公告',x.notice_cn],['葡文公告',x.notice_pt],['中文招标文件',x.attachment_zh],['葡文招标文件',x.attachment_pt],['开标结果',x.tender_result],['判给结果',x.award_result]].filter(v=>v[1]);$('#detailBody').innerHTML=`<div class="detail-inner"><p class="eyebrow dark">${esc(x.no)}</p><h2>${esc(x.name_zh||x.name_cn)}</h2><p class="pt">${esc(x.name_pt)}</p><div class="detail-meta"><span>展开采购日期<b>${day(x.procurement_date)}</b></span><span>公告日期<b>${day(x.announcement_date)}</b></span><span>开标日期<b>${day(x.contest_date)}</b></span></div><div class="actions">${links.map(v=>`<a href="${esc(v[1])}" target="_blank" rel="noopener">${v[0]} ↗</a>`).join('')||'暂无附件'}</div></div>`;$('#detail').showModal()}
$('.close').onclick=()=>$('#detail').close();$('#detail').onclick=e=>{if(e.target===$('#detail'))$('#detail').close()};
$('#searchBtn').onclick=()=>{state.q=$('#keyword').value.trim();state.page=1;load()};$('#keyword').onkeydown=e=>{if(e.key==='Enter')$('#searchBtn').click()};
document.querySelectorAll('.filter').forEach(b=>b.onclick=()=>{document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.status=b.dataset.status;state.page=1;load()});
$('#year').onchange=e=>{state.year=e.target.value;state.page=1;load()};$('#refresh').onclick=async e=>{e.currentTarget.disabled=true;e.currentTarget.textContent='刷新中…';try{const r=await fetch('/api/refresh',{method:'POST'}),d=await r.json();if(!r.ok)throw Error(d.message||d.error);await stats();await load()}catch(x){alert('刷新失败：'+x.message)}finally{e.currentTarget.disabled=false;e.currentTarget.textContent='↻ 刷新数据'}};
async function stats(){try{const d=await(await fetch('/api/stats')).json();$('#heroTotal').textContent=d.total;$('#updated').textContent='最后同步：'+(d.last_refresh?new Date(d.last_refresh).toLocaleString('zh-CN'):'等待首次同步')}catch(e){}}
stats();load();
