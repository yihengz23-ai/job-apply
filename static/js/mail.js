// 「投递」页（#tab-new）：贴链接 → 后台写 → 打开一封审 → 发出 / 定时 / 存草稿 / 只记一笔；还有隐藏的旧网申资料包（#wsCard）。
// ━━━ 页面上下文：防止「请求回来时已经换了内容」━━━
// S.gen：「投递」页编辑区现在装的是哪一份（打开 / 换下一条 / 关闭时 +1）。
//        分析、重写、生成问答、检查这些异步请求回来时先对一下，对不上就丢掉结果，绝不落到别的岗位上。
// S.lock：正在发送 / 记录的那几秒里，不许换条目、关闭、重写。
function busyGuard() {
  if (S.lock) { toast(`正在${S.lock}，等几秒它完成再操作`, 2500); return true; }
  return false;
}
const isDone = () => S.sent || S.wsRecorded;
const reviewingLive = () => !!S.queueId && !isDone();   // 队列里正在审核、还没发 / 记的条目：改动要存回队列
// 「邮箱+网申」邮件已发、网申还没补记：问答的改动也要存回队列（只存问答）
const kitEditable = () => reviewingLive() || (!!S.queueId && S.sent && !S.wsRecorded && !!S.result && S.result.apply_channel === '邮箱+网申');

function newContext() {
  S.gen++;
  clearTimeout(checkTimer); checkTimer = null;
  clearTimeout(kitSaveTimer); kitSaveTimer = null;
  S.editsDirty = false; S.userEdited = false; S.uncertain = false; S.regenWaiting = false; S.regenRev = '';
  const g = $('#wsGenBtn'); g._busyHtml = null; g.disabled = false;
  g.innerHTML = '<span class="material-symbols-outlined text-sm">auto_awesome</span>生成问答';
}

// 关掉编辑区（点「关闭这封」/ 没有下一条 / 条目在别处被处理掉）：一起清空，不留一封能脱离队列发出去的旧邮件
function leaveReview() {
  exitReview();
  newContext(); clearResultView(); clearInputs();
  S.page = null; S.pageJd = '';
  showDesk(false); setListOpen(true); renderEmptyDesk();
}

// 编辑区的输入清空：JD、目标岗位、给 AI 的要求
function clearInputs() {
  ['#jdInput', '#targetJob', '#extraInput'].forEach(s => $(s).value = '');
}

// 清掉上一份的分析结果和邮件（JD、链接、多岗位选择保留）
function clearResultView() {
  ['#emailTo', '#emailCc', '#emailSub', '#emailBody', '#resumeName', '#resumeNameEn', '#reportName'].forEach(s => $(s).value = '');
  ['#infoCards', '#rulesCard', '#issuesCard', '#analyzeStatus', '#wsCard', '#wsMini', '#wsOnlyNote'].forEach(s => $(s).classList.add('hidden'));
  $('#applyUrlBtn').classList.add('hidden'); $('#applyUrlBtn').classList.remove('flex');
  $('#recordBtn').classList.remove('hidden');
  resetActionButtons(false);
  S.analysis = null; S.result = null; S.fixes = []; S.sent = false; S.lastIssues = [];
  S.kit = null; S.sentRecordId = ''; S.wsRecorded = false; S.wsOpened = null; S.scheduled = false;
  $('#metaLine').textContent = '发送 = 直接发出；存为草稿 = 放进 Gmail 草稿箱，可在 Gmail 里改完或定时发送';
}

// 按当前状态决定几个按钮能不能点（「已发送」这类文字由 markDone 负责）
function refreshActionButtons() {
  const has = !!S.result, busy = !!S.lock || !!S.analyzeBusy || S.regenWaiting, noTo = !listArr($('#emailTo').value).length;
  ['sendBtn', 'draftBtn'].forEach(id => { const b = $('#' + id); if (b._busyHtml == null) b.disabled = !has || isDone() || busy || noTo; });
  ['recordBtn', 'regenBtn'].forEach(id => { const b = $('#' + id); if (b._busyHtml == null) b.disabled = !has || isDone() || busy; });
  renderWsRecordButton();
}
$('#emailTo').addEventListener('input', () => refreshActionButtons());

// ━━━ 重写当前这封 ━━━
// 招聘信息来源：只进自己的投递记录（抓链接时的公众号名 / 网站；粘贴的文字由 AI 从内容里认），邮件里不写
const pageSource = () => S.page ? (S.page.source_label || '') : '';
function payloadBase() {
  return {
    jd_text: $('#jdInput').value,
    source_label: pageSource(),
    target_job: $('#targetJob').value.trim(),
    source_url: S.page ? S.page.url : '',
    publish_date: S.page ? (S.page.publish_date || '') : '',
    queue_id: S.queueId || '',
  };
}

// 按补充要求重写：交给后台做，写好存回这一封（JD、几个指定、补充要求都按编辑区现在的来）。
// 不用在这里等：可以先点「跳过，看下一个」；停在这封上的话，写好会自动刷新成新内容
async function regenerate() {
  if (busyGuard()) return;
  if (S.analyzeBusy || S.regenWaiting) return toast('正在重写，写好会自动刷新', 2500);
  if (!S.result || isDone() || !S.queueId) { toast('这封已经发出 / 记录过了，不能再重写'); return; }
  if ($('#jdInput').value.trim().length < 50) { toast('JD 太短（至少 50 字）'); return; }
  const qid = S.queueId, gen = S.gen, btn = $('#regenBtn');
  S.analyzeBusy = true;   // 第一个 await 之前就占住，双击也只交一次
  setBusy(btn, true, '提交中…');
  try {
    const d = await api(`/api/queue/${encodeURIComponent(qid)}/regen`, {method: 'POST', body: {
      jd_text: $('#jdInput').value, target_job: $('#targetJob').value.trim(),
      position_hint: ($('#posSelect') || {}).value || '', resume_hint: $('#resumeVersion').value,
      report_hint: $('#chkReport').checked ? '附上' : '不附',
      extra: $('#extraInput').value.trim()}});
    if (!d.ok) { toast('这封现在不能重写（可能正在发送或已经处理过了）', 4000); return; }
    S.regenWatch.add(qid);
    if (gen === S.gen) {
      S.regenWaiting = true; S.regenRev = S.queueRev; S.editsDirty = false;
      setBusy(btn, false); setBusy(btn, true, '重写中…');
    }
    toast('在后台重写了（约 15 秒）：可以先看下一封，写好会存回这一封', 4000);
  } catch (e) { toast('重写失败：' + e.message, 6000); }
  finally {
    S.analyzeBusy = false;
    if (!S.regenWaiting || gen !== S.gen) setBusy(btn, false);
    if (gen === S.gen) refreshActionButtons();
    loadQueue(true);
  }
}
$('#regenBtn').onclick = regenerate;

// 其他附件（文章、作品、成绩单……）：自己加，发信时一起带上
function renderExtras() {
  const list = (S.result && S.result.extra_attachments) || [];
  $('#extraList').innerHTML = list.map((a, i) => `<div class="flex items-center gap-2"><span class="material-symbols-outlined text-sm text-slate-500">description</span>
    <span class="flex-1 truncate">${esc(a.name)}</span><button class="x-del text-slate-400 hover:text-red-600" data-i="${i}" title="不带这个附件">
    <span class="material-symbols-outlined text-base">close</span></button></div>`).join('') || '<p class="text-[11px] text-slate-400">没有</p>';
}
$('#extraList').addEventListener('click', e => {
  const b = e.target.closest('.x-del');
  if (!b || !S.result || isDone()) return;
  S.result.extra_attachments = (S.result.extra_attachments || []).filter((_, i) => i !== +b.dataset.i);
  renderExtras(); onUserEdit();
});
$('#extraFile').addEventListener('change', async e => {
  const f = e.target.files[0]; e.target.value = '';
  if (!f) return;
  if (!S.result || isDone()) return toast('先打开一封还没发的邮件再加附件');
  const gen = S.gen, fd = new FormData(); fd.append('file', f);
  try {
    const resp = await fetch('/api/attachment', {method: 'POST', body: fd, headers: {'X-Requested-With': 'jobapply'}});
    const d = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(d.error || `HTTP ${resp.status}`);
    if (gen !== S.gen) return toast('附件没加上：期间换了别的邮件', 4000);
    S.result.extra_attachments = [...(S.result.extra_attachments || []), {name: d.name, path: d.path}];
    renderExtras(); onUserEdit(); toast('已加上附件：' + d.name, 2500);
  } catch (err) { toast('加附件失败：' + err.message, 6000); }
});

