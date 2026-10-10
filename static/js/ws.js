// 「网申」页上半部分：网申待办（一家一行、交给助手、我已提交、读回）。用到 agent.js 里的 AG、agShow 等；
// 定时刷新和打开面板时的第一次加载在 core.js 末尾的「启动」里。
// ━━━ 网申待办（「网申」页）━━━
const WT = {items: [], sig: ''};
const WT_CLS = {'待填': 'bg-slate-200 text-slate-700', '助手在填': 'bg-teal-100 text-teal-700', '等你处理': 'bg-amber-300 text-amber-950', '已填待提交': 'bg-blue-100 text-blue-800',
  '已提交': 'bg-emerald-100 text-emerald-800', '不投了': 'bg-slate-100 text-slate-400'};
async function loadWsTasks(force = false) {
  let d;
  try { d = await api('/api/wstasks'); } catch (e) { return; }
  WT.items = d.tasks; WT.max = d.max_parallel || 3; WT.colors = d.colors || WT.colors || [];
  AGC.active = d.active || 0; AGC.waiting = d.waiting || 0; agFab();
  const ready = d.tasks.filter(x => x.status === '待填' && !x.agent_state && x.url).length;
  $('#wtRunAll').classList.toggle('hidden', ready < 2);
  $('#wtRunAllLabel').textContent = `待填的 ${ready} 家全部交给助手（同时 ${WT.max} 个）`;
  const c = d.counts || {}, todo = (c['待填'] || 0) + (c['助手在填'] || 0) + (c['等你处理'] || 0) + (c['已填待提交'] || 0);
  $('#wsBadge').textContent = todo; $('#wsBadge').classList.toggle('hidden', !todo);
  const sig = JSON.stringify(d.tasks.map(x => [x.id, x.status, x.updated_at, x.agent_state, x.last, x.todo]));
  if (AG.open && sig !== WT.sigAg) { WT.sigAg = sig; agLoadList().catch(() => {}); }
  if (S.tab === 'kit' && (sig !== WT.sig || force)) { WT.sig = sig; renderWsTasks(c); }
}
function wtTurn(x) {   // 这一行最显眼的那句：现在轮到谁
  if (x.agent_state === '排队中') return {chip: '排队中', cls: 'bg-slate-200 text-slate-600', line: x.agent_wait};
  if (x.agent_state === '在干活' && x.status === '等你处理')   // 助手留在这一轮里等本人（证件号、登录）：先说要你做什么
    return {chip: '等你处理', cls: WT_CLS['等你处理'], warn: true, line: '要你做：' + (x.todo || '看对话') + '（助手在等，你做好了它自己接着干）'};
  if (x.agent_state === '在干活') return {chip: '助手在填', cls: WT_CLS['助手在填'], spin: true, line: x.last ? '最新进展：' + x.last : '助手正在做'};
  if (x.status === '等你处理') return {chip: '等你处理', cls: WT_CLS['等你处理'], warn: true, line: '要你做：' + (x.todo || '助手没写，看对话')};
  if (x.status === '助手在填') return {chip: '助手停了', cls: 'bg-red-100 text-red-800', warn: true, line: (x.halted || '助手进程已经结束') + '；要它接着做就点「让助手接着做」'};
  return {chip: x.status, cls: WT_CLS[x.status] || '', line: x.status === '已填待提交' ? '填好了：你检查一下，在网站上提交后点「我已提交」' : ''};
}
function renderWsTasks(c) {
  $('#wsTasksSummary').textContent = Object.entries(c).map(([k, v]) => `${k} ${v}`).join(' ｜ ');
  $('#wsTaskList').innerHTML = WT.items.map(x => {
    const done = ['已提交', '不投了'].includes(x.status), url = safeUrl(x.url), turn = wtTurn(x), col = wtColor(x);
    const busy = !!x.agent_state, id = esc(x.id);
    const sub = [x.source, x.deadline ? '截止 ' + x.deadline : '', x.note].filter(Boolean).map(esc).join(' · ');
    const btn = (cls, label, extra = '', title = '') => `<button class="${cls}" data-id="${id}" ${extra} ${title ? `title="${esc(title)}"` : ''}>${label}</button>`;
    const P = 'px-3 py-1.5 rounded-lg text-xs font-bold text-white ', S = 'px-2 py-1.5 border rounded-lg text-xs ';
    let main = '';   // 最该点的那个按钮
    const idNeed = x.status === '等你处理' && x.need_kind === '证件号';   // 面板按「要你做」算的（和看板同一个判断）
    if (idNeed) main = !busy ? btn('wt-run ' + P + 'bg-amber-600', '我填好了', '', '证件号你在网页上填好了：叫助手接着做') : '';   // 助手还在等的话它自己会看到
    else if (!busy && x.status === '等你处理') main = btn('wt-run ' + P + 'bg-amber-600', '让助手接着做', '', '助手会先看一眼网页，确认你那边做好了再接着填');
    else if (!busy && x.status === '已填待提交') main = btn('wt-set ' + P + 'bg-blue-600', '我已提交', 'data-status="已提交"', '记进投递看板，助手再去网站把实际提交的内容读回来');
    else if (!busy && !done) main = btn('wt-run ' + P + 'bg-teal-600', x.chat_id ? '让助手接着做' : '让助手填');
    else if (x.status === '已提交' && x.readback_state !== '读回中' && !busy)
      main = btn('wt-rb ' + P + 'bg-cyan-600', x.readback && Object.keys(x.readback).length ? '查最新进度' : '读回网站内容', '', '助手去网站把实际提交的内容、进度、账号读回来（只看不改）');
    const more = [
      !busy && x.status === '已填待提交' ? btn('wt-run ' + S, '让助手接着改') : '',
      x.chat_id ? `<button class="wt-chat ${S}" data-chat="${esc(x.chat_id)}">看对话</button>` : '',
      !done && x.status !== '已填待提交' ? btn('wt-set ' + S, '我已提交', 'data-status="已提交"', '你已经在网站上提交了：记进投递看板') : '',
      !done ? btn('wt-set ' + S, '不投了', 'data-status="不投了"') : btn('wt-set ' + S, '放回待填', 'data-status="待填"'),
    ].join('');
    return `<div class="bg-slate-50 rounded-xl p-3 flex flex-wrap xl:flex-nowrap items-center gap-x-3 gap-y-2 ${done ? 'opacity-60' : ''}" ${done ? '' : `style="border-left:6px solid ${col[1]}"`}>
      <span class="chip ${turn.cls}">${turn.spin ? '<span class="material-symbols-outlined animate-spin text-xs">progress_activity</span>' : ''}${esc(turn.chip)}</span>
      <div class="flex-1 min-w-[12rem]"><div class="text-sm font-semibold truncate">${done ? '' : `<span title="${esc(col[2])}色：助手在网页四周画的框、对话框里也是这个颜色" style="color:${col[1]}">●</span> `}${esc(x.company || hostOf(x.url) || '（公司待定）')}｜${esc(x.job || '（岗位待定）')}</div>
        ${turn.line ? `<div class="text-xs mt-0.5 ${turn.warn ? 'text-amber-800 font-semibold' : 'text-slate-600'}">${esc(turn.line)}</div>` : ''}
        <div class="text-xs text-slate-500 truncate">${url ? `<a class="text-primary underline" target="_blank" rel="noopener" href="${esc(url)}">${esc(url)}</a>` : '还没有网申链接'}${sub ? ' · ' + sub : ''}</div>
        <div class="text-xs mt-0.5"><button class="wt-acct text-slate-600 hover:text-primary" data-id="${id}" title="在这家网站注册账号用的手机号 / 邮箱">申请账号：${x.account ? `<b>${esc(x.account)}</b>` : '<span class="text-amber-700">没记，点这里填</span>'} <span class="material-symbols-outlined !text-[13px] align-middle">edit</span></button></div>${wtReadbackLine(x)}</div>
      <div class="flex flex-wrap items-center justify-end gap-2 ml-auto">${main}${more}
        <button class="wt-del px-2 py-1.5 text-slate-400 hover:text-red-600 text-xs" data-id="${id}" title="从清单删掉"><span class="material-symbols-outlined text-base">close</span></button>
      </div></div>`;
  }).join('') || '<p class="text-sm text-slate-400 p-2">还没有网申待办。投递页遇到只能网申的岗位会自动加到这里，也可以在上面贴链接。</p>';
}
const WT_RB = {status: '投递记录', resume: '提交的简历', other: '其他页面'};
const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return ''; } };
function wtColor(x) {   // [圆点, 色值, 颜色名]：面板这一行、对话框、助手在网页四周画的框都用这个
  const cs = WT.colors || [];
  return (x && Number.isInteger(x.color) && cs.length) ? cs[x.color % cs.length] : ['', '#94a3b8', '灰'];
}
function wtReadbackLine(x) {   // 已提交的：网站上的进度、读回了哪几页（看板里能看到原文）
  const parts = Object.entries(x.readback || {}).map(([k, v]) => `${WT_RB[k] || k}（${esc((v.at || '').slice(5))}）`);
  const bits = [x.site_status ? `网站进度：<b>${esc(x.site_status)}</b>` : '',
    parts.length ? '已读回：' + parts.join('、') + '，看板里能看原文' : '',
    x.readback_state ? `<span class="${x.readback_state === '读回中' ? 'text-teal-700' : 'text-amber-700'}">${x.readback_state === '读回中' ? '<span class="material-symbols-outlined animate-spin !text-[12px] align-middle">progress_activity</span> ' : ''}${esc(x.readback_state)}</span>` : ''].filter(Boolean);
  return bits.length ? `<div class="text-xs mt-0.5 text-cyan-800">${bits.join(' · ')}</div>` : '';
}
async function wtReadback(id) {
  const d = await api(`/api/wstasks/${id}/readback`, {method: 'POST'});
  toast(d.message, 6000);
  if (d.chat) { AG.id = d.chat.id; AG.n = 0; $('#agentMsgs').innerHTML = ''; agAppend(d.chat.messages); AG.n = d.chat.total; agSetRunning(!d.chat.queued, d.chat.queued); }
  return loadWsTasks(true);
}
async function wtRun(id) {
  WT.inflight = WT.inflight || new Set();
  if (WT.inflight.has(id)) return;   // 快速点两次：第二次不发（不然会开出两个对话 / 发两遍）
  WT.inflight.add(id);
  document.querySelectorAll(`#wsTaskList .wt-run[data-id="${id}"]`).forEach(b => { b.disabled = true; b.classList.add('opacity-50'); });
  try {
    const d = await api(`/api/wstasks/${id}/agent`, {method: 'POST'});
    AG.id = d.id; AG.n = 0; $('#agentMsgs').innerHTML = '';
    agAppend(d.messages); AG.n = d.total; agSetRunning(!d.queued, d.queued);
    if (d.queued) toast('排队中：' + d.queued, 5000);
    await agShow(true);
    loadWsTasks(true);
  } catch (e) { toast(e.message, 6000); }
  finally { WT.inflight.delete(id); }
}
async function wtAdd(run) {
  const url = safeUrl($('#wsTaskUrl').value.trim());
  if (!url) return toast('先粘贴网申页面的链接（https:// 开头）');
  const [company, job] = $('#wsTaskName').value.split(/[｜|]/).map(s => (s || '').trim());
  try {
    const d = await api('/api/wstasks', {method: 'POST', body: {url, company: company || '', job: job || ''}});
    $('#wsTaskUrl').value = ''; $('#wsTaskName').value = '';
    await loadWsTasks(true);
    if (run) await wtRun(d.task.id); else toast('已加到网申待办');
  } catch (e) { toast(e.message, 5000); }
}
$('#wtRunAll').onclick = async () => {   // 待填的全部交给助手：同时几个一起填，其余排队，空出来自动接上
  const todo = WT.items.filter(x => x.status === '待填' && !x.agent_state && x.url);
  let n = 0;
  for (const x of todo) {
    try { await api(`/api/wstasks/${x.id}/agent`, {method: 'POST'}); n++; } catch (e) { toast(e.message, 5000); break; }
  }
  if (n) toast(`交给助手 ${n} 家：同时做 ${Math.min(n, WT.max || 3)} 家，其余排队（同一个网站的一家一家来）`, 6000);
  loadWsTasks(true);
};
$('#wsTaskAddRun').onclick = () => wtAdd(true);
$('#wsTaskAdd').onclick = () => wtAdd(false);
$('#wsTaskUrl').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); wtAdd(true); } });
$('#wsTaskList').addEventListener('click', async e => {
  const b = e.target.closest('button'); if (!b) return;
  try {
    if (b.classList.contains('wt-run')) return wtRun(b.dataset.id);
    if (b.classList.contains('wt-rb')) return wtReadback(b.dataset.id);
    if (b.classList.contains('wt-acct')) {
      const cur = (WT.items.find(x => x.id === b.dataset.id) || {}).account || '';
      const val = await modal({title: '申请账号', html: `<p class="text-xs text-slate-500 mb-2">在这家网申网站注册账号用的手机号或邮箱（以后登录、查进度用）。</p><input id="wtAcctInput" class="field" value="${esc(cur)}" placeholder="手机号或邮箱（打码的照抄，如 138****0000）"/>`,
        buttons: [{label: '取消', value: null}, {label: '保存', value: 'save', kind: 'primary'}]});
      if (val !== 'save') return;
      await api(`/api/wstasks/${b.dataset.id}`, {method: 'PUT', body: {account: ($('#wtAcctInput') || {}).value || ''}});
      toast('申请账号已记下（看板那条记录也同步了）');
      return loadWsTasks(true);
    }
    if (b.classList.contains('wt-chat')) { await agShow(true); $('#agentChats').value = b.dataset.chat; return agOpenChat(b.dataset.chat); }
    if (b.classList.contains('wt-set')) {
      const d = await api(`/api/wstasks/${b.dataset.id}`, {method: 'PUT', body: {status: b.dataset.status}});
      toast(d.task.status === '已提交' ? '已记进投递看板（已投递）' + (d.readback ? '；' + d.readback : '') : d.task.status === '不投了' ? '已标成不投了（看板里的草稿删掉了）' : '已放回待填', 6000);
      if (d.readback && d.task.chat_id) { $('#agentChats').value = d.task.chat_id; await agOpenChat(d.task.chat_id); }
      return loadWsTasks(true);
    }
    if (b.classList.contains('wt-del')) {
      const ok = await modal({title: '从清单删掉这条？', html: '<p>只删网申待办（没提交的话，看板里对应的草稿一起删）；已提交的记录留在看板里。</p>',
        buttons: [{label: '取消', value: false}, {label: '删掉', value: true, kind: 'danger'}]});
      if (!ok) return;
      await api(`/api/wstasks/${b.dataset.id}`, {method: 'DELETE'});
      return loadWsTasks(true);
    }
  } catch (err) { toast(err.message, 5000); }
});
$('#wsResumeDl2').onclick = async () => {
  const body = {version: '中文', filename: (S.cfg && S.cfg.resume_default_zh) || '简历', i: 0};
  const resp = await fetch('/api/resume-download', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'jobapply'}, body: JSON.stringify(body)});
  if (!resp.ok) return toast('下载失败');
  const blob = await resp.blob(), a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (body.filename || '简历').replace(/\.pdf$/i, '') + '.pdf';
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  toast('已下载：' + a.download + '（在「下载」文件夹，网申要上传简历时用它）', 5000);
};
