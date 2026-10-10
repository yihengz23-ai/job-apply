// 「投递看板」页（#tab-board）：列表、筛选、详情里改记录、检查回复 / Gmail 同步。
// ━━━ 看板 ━━━
const STATUS_CLS = {'草稿': 'bg-slate-200 text-slate-600', '已投递': 'bg-blue-100 text-blue-700', '笔试': 'bg-cyan-100 text-cyan-800', '已电联': 'bg-indigo-100 text-indigo-700', '面试中': 'bg-amber-100 text-amber-800', 'offer': 'bg-green-100 text-green-700', '拒绝': 'bg-red-100 text-red-700', '无回复': 'bg-slate-100 text-slate-500'};
const statusBadge = s => `<span class="chip ${STATUS_CLS[s] || 'bg-slate-100 text-slate-600'}">${esc(s)}</span>`;
const REPLY_CLS = {'有回复': 'bg-green-600 text-white', '退信': 'bg-red-600 text-white', '来信': 'bg-amber-100 text-amber-800', '自动回复': 'bg-slate-200 text-slate-600'};
const replyBadge = r => r.reply_status ? `<span class="chip ${REPLY_CLS[r.reply_status] || 'bg-slate-200 text-slate-600'}" title="${esc((r.reply_at || '') + ' ' + (r.reply_from || '') + '：' + (r.reply_snippet || ''))}">${esc(r.reply_status === '退信' ? '退信·没送达' : r.reply_status)}</span>` : '';
const posBadge = p => p ? `<span class="chip ${p === '全职' ? 'bg-purple-100 text-purple-700' : p === '留用实习' ? 'bg-teal-100 text-teal-700' : 'bg-slate-100 text-slate-600'}">${esc(p)}</span>` : '';

function metric(label, value, color = '#005bbf', tip = []) {
  const tipHtml = tip.length ? `<div class="mc-tip hidden absolute left-0 top-full mt-2 z-50 bg-slate-800 text-white text-[11px] rounded-xl px-4 py-3 shadow-xl max-h-64 overflow-y-auto w-64">${tip.map(t => `<div class="py-0.5">${esc(t)}</div>`).join('')}</div>` : '';
  return `<div class="relative bg-white p-4 rounded-2xl ${tip.length ? 'cursor-pointer has-tip' : ''}"><p class="text-[10px] font-bold text-slate-400">${esc(label)}</p><h3 class="text-3xl font-bold mt-1" style="color:${color}">${esc(value)}</h3>${tipHtml}</div>`;
}

function bindTips(root) {
  root.querySelectorAll('.has-tip').forEach(el => {
    el.onmouseenter = () => el.querySelector('.mc-tip')?.classList.remove('hidden');
    el.onmouseleave = () => el.querySelector('.mc-tip')?.classList.add('hidden');
  });
}

async function loadBoard() {
  const camp = $('#fCampaign').value;
  try {
    const [recs, stats] = await Promise.all([api('/api/records?campaign=' + encodeURIComponent(camp)), api('/api/stats?campaign=' + encodeURIComponent(camp))]);
    S.records = recs;
    $('#boardMetrics').innerHTML = metric('总投递', stats.total) + metric('本周新增', '+' + stats.week_new) +
      metric('有回复', stats.replied, '#16a34a') + metric('待跟进（>7天无回复）', stats.followup.length, '#b45309', stats.followup) +
      (stats.bounced.length ? metric('退信·没送达（换邮箱重投）', stats.bounced.length, '#dc2626', stats.bounced) : '') +
      (stats.excel && !stats.excel.ok ? `<div class="col-span-full text-xs text-amber-700 bg-amber-50 rounded-xl p-2">桌面的 Excel 镜像没同步上（${esc(stats.excel.error)}）。多半是 Excel 正开着这个文件：关掉后下次保存会自动补上。</div>` : '');
    bindTips($('#boardMetrics'));
    const keep = (id, vals) => { const sel = $(id), cur = sel.value, first = sel.options[0].outerHTML; sel.innerHTML = first + vals.map(v => opt(v, v, cur)).join(''); };
    keep('#fType', Object.keys(stats.type_dist)); keep('#fCity', Object.keys(stats.loc_dist));
    renderBoard();
  } catch (e) { toast('加载看板失败：' + e.message); }
  if (typeof loadApps === 'function') loadApps();   // 上半部分：今天、按申请看（apps.js；没取到也不影响下面的老表格）
}

const normCity = l => { l = (l || '').trim(); if (!l || l.startsWith('[') || ['未注明', '不限', '不明确'].includes(l)) return ''; for (const c of ['上海', '北京', '深圳', '杭州', '香港', '广州', '苏州', '南京', '成都', '新加坡']) if (l.includes(c)) return c; if (l.includes('线上') || l.includes('远程')) return '远程'; return l.split('/')[0].split('·')[0].trim().slice(0, 4); };