function fillEmail(r) {
  $('#emailTo').value = listStr(r.to_emails);
  $('#emailCc').value = listStr(r.cc_emails);
  $('#wsOnlyNote').classList.toggle('hidden', listArr(r.to_emails).length > 0);
  $('#emailSub').value = r.email_subject || '';
  $('#emailBody').value = r.email_body || '';
  $('#resumeVersion').value = r.resume_version || '中文';
  $('#resumeName').value = r.resume_filename || '';
  $('#resumeNameEn').value = r.resume_filename_en || '';
  $('#chkResume').checked = r.attach_resume !== false;
  $('#chkReport').checked = !!r.attach_report;
  $('#reportName').value = r.report_filename || S.cfg.report_default_name;
  updateResumeControls(); renderExtras();
  autoGrow($('#emailSub')); autoGrow($('#emailBody'));
  const url = safeUrl(r.apply_url);
  const a = $('#applyUrlBtn');
  if (url) { a.href = url; a.classList.remove('hidden'); a.classList.add('flex'); }
  else { a.classList.add('hidden'); a.classList.remove('flex'); }
}
$('#applyUrlBtn').addEventListener('click', () => noteWsOpened());

function autoGrow(el) { el.style.height = 'auto'; el.style.height = (el.scrollHeight + 2) + 'px'; }
document.querySelectorAll('.grow-y').forEach(el => el.addEventListener('input', () => autoGrow(el)));
let resizeTimer = null;
window.addEventListener('resize', () => {  // 窗口变窄 / 变宽后，文字框高度跟着重算
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => document.querySelectorAll('.grow-y, #wsAnswers textarea').forEach(autoGrow), 150);
});
$('#emailSub').addEventListener('keydown', e => { if (e.key === 'Enter') e.preventDefault(); });
$('#emailSub').addEventListener('input', e => { if (/\n/.test(e.target.value)) e.target.value = e.target.value.replace(/\n/g, ' '); });

function updateResumeControls() {
  const v = $('#resumeVersion').value || '中文';
  $('#resumeNameEn').classList.toggle('hidden', v !== '中英两份');
  $('#previewResume').href = '/api/resume-preview?version=' + encodeURIComponent(v);
}
$('#resumeVersion').onchange = () => {
  const v = $('#resumeVersion').value;
  if (v === '英文' && $('#resumeName').value === S.cfg.resume_default_zh) $('#resumeName').value = S.cfg.resume_default_en;
  if (v !== '英文' && $('#resumeName').value === S.cfg.resume_default_en) $('#resumeName').value = S.cfg.resume_default_zh;
  if (v === '中英两份' && !$('#resumeNameEn').value) $('#resumeNameEn').value = S.cfg.resume_default_en;
  updateResumeControls(); onUserEdit();
};

function collectResult() {
  const r = {...(S.result || {})};
  r.to_emails = $('#emailTo').value; r.cc_emails = $('#emailCc').value;
  r.email_subject = $('#emailSub').value; r.email_body = $('#emailBody').value;
  r.resume_version = $('#resumeVersion').value; r.resume_filename = $('#resumeName').value; r.resume_filename_en = $('#resumeNameEn').value;
  r.attach_resume = $('#chkResume').checked; r.attach_report = $('#chkReport').checked; r.report_filename = $('#reportName').value;
  const pt = $('#posSelect'); if (pt) r.position_type = pt.value;
  return r;
}

function card(label, value, cls = '') {
  return `<div class="bg-white p-3 rounded-2xl shadow-sm border flex flex-col gap-1 ${cls}"><span class="text-[10px] text-slate-400 font-bold">${esc(label)}</span><span class="text-sm font-bold truncate" title="${esc(value)}">${esc(value || '—')}</span></div>`;
}

function renderInfo(r) {
  const posSel = `<div class="bg-indigo-50 p-3 rounded-2xl shadow-sm border border-indigo-200 flex flex-col gap-1"><span class="text-[10px] text-indigo-600 font-bold">岗位类型（可改）</span>
    <select id="posSelect" class="text-sm font-bold bg-transparent border-none p-0 focus:ring-0 text-indigo-700">${S.cfg.position_types.map(p => opt(p, p, r.position_type)).join('')}</select></div>`;
  const dates = [r.deadline ? '截止 ' + r.deadline : '', r.job_post_date ? '发布 ' + r.job_post_date : ''].filter(Boolean).join(' / ');
  $('#infoCards').innerHTML = card('机构', r.company_name) + card('岗位', r.job_title) + posSel + card('地点', r.job_location) +
    card('机构性质', r.company_type) + card('行业', r.focus_industry) + card('投递方式', r.apply_channel) + card('日期', dates || '—');
  $('#infoCards').classList.remove('hidden');
  $('#posSelect').onchange = onUserEdit;
}

function renderRules(r) {
  const rules = r.jd_rules || {};
  const row = (k, v) => v && (Array.isArray(v) ? v.length : true) ?
    `<div class="flex gap-3"><span class="text-xs text-slate-400 w-20 shrink-0 pt-0.5">${esc(k)}</span><span class="flex-1">${Array.isArray(v) ? v.map(esc).join('；') : esc(v)}</span></div>` : '';
  const html = row('标题格式', rules.subject_format) + row('简历命名', rules.resume_filename_format) + row('样本命名', rules.report_filename_format) +
    row('正文须写', rules.body_requirements) + row('需附材料', rules.requested_materials) + row('联系人', r.contact_in_jd) +
    row('网申链接', r.apply_url) + row('JD 语言', r.jd_language);
  $('#rulesBody').innerHTML = html || '<p class="text-slate-400 text-xs">JD 没有特别的格式要求，用的是默认写法。</p>';
  $('#rulesCard').classList.remove('hidden');
}

function renderIssues(issues) {
  const lv = {error: ['必须处理', 'bg-red-50 text-red-700', 'error'], warn: ['注意', 'bg-amber-50 text-amber-800', 'warning'], info: ['提示', 'bg-slate-50 text-slate-600', 'info']};
  const sorted = [...issues].sort((a, b) => ['error', 'warn', 'info'].indexOf(a.level) - ['error', 'warn', 'info'].indexOf(b.level));
  let html = sorted.map(i => `<div class="flex gap-2 items-start p-2 rounded-lg ${lv[i.level][1]}"><span class="material-symbols-outlined text-base">${lv[i.level][2]}</span><span><b class="text-xs">${lv[i.level][0]}</b> ${esc(i.msg)}</span></div>`).join('');
  html += S.fixes.map(f => `<div class="flex gap-2 items-start p-2 rounded-lg bg-green-50 text-green-700"><span class="material-symbols-outlined text-base">auto_fix_high</span><span><b class="text-xs">已自动修正</b> ${esc(f)}</span></div>`).join('');
  const n = issues.filter(i => i.level === 'error').length, w = issues.filter(i => i.level === 'warn').length;
  $('#issuesSummary').textContent = n ? `${n} 个必须处理，${w} 个注意` : (w ? `${w} 个需要留意` : '全部通过');
  $('#issuesBody').innerHTML = html || '<p class="text-green-700 text-sm">✓ 没发现问题</p>';
  $('#issuesCard').classList.remove('hidden');
  S.lastIssues = issues;
}

// ━━━ 实时检查 + 队列条目自动保存 ━━━
let checkTimer = null;
function onUserEdit() { if (!S.result || isDone() || S.regenWaiting) return; S.userEdited = true; S.editsDirty = true; scheduleCheck(); }
function scheduleCheck() {
  if (!S.result || isDone()) return;
  clearTimeout(checkTimer);
  const gen = S.gen;
  checkTimer = setTimeout(async () => {
    checkTimer = null;
    if (gen !== S.gen || isDone()) return;
    const result = collectResult();
    if (reviewingLive() && S.editsDirty) { S.editsDirty = false; saveQueueEdits({edited: result}); }  // 先把内容存上，不等检查结果
    try {
      const d = await api('/api/check', {method: 'POST', body: {result, jd_text: $('#jdInput').value, source_label: pageSource(),
        publish_date: S.page ? (S.page.publish_date || '') : ''}});
      if (gen !== S.gen) return;
      renderIssues(d.issues);
      if (reviewingLive()) saveQueueEdits({issues: d.issues});
    } catch (e) {}
  }, 700);
}
function saveQueueEdits(body, keepalive = false) {
  const qid = S.queueId, gen = S.gen;
  if (!qid) return Promise.resolve();
  return api('/api/queue/' + encodeURIComponent(qid), {method: 'PUT', body: {...body, rev: S.queueRev || undefined}, keepalive})
    .then(d => {
      if (d && d.ok === false && gen === S.gen && !isDone() && !S.lock)
        toast('这条在别处发送或重做过了，刚才的修改没存上。回队列刷新看看。', 6000);
    })
    .catch(() => {});
}
// 换条目 / 离开页面前：还没来得及存的修改立刻存掉（邮件 + 网申问答）
async function flushQueueEdits(keepalive = false) {
  const jobs = [];
  if (checkTimer) { clearTimeout(checkTimer); checkTimer = null; }
  if (reviewingLive() && S.editsDirty && S.result) { S.editsDirty = false; jobs.push(saveQueueEdits({edited: collectResult()}, keepalive)); }
  if (kitSaveTimer) {
    clearTimeout(kitSaveTimer); kitSaveTimer = null;
    if (kitEditable() && S.kit) jobs.push(saveQueueEdits({wangshen: S.kit}, keepalive));
  }
  await Promise.all(jobs);
}
['#emailTo', '#emailCc', '#emailSub', '#emailBody', '#resumeName', '#resumeNameEn', '#reportName'].forEach(s => $(s).addEventListener('input', onUserEdit));
['#chkResume', '#chkReport'].forEach(s => $(s).addEventListener('change', onUserEdit));

