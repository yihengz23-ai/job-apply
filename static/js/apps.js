// 「投递看板」页上半部分（第 2 批）：今天（口述框、轮到你、停了、一周内到期、最近口述可撤销）＋ 按申请成组的看板
// （一张卡 = 一次申请，同一家几个岗位 / 志愿合在一起，串行志愿显示在看 / 排队 / 已流转）＋ 单家详情抽屉。
// 数据都来自 /api/apps/* 和 /api/progress*（jobapply/app_api.py 算好）。「按岗位看」的老表格还在，见 board.js。
const AP = {apps: [], today: null, view: 'app', detailId: null, inflight: new Set(), lastProg: null};
const TURN_CLS = {'轮到你': 'bg-amber-300 text-amber-950', '停了': 'bg-red-100 text-red-700', '面板在做': 'bg-teal-100 text-teal-700',
  '等对方': 'bg-slate-100 text-slate-600', '已结束': 'bg-slate-100 text-slate-400'};
const VOL_LABEL = {'串行': '串行志愿', '平行': '平行', '单个': '单个岗位', '': ''};
const STAGE_OPTS = [['草稿', '准备中'], ['已投递', '已投递'], ['笔试', '笔试/测评'], ['已电联', '已电联'], ['面试中', '面试'], ['offer', 'offer'],
  ['拒绝', '未通过'], ['无回复', '无回复'], ['放弃', '放弃']];
const stageSelect = (cls, cur, extra = '') => `<select class="${cls} field !w-auto !py-0.5 !px-2 text-xs" ${extra}>${STAGE_OPTS.map(([k, l]) => opt(k, l, cur)).join('')}</select>`;
const turnChip = c => `<span class="chip ${TURN_CLS[c.turn] || ''}">${esc(c.turn)}</span>`;
const apStage = (key, label) => `<span class="chip ${STATUS_CLS[key] || 'bg-slate-100 text-slate-600'}">${esc(label || key)}</span>`;
const apCounts = c => Object.entries(c.stage_counts || {}).map(([k, n]) => `${k} ${n}`).join(' · ');
const apDot = c => `<span class="inline-block w-2.5 h-2.5 rounded-full shrink-0" style="background:${esc(c.color_hex || '#cbd5e1')}" title="${esc(c.color_label)}"></span>`;
try { AP.view = localStorage.getItem('boardView') === 'record' ? 'record' : 'app'; } catch (e) {}
function apViewToggle() {   // 「按申请看 / 按岗位看」两块的显示：一加载就切好，不等新接口（新接口出错也不挡老表格）
  $('#appsView').classList.toggle('hidden', AP.view !== 'app');
  $('#recordsView').classList.toggle('hidden', AP.view === 'app');
  document.querySelectorAll('.ap-view').forEach(b => { b.classList.toggle('bg-white', b.dataset.view === AP.view); b.classList.toggle('shadow-sm', b.dataset.view === AP.view); });
  ['#fPosition', '#fType', '#fCity'].forEach(x => $(x).classList.toggle('hidden', AP.view === 'app'));   // 这几个只筛「按岗位看」
}
apViewToggle();

function apVolLine(c) {   // 「3 个岗位」；网站写了志愿规则的才多一句「串行志愿 / 平行」（不知道就不提，不打扰）
  const n = c.positions.length;
  if (n <= 1) return '';
  const mode = VOL_LABEL[c.volunteer_mode] ?? c.volunteer_mode;
  return `${n} 个${c.volunteer_mode === '串行' ? '志愿' : '岗位'}` + (mode ? ` · ${esc(mode)}` : '');
}
function apNext(c) {
  const n = c.next || {};
  if (!n.text) return '';
  const due = n.due && !n.text.includes(n.due) ? `（截止 ${esc(n.due)}）` : '';
  return `<span class="${n.done ? 'line-through text-slate-400' : ''}">${esc(n.text)}${due}</span>${n.inferred ? ' <span class="chip bg-slate-100 text-slate-500">推算</span>' : ''}`;
}
function apBtn(c, cls = '') {   // 这张卡上「轮到你 / 停了」那一个按钮（按钮上的字 = 点了会怎样）
  const b = c.button;
  if (!b) return '';
  return `<button class="ap-act shrink-0 px-3 py-1.5 rounded-lg text-xs font-bold ${c.turn === '停了' ? 'bg-red-600' : 'bg-amber-600'} text-white ${cls}" data-id="${esc(c.id)}" data-action="${esc(b.action)}">${esc(b.label)}</button>`;
}

