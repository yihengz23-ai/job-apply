// 公用：工具函数、接口调用、弹窗、导航、初始化（文件末尾的「启动」）。
// 页面脚本都是普通 <script>，按 index.html 里的顺序加载：顶层的函数和 const/let 各文件共用，不能重名；
// 一加载就执行、又要用到后面文件里函数的语句（定时刷新、打开面板时拉数据）只放文件末尾的「启动」，所有脚本执行完它才跑。
// ━━━ 工具 ━━━
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const escBr = s => esc(s).replace(/\n/g, '<br>');
const opt = (v, label, sel) => `<option value="${esc(v)}" ${v === sel ? 'selected' : ''}>${esc(label ?? v)}</option>`;
// 收件人可能是列表，也可能是用户在框里改过的「a; b」字符串
const listArr = v => Array.isArray(v) ? v.filter(Boolean) : String(v || '').split(/[;,，；\s]+/).filter(Boolean);
const listStr = v => listArr(v).join('; ');
// 只放行 http(s) 链接（防止 javascript: 之类的东西被点开）
const safeUrl = u => /^https?:\/\/[^\s"'<>]+$/i.test(String(u || '').trim()) ? String(u).trim() : '';
const isWangshen = r => !!r && (['网申/链接', '邮箱+网申'].includes(r.apply_channel) || !listArr(r.to_emails).length);
// HR 在国内：凌晨提醒按北京时间算（人在美国时也对）
const bjHour = () => +new Intl.DateTimeFormat('en-US', {timeZone: 'Asia/Shanghai', hour: 'numeric', hourCycle: 'h23'}).format(new Date());
const isNight = () => { const [s, e] = (S.cfg && S.cfg.night_hours) || [21, 7], h = bjHour(); return h >= s || h < e; };
const sendAt = () => (S.cfg && S.cfg.send_at) || '10:00';
const BASE_TITLE = document.title;
async function copyText(text, label = '') {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {  // 通过隧道用 http 打开时没有剪贴板权限，退回老办法
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  toast('已复制' + (label ? '：' + label : ''), 1500);
}

async function api(url, opts = {}) {
  const o = {...opts, headers: {'Content-Type': 'application/json', 'X-Requested-With': 'jobapply', ...(opts.headers || {})}};
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const resp = await fetch(url, o);
  let data = null;
  try { data = await resp.json(); } catch (e) {}
  if (!resp.ok) {
    const err = new Error((data && data.error) || `HTTP ${resp.status}`);
    err.status = resp.status; err.data = data; throw err;
  }
  return data;
}

function toast(msg, ms = 4000) {
  const t = $('#toast'); t.textContent = msg;
  t.classList.remove('-translate-y-24', 'opacity-0');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('-translate-y-24', 'opacity-0'), ms);
}

let modalChain = Promise.resolve();
function modal(opts) {
  const p = modalChain.then(() => showModal(opts));
  modalChain = p.then(() => {}, () => {});
  return p;
}
function showModal({title, html, buttons}) {
  return new Promise(resolve => {
    $('#modalTitle').textContent = title;
    $('#modalBody').innerHTML = html;
    const box = $('#modalButtons'); box.innerHTML = '';
    buttons.forEach(b => {
      const el = document.createElement('button');
      el.textContent = b.label;
      el.className = 'px-4 py-2 rounded-xl text-sm font-bold ' + (b.kind === 'primary' ? 'bg-primary text-white' : b.kind === 'danger' ? 'bg-red-600 text-white' : 'bg-slate-100 text-slate-600');
      el.onclick = () => { $('#modal').classList.add('hidden'); resolve(b.value); };
      box.appendChild(el);
    });
    $('#modal').classList.remove('hidden');
  });
}

function setBusy(btn, busy, label) {
  if (busy) { if (btn._busyHtml == null) btn._busyHtml = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="material-symbols-outlined animate-spin">progress_activity</span>${esc(label || '处理中…')}`; }
  else { btn.disabled = false; if (btn._busyHtml != null) { btn.innerHTML = btn._busyHtml; btn._busyHtml = null; } }
}
// 发送 / 草稿 / 记录 按钮：做完后会变成「已发送」等，换下一条时要恢复原样
const ACTION_BTNS = ['sendBtn', 'draftBtn', 'recordBtn', 'regenBtn', 'wsRecordBtn'];
const ORIG_HTML = {};
ACTION_BTNS.forEach(id => ORIG_HTML[id] = $('#' + id).innerHTML);
function resetActionButtons(enabled) {
  ACTION_BTNS.forEach(id => { const b = $('#' + id); b.innerHTML = ORIG_HTML[id]; b._busyHtml = null; b.disabled = !enabled; });
  updateSendLabel();
}
// 下一个自动发送的时间，按北京时间说「今天 / 明天上午 10:00」
function whenLabel() {
  const [h, m] = sendAt().split(':').map(Number);
  const now = new Intl.DateTimeFormat('en-US', {timeZone: 'Asia/Shanghai', hour: 'numeric', minute: 'numeric', hourCycle: 'h23'}).formatToParts(new Date());
  const hh = +now.find(x => x.type === 'hour').value, mm = +now.find(x => x.type === 'minute').value;
  return `${hh * 60 + mm < h * 60 + m ? '今天' : '明天'}上午 ${sendAt()}`;
}
// 「发出」按钮：白天 = 现在发；晚上 = 定在明早发（确认框里还能选现在就发）
function updateSendLabel() {
  const b = $('#sendBtn');
  if (!b || b._busyHtml != null || (S.result && (S.sent || S.wsRecorded))) return;
  b.innerHTML = isNight() ? `<span class="material-symbols-outlined">schedule</span>定在${whenLabel()} 发出`
    : '<span class="material-symbols-outlined" style="font-variation-settings:\'FILL\' 1">send</span>发出';
}
function markDone(btn, text) {
  btn._busyHtml = null; btn.innerHTML = `<span class="material-symbols-outlined">check_circle</span>${esc(text)}`; btn.disabled = true;
}

// ━━━ 全局状态 ━━━
const S = {cfg: null, page: null, pageJd: '', analysis: null, result: null, records: [], fixes: [], sent: false, queue: [], queueId: null,
  queueRev: '', tab: 'new', kit: null, kitData: null, sentRecordId: '', wsRecorded: false, wsOpened: null,
  gen: 0, lock: null, analyzeBusy: false, kitLoadingGen: null, editsDirty: false, userEdited: false, uncertain: false, lastIssues: [],
  autoOpen: true, opening: false, listOpen: true, regenWaiting: false, regenRev: '', regenWatch: new Set()};

// ━━━ Tab ━━━
function switchTab(tab) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  $('#tab-' + tab).classList.add('active');
  document.querySelectorAll('.nav-link').forEach(el => {
    el.className = 'nav-link flex items-center gap-3 px-4 py-2 cursor-pointer rounded-lg ' +
      (el.dataset.tab === tab ? 'text-slate-900 font-semibold bg-slate-200/60' : 'text-slate-500 hover:bg-slate-200/40');
  });
  document.querySelectorAll('.mob-tab').forEach(el => el.classList.toggle('text-primary', el.dataset.tab === tab));
  $('#pageTitle').textContent = {new: '投递', board: '投递看板', analytics: '数据分析', kit: '网申'}[tab];
  S.tab = tab;
  if (tab === 'new') loadQueue(true);
  if (tab === 'board') loadBoard();
  if (tab === 'analytics') loadAnalytics();
  if (tab === 'kit') loadWsTasks(true);
  if (tab === 'kit') loadKitPage();
}
document.querySelectorAll('[data-tab]').forEach(el => el.addEventListener('click', () => switchTab(el.dataset.tab)));

// ━━━ 初始化 ━━━
async function init() {
  try {
    S.cfg = await api('/api/config');
  } catch (e) { toast('读取配置失败：' + e.message); return; }
  const c = S.cfg;
  if (c.records_error) modal({title: '投递记录文件读不了', html: `<p class="text-red-700">${esc(c.records_error)}</p><p class="text-xs mt-2">为了不覆盖历史记录，系统已停止写入。把这个提示截图给帮你维护的人，或从 backups 文件夹里的备份恢复。</p>`,
    buttons: [{label: '知道了', value: true, kind: 'primary'}]});
  $('#campaignLabel').textContent = '本轮：' + c.campaign;
  $('#reportLabel').textContent = c.report_label || '研究样本';
  updateSendLabel();
  $('#modelBadge').textContent = c.model + ' · ' + c.effort + (c.backend === 'claude_code' ? ' · 会员额度' : ' · API');
  $('#qPos').innerHTML += c.position_types.filter(p => p !== '不明确').map(p => opt(p)).join('');
  $('#qResume').innerHTML += c.resume_versions.map(v => opt(v)).join('');
  $('#resumeVersion').innerHTML = c.resume_versions.map(v => opt(v)).join('');
  $('#fStatus').innerHTML += c.statuses.map(s => opt(s)).join('');
  $('#fPosition').innerHTML += c.position_types.map(p => opt(p)).join('');
  const camps = [opt(c.campaign, '本轮：' + c.campaign, c.campaign), opt('全部', '全部批次')]
    .concat(c.campaigns.filter(x => x !== c.campaign).map(x => opt(x)));
  $('#fCampaign').innerHTML = camps.join(''); $('#aCampaign').innerHTML = camps.join('');
  const r = c.resume;
  const resumeLine = r.ok ? (r.grad_problems.length ? `<span class="text-red-600">简历毕业时间不对：${esc(r.grad_problems.join('；'))}</span>`
      : `简历：${r.pages} 页（中 ${r.zh_pages.length} / 英 ${r.en_pages.length}），${r.size_kb}KB`) : `<span class="text-red-600">${esc(r.error)}</span>`;
  $('#sysStatus').innerHTML = `<div>${resumeLine}</div><div>研究样本：${c.report_exists ? '已就绪' : '<span class=text-red-600>缺文件</span>'}</div><div id="gmailSide">Gmail：检查中…</div>`;
  updateResumeControls();
  refreshGmail();
  if (c.local) fetch('/api/tunnel-url').then(r => r.json()).then(d => {
    if (d.url) { $('#tunnelUrl').href = d.url; $('#tunnelUrl').textContent = d.url; $('#tunnelInfo').classList.remove('hidden'); }
  }).catch(() => {});
}

async function refreshGmail() {
  const pill = $('#gmailPill');
  try {
    const st = await api('/api/gmail/status');
    if (st.ok) {
      pill.className = 'chip bg-green-600/20 text-green-300'; pill.textContent = 'Gmail 已连接'; pill.onclick = null;
      $('#gmailSide').innerHTML = 'Gmail：' + esc(st.email);
    } else {
      pill.className = 'chip bg-red-500/30 text-red-100 cursor-pointer'; pill.textContent = 'Gmail 未授权（点这里）';
      pill.onclick = gmailAuth;
      $('#gmailSide').innerHTML = '<span class="text-red-600">Gmail 未授权</span> <button class="underline text-primary" id="sideAuth">授权</button>';
      $('#sideAuth').onclick = gmailAuth;
    }
  } catch (e) { pill.textContent = 'Gmail 状态未知'; }
}

async function gmailAuth() {
  if (!S.cfg.local) { toast('请在电脑上打开面板完成 Gmail 授权'); return; }
  toast('已在浏览器打开 Google 授权页，请点「允许」（5 分钟内有效）', 8000);
  try { await api('/api/gmail/auth', {method: 'POST', body: {}}); toast('Gmail 授权成功'); }
  catch (e) { toast('授权失败：' + e.message, 6000); }
  refreshGmail();
}

$('#copyTunnel').onclick = () => { navigator.clipboard.writeText($('#tunnelUrl').href); toast('已复制'); };

// ━━━ 启动 ━━━
// 定时刷新、打开面板时拉数据：要用到后面几个文件里的函数，所以等所有页面脚本执行完（DOMContentLoaded）再跑，先后和拆文件前一样。
// 每句单独 try：哪个脚本没取到（隧道 502、超时），只少它那一块，配置、队列这些照常拉。
document.addEventListener('DOMContentLoaded', () => [
  () => setInterval(() => { if (AG.id && (AG.open || AG.running || AG.waiting) && !document.hidden) agPoll(); }, 1500),
  () => api('/api/agent').then(d => { if (d.running) { AG.id = d.running; agSetRunning(true); } }).catch(() => {}),   // 打开面板时它还在做：右下角标出来
  () => setInterval(() => { if (!document.hidden) loadWsTasks(); }, 4000),
  () => loadWsTasks(),
  () => setInterval(() => { if (!document.hidden) loadQueue(); }, 4000),
  () => setInterval(updateSendLabel, 60000),
  () => switchTab('new'),
  () => init(),
  () => loadQueue(),
].forEach(f => { try { f(); } catch (e) { console.error('启动出错', e); } }));