// ━━━ 发送 / 草稿 ━━━
const openGmail = box => window.open(`https://mail.google.com/mail/u/0/#${box}`, '_blank', 'noopener');

async function deliver(mode) {
  if (!S.result || isDone() || busyGuard()) return;
  if (S.analyzeBusy || S.regenWaiting) return toast('正在重写，等新内容出来、看过再发', 3000);
  const gen = S.gen;
  S.lock = mode === 'draft' ? '存草稿' : '发送';
  refreshActionButtons();
  try {
    await doDeliver(mode, gen);
  } finally {
    S.lock = null;
    if (gen === S.gen) refreshActionButtons();
  }
}

async function doDeliver(mode, gen) {
  const qid = S.queueId;
  const stale = () => gen !== S.gen || isDone();
  let r = collectResult();
  if (!listArr(r.to_emails).length) return toast('收件人为空：这家只能网申，去「网申」页让助手填', 6000);
  const it = qid && S.queue.find(x => x.id === qid);
  if (S.uncertain || (it && it.send_uncertain)) {  // 上次发送结果不确定：先去 Gmail 确认
    const msg = S.uncertain ? '上次点发送时结果不确定，邮件可能已经发出了。' : it.error;
    const ans = await modal({title: '先确认一下', html: `<p class="text-red-700">${esc(msg)}</p><p class="text-xs mt-2">请先到 Gmail「已发送」里看看有没有这封，确认没有再继续，免得给 HR 发两封。</p>`,
      buttons: [{label: '取消', value: false}, {label: '去 Gmail 看看', value: 'gmail'}, {label: '确认没发出，继续', value: true, kind: 'danger'}]});
    if (ans === 'gmail') openGmail('sent');
    if (ans !== true || stale()) return;
  }
  let rev;
  try { rev = await api('/api/check', {method: 'POST', body: {result: r, jd_text: $('#jdInput').value, source_label: pageSource(),
    publish_date: S.page ? (S.page.publish_date || '') : ''}}); }
  catch (e) { return toast('检查失败：' + e.message); }
  if (stale()) return;
  renderIssues(rev.issues);
  const errors = rev.issues.filter(i => i.level === 'error');
  const warns = rev.issues.filter(i => i.level === 'warn');
  const verb = mode === 'draft' ? '存为草稿' : '发送';
  const att = [r.attach_resume ? `简历（${r.resume_version}）：${r.resume_filename}${r.resume_version === '中英两份' ? ' + ' + r.resume_filename_en : ''}` : '', r.attach_report ? '研究样本：' + r.report_filename : '',
    ...(r.extra_attachments || []).map(a => '其他：' + a.name)].filter(Boolean);
  const summary = `<div class="p-3 bg-slate-50 rounded-xl space-y-1 text-xs"><div><b>收件人：</b>${esc(listStr(r.to_emails))}${listArr(r.cc_emails).length ? ' ｜ <b>抄送：</b>' + esc(listStr(r.cc_emails)) : ''}</div><div><b>标题：</b>${esc(r.email_subject)}</div><div><b>附件：</b>${esc(att.join('；') || '无')}</div></div>`;
  // 晚上点「发送」：默认排到明早 10:00 由面板自己发（现在发，HR 早上会被一堆新邮件压住）
  const night = mode === 'send' && isNight();
  const nightNote = night ? `<p class="text-xs text-indigo-700">现在是北京时间晚上：选「定在${whenLabel()} 发出」，到点面板自己发出（面板别关、电脑别合盖）。</p>` : '';
  let force = false, choice;
  if (errors.length) {
    choice = await modal({title: '还有必须处理的问题', html: errors.map(i => `<div class="p-2 bg-red-50 text-red-700 rounded-lg">${esc(i.msg)}</div>`).join('') + summary + nightNote,
      buttons: night ? [{label: '返回修改', value: false}, {label: '我确认没问题，现在就发', value: 'now', kind: 'danger'}, {label: `我确认没问题，定在${whenLabel()} 发出`, value: 'schedule', kind: 'primary'}]
        : [{label: '返回修改', value: false}, {label: '我确认没问题，仍然' + verb, value: 'now', kind: 'danger'}]});
    if (!choice) return; force = true;
  } else {
    choice = await modal({title: night ? `现在发，还是定在${whenLabel()} 发？` : `确认${verb}？`, html: summary + (warns.length ? `<p class="text-amber-700 text-xs">还有 ${warns.length} 条「注意」，已在左侧列出。</p>` : '') + nightNote,
      buttons: night ? [{label: '取消', value: false}, {label: '现在就发', value: 'now'}, {label: `定在${whenLabel()} 发出`, value: 'schedule', kind: 'primary'}]
        : [{label: '取消', value: false}, {label: verb, value: 'now', kind: 'primary'}]});
    if (!choice) return;
  }
  if (stale()) return;
  if (choice === 'schedule') return doSchedule(r, force, qid, gen);
  // 发出的就是上面检查过、确认框里给你看过的那封（r），不再重新读表单
  const btn = mode === 'draft' ? $('#draftBtn') : $('#sendBtn');
  setBusy(btn, true, mode === 'draft' ? '存草稿中…' : '发送中…');
  clearTimeout(checkTimer); checkTimer = null;
  try {
    const d = await api('/api/send', {method: 'POST', body: {...payloadBase(), result: r, mode, force}});
    setBusy(btn, false);
    if (gen !== S.gen) { toast(`${d.mode === '草稿' ? '已存草稿' : '已发送'}：${r.company_name || ''}（已记入看板）`, 6000); return; }
    S.sent = true; S.sentRecordId = d.record_id || ''; S.uncertain = false; S.editsDirty = false;
    markDone(btn, mode === 'draft' ? '已存草稿' : '已发送');
    toast(d.record_error ? d.record_error : `${d.mode === '草稿' ? '已存入 Gmail 草稿箱' : '已发送'}，并记入看板（附件：${d.attachments.join('、')}）`, 7000);
    if (d.mode !== '草稿' && d.record_id) setTimeout(() => checkBounce(d.record_id, r.company_name), 90000);
    renderKit();
    if (S.result && S.result.apply_channel === '邮箱+网申') toast('邮件已发出。这家还要网申，已在「网申」页的待办里', 7000);
    if (qid) setTimeout(() => nextQueueItem(qid), 1200);
  } catch (e) {
    setBusy(btn, false);
    if (e.status === 504 && e.data && e.data.uncertain) {
      S.uncertain = true;
      const box = mode === 'draft' ? 'drafts' : 'sent';
      modal({title: mode === 'draft' ? '不确定草稿存上了没有' : '不确定有没有发出去', html: `<p>${esc(e.message)}</p>`,
        buttons: [{label: '知道了', value: true}, {label: mode === 'draft' ? '去 Gmail「草稿」看看' : '去 Gmail「已发送」看看', value: 'gmail', kind: 'primary'}]})
        .then(v => { if (v === 'gmail') openGmail(box); });
      if (qid) loadQueue(true);
    }
    else if (e.status === 409 && e.data && e.data.queue_taken) { toast(e.message, 6000); loadQueue(true); }
    else if (e.status === 409 && e.data && e.data.issues) { renderIssues(e.data.issues); toast('还有必须处理的问题，没发出'); }
    else if (e.status === 401) { toast(e.message, 8000); refreshGmail(); }
    else toast(verb + '失败：' + e.message, 8000);
  }
}
// 晚上：这封原样存好，明早 10:00 由面板自己发出（面板要开着、电脑别合盖）
async function doSchedule(r, force, qid, gen) {
  const btn = $('#sendBtn');
  setBusy(btn, true, '定时中…');
  clearTimeout(checkTimer); checkTimer = null;
  try {
    const d = await api('/api/schedule', {method: 'POST', body: {...payloadBase(), result: r, force}});
    setBusy(btn, false);
    const at = whenLabel();
    if (gen !== S.gen) { toast(`已定时：${r.company_name || ''}，${at}发出`, 6000); return; }
    S.sent = true; S.scheduled = true; S.editsDirty = false;
    markDone(btn, `已定在${at}发出`);
    renderWsRecordButton();
    toast(`已定在${at}发出（面板别关、电脑别合盖）`, 7000);
    if (S.result && S.result.apply_channel === '邮箱+网申') toast('这个岗位还要网申：「网申」页里有这家的待办，在那边让助手填', 9000);
    if (qid) setTimeout(() => nextQueueItem(qid), 1200);
  } catch (e) {
    setBusy(btn, false);
    if (e.status === 409 && e.data && e.data.queue_taken) { toast(e.message, 6000); loadQueue(true); }
    else if (e.status === 409 && e.data && e.data.issues) { renderIssues(e.data.issues); toast('还有必须处理的问题，没定时'); }
    else if (e.status === 401) { toast('Gmail 授权失效，到点会发不出去：先重新授权再定时。' + e.message, 9000); refreshGmail(); }
    else toast('定时失败：' + e.message, 8000);
  }
}
// 发出 90 秒后自动查一次退信（邮箱写错 / 对方邮箱失效会马上退回）
async function checkBounce(id, company) {
  try {
    await api('/api/check-replies', {method: 'POST', body: {campaign: '全部', record_ids: [id]}});
    const recs = await api('/api/records?campaign=全部');
    const rec = recs.find(x => x.id === id);
    if (rec && rec.reply_status === '退信') {
      await modal({title: '邮件被退回了', html: `<p class="text-red-700">发给 ${esc(company)}（${esc(rec.to_email)}）的邮件没送达：</p><p class="text-xs bg-red-50 p-2 rounded-lg">${esc(rec.reply_snippet)}</p><p class="text-xs">请核对邮箱，或换 JD 里的其他联系方式重投。</p>`, buttons: [{label: '知道了', value: true, kind: 'primary'}]});
    }
  } catch (e) {}
}
$('#sendBtn').onclick = () => deliver('send');
$('#draftBtn').onclick = () => deliver('draft');