async function loadApps() {
  const camp = $('#fCampaign') ? $('#fCampaign').value : '';
  const [o, t] = await Promise.allSettled([api('/api/apps/overview?campaign=' + encodeURIComponent(camp)), api('/api/apps/today')]);
  if (t.status === 'fulfilled') { AP.today = t.value; renderToday(); }
  else $('#todayLists').innerHTML = `<p class="text-xs text-red-600">「今天」没取到：${esc(t.reason.message)}</p>`;
  if (o.status === 'fulfilled') { AP.apps = o.value.apps; AP.err = ''; }
  else AP.err = o.reason.message;
  renderApps();
  if (AP.detailId && $('#appDrawer').classList.contains('open')) apDetail(AP.detailId, true);
}

// ━━━ 今天 ━━━
function renderToday() {
  const t = AP.today;
  if (!t) return;
  const row = c => `<div class="flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5 border-b border-slate-100 last:border-0">${apDot(c)}
      <button class="ap-open font-semibold text-sm hover:underline max-w-[45%] truncate" data-id="${esc(c.id)}" title="${esc(c.company)}">${esc(c.company)}</button>
      <span class="text-sm text-slate-600 flex-1 min-w-[8rem]">${esc(c.todo || (c.next || {}).text || '')}</span><span class="flex gap-2 ml-auto">${apBtn(c)}</span></div>`;
  const soon = c => `<div class="flex items-center gap-2 py-1.5 border-b border-slate-100 last:border-0">${apDot(c)}
      <button class="ap-open font-semibold text-sm hover:underline shrink-0" data-id="${esc(c.id)}">${esc(c.company)}</button>
      <span class="text-sm text-slate-600 flex-1 min-w-0">${apNext(c)}</span></div>`;
  const recent = (t.recent_progress || []).map(e => `<div class="flex items-start gap-2 py-1 text-xs ${e.undone ? 'text-slate-400 line-through' : 'text-slate-600'}">
      <span class="shrink-0 text-slate-400">${esc((e.at || '').slice(5, 16))}</span><span class="flex-1 min-w-0">「${esc(e.text.length > 60 ? e.text.slice(0, 60) + '…' : e.text)}」${(e.applied || []).length ? ' → ' + esc(e.applied.map(a => a.company_name || a.company).join('、')) : ''}</span>
      ${e.undone ? '' : `<button class="prog-undo shrink-0 text-primary hover:underline" data-id="${esc(e.id)}">撤销</button>`}</div>`).join('');
  $('#todayLists').innerHTML = `
    <div><h4 class="text-[11px] font-bold text-slate-400 mb-1">轮到你（${t.your_turn.length}）</h4>
      ${t.your_turn.map(row).join('') || '<p class="text-xs text-slate-400 py-1">现在没有要你做的事。</p>'}
      ${t.mail_review ? `<button class="ap-go-mail mt-2 text-xs font-bold text-primary hover:underline">${t.mail_review} 封信等你审 → 去「投递」页</button>` : ''}</div>
    <div>${t.stopped.length ? `<h4 class="text-[11px] font-bold text-red-500 mb-1">助手停了（${t.stopped.length}）</h4>${t.stopped.map(row).join('')}` : ''}
      ${t.soon.length ? `<h4 class="text-[11px] font-bold text-slate-400 mb-1 ${t.stopped.length ? 'mt-3' : ''}">一周内到期</h4>${t.soon.map(soon).join('')}` : ''}
      <p class="text-[11px] text-slate-400 mt-2">面板在做 ${t.busy} 家 · 等对方 ${t.waiting} 家</p>
      ${recent ? `<details class="mt-2"><summary class="cursor-pointer text-[11px] font-bold text-slate-400">最近说过的进展（可撤销）</summary><div class="mt-1">${recent}</div></details>` : ''}</div>`;
}

