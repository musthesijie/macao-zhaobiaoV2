const state = { page: 1, size: 10, status: 'all', year: '', query: '', total: 0 };
const $ = selector => document.querySelector(selector);
const escapeHtml = value => String(value || '').replace(/[&<>'"]/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char]));
const formatDate = value => value ? new Intl.DateTimeFormat('zh-Hant-MO', { year:'numeric', month:'2-digit', day:'2-digit' }).format(new Date(value)) : '日期未提供';
const dateOnly = value => value ? String(value).slice(0, 10) : '';
let searchTimer;

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || result.error || '資料服務暫時不可用');
  return result;
}

async function init() {
  await loadStats();
  await Promise.all([loadLatest(), loadDirectory()]);
  bindEvents();
}

async function loadStats() {
  const stats = await requestJson('/api/stats');
  $('#tenderCount').textContent = stats.total;
  $('#activeCount').textContent = stats.active;
  $('#allCount').textContent = stats.total;
  $('#openCount').textContent = stats.active;
  $('#closedCount').textContent = Math.max(0, stats.total - stats.active);
  $('#updatedAt').textContent = stats.last_refresh
    ? new Date(stats.last_refresh).toLocaleString('zh-Hant-MO', { month:'short', day:'2-digit', hour:'2-digit', minute:'2-digit' })
    : '尚未同步';
  $('#refreshStatus').textContent = stats.last_error ? `上次同步異常 · ${stats.last_error}` : '公開招標資料已同步';
  $('.refresh-strip').classList.toggle('error', Boolean(stats.last_error));
}

async function loadLatest() {
  const data = await requestJson('/api/tenders?page=1&size=10');
  if (!data.items.length) {
    $('#latestList').innerHTML = '<div class="board-empty"><b>等待首次資料同步</b><span>按左上角 H 即可從官方 API 刷新。</span></div>';
    return;
  }
  $('#latestList').innerHTML = data.items.map((item, index) => {
    const active = item.contest_date && new Date(item.contest_date) >= new Date();
    return `<button class="latest-row latest-button" data-detail="${encodeURIComponent(item.no)}">
      <span class="rank"><b>${String(index + 1).padStart(2, '0')}</b><time>${formatDate(item.announcement_date)}</time></span>
      <span class="latest-title"><small>市政署 · MUNICIPAL AFFAIRS BUREAU</small><strong>${escapeHtml(item.name_zh || item.name_cn)}</strong></span>
      <span class="owner"><b>${escapeHtml(item.no)}</b><small>${escapeHtml(item.name_pt)}</small></span>
      <span class="status"><i></i>${active ? '正在招標' : '已截止'}</span><span class="arrow">↗</span>
    </button>`;
  }).join('');
  bindDetailButtons();
}

async function loadDirectory() {
  $('#tenderGrid').innerHTML = '<div class="grid-empty">正在整理公開招標資料。</div>';
  const params = new URLSearchParams({ page: state.page, size: state.size, status: state.status });
  if (state.query) params.set('q', state.query);
  if (state.year) params.set('year', state.year);
  try {
    const data = await requestJson(`/api/tenders?${params}`);
    state.total = data.total;
    populateYears(data.years);
    renderDirectory(data.items);
    renderPagination(data.total, data.size);
    const labels = { all:'全部項目', active:'正在招標', closed:'已截止項目' };
    $('#directoryTitle').textContent = labels[state.status];
    $('#resultSummary').textContent = `共找到 ${data.total} 條記錄；依公告日期由新至舊排列。`;
  } catch (error) {
    $('#tenderGrid').innerHTML = `<div class="grid-empty">${escapeHtml(error.message)}</div>`;
  }
}

function populateYears(years) {
  const select = $('#yearSelect');
  if (select.options.length > 1) return;
  years.forEach(year => select.add(new Option(`${year} 年`, year)));
}

function renderDirectory(items) {
  if (!items.length) {
    $('#tenderGrid').innerHTML = '<div class="grid-empty">沒有符合條件的公開招標項目。</div>';
    return;
  }
  $('#tenderGrid').innerHTML = items.map(item => {
    const active = item.contest_date && new Date(item.contest_date) >= new Date();
    const documentCount = [item.notice_zh, item.notice_cn, item.notice_pt, item.attachment_zh, item.attachment_pt, item.tender_result, item.award_result].filter(Boolean).length;
    return `<article class="agency-card tender-card" data-detail="${encodeURIComponent(item.no)}">
      <header><div><span>市政署 · ${escapeHtml(item.no)}</span><h4>${escapeHtml(item.name_zh || item.name_cn)}</h4></div><button type="button" aria-label="查看項目詳情">↗</button></header>
      <div class="card-meta"><span class="sync ${active ? 'online' : ''}"><i></i>${active ? '正在招標' : '已截止'}</span><time>${escapeHtml(item.name_pt)}</time></div>
      <div class="card-items"><dl>
        <div><dt>展開採購</dt><dd>${formatDate(item.procurement_date)}</dd></div>
        <div><dt>公告日期</dt><dd>${formatDate(item.announcement_date)}</dd></div>
        <div><dt>開標日期</dt><dd>${formatDate(item.contest_date)}</dd></div>
      </dl></div>
      <footer><button type="button">查看公告及文件 ↗</button><span>${documentCount} 個相關連結</span></footer>
    </article>`;
  }).join('');
  bindDetailButtons();
}