// ━━━ 只记录 / 我已网申 ━━━
$('#recordBtn').onclick = async () => {
  if (!S.result || isDone() || busyGuard()) return;
  const ok = await modal({title: '只记录，不发邮件？', html: '<p>适用于已经在别处投过（网申 / 官网 / 别的邮箱）：记一笔，看板里就能跟进。</p>',
    buttons: [{label: '取消', value: false}, {label: '记录', value: true, kind: 'primary'}]});
  if (ok) recordApplication($('#recordBtn'), {standalone: true});
};
$('#wsRecordBtn').onclick = () => recordApplication($('#wsRecordBtn'));
$('#wsSkipMail').onclick = async () => {
  if (!S.result || isDone() || busyGuard()) return;
  const ok = await modal({title: '不发邮件，只记网申？', html: '<p>适用于邮箱失效、退信，或者邮件已经在别处发过。会单独记一条网申记录。</p>',
    buttons: [{label: '取消', value: false}, {label: '只记网申', value: true, kind: 'primary'}]});
  if (ok) recordApplication($('#wsRecordBtn'), {standalone: true});
};

async function recordApplication(btn, {standalone = false} = {}) {
  if (!S.result || S.wsRecorded || busyGuard()) return;
  if (!standalone && needMailFirst()) return;
  if (S.analyzeBusy) return toast('正在重写 / 分析，等它回来再记', 3000);
  const gen = S.gen, qid = S.queueId;
  S.lock = '记录';
  refreshActionButtons();
  setBusy(btn, true, '记录中…');
  clearTimeout(checkTimer); checkTimer = null;
  try {
    const inWs = !$('#wsCard').classList.contains('hidden');
    const d = await api('/api/record', {method: 'POST', body: {...payloadBase(), result: collectResult(), kit: collectKit(),
      upload_version: inWs ? $('#wsResumeVer').value : $('#resumeVersion').value, record_id: standalone ? '' : S.sentRecordId}});
    setBusy(btn, false);
    if (gen !== S.gen) return;
    S.wsRecorded = true; S.wsOpened = null; S.editsDirty = false;
    markDone($('#wsRecordBtn'), d.merged ? '已记在刚发的邮件记录上' : '已记录');
    markDone($('#recordBtn'), '已记录');
    toast(d.merged ? '已在这条投递记录上补记「已同时网申」' : '已记录，之后「检查回复」会帮你盯这家机构的来信', 5000);
    if (qid) setTimeout(() => nextQueueItem(qid), 1200);
  } catch (e) {
    setBusy(btn, false);
    if (e.status === 409) { toast(e.message, 6000); loadQueue(true); } else toast('记录失败：' + e.message);
  } finally {
    S.lock = null;
    if (gen === S.gen) refreshActionButtons();
  }
}

$('#downloadResume').onclick = async () => {
  const body = {version: $('#resumeVersion').value, filename: $('#resumeName').value, filename_en: $('#resumeNameEn').value};
  const files = body.version === '中英两份' ? [0, 1] : [0];
  for (const i of files) {
    const resp = await fetch('/api/resume-download', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'jobapply'}, body: JSON.stringify({...body, i})});
    if (!resp.ok) { toast('下载失败'); return; }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = i === 0 ? (body.filename || '简历.pdf') : (body.filename_en || 'Resume.pdf');
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
};

window.addEventListener('beforeunload', e => {
  if (S.queueId) flushQueueEdits(true);  // 队列条目：改动自动存回队列（keepalive，页面关了也能发出去），不用拦
  else if (S.result && !isDone() && S.userEdited) { e.preventDefault(); e.returnValue = ''; }
});
window.addEventListener('pagehide', () => { if (S.queueId) flushQueueEdits(true); });

// ━━━ 批量队列 ━━━
const Q_ORDER = {'待审核': 0, '需处理': 1, '发送中': 2, '处理中': 2, '重写中': 2, '排队中': 3, '失败': 4, '已定时': 4.5, '已发送': 5, '已存草稿': 5, '已记录': 5};
const Q_CLS = {'待审核': 'bg-green-100 text-green-700', '需处理': 'bg-red-100 text-red-700', '处理中': 'bg-blue-100 text-blue-700',
  '重写中': 'bg-blue-100 text-blue-700',
  '发送中': 'bg-blue-600 text-white', '排队中': 'bg-slate-100 text-slate-600', '失败': 'bg-red-600 text-white',
  '已发送': 'bg-slate-200 text-slate-500', '已存草稿': 'bg-slate-200 text-slate-500', '已记录': 'bg-slate-200 text-slate-500',
  '已定时': 'bg-purple-100 text-purple-700'};
// 审核时改过的条目，用最新一次检查的结果
const issuesOf = it => it.live_issues || (it.analysis ? it.analysis.issues : []);
const resultOf = it => it.analysis ? (it.edited || it.analysis.result) : null;
const reviewable = it => it.status === '待审核' || it.status === '需处理';
// 「邮箱+网申」补记网申：已经停用（网申都在「网申」页做），一律不能补记；用到它的分支等写邮件页瘦身时一起删
const canAddWs = it => false;
// 排队：待审核 → 需处理 → 跳过过的（按跳过的先后，排在所有没看过的后面）→ 其他
const qRank = it => reviewable(it) && it.skipped_at ? 1.5 : (Q_ORDER[it.status] ?? 9);
const qKey = it => (reviewable(it) && it.skipped_at) || it.created_at;
const sortedQueue = () => [...S.queue].sort((a, b) => qRank(a) - qRank(b) || qKey(a).localeCompare(qKey(b)));

async function loadQueue(force = false) {
  let d;
  try { d = await api('/api/queue'); }
  catch (e) { if (!S.queueErrShown) { S.queueErrShown = true; toast('读取队列失败：' + e.message, 5000); } return; }
  S.queueErrShown = false;
  try {
    S.queue = d.items; $('#queueWorkers').textContent = d.workers;
    const c = d.counts, waiting = (c['待审核'] || 0) + (c['需处理'] || 0), active = (c['排队中'] || 0) + (c['处理中'] || 0) + (c['发送中'] || 0);
    const badge = $('#queueBadge');
    badge.textContent = active ? `${waiting}·${active}…` : waiting;
    badge.classList.toggle('hidden', !waiting && !active);
    document.title = (waiting ? `(${waiting}) ` : '') + BASE_TITLE;
    const sig = JSON.stringify(d.items.map(i => [i.id, i.status, i.updated_at]));
    if (S.tab === 'new' && (sig !== S.queueSig || force)) { S.queueSig = sig; renderQueue(c); }
    if (S.queueId) updateReviewBanner();
    else if (S.tab === 'new') { renderEmptyDesk(); maybeAutoOpen(); }
    for (const id of [...S.regenWatch]) {   // 后台重写的别的几封：写好了提醒一声
      const x = d.items.find(i => i.id === id);
      if (x && x.status === '重写中') continue;
      S.regenWatch.delete(id);
      if (x && id !== S.queueId) { const rx = resultOf(x) || {};
        toast(x.error && x.error.startsWith('重写失败') ? `「${rx.company_name || ''}」${x.error}` : `「${rx.company_name || ''}」重写好了，在列表里`, 4000); }
    }
    if (reviewingLive() && !S.kit && S.kitLoadingGen !== S.gen) {  // 网申问答在后台生成完了：补上
      const it = S.queue.find(x => x.id === S.queueId);
      if (it && it.wangshen && !it.wangshen.error) { S.kit = it.wangshen; renderKit(); }
    }
  } catch (e) { console.error('队列显示出错', e); }
}