async function progSend(text, events, restAsk = []) {   // restAsk：点了一个候选以后，还没对上的那几件留着接着点
  const btn = $('#progSend');
  setBusy(btn, true, events ? '在记…' : '在拆…');
  try {
    const d = await api('/api/progress', {method: 'POST', body: events ? {text, events} : {text}});
    if (!events && $('#progText').value.trim() === text) $('#progText').value = '';   // 等的时候又打了下一句：不清掉
    d.ask = (d.ask || []).concat(restAsk);
    progShow(d);
    loadBoard();
  } catch (e) { toast(e.message, 8000); }
  finally { setBusy(btn, false); }
}
function progShow(d) {
  AP.lastProg = d;
  const did = (d.applied || []).map(a => `<li><b>${esc(a.company_name || a.company)}</b>：${esc(a.event)}${a.round ? '（' + esc(a.round) + '）' : ''}${a.due ? '，截止 ' + esc(a.due) : ''}${a.note ? `<span class="text-amber-700">——${esc(a.note)}</span>` : ''}</li>`).join('');
  const ask = (d.ask || []).map((a, i) => `<li><b>${esc(a.company || '？')}</b>：${esc(a.event)}——${a.candidates.length ? '是哪一家？' + a.candidates.map(c =>
      `<button class="prog-pick ml-1 mt-1 px-2 py-0.5 rounded-lg bg-slate-100 hover:bg-slate-200" data-i="${i}" data-app="${esc(c.app_id)}">${esc(c.label || c.company)}</button>`).join('') : '看板里没找到这家（先投递 / 记一笔再说）'}</li>`).join('');
  $('#progResult').innerHTML = (did || ask) ? `<div class="text-xs bg-slate-50 rounded-xl p-3 space-y-1">
      ${did ? `<p class="text-slate-500">记下了：</p><ul class="list-disc pl-5">${did}</ul>` : ''}${ask ? `<p class="text-amber-700 mt-1">这几件没对上：</p><ul class="list-disc pl-5">${ask}</ul>` : ''}
      ${did ? `<button class="prog-undo text-primary hover:underline" data-id="${esc(d.id)}">撤销这一句</button>` : ''}</div>`
    : '<p class="text-xs text-slate-400">没听出具体进展（换个说法，比如「收到 A 公司的笔试，周日截止」）。</p>';
}