function renderBoard() {
  const q = $('#fSearch').value.trim().toLowerCase();
  const fs = $('#fStatus').value, fp = $('#fPosition').value, ft = $('#fType').value, fc = $('#fCity').value;
  const list = S.records.filter(r => (!fs || r.status === fs) && (!fp || (r.position_type || '不明确') === fp) &&
    (!ft || r._type === ft || r.company_type === ft) && (!fc || normCity(r.job_location) === fc) &&
    (!q || [r.company_name, r.job_title, r.to_email, r.cc_email, r.subject, r.focus_industry, r.job_location, r.job_source, r.notes, r.email_body].join(' ').toLowerCase().includes(q)));
  if (typeof AP === 'undefined' || AP.view !== 'app') $('#recordCount').textContent = `显示 ${list.length} / ${S.records.length} 条`;
  $('#boardTable').innerHTML = list.map(r => `
    <tr class="hover:bg-white/70 cursor-pointer border-b border-slate-100" data-rid="${esc(r.id)}">
      <td class="px-5 py-3"><div class="flex items-center gap-2 flex-wrap"><span class="font-semibold">${esc(r.company_name || '—')}</span>${replyBadge(r)}</div></td>
      <td class="px-5 py-3">${esc(r.job_title || '—')}</td>
      <td class="px-4 py-3">${posBadge(r.position_type)}</td>
      <td class="px-4 py-3">${statusBadge(r.status || '已投递')}</td>
      <td class="px-4 py-3 text-slate-500">${esc(r.job_location || '—')}</td>
      <td class="px-4 py-3 text-slate-500">${esc(r.focus_industry || '—')}</td>
      <td class="px-4 py-3 text-right text-slate-400 whitespace-nowrap">${esc(r.sent_at || '—')}${r.send_mode === '草稿' ? ' <span class="chip bg-slate-200 text-slate-600">草稿</span>' : r.send_mode === '未发邮件' ? ' <span class="chip bg-slate-100 text-slate-500">网申</span>' : ''}</td>
    </tr>
    <tr id="detail-${esc(r.id)}" class="row-detail"><td colspan="7" class="p-0">${detailHtml(r)}</td></tr>`).join('');
  $('#boardCards').innerHTML = list.map(r => `
    <div class="bg-white rounded-xl p-4 shadow-sm border" data-mid="${esc(r.id)}">
      <div class="flex items-center justify-between gap-2"><div><p class="font-semibold text-sm">${esc(r.company_name || '—')} ${replyBadge(r)}</p><p class="text-xs text-slate-400">${esc(r.job_title || '—')}</p></div>${statusBadge(r.status || '已投递')}</div>
      <div class="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-[11px] text-slate-400">${posBadge(r.position_type)}<span>${esc(r.job_location)}</span><span>${esc(r.sent_at)}</span></div>
      <div class="mob-detail hidden mt-3 pt-3 border-t">${detailHtml(r, true)}</div>
    </div>`).join('');
}
['#fStatus', '#fPosition', '#fType', '#fCity'].forEach(s => $(s).addEventListener('change', renderBoard));
$('#fSearch').addEventListener('input', renderBoard);
$('#fCampaign').addEventListener('change', loadBoard);