// 同一个岗位（和后端 checks.similar_job 一样的算法）
const TITLE_FILLER = /[（）()\[\]【】\-—–·|｜/、，,\s]|实习生|实习|岗位|岗|职位|招聘|方向|日常/g;
function similarJob(a, b) {
  a = (a || '').replace(TITLE_FILLER, ''); b = (b || '').replace(TITLE_FILLER, '');
  if (!a || !b) return false;
  if (a.includes(b) || b.includes(a)) return true;
  const g = s => new Set(s.length < 2 ? [s] : [...Array(s.length - 1).keys()].map(i => s.slice(i, i + 2)));
  const x = g(a), y = g(b), inter = [...x].filter(v => y.has(v)).length;
  return inter / (x.size + y.size - inter) >= 0.5;
}
// 队列里同一个邮箱、同一个岗位已经有一封定了时 / 存了草稿 / 发了（还没进记录的定时信，后端查不到，这里补上）
function duplicateOf(it) {
  const r = resultOf(it); if (!r || !reviewable(it)) return null;
  // 同一个邮箱，或同一家机构的邮箱域名（发给同一家的不同人也算；公共邮箱 gmail / 163 之类不算）
  const PUBLIC = /^(gmail|163|126|qq|foxmail|sina|sohu|yeah|139|outlook|hotmail|live|icloud|me|yahoo|aliyun|88)\./;
  const keys = es => es.flatMap(e => { e = e.toLowerCase(); const d = e.split('@')[1] || ''; return PUBLIC.test(d) ? [e] : [e, '@' + d]; });
  const mine = new Set(keys(listArr(r.to_emails).concat(listArr(r.cc_emails))));
  return S.queue.find(x => x.id !== it.id && ['已定时', '已存草稿', '已发送', '发送中'].includes(x.status) && resultOf(x)
    && keys(listArr(resultOf(x).to_emails).concat(listArr(resultOf(x).cc_emails))).some(k => mine.has(k))
    && similarJob(r.job_title, resultOf(x).job_title)) || null;
}

// 发出去的、记过的不在列表里待着（都在「投递看板」里）；「邮箱+网申」还要补记网申的留着
const listed = it => !['已发送', '已记录', '转网申'].includes(it.status);
function renderQueue(c) {
  const items = sortedQueue().filter(listed);
  const gone = S.queue.length - items.length;
  const toWs = c['转网申'] || 0;
  $('#queueSummary').textContent = (Object.entries(c).filter(([k]) => !['已发送', '已记录', '转网申'].includes(k)).map(([k, v]) => `${k} ${v}`).join(' ｜ ') || '列表是空的')
    + (gone - toWs > 0 ? `（已发出 / 记过的 ${gone - toWs} 封在「投递看板」里）` : '') + (toWs ? `（只能网申的 ${toWs} 条已转到「网申」页）` : '');
  const drafts = S.queue.filter(x => x.status === '已存草稿' && x.record_id).length;   // 存了草稿还没发的：可以排到明早自动发
  $('#queueDraftsBtn').textContent = `把 ${drafts} 封草稿定在${whenLabel()} 发出`;
  $('#queueDraftsBtn').classList.toggle('hidden', !drafts);
  $('#queueList').innerHTML = items.map(it => {
    const r = resultOf(it);
    const title = r ? `${r.company_name || '—'}｜${r.job_title || '—'}` : (it.page && it.page.title) || it.input.slice(0, 80);
    const ws = r && isWangshen(r), wsOnly = ws && !listArr(r.to_emails).length;
    const iss = wsOnly ? [] : issuesOf(it);
    const errs = iss.filter(i => i.level === 'error').length, warns = iss.filter(i => i.level === 'warn').length;
    const sub = it.error ? `<span class="text-red-600">${esc(it.error)}</span>` : r ? esc(wsOnly ? '网申岗位' + (r.apply_url ? '：' + r.apply_url : '（可能要扫码）') : r.email_subject) :
      (it.status === '处理中' ? '正在抓取 / 写邮件…' : '等待处理…');
    const done = ['已发送', '已存草稿', '已记录'].includes(it.status), addWs = canAddWs(it);
    const wsChip = (ws ? `<span class="chip bg-teal-100 text-teal-700">${wsOnly ? '网申' : '邮箱+网申'}</span>` : '') +
      (it.send_uncertain && !done ? '<span class="chip bg-red-600 text-white">可能已发出</span>' : '');
    const kitTip = ws && !done ? (it.wangshen && !it.wangshen.error ? '<span class="text-[11px] text-teal-700 whitespace-nowrap">问答已写好</span>' : it.wangshen && it.wangshen.error ? '<span class="text-[11px] text-amber-700 whitespace-nowrap">问答生成失败，打开后可重试</span>' : '') : '';
    const counts = it.analysis && !wsOnly && !done ? `<span class="text-[11px] whitespace-nowrap ${errs ? 'text-red-600' : warns ? 'text-amber-700' : 'text-green-700'}">${errs ? errs + ' 必须处理 ' : ''}${warns ? warns + ' 注意' : (errs ? '' : '检查全过')}</span>` : '';
    return `<div class="bg-white rounded-xl border p-3 flex flex-wrap xl:flex-nowrap items-center gap-x-3 gap-y-2 ${done && !addWs ? 'opacity-50' : ''}">
      <span class="chip ${Q_CLS[it.status] || ''}">${['处理中', '发送中', '重写中'].includes(it.status) ? '<span class="material-symbols-outlined animate-spin text-xs">progress_activity</span>' : ''}${esc(it.status)}${it.status === '已定时' && it.send_at ? ' ' + esc(it.send_at.slice(5)) : ''}</span>
      <div class="flex-1 min-w-[10rem]"><div class="text-sm font-semibold truncate">${esc(title)} ${r && r.position_type ? posBadge(r.position_type) : ''} ${wsChip} ${it.multi_job ? `<span class="chip bg-amber-100 text-amber-800">同一篇·共 ${it.multi_job} 个岗位</span>` : ''} ${optsChip(it)}${duplicateOf(it) ? `<span class="chip bg-red-600 text-white" title="同一个邮箱、同一个岗位已经有一封${esc(duplicateOf(it).status)}">重复：已有一封${esc(duplicateOf(it).status)}</span>` : ''}</div>
        <div class="text-xs text-slate-500 truncate">${sub}</div></div>
      <div class="flex flex-wrap items-center justify-end gap-2 ml-auto">
        ${kitTip}${counts}
        ${reviewable(it) ? `<button class="q-open px-3 py-1.5 bg-primary text-white rounded-lg text-xs font-bold" data-id="${esc(it.id)}" title="在下面打开这封，看完再决定发不发">打开</button>` : ''}
        ${addWs ? `<button class="q-open px-3 py-1.5 bg-teal-600 text-white rounded-lg text-xs font-bold" data-id="${esc(it.id)}">补记网申</button>` : ''}
        ${it.status === '已存草稿' && it.record_id ? `<button class="q-draft px-2 py-1.5 border border-purple-300 text-purple-700 rounded-lg text-xs" data-id="${esc(it.id)}" title="点了会先让你确认；到点由面板把这封草稿从 Gmail 原样发出">定在${whenLabel()} 发出</button>` : ''}
        ${it.status === '已定时' ? `<button class="q-unsched px-2 py-1.5 border rounded-lg text-xs" data-id="${esc(it.id)}">取消定时</button>` : ''}
        ${['失败', '需处理'].includes(it.status) ? `<button class="q-retry px-2 py-1.5 border rounded-lg text-xs" data-id="${esc(it.id)}" title="重新抓文章、重新写这封（你在这封上改过的内容会丢）">从头重写</button>` : ''}
        ${!done && it.status !== '发送中' ? `<button class="q-del px-2 py-1.5 text-slate-400 hover:text-red-600 text-xs" data-id="${esc(it.id)}" title="从列表删掉这条（不影响已经发出的信和看板记录）"><span class="material-symbols-outlined text-base">close</span></button>` : ''}
      </div>
    </div>`;
  }).join('') || '<p class="text-sm text-slate-400 p-4">还没有内容。去公众号复制链接，回来在上面的框里 ⌘V。</p>';
}

// 编辑区：有打开的一封才显示，没有就显示提示
function showDesk(on) {
  $('#desk').classList.toggle('hidden', !on);
  $('#emptyDesk').classList.toggle('hidden', on);
}
function renderEmptyDesk() {
  const writing = S.queue.some(x => ['排队中', '处理中', '重写中'].includes(x.status));
  $('#emptyDesk').textContent = !S.autoOpen ? '已关闭这封。点上面列表里的「审核」接着看。'
    : writing ? '正在写……写好一封就会在这里自动打开。'
    : S.queue.some(reviewable) ? '点上面列表里的「审核」打开一封。'
    : '把链接或 JD 贴到上面，写好的邮件会自动在这里打开。';
}
// 待办列表：打开一封时自动收起（专心改这封），关掉后展开；随时可以手动展开 / 收起
function setListOpen(open) {
  S.listOpen = open;
  $('#queueList').classList.toggle('hidden', !open);
  $('#queueToggle').textContent = open ? '收起列表' : '展开列表';
}
$('#queueToggle').onclick = () => setListOpen(!S.listOpen);
// 编辑区空着、没在忙：写好的下一封自动打开（点过「关闭这封」就先不自动开，等你点「审核」或贴新的）
function maybeAutoOpen() {
  if (!S.cfg || !S.autoOpen || S.opening || S.lock || S.analyzeBusy || S.queueId || S.tab !== 'new') return;
  const next = sortedQueue().find(reviewable);
  if (next) openQueueItem(next.id, {scroll: false});
}