// ━━━ 按申请成组的看板 ━━━
function apFiltered() {
  const q = ($('#fSearch').value || '').trim().toLowerCase(), fs = $('#fStatus').value;
  return AP.apps.filter(c => (!fs || c.positions.some(p => p.status === fs)) &&
    (!q || [c.company, c.account, c.todo, c.site_progress, c.search, ...c.positions.map(p => p.job_title + ' ' + p.org + ' ' + p.location)].join(' ').toLowerCase().includes(q)));
}
function apPositions(c) {   // 展开后：每个岗位一行（志愿序号、岗位、阶段、串行时的处境、网站进度）
  return c.positions.map(p => `<div class="flex items-center gap-2 py-1 text-xs">
      <span class="w-14 shrink-0 text-slate-400">${p.choice_no ? `志愿 ${esc(p.choice_no)}${p.choice_guessed ? '<span title="从网站读回的原文里认出来的">*</span>' : ''}` : ''}</span>
      <span class="flex-1 min-w-0 ${p.serial_state.startsWith('未通过') ? 'line-through text-slate-400' : ''}">${esc(p.job_title || '—')}${p.org ? ` <span class="text-slate-400">· ${esc(p.org)}</span>` : ''}</span>
      ${p.serial_state ? `<span class="chip ${p.serial_state === '在看' ? 'bg-teal-100 text-teal-800' : 'bg-slate-100 text-slate-500'}">${esc(p.serial_state)}</span>` : ''}
      ${apStage(p.status, p.stage_label)}</div>`).join('');
}
function renderApps() {
  const list = apFiltered();
  apViewToggle();
  if (AP.view !== 'app') return renderBoard();
  if (AP.err) {
    $('#recordCount').textContent = '';
    $('#appsTable').innerHTML = `<tr><td colspan="7" class="px-5 py-6 text-sm text-red-600">按申请看的数据没取到（${esc(AP.err)}）。可以先切到「按岗位看」。</td></tr>`;
    $('#appsCards').innerHTML = `<p class="text-sm text-red-600">按申请看的数据没取到（${esc(AP.err)}）。可以先切到「按岗位看」。</p>`;
    return;
  }
  $('#recordCount').textContent = `显示 ${list.length} / ${AP.apps.length} 次申请（同一家一起投的几个岗位算一次）`;
  $('#appsTable').innerHTML = list.map(c => `
    <tr class="ap-row hover:bg-white/70 cursor-pointer border-b border-slate-100" data-id="${esc(c.id)}">
      <td class="px-5 py-3"><div class="flex items-center gap-2">${apDot(c)}<span class="font-semibold">${esc(c.company || '—')}</span></div>
        <div class="text-[11px] text-slate-400 mt-0.5">${c.positions.length === 1 ? esc(c.positions[0].job_title) : apVolLine(c)}</div></td>
      <td class="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">${esc(c.channel)}</td>
      <td class="px-4 py-3">${apStage(c.stage, c.stage_label)}<div class="text-[11px] text-slate-400 mt-0.5">${esc(apCounts(c) || (c.sub || []).join(' · '))}</div></td>
      <td class="px-4 py-3"><div class="flex items-center gap-2">${turnChip(c)}${c.turn === '轮到你' || c.turn === '停了' ? apBtn(c) : ''}</div>
        ${c.todo && (c.button || {}).action !== 'step_done' ? `<div class="text-[11px] text-slate-500 mt-0.5 max-w-[18rem]">${esc(c.todo)}</div>` : ''}</td>
      <td class="px-4 py-3 text-xs max-w-[16rem]">${apNext(c) || '<span class="text-slate-300">—</span>'}</td>
      <td class="px-4 py-3 text-xs text-slate-500 max-w-[14rem]" title="${esc(c.last_text || '')}"><span class="text-slate-400 whitespace-nowrap">${esc((c.last_activity || '').slice(5, 16))}</span><div class="truncate">${esc(c.last_text || '')}</div></td>
      <td class="px-4 py-3 text-right text-xs text-slate-400 whitespace-nowrap" ${c.first_submitted_guess ? 'title="按岗位记录里最早的投递时间"' : ''}>${esc((c.first_submitted_at || '').slice(0, 16) || '—')}</td>
    </tr>
    <tr id="apx-${esc(c.id)}" class="row-detail"><td colspan="7" class="px-5 pb-3"><div class="rounded-xl bg-white border p-3">
      ${apPositions(c)}
      <div class="flex flex-wrap gap-2 mt-2 pt-2 border-t">
        <button class="ap-open px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs font-bold" data-id="${esc(c.id)}">这家的详情</button>
        ${c.site_progress ? `<span class="text-xs text-cyan-800 self-center">网站进度：${esc(c.site_progress)}</span>` : ''}</div></div></td></tr>`).join('')
    || '<tr><td colspan="7" class="px-5 py-6 text-sm text-slate-400">这个批次还没有申请。</td></tr>';
  $('#appsCards').innerHTML = list.map(c => `
    <div class="bg-white rounded-xl p-4 shadow-sm border" ${c.color_hex ? `style="border-left:5px solid ${esc(c.color_hex)}"` : ''}>
      <div class="flex items-start justify-between gap-2"><button class="ap-open text-left" data-id="${esc(c.id)}">
        <p class="font-semibold text-sm">${esc(c.company || '—')}</p><p class="text-xs text-slate-400">${c.positions.length === 1 ? esc(c.positions[0].job_title) : apVolLine(c)}</p></button>${turnChip(c)}</div>
      ${c.todo ? `<p class="text-xs text-slate-600 mt-2">${esc(c.todo)}</p>` : ''}
      <div class="flex items-center gap-2 mt-2 text-[11px] text-slate-400">${apStage(c.stage, c.stage_label)}${apNext(c)}<span class="ml-auto">${apBtn(c)}</span></div>
    </div>`).join('');
}

