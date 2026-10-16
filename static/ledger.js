const $=selector=>document.querySelector(selector);
const esc=value=>String(value||'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const labels={participate:'參與投標',decline:'放棄投標'};
const currentMonth=new Date().toLocaleDateString('en-CA',{year:'numeric',month:'2-digit'});
let register={items:[],counts:{participate:0,decline:0,pending:0},total:0};

async function api(url,options){const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw new Error(data.error||'台賬服務異常');return data}
function formatDate(value){return new Intl.DateTimeFormat('zh-Hant-MO',{year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(`${value}T00:00:00Z`))}
function toast(message){const node=$('#toast');node.textContent=message;node.classList.add('show');setTimeout(()=>node.classList.remove('show'),2200)}

function selectedPeriod(){
  const range=document.querySelector('[name="periodMode"]:checked').value==='range';
  return range?{start:$('#startMonthPicker').value,end:$('#endMonthPicker').value}:{start:$('#monthPicker').value,end:$('#monthPicker').value};
}
function render(){
  $('#totalCount').textContent=register.total;$('#participateCount').textContent=register.counts.participate;$('#declineCount').textContent=register.counts.decline;$('#pendingCount').textContent=register.counts.pending;
  const formatMonth=value=>{const [year,month]=value.split('-');return `${year} 年 ${Number(month)} 月`};
  const period=register.start_month===register.end_month?formatMonth(register.start_month):`${formatMonth(register.start_month)} — ${formatMonth(register.end_month)}`;
  $('#periodLabel').textContent=`${period} · ${register.months} MONTH${register.months>1?'S':''}`;
  $('#ledgerRows').innerHTML=register.items.length?register.items.map((item,index)=>`<article class="ledger-row" data-source="${esc(item.source_key)}" data-id="${esc(item.tender_id)}">
    <span class="ledger-date"><b>${formatDate(item.published_date)}</b><small>${esc(item.source)}</small></span>
    <span class="ledger-subject"><small>NO. ${String(index+1).padStart(3,'0')}</small><a href="${esc(item.detail_url)}" target="_blank" rel="noreferrer">${esc(item.title)} ↗</a></span>
    <span class="ledger-agency">${esc(item.agency)}</span>
    <span><b class="decision-badge ${item.decision||'pending'}">${labels[item.decision]||'待決策'}</b>${item.decided_at?`<small class="decision-time">更新於 ${new Date(item.decided_at).toLocaleString('zh-Hant-MO')}</small>`:''}</span>
    <span class="decision-actions"><button data-decision="participate" class="${item.decision==='participate'?'selected':''}">參與投標</button><button data-decision="decline" class="${item.decision==='decline'?'selected':''}">放棄投標</button>${item.decision?'<small>可隨時修改決策</small>':''}</span>
  </article>`).join(''):'<div class="board-empty"><b>本月暫無收錄資訊</b><span>請選擇其他月份查看台賬。</span></div>';
  document.querySelectorAll('[data-decision]').forEach(button=>button.onclick=()=>saveDecision(button));
}

async function loadLedger(){
  const period=selectedPeriod();
  if(!period.start||!period.end)return;
  if(period.end<period.start){toast('結束月份不可早於開始月份');return}
  $('#ledgerRows').innerHTML='<div class="board-empty"><b>正在生成台賬</b></div>';
  try{register=await api(`/api/ledger?start_month=${encodeURIComponent(period.start)}&end_month=${encodeURIComponent(period.end)}`);render()}catch(error){if(error.message.includes('口令')||error.message.includes('登入'))showGate();else $('#ledgerRows').innerHTML=`<div class="board-empty"><b>載入失敗</b><span>${esc(error.message)}</span></div>`}
}
async function saveDecision(button){
  const row=button.closest('.ledger-row');row.querySelectorAll('button').forEach(node=>node.disabled=true);
  try{await api('/api/ledger/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_key:row.dataset.source,tender_id:row.dataset.id,decision:button.dataset.decision})});await loadLedger();toast(`已記錄：${button.textContent}`)}catch(error){toast(error.message);row.querySelectorAll('button').forEach(node=>node.disabled=false)}
}
function showApp(){$('#accessGate').hidden=true;$('#ledgerApp').hidden=false;loadLedger()}
function showGate(){$('#ledgerApp').hidden=true;$('#accessGate').hidden=false;$('#password').focus()}

['#monthPicker','#startMonthPicker','#endMonthPicker'].forEach(selector=>{$(selector).value=currentMonth;$(selector).onchange=loadLedger});
document.querySelectorAll('[name="periodMode"]').forEach(input=>input.onchange=()=>{const range=input.value==='range'&&input.checked;$('#singlePeriod').hidden=range;$('#rangePeriod').hidden=!range;$('#periodHint').textContent=range?'將開始至結束月份合併為一份台賬（最多 24 個月）':'顯示所選月份的全部收錄資訊';loadLedger()});
$('#printButton').onclick=()=>window.print();
$('#loginForm').onsubmit=async event=>{event.preventDefault();const submit=event.currentTarget.querySelector('button');submit.disabled=true;$('#loginError').textContent='';try{await api('/api/ledger/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('#password').value})});$('#password').value='';showApp()}catch(error){$('#loginError').textContent=error.message;$('#password').select()}finally{submit.disabled=false}};
api('/api/ledger/session').then(data=>data.authenticated?showApp():showGate()).catch(()=>showGate());