// 加入队列时指定过的设置，列表里标出来
const optsText = o => [o.position_hint || '', o.resume_hint ? o.resume_hint + '简历' : '',
  o.report_hint ? (o.report_hint === '附上' ? '附' : '不附') + '研究样本' : '', o.extra ? '有补充要求' : ''].filter(Boolean).join(' · ');
function optsChip(it) {
  const o = it.opts || {}, t = optsText(o);
  return t ? `<span class="chip bg-indigo-50 text-indigo-700" title="${esc(o.extra || '')}">指定：${esc(t)}</span>` : '';
}

// 「这批的设置」
const queueOpts = () => ({position_hint: $('#qPos').value, resume_hint: $('#qResume').value,
  report_hint: $('#qReport').value, extra: $('#qExtra').value.trim()});
function refreshQueueOpts() {
  const o = queueOpts(), on = Object.values(o).some(Boolean);
  $('#queueOpts').classList.toggle('opts-on', on);
  $('#qOptsReset').classList.toggle('hidden', !on);
  $('#qOptsSummary').textContent = on ? '已指定：' + optsText(o) : '都按默认（AI 按 JD 判断）';
}
// 设置默认收起，只显示一行摘要；点「改设置」展开
function showQueueOpts(open) {
  ['#qOptsGrid', '#qOptsHint'].forEach(s => $(s).classList.toggle('hidden', !open));
  $('#qOptsMore').textContent = open ? '收起' : '改设置';
}
$('#qOptsMore').onclick = $('#qOptsToggle').onclick = () => showQueueOpts($('#qOptsGrid').classList.contains('hidden'));
['#qPos', '#qResume', '#qReport'].forEach(s => $(s).addEventListener('change', refreshQueueOpts));
$('#qExtra').addEventListener('input', refreshQueueOpts);
$('#qOptsReset').onclick = () => { ['#qPos', '#qResume', '#qReport', '#qExtra'].forEach(s => $(s).value = ''); refreshQueueOpts(); };

// 一条一条地加（连着粘贴时，后一条等前一条提交完，只提交新粘贴的那部分）
let addChain = Promise.resolve();
function addToQueue() { addChain = addChain.then(doAddToQueue, doAddToQueue); return addChain; }
async function doAddToQueue() {
  const text = $('#queueInput').value.trim();
  if (!text) return;
  try {
    const opts = queueOpts();
    const d = await api('/api/queue', {method: 'POST', body: {text, opts}});
    const cur = $('#queueInput').value;
    $('#queueInput').value = cur.trim() === text ? '' : cur.replace(text, '').trim();  // 提交期间又粘贴进来的保留
    S.autoOpen = true;
    toast(d.added ? `已加入 ${d.added} 条，后台在写${Object.values(opts).some(Boolean) ? '（按这批的设置）' : ''}，写好会自动打开` : '这些链接已经在队列里了', 2500);
    loadQueue(true);
  } catch (e) { toast(e.message, 5000); }
}
$('#queueAddBtn').onclick = addToQueue;
$('#queueInput').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); addToQueue(); } });
$('#queueInput').addEventListener('paste', () => { if ($('#autoAdd').checked) setTimeout(addToQueue, 50); });
// 投递页上没点进任何输入框时 ⌘V：当成往上面的框里贴
document.addEventListener('paste', e => {
  if (S.tab !== 'new' || (e.target.closest && e.target.closest('input, textarea, select, [contenteditable]'))) return;
  const text = (e.clipboardData && e.clipboardData.getData('text')) || '';
  if (!text.trim()) return;
  e.preventDefault();
  $('#queueInput').value = ($('#queueInput').value.trim() + '\n' + text).trim();
  if ($('#autoAdd').checked) addToQueue(); else $('#queueInput').focus();
});

$('#queueList').addEventListener('click', async e => {
  const b = e.target.closest('button[data-id]');
  if (!b) return;
  const id = b.dataset.id, it = S.queue.find(x => x.id === id);
  try {
    if (b.classList.contains('q-open')) { S.autoOpen = true; return await openQueueItem(id); }
    if (b.classList.contains('q-draft')) await scheduleDrafts([id]);
    if (b.classList.contains('q-unsched')) {
      const d = await api(`/api/queue/${encodeURIComponent(id)}/unschedule`, {method: 'POST', body: {}});
      toast(d.ok ? '已取消定时：这封回到待审核，可以再改再发' : '取消不了：可能已经发出了', 4000);
    }
    if (b.classList.contains('q-retry')) {
      const d = await api(`/api/queue/${encodeURIComponent(id)}/retry`, {method: 'POST', body: {}});
      if (!d.ok) toast('这条现在不能重做（可能正在处理或已经完成）');
    }
    if (b.classList.contains('q-del')) {
      if (it && it.analysis) {
        const r = resultOf(it);
        const ok = await modal({title: '从队列里删掉这条？', html: `<p><b>${esc(r.company_name || '')}｜${esc(r.job_title || '')}</b></p><p class="text-xs text-slate-500">只是从队列里删掉，不影响已发的邮件和看板记录。</p>`,
          buttons: [{label: '取消', value: false}, {label: '删除', value: true, kind: 'danger'}]});
        if (!ok) return;
      }
      if (id === S.queueId) { if (busyGuard()) return; exitReview(); }
      const d = await api('/api/queue/' + encodeURIComponent(id), {method: 'DELETE'});
      if (!d.ok) toast('删不了：这条正在发送');
    }
  } catch (err) { toast('操作失败：' + err.message, 5000); }
  loadQueue(true);
});
// 存了草稿还没发的：明早 10:00 由面板从 Gmail 原样发出（在 Gmail 里改过草稿，发的是改过的）
async function scheduleDrafts(ids) {
  const n = ids ? ids.length : S.queue.filter(x => x.status === '已存草稿' && x.record_id).length;
  const ok = await modal({title: `${n} 封草稿定在${whenLabel()} 发出？`,
    html: `<p>到点由面板从 Gmail 把这${ids ? '封' : '几封'}草稿原样发出（你在 Gmail 里改过的话，发的是改过的）。面板别关、电脑别合盖。</p><p class="text-xs text-slate-500 mt-2">反悔了在列表里点「取消定时」。</p>`,
    buttons: [{label: '取消', value: false}, {label: `定在${whenLabel()} 发出`, value: true, kind: 'primary'}]});
  if (!ok) return;
  try {
    const d = await api('/api/queue/schedule-drafts', {method: 'POST', body: ids ? {ids} : {}});
    toast(`已定时 ${d.scheduled} 封草稿：${d.send_at.slice(5)} 发出`, 5000);
  } catch (e) { toast(e.status === 401 ? 'Gmail 授权失效，到点会发不出去：先重新授权。' + e.message : '定时失败：' + e.message, 8000); if (e.status === 401) refreshGmail(); }
  loadQueue(true);
}
$('#queueDraftsBtn').onclick = () => scheduleDrafts(null);
// 打开一条到编辑区。scroll=false：后台写好自动打开的，不打断你（不滚动页面）
async function openQueueItem(id, {scroll = true} = {}) {
  if (busyGuard() || S.opening) return;
  S.opening = true;   // 第一个 await 之前占住：轮询里的自动打开不会同时再开一条
  try {
    await flushQueueEdits();
    await loadQueue();  // 用最新的内容，不用几秒前的快照
    const it = S.queue.find(x => x.id === id);
    if (!it || !it.analysis) return;
    const addWs = canAddWs(it);
    if (!reviewable(it) && !addWs) { toast('这条已经在发送或处理过了'); loadQueue(true); return; }
    newContext(); clearResultView();
    switchTab('new');
    S.queueId = id; S.queueRev = it.rev || '';
    S.sent = addWs; S.sentRecordId = addWs ? it.record_id : '';
    S.page = it.page || null; S.pageJd = it.jd_text || '';
    $('#jdInput').value = it.jd_text || '';
    $('#targetJob').value = it.target_job || '';
    $('#extraInput').value = (it.regen_req || {}).extra || '';   // 上次让 AI 重写时写的要求
    S.analysis = it.analysis; S.result = resultOf(it); S.fixes = it.analysis.fixes || [];
    S.kit = it.wangshen && !it.wangshen.error ? it.wangshen : null;
    showDesk(true); setListOpen(false);
    fillEmail(S.result); renderInfo(S.result); renderRules(S.result); renderIssues(issuesOf(it));
    renderKit(it.wangshen && it.wangshen.error ? it.wangshen.error : '');
    if (addWs) markDone($('#sendBtn'), it.status === '已存草稿' ? '已存草稿' : '已发送');
    refreshActionButtons();
    const m = it.analysis.meta || {};
    $('#metaLine').textContent = `${m.model || ''} · ${m.seconds || '?'} 秒 · ${m.backend || ''}（后台写的）`;
    updateReviewBanner();
    if (scroll) window.scrollTo(0, Math.max(0, $('#reviewBanner').getBoundingClientRect().top + window.scrollY - 80));
    else toast(`写好了一封：${resultOf(it).company_name || ''}，已在下面打开`, 2500);
    if (reviewable(it)) scheduleCheck();  // 只刷新检查结果；没改过就不往回存
    // 网申岗位还没有问答（老条目 / 后台没来得及写）：顺手生成；后台生成失败的不自动重试，留「生成问答」按钮
    // 网申那部分在「网申」页，这里不再自动生成网申问答
  } finally { S.opening = false; }
}