// ━━━ 单家详情（抽屉）━━━
async function apDetail(id, quiet = false) {
  AP.detailId = id;
  const box = $('#appDrawerBody');
  if (!quiet) { box.innerHTML = '<p class="text-sm text-slate-400 p-4">读取中…</p>'; $('#appDrawer').classList.add('open'); }
  let d;
  try { d = await api('/api/apps/' + encodeURIComponent(id)); }
  catch (e) { box.innerHTML = `<p class="text-sm text-red-600 p-4">${esc(e.message)}</p>`; return; }
  if (AP.detailId !== id) return;
  const c = d.card, rep = c.reply || {};
  $('#appDrawer').style.borderLeft = c.color_hex ? `6px solid ${c.color_hex}` : '';
  $('#appDrawerTitle').innerHTML = `${apDot(c)}<span class="font-bold truncate">${esc(c.company)}</span><span class="chip bg-slate-100 text-slate-600">${esc(c.channel)}</span>`;
  const kv = (k, v) => v ? `<div class="kv flex gap-2 py-0.5 text-sm"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>` : '';
  const entry = safeUrl(c.entry_url);
  const vol = d.positions.length > 1 && c.volunteer_mode ? `<p class="text-[11px] text-slate-500 mb-2">${c.volunteer_mode === '串行' ? '串行志愿：前一个志愿有结果才看下一个' : c.volunteer_mode === '平行' ? '平行：几个岗位同时看' : esc(c.volunteer_mode)}${c.volunteer_note ? `（网站原话：${esc(c.volunteer_note)}）` : ''}<span class="text-slate-400"> · 不对的话在口述框说一句「${esc(c.company)} 是${c.volunteer_mode === '串行' ? '平行' : '串行'}的」</span></p>` : '';
  const serial = c.volunteer_mode === '串行';
  const pos = d.positions.map((p, i) => `<div class="${serial ? 'relative pl-5' : ''} rounded-xl border p-3 ${p.serial_state === '在看' ? 'border-teal-400 bg-teal-50/40' : 'bg-white'}">
      ${serial && i < d.positions.length - 1 ? '<span class="absolute left-2 top-8 bottom-[-0.75rem] border-l-2 border-dashed border-slate-300"></span>' : ''}
      ${serial ? `<span class="absolute left-0.5 top-3 w-3 h-3 rounded-full ${p.serial_state === '在看' ? 'bg-teal-500' : 'bg-slate-300'}"></span>` : ''}
      <div class="flex items-center gap-2 flex-wrap"><span class="text-xs text-slate-400">${p.choice_no ? '志愿 ' + esc(p.choice_no) + (p.choice_guessed ? '*' : '') : ''}</span>
        <span class="font-semibold text-sm ${p.serial_state.startsWith('未通过') ? 'line-through text-slate-400' : ''}">${esc(p.job_title || '—')}</span>${stageSelect('ap-pos-stage', p.status, `data-rid="${esc(p.id)}" title="改阶段（往回改也可以，记进时间线）"`)}
        ${p.serial_state ? `<span class="chip ${p.serial_state === '在看' ? 'bg-teal-100 text-teal-800' : 'bg-slate-100 text-slate-500'}">${esc(p.serial_state)}</span>` : ''}</div>
      <div class="text-[11px] text-slate-400 mt-0.5">${[p.org, p.location, p.sent_at ? (p.status === '草稿' ? '建立 ' : '投出 ') + p.sent_at : ''].filter(Boolean).map(esc).join(' · ')}</div>
      ${p.site_status ? `<div class="text-xs text-cyan-800 mt-1">网站进度：${esc(p.site_status)}</div>` : ''}
      ${p.jd_text ? `<details class="mt-1 text-xs"><summary class="cursor-pointer text-slate-500">JD 原文</summary><div class="mt-1 bg-slate-50 rounded-lg p-2 max-h-60 overflow-y-auto">${escBr(p.jd_text)}</div></details>` : '<p class="text-[11px] text-slate-400 mt-1">JD：缺</p>'}
      ${(p.history || []).length ? `<details class="mt-1 text-xs"><summary class="cursor-pointer text-slate-500">这个岗位的改动（${p.history.length}）</summary><div class="mt-1 space-y-0.5">${p.history.slice().reverse().map(h => `<div><span class="text-slate-400">${esc((h.at || '').slice(5, 16))}</span> ${esc(h.text || '')}</div>`).join('')}</div></details>` : ''}
    </div>`).join('');
  const sugg = (c.suggestions || []).map(s => `<div class="flex flex-wrap items-center gap-2 py-1 text-sm"><span class="flex-1 min-w-[12rem]">${esc(s.text)}</span>
      <button class="ap-sugg px-2 py-1 rounded-lg bg-primary text-white text-xs font-bold" data-id="${esc(c.id)}" data-sid="${esc(s.id)}" data-action="accept">采纳</button>
      ${s.kind === '阶段' && (s.payload || {}).to ? `<span class="text-xs text-slate-400">认错了，改成</span>${stageSelect('ap-sugg-to', s.payload.to, `data-id="${esc(c.id)}" data-sid="${esc(s.id)}"`)}` : ''}
      <button class="ap-sugg px-2 py-1 rounded-lg bg-slate-100 text-xs" data-id="${esc(c.id)}" data-sid="${esc(s.id)}" data-action="dismiss">不用</button></div>`).join('');
  const nxt = c.next || {};
  $('#appDrawerBody').innerHTML = `<div class="p-4 space-y-4">
    <div class="rounded-xl bg-slate-50 p-3">
      <div class="flex items-center gap-2 flex-wrap">${turnChip(c)}<span class="text-sm">${esc(c.todo || '')}</span><span class="ml-auto">${apBtn(c)}</span></div>
      <div class="mt-2">${kv('阶段', esc(c.stage_label) + ((c.sub || []).length ? ` <span class="text-slate-400">· ${esc(c.sub.join(' · '))}</span>` : ''))}
        ${kv('下一步', (nxt.text ? apNext(c) + (!nxt.done ? ` <button class="ap-step text-xs text-primary hover:underline" data-id="${esc(c.id)}">做完了</button>`
          : ` <button class="ap-step-undo text-xs text-slate-500 hover:underline" data-id="${esc(c.id)}">点错了，还没做完</button>`) : '<span class="text-slate-400">没有</span>')
          + ` <button class="ap-step-edit text-xs text-slate-500 hover:underline">改</button>
          <span class="ap-step-form hidden"><input class="ap-step-text field !w-48 !py-1 text-xs" value="${esc(nxt.done ? '' : nxt.text || '')}" placeholder="比如：二面（线上）"/>
          <input class="ap-step-due field !w-36 !py-1 text-xs" type="date" value="${esc((nxt.done ? '' : nxt.due || '').slice(0, 10))}"/>
          <button class="ap-step-save text-xs font-bold text-primary" data-id="${esc(c.id)}">保存</button>
          <button class="ap-step-clear text-xs text-slate-500" data-id="${esc(c.id)}">清掉</button></span>`)}
        ${kv('首次投出', esc(c.first_submitted_at) + (c.first_submitted_guess ? ' <span class="text-slate-400 text-xs">（按岗位记录）</span>' : ''))}${kv('申请账号', esc(c.account))}${kv('网站进度', esc(c.site_progress))}
        ${kv('入口', entry ? `<a class="text-primary hover:underline break-all" target="_blank" rel="noopener" href="${esc(entry)}">${esc(entry)}</a>` : '')}</div></div>
    ${rep.status ? `<div class="rounded-xl p-3 text-xs ${rep.status === '退信' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-800'}"><b>${esc(rep.status)}</b> ${esc(rep.at || '')} ${esc(rep.from || '')}
      ${rep.subject ? `<div class="mt-1">${esc(rep.subject)}</div>` : ''}<div class="mt-1">${esc(rep.snippet || '')}</div>
      ${rep.handled ? '' : `<button class="ap-reply mt-2 px-2 py-1 rounded-lg bg-white border text-xs font-bold" data-id="${esc(c.id)}">看过了</button>`}</div>` : ''}
    ${sugg ? `<div><h4 class="text-[11px] font-bold text-amber-700 mb-1">要你确认的（自动识别的，点了才改）</h4>${sugg}</div>` : ''}
    <div><h4 class="text-[11px] font-bold text-slate-400 mb-2">${d.positions.length > 1 ? `岗位 / 志愿（${d.positions.length}）` : '岗位'}</h4>${vol}<div class="space-y-3">${pos || '<p class="text-xs text-slate-400">还没有岗位</p>'}</div></div>
    ${c.chat_id ? `<button class="ap-chat px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs font-bold" data-chat="${esc(c.chat_id)}">看助手对话</button>` : ''}
    ${d.snapshots.length ? `<div><h4 class="text-[11px] font-bold text-slate-400 mb-1">从网站读回的原文</h4>${d.snapshots.slice().reverse().map(s => `<details class="text-xs mb-1"><summary class="cursor-pointer text-teal-700">${esc(s.kind || '读回')} · ${esc((s.at || '').slice(0, 16))}</summary>
      <div class="mt-1 bg-teal-50/50 rounded-lg p-2 whitespace-pre-wrap max-h-80 overflow-y-auto">${esc(s.text)}</div></details>`).join('')}</div>` : ''}
    <div><h4 class="text-[11px] font-bold text-slate-400 mb-1">时间线</h4><div class="space-y-1">${d.timeline.map(e => `<div class="text-xs flex gap-2"><span class="text-slate-400 shrink-0 w-20">${esc((e.at || '').slice(5, 16))}</span>
      <span class="chip bg-slate-100 text-slate-500 shrink-0">${esc(e.kind || '')}</span><span class="flex-1 min-w-0">${esc(e.text || '')}</span><span class="text-slate-400 shrink-0">${esc(e.by || '')}</span></div>`).join('') || '<p class="text-xs text-slate-400">还没有</p>'}</div></div>
  </div>`;
}