const EDIT_DEFAULTS = {status: '已投递', position_type: '不明确', reply_status: ''};  // 旧记录没填时下拉框显示的值
function detailHtml(r, mobile = false) {
  const atts = (r.attachments && r.attachments.length) ? r.attachments.map(a => `${a.kind}（${a.version}）：${a.filename}`) :
    [r.resume_version ? '简历：' + r.resume_version : '', r.attach_report === true ? '研究样本' : ''].filter(Boolean);
  const gmailLink = r.gmail_thread_id ? `<a class="text-primary hover:underline" target="_blank" rel="noopener" href="https://mail.google.com/mail/u/0/#all/${encodeURIComponent(r.gmail_thread_id)}">在 Gmail 打开</a>` : '';
  const au = safeUrl(r.apply_url), su = safeUrl(r.source_url);
  const links = [gmailLink, au ? `<a class="text-primary hover:underline" target="_blank" rel="noopener" href="${esc(au)}">网申链接</a>` : '', su ? `<a class="text-primary hover:underline" target="_blank" rel="noopener" href="${esc(su)}">原文</a>` : ''].filter(Boolean).join(' ｜ ');
  const ws = r.wangshen || {};
  const wsQA = [['自我介绍（短）', ws.self_intro_short], ['自我介绍', ws.self_intro], ['为什么申请', ws.why_this_role], ['匹配点', ws.fit_points]]
    .concat((ws.custom_answers || []).map(a => [a.question, a.answer])).filter(x => x[1]);
  const wsHtml = wsQA.length ? `<details class="text-xs"><summary class="cursor-pointer text-teal-700 font-bold">网申时填的问答（面试前回看）${ws.platform ? ' · ' + esc(ws.platform) : ''}</summary>
      <div class="mt-2 space-y-2">${wsQA.map(([q, a]) => `<div><p class="text-slate-400 font-bold">${esc(q)}</p><p class="whitespace-pre-wrap">${esc(a)}</p></div>`).join('')}</div></details>` : '';
  const reply = r.reply_status ? `<div class="p-3 rounded-xl ${r.reply_status === '有回复' ? 'bg-green-50 text-green-800' : r.reply_status === '退信' ? 'bg-red-50 text-red-700' : 'bg-slate-50 text-slate-600'} text-xs"><b>${esc(r.reply_status)}</b> ${esc(r.reply_at)} ${esc(r.reply_from)}<br>${esc(r.reply_snippet)}</div>` : '';
  const sel = (key, vals) => {  // 旧记录里不在选项中的值也列出来，免得保存别的栏目时被悄悄改掉
    const cur = r[key] || EDIT_DEFAULTS[key], all = vals.includes(cur) ? vals : [cur, ...vals];
    return `<select data-k="${key}" class="field mt-1">${all.map(v => opt(v, v || '（没有）', cur)).join('')}</select>`;
  };
  const inp = (key, ph = '') => `<input data-k="${key}" class="field mt-1" value="${esc(r[key])}" placeholder="${esc(ph)}"/>`;
  const lbl = (t, x) => `<label class="block text-[11px] font-bold text-slate-400">${esc(t)}${x}</label>`;
  const edit = `<div class="space-y-2 edit-box" data-id="${esc(r.id)}">
      ${lbl('状态', sel('status', S.cfg.statuses))}${lbl('岗位类型', sel('position_type', S.cfg.position_types))}
      ${lbl('回复（判断错了可以改，改过就不再自动覆盖）', sel('reply_status', ['', '有回复', '来信', '自动回复', '退信']))}
      ${lbl('来源', inp('job_source', '公众号 / Boss / 官网'))}${lbl('地点', inp('job_location'))}${lbl('行业', inp('focus_industry'))}
      ${lbl('截止日期', inp('deadline', 'YYYY-MM-DD'))}
      ${r.send_mode === '未发邮件' || (r.apply_channel || '').includes('网申') || r.apply_account ? lbl('申请账号（注册网申账号的手机号 / 邮箱）', inp('apply_account', '手机号或邮箱')) : ''}
      ${lbl('备注', `<textarea data-k="notes" class="field mt-1" rows="4" placeholder="面试时间、联系人…">${esc(r.notes)}</textarea>`)}
      <div class="flex gap-2 pt-1"><button class="save-btn flex-1 bg-primary text-white py-2 rounded-xl text-xs font-bold">保存</button><button class="del-btn px-4 border-2 border-red-200 text-red-600 rounded-xl text-xs font-bold">删除</button></div></div>`;
  const content = `<div class="space-y-3">
      <div class="text-[11px] text-slate-400 flex flex-wrap gap-x-3">${r.campaign ? `<span>批次：${esc(r.campaign)}</span>` : ''}<span>方式：${esc(r.send_mode)}</span>${r.apply_account ? `<span>申请账号：${esc(r.apply_account)}</span>` : ''}${r.status_updated_at ? `<span>状态更新 ${esc(r.status_updated_at)}</span>` : ''}${r.job_post_date ? `<span>岗位发布 ${esc(r.job_post_date)}</span>` : ''}${links ? `<span>${links}</span>` : ''}</div>
      ${r.site_status ? `<div class="p-3 rounded-xl bg-cyan-50 text-cyan-900 text-xs"><b>网站上的进度：</b>${esc(r.site_status)}${r.site_status_at ? `<span class="text-cyan-700/70">（${esc(r.site_status_at)} 查）</span>` : ''}</div>` : ''}
      ${reply}${r.ws_submitted ? `<details class="text-xs"><summary class="cursor-pointer text-teal-700 font-bold">网申实际提交的内容（从网站读回来的原文）</summary><div class="mt-2 bg-teal-50/50 p-3 rounded-xl whitespace-pre-wrap max-h-96 overflow-y-auto">${esc(r.ws_submitted)}</div></details>`
        : (r.send_mode === '未发邮件' && r.status !== '草稿' && r.campaign === (S.cfg && S.cfg.campaign)) ? '<p class="text-xs text-slate-400">网申实际提交的内容还没从网站读回</p>' : ''}${wsHtml}
      <div class="grid ${mobile || r.send_mode === '未发邮件' ? 'grid-cols-1' : 'grid-cols-2'} gap-4">
        ${r.send_mode === '未发邮件' ? '' : `<div><h4 class="text-[11px] font-bold text-slate-400 mb-2">发出的邮件</h4>
          <div class="bg-blue-50/60 p-3 rounded-xl text-xs leading-relaxed max-h-72 overflow-y-auto border border-blue-100">
            ${r.to_email ? `<div class="mb-1"><span class="text-blue-400">收件人：</span>${esc(r.to_email)}</div>` : ''}
            ${r.cc_email ? `<div class="mb-1"><span class="text-purple-400">抄送：</span>${esc(r.cc_email)}</div>` : ''}
            ${r.subject ? `<div class="mb-2 pb-2 border-b border-blue-100"><span class="text-blue-400">标题：</span>${esc(r.subject)}</div>` : ''}
            ${escBr(r.email_body || '（无）')}</div>
          <div class="mt-2 text-[11px] text-slate-500">附件：${esc(atts.join('；') || '—')}</div>
          ${r.issues_at_send && r.issues_at_send.length ? `<div class="mt-1 text-[11px] text-amber-700">发送时的提示：${esc(r.issues_at_send.join('；'))}</div>` : ''}</div>`}
        <div><h4 class="text-[11px] font-bold text-slate-400 mb-2">JD 原文</h4>
          <div class="bg-slate-50 p-3 rounded-xl text-xs leading-relaxed max-h-80 overflow-y-auto">${escBr(r.jd_text || '（无）')}</div></div>
      </div></div>`;
  return mobile ? `<div class="space-y-4">${edit}${content}</div>` :
    `<div class="mx-3 mb-3 rounded-2xl border bg-white/90 overflow-hidden animate-in"><div class="grid grid-cols-12"><div class="col-span-3 p-5 border-r bg-slate-50/60">${edit}</div><div class="col-span-9 p-5">${content}</div></div></div>`;
}