function updateReviewBanner() {
  const it = S.queue.find(x => x.id === S.queueId);
  if (!it && !S.lock) { leaveReview(); toast('这条已经不在队列里了，页面已清空', 4000); return; }
  if (!it) return;
  const r0 = resultOf(it) || {};
  if (it.status === '重写中') {   // 在后台重写：留在这儿等，写好自动刷新
    $('#reviewText').innerHTML = `正在重写：<b>${esc(r0.company_name || '')}｜${esc(r0.job_title || '')}</b>（约 15 秒，写好自动刷新；可以先点「跳过，看下一个」）`;
    $('#reviewBanner').classList.remove('hidden'); $('#reviewBanner').classList.add('flex');
    return;
  }
  if (S.regenWaiting && reviewable(it)) {
    S.regenWaiting = false;
    if (it.rev !== S.regenRev) { S.regenWatch.delete(it.id); openQueueItem(it.id, {scroll: false}); toast('重写好了，已换成新写的这封', 3000); return; }
    setBusy($('#regenBtn'), false); refreshActionButtons();   // 重写没成功：原来那封还在，原因写在横幅里
  }
  // 这条在别处（一键处理 / 另一个窗口）被发掉了：页面清空，免得再发一封
  if (!reviewable(it) && !canAddWs(it) && !isDone() && !S.lock) {
    leaveReview(); toast(`这条已经在别处处理（${it.status}），页面已清空`, 5000); return;
  }
  const left = S.queue.filter(x => reviewable(x) && x.id !== S.queueId).length;
  const r = resultOf(it) || {};
  const what = canAddWs(it) && !S.wsRecorded ? '补记网申' : '正在看';
  $('#reviewText').innerHTML = `${what}：<b>${esc(r.company_name || '')}｜${esc(r.job_title || '')}</b>（还有 ${left} 封待看）` +
    (it.multi_job && reviewable(it) ? `<br><span class="text-amber-800 text-xs">这篇文章拆出了 ${it.multi_job} 个岗位，每个一条：只发想投的，其余在队列里删掉</span>` : '') +
    (duplicateOf(it) ? `<br><span class="text-red-600 text-xs font-bold">重复投递：同一个邮箱、同一个岗位已经有一封${esc(duplicateOf(it).status)}（${esc(resultOf(duplicateOf(it)).company_name || '')}），这封别再发，删掉就行</span>` : '') +
    (it.error && reviewable(it) ? `<br><span class="text-red-600 text-xs">${esc(it.error)}</span>` : '');
  $('#reviewBanner').classList.remove('hidden'); $('#reviewBanner').classList.add('flex');
}

function exitReview() {
  S.queueId = null; S.queueRev = '';
  $('#reviewBanner').classList.add('hidden'); $('#reviewBanner').classList.remove('flex');
}

// 下一条：按队列顺序往后找（到底再从头），fromId 是「发完 / 记完自动跳」时那一条——用户已经去别处了就不跳
async function nextQueueItem(fromId) {
  if (fromId !== undefined && (fromId !== S.queueId || S.tab !== 'new')) return;
  if (busyGuard()) return;
  await flushQueueEdits();
  await loadQueue();
  const order = sortedQueue(), cur = S.queueId;
  const i = order.findIndex(x => x.id === cur);
  const rest = i >= 0 ? order.slice(i + 1).concat(order.slice(0, i)) : order;
  const next = rest.find(x => reviewable(x) && x.id !== cur);
  if (next) await openQueueItem(next.id);
  else if (fromId === undefined && cur && reviewable(S.queue.find(x => x.id === cur) || {})) toast('队列里没有别的待审核了', 3000);  // 点「跳过」时就是最后一条：留在这条
  else {
    leaveReview();
    toast(S.queue.some(x => ['排队中', '处理中', '重写中'].includes(x.status)) ? '写好的都看完了，剩下的还在写，写好会自动打开' : '都看完了，没有待审核的了', 3500);
  }
}
// 跳过：这封排到待看的最后，先看别的
$('#reviewSkip').onclick = async () => {
  if (busyGuard()) return;
  const cur = S.queueId;
  if (cur) { try { await api(`/api/queue/${encodeURIComponent(cur)}/skip`, {method: 'POST', body: {}}); } catch (e) {} }
  nextQueueItem();
};
$('#reviewBack').onclick = async () => { if (busyGuard()) return; await flushQueueEdits(); S.autoOpen = false; leaveReview(); };

// ━━━ 网申资料包 ━━━
const QR_MARK = '【文章图片里的二维码链接（系统自动识别）】';
function wsLinks() {
  const out = [];
  const add = (u, label) => { u = safeUrl(u); if (u && !out.some(x => x.u === u)) out.push({u, label}); };
  if (S.result) add(S.result.apply_url, '网申链接');
  ((S.page && S.page.qr_urls) || []).forEach(u => add(u, '文章二维码里的链接'));
  const jd = $('#jdInput').value, i = jd.indexOf(QR_MARK);
  if (i >= 0) jd.slice(i + QR_MARK.length).split('\n').map(s => s.trim()).filter(Boolean).forEach(u => add(u, '文章二维码里的链接'));
  return out;
}

function renderKit(error = '') {
  const r = S.result;
  $('#wsCard').classList.add('hidden');
  $('#recordBtn').classList.remove('hidden');
  if (!r || !isWangshen(r)) { $('#wsMini').classList.add('hidden'); return; }
  $('#wsMiniText').textContent = listArr(r.to_emails).length
    ? '这家除了发邮件还要网申：已加到「网申」页的待办，邮件在这里照常发。'
    : '这家只能网申，不用发邮件：已转到「网申」页，在那里让助手填。';
  $('#wsMini').classList.remove('hidden');
}
function renderKitOld(error = '') {
  const r = S.result;
  if (!r || !isWangshen(r)) { $('#wsCard').classList.add('hidden'); $('#recordBtn').classList.remove('hidden'); return; }
  $('#wsCard').classList.remove('hidden');
  $('#recordBtn').classList.add('hidden');  // 网申岗位用卡片里的「我已网申」
  const k = S.kit;
  $('#wsChannel').textContent = r.apply_channel === '邮箱+网申' ? '邮件 + 网申都要' : '只网申';
  $('#wsChannel').classList.remove('hidden');
  $('#wsPlatform').textContent = k && k.platform ? k.platform : '';
  $('#wsPlatform').classList.toggle('hidden', !(k && k.platform && !['不明确', '其他'].includes(k.platform)));
  // ① 链接
  const links = wsLinks();
  $('#wsLinks').innerHTML = links.length ? links.map(l => `<div class="flex items-center gap-2 min-w-0">
      <a href="${esc(l.u)}" target="_blank" rel="noopener" class="ws-open shrink-0 px-3 py-1.5 bg-slate-900 text-white rounded-lg text-xs font-bold flex items-center gap-1"><span class="material-symbols-outlined text-sm">open_in_new</span>打开</a>
      <span class="text-xs text-slate-400 shrink-0">${esc(l.label)}</span><span class="text-xs truncate text-slate-600" title="${esc(l.u)}">${esc(l.u)}</span>
      <button class="copy-btn shrink-0" data-copy="${esc(l.u)}">复制</button></div>`).join('')
    : '<p class="text-xs text-amber-700">JD 里没找到网申链接：可能要用手机微信扫文章里的二维码，或者去机构官网的招聘页投。</p>';
  // ② 简历：英文 JD 默认英文版，其余默认中文单页；JD 规定了文件名就用 JD 的（换了岗位才重置，免得冲掉你改的）
  const en = r.jd_language === '英文';
  if ($('#wsResumeName').dataset.gen !== String(S.gen)) {
    $('#wsResumeVer').value = en ? '英文' : '中文';
    const fmt = (r.jd_rules || {}).resume_filename_format;
    $('#wsResumeName').value = fmt && r.resume_filename ? r.resume_filename : (en ? S.cfg.resume_default_en : S.cfg.resume_default_zh);
    $('#wsResumeName').dataset.gen = String(S.gen);
  }
  // ④ 问答
  const box = $('#wsAnswers'), loading = S.kitLoadingGen === S.gen;
  if (!k) {
    box.innerHTML = loading ? '<p class="text-xs text-slate-500 flex items-center gap-1"><span class="material-symbols-outlined animate-spin text-sm">progress_activity</span>AI 正在按这个岗位写自我介绍、为什么申请等回答（约 20 秒）…</p>'
      : error ? `<p class="text-xs text-red-600">问答生成失败：${esc(error)}。点右上角「生成问答」重试。</p>`
      : '<p class="text-xs text-slate-500">还没生成。点右上角「生成问答」，AI 会按这个岗位写好自我介绍、为什么申请、匹配点，以及 JD 里点名要回答的问题。</p>';
    if (!loading) $('#wsGenBtn').innerHTML = '<span class="material-symbols-outlined text-sm">auto_awesome</span>生成问答';
  } else {
    const qa = [['self_intro_short', '自我介绍（短版）'], ['self_intro', '自我介绍（长版）'], ['why_this_role', '为什么申请这个岗位'], ['fit_points', '我和岗位的匹配点']]
      .filter(([key]) => k[key]).map(([key, label]) => ({key, label, text: k[key]}))
      .concat((k.custom_answers || []).map((a, i) => ({key: 'custom:' + i, label: 'JD 里的问题：' + a.question, text: a.answer})));
    box.innerHTML = qa.map(x => `<div><div class="flex items-center gap-2 mb-1"><span class="text-xs font-bold text-slate-600 flex-1">${esc(x.label)}</span>
        <span class="ws-count text-[11px] text-slate-400">${x.text.length} 字</span><button class="copy-btn ws-copy">复制</button></div>
        <textarea class="ws-ans grow-y w-full bg-slate-50 border-none rounded-xl p-3 text-sm leading-relaxed resize-none overflow-hidden focus:ring-2 focus:ring-teal-200" data-key="${esc(x.key)}">${esc(x.text)}</textarea>
        ${/\[待填\]/.test(x.text) ? '<p class="text-[11px] text-amber-700">里面有 [待填]，复制前先改成你的实际情况。</p>' : ''}</div>`).join('');
    box.querySelectorAll('textarea').forEach(autoGrow);
    if (!loading) $('#wsGenBtn').innerHTML = '<span class="material-symbols-outlined text-sm">refresh</span>重新生成';
  }
  // 步骤 / 注意事项
  const steps = (k && k.apply_steps) || [], notes = (k && k.notes) || [];
  $('#wsExtraBody').innerHTML = (steps.length ? `<div><p class="font-bold text-slate-600 mb-1">AI 看 JD 整理的投递步骤</p><ol class="list-decimal pl-4 space-y-0.5 text-slate-600">${steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol></div>` : '') +
    (notes.length ? `<div><p class="font-bold text-amber-700 mb-1">注意</p><ul class="list-disc pl-4 space-y-0.5 text-amber-800">${notes.map(s => `<li>${esc(s)}</li>`).join('')}</ul></div>` : '');
  $('#wsExtra').classList.toggle('hidden', !(steps.length || notes.length));
  renderWsRecordButton();
}