// ━━━ 按钮 ━━━
async function apRun(id) {   // 让助手填 / 接着做 / 我弄完了：都是叫这家的助手接着做（申请 id 就是网申待办 id），打开对话框
  if (AP.inflight.has(id)) return;
  AP.inflight.add(id);
  try {
    const d = await api(`/api/wstasks/${encodeURIComponent(id)}/agent`, {method: 'POST'});
    AG.id = d.id; AG.n = 0; $('#agentMsgs').innerHTML = '';
    agAppend(d.messages); AG.n = d.total; agSetRunning(!d.queued, d.queued);
    if (d.queued) toast('排队中：' + d.queued, 5000);
    await agShow(true);
  } catch (e) { toast(e.message, 6000); }
  finally { AP.inflight.delete(id); loadApps(); }
}
async function apAction(id, action) {
  const post = (url, body) => api(url, {method: 'POST', body: body || {}});
  try {
    if (action === 'step_done') {   // 带上看到的那一步：期间被来信换掉了就不标，提示刷新
      const c = AP.apps.find(x => x.id === id) || (AP.today ? [...AP.today.your_turn, ...AP.today.soon].find(x => x.id === id) : null);
      await post(`/api/apps/${encodeURIComponent(id)}/step-done`, c && c.next ? {text: c.next.text} : {});
      toast('记下了：做完了', 2000); return loadBoard();
    }
    const card = AP.apps.find(x => x.id === id) || {};
    if (['need_done', 'continue', 'run'].includes(action) && !(action === 'continue' && card.web_phase === '已提交')) return apRun(id);
    if (action === 'login_done' || action === 'readback' || action === 'continue') {   // 已提交的停在读回上：再去读回（只看不改），不是「接着填」   // 读回要先登录 / 读回不完整：叫助手再去网站读一次
      const d = await api(`/api/wstasks/${encodeURIComponent(id)}/readback`, {method: 'POST'});
      toast(d.message, 6000);
      if (d.chat) { AG.id = d.chat.id; AG.n = 0; $('#agentMsgs').innerHTML = ''; agAppend(d.chat.messages); AG.n = d.chat.total; agSetRunning(!d.chat.queued, d.chat.queued); await agShow(true); }
      return loadBoard();
    }
    if (action === 'submitted') {
      const d = await api(`/api/wstasks/${encodeURIComponent(id)}`, {method: 'PUT', body: {status: '已提交'}});
      toast(d.readback ? '记下了：已提交。助手去网站把实际提交的内容读回来' : '记下了：已提交', 5000);
      return loadBoard();
    }
    if (action === 'open_mail' || action === 'retry') return switchTab('new');
    if (['answer', 'choose'].includes(action)) return switchTab('kit');
    return apDetail(id);   // review、open_reply：打开这家的详情
  } catch (e) { toast(e.message, 6000); }
}