document.addEventListener('click', async e => {
  const tr = e.target.closest('tr[data-rid]');
  if (tr) { $('#detail-' + CSS.escape(tr.dataset.rid))?.classList.toggle('open'); return; }
  const mc = e.target.closest('[data-mid]');
  if (mc && !e.target.closest('.mob-detail')) { mc.querySelector('.mob-detail').classList.toggle('hidden'); return; }
  const box = e.target.closest('.edit-box');
  if (!box) return;
  const id = box.dataset.id;
  if (e.target.closest('.save-btn')) {
    const orig = S.records.find(x => x.id === id) || {};
    const data = {};
    box.querySelectorAll('[data-k]').forEach(el => {
      const k = el.dataset.k, cur = String(orig[k] ?? '') || EDIT_DEFAULTS[k] || '';
      if (el.value !== cur) data[k] = el.value;
    });
    if (!Object.keys(data).length) return toast('没有改动', 1500);
    try { await api('/api/records/' + encodeURIComponent(id), {method: 'PUT', body: data}); toast('已保存'); loadBoard(); }
    catch (err) { toast('保存失败：' + err.message); }
  }
  if (e.target.closest('.del-btn')) {
    const ok = await modal({title: '删除这条记录？', html: '<p>只删除看板里的记录，不会撤回已发出的邮件。</p>', buttons: [{label: '取消', value: false}, {label: '删除', value: true, kind: 'danger'}]});
    if (!ok) return;
    try { await api('/api/records/' + encodeURIComponent(id), {method: 'DELETE'}); toast('已删除'); loadBoard(); }
    catch (err) { toast('删除失败：' + err.message); }
  }
});

async function runJob(btn, url, label, body = {}) {
  setBusy(btn, true, label);
  try {
    const d = await api(url, {method: 'POST', body});
    await modal({title: '完成', html: `<pre class="text-xs bg-slate-50 p-3 rounded-xl whitespace-pre-wrap">${esc((d.logs || []).join('\n'))}</pre>`, buttons: [{label: '关闭', value: true, kind: 'primary'}]});
    loadBoard();
  } catch (e) {
    toast(e.message, 8000);
    if (e.status === 401) refreshGmail();
  } finally { setBusy(btn, false); }
}
$('#repliesBtn').onclick = () => runJob($('#repliesBtn'), '/api/check-replies', '检查中…', {campaign: $('#fCampaign').value});
$('#syncBtn').onclick = () => runJob($('#syncBtn'), '/api/gmail-sync', '同步中…');