function renderPagination(total, size) {
  const pages = Math.ceil(total / size);
  const box = $('#pagination');
  if (pages <= 1) { box.innerHTML = ''; return; }
  let start = Math.max(1, state.page - 2);
  let end = Math.min(pages, start + 4);
  start = Math.max(1, end - 4);
  box.innerHTML = Array.from({ length: end - start + 1 }, (_, index) => start + index)
    .map(page => `<button class="${page === state.page ? 'active' : ''}" data-page="${page}">${page}</button>`).join('');
  box.querySelectorAll('button').forEach(button => button.onclick = () => {
    state.page = Number(button.dataset.page);
    loadDirectory();
    $('#tenders').scrollIntoView({ behavior:'smooth' });
  });
}

function bindDetailButtons() {
  document.querySelectorAll('[data-detail]').forEach(element => element.onclick = () => openDetail(decodeURIComponent(element.dataset.detail)));
}

async function openDetail(number) {
  const item = await requestJson(`/api/tenders/${encodeURIComponent(number)}`);
  $('#detailNo').textContent = item.no;
  $('#detailTitle').textContent = item.name_zh || item.name_cn;
  $('#detailPortuguese').textContent = item.name_pt || '';
  $('#detailDates').innerHTML = [
    ['展開採購日期', item.procurement_date], ['公告日期', item.announcement_date], ['開標日期', item.contest_date]
  ].map(([label, value]) => `<div><span>${label}</span><b>${formatDate(value)}</b></div>`).join('');
  const links = [
    ['繁體中文公告', item.notice_zh], ['簡體中文公告', item.notice_cn], ['葡文公告', item.notice_pt],
    ['中文招標文件', item.attachment_zh], ['葡文招標文件', item.attachment_pt],
    ['開標結果', item.tender_result], ['判給結果', item.award_result],
    [item.other_title_zh || '其他中文文件', item.other_file_zh], [item.other_title_pt || '其他葡文文件', item.other_file_pt]
  ].filter(([, url]) => url);
  $('#detailLinks').innerHTML = links.length
    ? links.map(([label, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)} ↗</a>`).join('')
    : '<p>此項目暫未提供相關文件。</p>';
  $('#detailDialog').showModal();
}

async function refreshAll() {
  const button = $('#refreshAllButton');
  button.disabled = true;
  $('#refreshIndicator').classList.add('running');
  $('#refreshStatus').textContent = '正在從澳門政府數據開放平台同步…';
  try {
    const result = await requestJson('/api/refresh', { method:'POST' });
    $('#refreshStatus').textContent = `同步完成 · ${result.count} 條記錄 · 新增 ${result.new} 條`;
    await loadStats();
    await Promise.all([loadLatest(), loadDirectory()]);
  } catch (error) {
    $('#refreshStatus').textContent = `同步失敗 · ${error.message}`;
    $('.refresh-strip').classList.add('error');
  } finally {
    button.disabled = false;
    $('#refreshIndicator').classList.remove('running');
  }
}

function bindEvents() {
  $('#refreshAllButton').onclick = refreshAll;
  $('#sourcesButton').onclick = () => $('#sourcesDialog').showModal();
  $('#closeDialog').onclick = () => $('#sourcesDialog').close();
  $('#closeDetail').onclick = () => $('#detailDialog').close();
  [$('#sourcesDialog'), $('#detailDialog')].forEach(dialog => dialog.onclick = event => { if (event.target === dialog) dialog.close(); });
  document.querySelectorAll('[data-status]').forEach(button => button.onclick = () => {
    document.querySelectorAll('[data-status]').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    state.status = button.dataset.status;
    state.page = 1;
    loadDirectory();
  });
  $('#yearSelect').onchange = event => { state.year = event.target.value; state.page = 1; loadDirectory(); };
  $('#searchInput').oninput = event => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = event.target.value.trim(); state.page = 1; loadDirectory(); }, 280);
  };
}

init().catch(error => {
  $('#latestList').innerHTML = `<div class="board-empty"><b>看板載入失敗</b><span>${escapeHtml(error.message)}</span></div>`;
  $('#refreshStatus').textContent = '資料服務暫時不可用';
  $('.refresh-strip').classList.add('error');
  bindEvents();
});