document.addEventListener('click', async e => {
  const t = e.target;
  const act = t.closest('.ap-act');
  if (act) { e.stopPropagation(); setBusy(act, true, ''); await apAction(act.dataset.id, act.dataset.action); setBusy(act, false); return; }
  const open = t.closest('.ap-open');
  if (open) { e.stopPropagation(); return apDetail(open.dataset.id); }
  const row = t.closest('tr.ap-row');
  if (row) return $('#apx-' + CSS.escape(row.dataset.id))?.classList.toggle('open');
  if (t.closest('.ap-view')) { AP.view = t.closest('.ap-view').dataset.view; try { localStorage.setItem('boardView', AP.view === 'record' ? 'record' : 'app'); } catch (er) {} return renderApps(); }
  if (t.closest('.ap-go-mail')) return switchTab('new');
  if (t.closest('#appDrawerClose')) { $('#appDrawer').classList.remove('open'); AP.detailId = null; return; }
  const step = t.closest('.ap-step');
  if (step) { await apAction(step.dataset.id, 'step_done'); return; }
  const su = t.closest('.ap-step-undo');
  if (su) { try { await api(`/api/apps/${encodeURIComponent(su.dataset.id)}/step-undo`, {method: 'POST'}); toast('改回没做完了', 2000); } catch (er) { toast(er.message, 6000); } return loadBoard(); }
  const sg = t.closest('.ap-sugg');
  if (sg) {
    try { await api(`/api/apps/${encodeURIComponent(sg.dataset.id)}/suggestions/${encodeURIComponent(sg.dataset.sid)}`, {method: 'POST', body: {action: sg.dataset.action}}); toast(sg.dataset.action === 'accept' ? '已采纳' : '好的，不用', 2000); }
    catch (er) { toast(er.message, 6000); }
    return loadBoard();
  }
  if (t.closest('.ap-step-edit')) { const f = t.closest('.kv').querySelector('.ap-step-form'); f.classList.toggle('hidden'); f.querySelector('input').focus(); return; }
  const sv = t.closest('.ap-step-save') || t.closest('.ap-step-clear');
  if (sv) {
    const f = sv.closest('.ap-step-form'), clear = sv.classList.contains('ap-step-clear');
    const body = clear ? {text: '', due: ''} : {text: f.querySelector('.ap-step-text').value.trim(), due: f.querySelector('.ap-step-due').value};
    if (!clear && !body.text) return toast('下一步写点什么（比如「二面」）；不要了就点「清掉」', 3000);
    try { await api(`/api/apps/${encodeURIComponent(sv.dataset.id)}/next-step`, {method: 'POST', body}); toast(clear ? '下一步清掉了' : '下一步改好了', 2000); }
    catch (er) { toast(er.message, 6000); }
    return loadBoard();
  }
  const rp = t.closest('.ap-reply');
  if (rp) { try { await api(`/api/apps/${encodeURIComponent(rp.dataset.id)}/reply-handled`, {method: 'POST'}); } catch (er) { toast(er.message); } return loadBoard(); }
  const ch = t.closest('.ap-chat');
  if (ch) { await agShow(true); $('#agentChats').value = ch.dataset.chat; return agOpenChat(ch.dataset.chat); }
  const pick = t.closest('.prog-pick');
  if (pick && AP.lastProg) {
    if (AP.picking) return;   // 连点两下：只记一次
    AP.picking = true;
    document.querySelectorAll('.prog-pick').forEach(b => { b.disabled = true; b.classList.add('opacity-50'); });
    const i = +pick.dataset.i, a = AP.lastProg.ask[i];
    try { await progSend(AP.lastProg.text, [{...a, app_id: pick.dataset.app}], AP.lastProg.ask.filter((_, j) => j !== i)); }
    finally { AP.picking = false; }
    return;
  }
  const un = t.closest('.prog-undo');
  if (un) {
    try {
      const d = await api(`/api/progress/${encodeURIComponent(un.dataset.id)}/undo`, {method: 'POST'});
      toast(d.undone ? `撤销了 ${d.undone} 处改动` : '这一句已经撤销过了', 3000);
      if (AP.lastProg && AP.lastProg.id === un.dataset.id) $('#progResult').innerHTML = '';   // 撤的是上面显示的那句才清掉（还没点的候选留着）
    }
    catch (er) { toast(er.message, 6000); }
    return loadBoard();
  }
});
document.addEventListener('change', async e => {
  const ps = e.target.closest('.ap-pos-stage');
  if (ps) {   // 详情里改某个岗位的阶段（本人手动改，往回也可以；记进这家的时间线）
    try { await api(`/api/records/${encodeURIComponent(ps.dataset.rid)}`, {method: 'PUT', body: {status: ps.value}}); toast('阶段改好了', 1500); }
    catch (er) { toast(er.message, 6000); }
    return loadBoard();
  }
  const st = e.target.closest('.ap-sugg-to');
  if (st) {   // 建议认错了：按本人选的阶段采纳
    try { await api(`/api/apps/${encodeURIComponent(st.dataset.id)}/suggestions/${encodeURIComponent(st.dataset.sid)}`, {method: 'POST', body: {action: 'accept', to: st.value}}); toast('按你选的改好了', 2000); }
    catch (er) { toast(er.message, 6000); }
    return loadBoard();
  }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('#appDrawer').classList.contains('open')) { $('#appDrawer').classList.remove('open'); AP.detailId = null; } });
$('#progSend').addEventListener('click', () => { const x = $('#progText').value.trim(); if (x) progSend(x); });
$('#progText').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); $('#progSend').click(); } });
['#fStatus'].forEach(s => $(s).addEventListener('change', renderApps));
$('#fSearch').addEventListener('input', renderApps);
function apHash() {   // Excel 里的「面板链接」（…/#app/<申请id>）：打开看板和这家的详情
  const m = (location.hash || '').match(/^#app\/([\w-]+)$/);
  if (!m) return;
  switchTab('board');
  apDetail(m[1]);
}
window.addEventListener('hashchange', apHash);
document.addEventListener('DOMContentLoaded', () => setTimeout(apHash, 0));