// 「邮箱+网申」：先发邮件，网申补记在同一条记录上（一个岗位只留一条记录）；邮件发不了就给个「只记网申」的出口
const needMailFirst = () => !!S.result && S.result.apply_channel === '邮箱+网申' && !S.sent && !S.wsRecorded;
function renderWsRecordButton() {
  const btn = $('#wsRecordBtn'), skip = $('#wsSkipMail');
  skip.classList.toggle('hidden', !needMailFirst() || !!S.lock);
  if (S.wsRecorded || btn._busyHtml != null) return;
  if (S.scheduled) {
    btn.innerHTML = '<span class="material-symbols-outlined">schedule</span>邮件定时发出后，在列表里点「补记网申」记一笔';
    btn.disabled = true; return;
  }
  btn.innerHTML = needMailFirst() ? '<span class="material-symbols-outlined">mail</span>这个岗位还要发邮件：先把邮件发出，再点这里补记网申' : ORIG_HTML.wsRecordBtn;
  btn.disabled = !S.result || needMailFirst() || !!S.lock;
}

function collectKit() {
  if (!S.kit) return null;
  const k = JSON.parse(JSON.stringify(S.kit));
  $('#wsAnswers').querySelectorAll('textarea[data-key]').forEach(t => {
    const key = t.dataset.key;
    if (key.startsWith('custom:')) { const a = (k.custom_answers || [])[+key.slice(7)]; if (a) a.answer = t.value; }
    else k[key] = t.value;
  });
  return k;
}

let kitSaveTimer = null;
$('#wsAnswers').addEventListener('input', e => {
  const t = e.target.closest('textarea'); if (!t) return;
  autoGrow(t);
  t.parentElement.querySelector('.ws-count').textContent = t.value.length + ' 字';
  S.kit = collectKit();
  clearTimeout(kitSaveTimer); kitSaveTimer = null;
  if (!kitEditable()) return;  // 非队列：记录时会连同问答一起存
  const gen = S.gen;
  kitSaveTimer = setTimeout(() => { kitSaveTimer = null; if (gen === S.gen) saveQueueEdits({wangshen: S.kit}); }, 800);
});
document.addEventListener('click', e => {
  const c = e.target.closest('.ws-copy');
  if (c) { const t = c.parentElement.parentElement.querySelector('textarea'); copyText(t.value, c.parentElement.querySelector('span').textContent); return; }
  const d = e.target.closest('[data-copy]');
  if (d) { copyText(d.dataset.copy, d.dataset.label || ''); return; }
  if (e.target.closest('.ws-open')) noteWsOpened();
});

async function genKit(auto = false) {
  if (!S.result || busyGuard() || S.kitLoadingGen === S.gen) return;
  const gen = S.gen, btn = $('#wsGenBtn');
  S.kitLoadingGen = gen; renderKit(); setBusy(btn, true, '生成中…');
  let err = '';
  try {
    const d = await api('/api/wangshen', {method: 'POST', body: {...payloadBase(), result: collectResult()}});
    if (gen === S.gen) { S.kit = d.kit; if (!auto) toast('网申问答已生成'); }  // 期间换了岗位就不用（队列条目的服务器那边已经存好了）
  } catch (e) {
    if (gen === S.gen) err = e.message;
  }
  if (S.kitLoadingGen === gen) S.kitLoadingGen = null;
  if (gen === S.gen) { setBusy(btn, false); renderKit(err); }
}
$('#wsGenBtn').onclick = () => genKit(false);

$('#wsResumeDl').onclick = async () => {
  const body = {version: $('#wsResumeVer').value, filename: $('#wsResumeName').value, i: 0};
  const resp = await fetch('/api/resume-download', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'jobapply'}, body: JSON.stringify(body)});
  if (!resp.ok) return toast('下载失败');
  const blob = await resp.blob(), a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (body.filename || '简历').replace(/\.pdf$/i, '') + '.pdf';
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  toast('已下载：' + a.download + '（在「下载」文件夹，网申页面上传它）', 5000);
};

// 点了「打开网申页面」→ 去别的标签页投 → 回到面板时问一句「投完了吗？」
function noteWsOpened() {}   // 网申在「网申」页做（助手填、读回网站上实际提交的内容），这里不再弹「网申投完了吗」
async function askWsDone() {
  const o = S.wsOpened;
  if (!o || S.wsRecorded || document.hidden || Date.now() - o.at < 20000 || S.asking || S.lock) return;
  if (o.gen !== S.gen || !S.result) { S.wsOpened = null; return; }
  S.asking = true;
  try {
    const r = S.result;
    if (needMailFirst()) {
      S.wsOpened = null;
      await modal({title: '网申投完了？还差一封邮件', html: '<p>这个岗位除了网申还要发邮件。先把邮件发出（或存草稿），再点网申卡片最下面的「我已网申，记一笔」，会记在同一条投递记录上。</p><p class="text-xs text-slate-500">邮件发不了的话，点卡片最下面的「只记网申」。</p>',
        buttons: [{label: '好', value: true, kind: 'primary'}]});
      return;
    }
    const ans = await modal({title: '网申投完了吗？', html: `<p><b>${esc(r.company_name)}｜${esc(r.job_title)}</b></p><p class="text-xs text-slate-500">投完了点「记一笔」，看板里就有这条，之后「检查回复」会帮你盯这家机构的来信（笔试、面试通知）。</p>`,
      buttons: [{label: '不投了', value: 'no'}, {label: '还没投完', value: 'later'}, {label: '投完了，记一笔', value: 'yes', kind: 'primary'}]});
    if (o.gen !== S.gen) return;
    if (ans === 'yes') recordApplication($('#wsRecordBtn'));
    else if (ans === 'later') S.wsOpened = {...o, at: Date.now()};
    else S.wsOpened = null;
  } finally { S.asking = false; }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) setTimeout(askWsDone, 400); });
window.addEventListener('focus', () => setTimeout(askWsDone, 400));

$('#wsAgentBtn').onclick = () => {
  const r = S.result;
  if (!r) return;
  const links = wsLinks(), k = collectKit() || {};
  const qa = [['self_intro_short', '自我介绍（短版）'], ['self_intro', '自我介绍（长版）'], ['why_this_role', '为什么申请这个岗位'], ['fit_points', '我和岗位的匹配点']]
    .filter(([key]) => k[key]).map(([key, label]) => `【${label}】${k[key]}`)
    .concat((k.custom_answers || []).map(a => `【${a.question}】${a.answer}`));
  agStart(`帮我填这家网申：${r.company_name || ''}｜${r.job_title || ''}\n` +
    (links.length ? `网申链接：${links[0].u}\n` : '网申链接：JD 里没找到，先问我要。\n') +
    (qa.length ? `开放问题用面板写好的这些回答（有字数上限就删减，不改意思）：\n${qa.join('\n')}\n` : '') +
    '按网申底稿和简历填，填完暂存，列出留空的和要我决定的。');
};
