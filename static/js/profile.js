// 「网申」页下半部分（我的资料）：简历内容、网申底稿（直接改、自动保存）。
// 文件末尾每 10 秒和服务器对一次底稿的定时器只用到本文件和 core.js 里的东西，所以留在这里，不放 core.js 的「启动」。
// ━━━ 我的资料（网申常用栏目，一键复制）━━━
async function loadKitPage() {
  loadWsProfile();
  loadIdcard();
  if (!S.kitData) {
    try { S.kitData = await api('/api/kit'); } catch (e) { $('#kitBody').innerHTML = `<p class="text-red-600 text-sm">${esc(e.message)}</p>`; return; }
  }
  const K = S.kitData;
  const row = (k, v) => { const todo = /\[待填\]/.test(v); return `<div class="kv"><span class="k">${esc(k)}</span><span class="v ${todo ? 'text-amber-700' : ''}">${esc(todo ? v + '（按岗位自己填）' : v)}</span>${v && !todo ? `<button class="copy-btn" data-copy="${esc(v)}" data-label="${esc(k)}">复制</button>` : ''}</div>`; };
  const pairs = (title, list, icon) => `<section class="bg-white rounded-2xl p-5 border shadow-sm"><h3 class="text-sm font-bold mb-2 flex items-center gap-2"><span class="material-symbols-outlined text-teal-600">${icon}</span>${esc(title)}</h3>${(list || []).map(([k, v]) => row(k, v)).join('')}</section>`;
  const objs = (title, list, icon) => `<section class="bg-white rounded-2xl p-5 border shadow-sm ${title === '实习经历' ? 'lg:col-span-2' : ''}"><h3 class="text-sm font-bold mb-2 flex items-center gap-2"><span class="material-symbols-outlined text-teal-600">${icon}</span>${esc(title)}</h3>` +
    (list || []).map(o => `<div class="mb-4 p-3 rounded-xl bg-slate-50/70">${Object.entries(o).map(([k, v]) => Array.isArray(v)
        ? `<div class="kv"><span class="k">${esc(k)}</span><div class="v space-y-1.5">${v.map(b => `<div class="flex gap-2 items-start"><span class="flex-1">${esc(b)}</span><button class="copy-btn" data-copy="${esc(b)}" data-label="这一条">复制</button></div>`).join('')}</div><button class="copy-btn" data-copy="${esc(v.join('\n'))}" data-label="${esc(k)}（全部）">全部</button></div>`
        : row(k, v)).join('')}</div>`).join('') + '</section>';
  $('#kitBody').innerHTML = pairs('基本信息', K['基本信息'], 'person') + pairs('求职意向', K['求职意向'], 'flag') +
    objs('教育经历', K['教育经历'], 'school') + pairs('技能与其他', K['技能与其他'], 'construction') + objs('实习经历', K['实习经历'], 'work') +
    `<p class="lg:col-span-2 text-[11px] text-slate-400">${esc(K['_说明'] || '')}</p>`;
}

// ━━━ 网申底稿（简历以外、网申会问的个人信息；直接改，自动保存）━━━
const WS_SECTIONS = [['基本信息', 'person'], ['联系方式', 'call'], ['高中', 'school'], ['家庭成员', 'family_restroom'],
  ['经历精确日期', 'event'], ['项目经历', 'code'], ['资格与考试', 'workspace_premium'], ['求职偏好与声明', 'tune'], ['其他', 'notes']];
let wsSaveTimer = null;
async function loadWsProfile() {
  if (wsSaveTimer) return;   // 正在等自动保存：别用服务器上的旧内容冲掉刚打的字
  let d;
  try { d = await api('/api/wangshen-profile'); } catch (e) { $('#wsProfileBody').innerHTML = `<p class="text-red-600 text-sm">${esc(e.message)}</p>`; return; }
  S.wsProfile = d.profile;
  S.wsProfileBase = JSON.parse(JSON.stringify(d.profile));   // 打开时的样子：保存时只提交改过的格子
  renderWsAlert(d.missing, d.notes, d.example);
  const input = (sec, i, k, v, sub) => {
    const attrs = `class="wsf field !py-1.5 ${v.trim() ? '' : 'empty'}" data-sec="${esc(sec)}" data-i="${i}" data-k="${esc(k)}"${sub ? ' data-sub="1"' : ''}`;
    return v.length > 36 || /自我评价|兴趣|作品|地址|证明人/.test(k) ? `<textarea rows="1" ${attrs} style="resize:none">${esc(v)}</textarea>` : `<input ${attrs} value="${esc(v)}"/>`;
  };
  const row = (sec, i, k, v, sub) => `<div class="kv items-center"><span class="k">${esc(k)}</span><div class="flex-1 min-w-0">${input(sec, i, k, v, sub)}</div><button class="copy-btn wsf-copy">复制</button></div>`;
  $('#wsProfileBody').innerHTML = WS_SECTIONS.map(([sec, icon]) => {
    const items = d.profile[sec] || [];
    const body = items.map((it, i) => Array.isArray(it) ? row(sec, i, it[0], it[1] || '', false)
      : `<div class="mb-3 p-3 rounded-xl bg-slate-50/70">${Object.entries(it).map(([k, v]) => row(sec, i, k, v || '', true)).join('')}</div>`).join('');
    const idNote = sec === '基本信息' ? '<div class="kv"><span class="k">证件号码</span><span class="v text-slate-400">不存，每次网申你自己填</span></div>' : '';
    return `<section class="bg-white rounded-2xl p-5 border shadow-sm ${['家庭成员', '项目经历', '其他'].includes(sec) ? 'lg:col-span-2' : ''}"><h3 class="text-sm font-bold mb-2 flex items-center gap-2"><span class="material-symbols-outlined text-teal-600">${icon}</span>${esc(sec)}</h3>${body}${idNote}</section>`;
  }).join('');
  $('#wsProfileBody').querySelectorAll('textarea').forEach(autoGrow);
}
function renderWsAlert(missing, notes, example) {
  const parts = [];
  if (example) parts.push('<p class="font-bold text-amber-800 mb-1">现在显示的是示例底稿，改完会存成你自己的。</p>');
  if (missing.length) parts.push(`<p class="font-bold text-amber-800 mb-1">还缺 ${missing.length} 项（下面标黄的框；网申要填时助手会留空等你）：</p><p class="text-xs text-amber-900">${missing.map(esc).join('、')}</p>`);
  if (notes.length) parts.push(`<p class="font-bold text-amber-800 mt-2 mb-1">牛客在线简历要核对的地方（插件按牛客里的资料填，那边错了每家都会错）：</p><ul class="list-disc pl-5 text-xs text-amber-900 space-y-1">${notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>`);
  $('#wsProfileAlert').innerHTML = parts.join('');
  $('#wsProfileAlert').classList.toggle('hidden', !parts.length);
}
function collectWsProfile() {
  const p = JSON.parse(JSON.stringify(S.wsProfile || {}));
  $('#wsProfileBody').querySelectorAll('.wsf').forEach(el => {
    const items = p[el.dataset.sec], i = +el.dataset.i;
    if (!items || !items[i]) return;
    if (el.dataset.sub) items[i][el.dataset.k] = el.value; else items[i][1] = el.value;
  });
  return p;
}
$('#wsProfileBody').addEventListener('input', e => {
  const el = e.target.closest('.wsf'); if (!el) return;
  if (el.tagName === 'TEXTAREA') autoGrow(el);
  el.classList.toggle('empty', !el.value.trim());
  $('#wsProfileSaved').textContent = '有改动，正在保存…';
  clearTimeout(wsSaveTimer);
  wsSaveTimer = setTimeout(saveWsProfile, 900);
});
$('#wsProfileBody').addEventListener('click', e => {
  const b = e.target.closest('.wsf-copy'); if (!b) return;
  const el = b.closest('.kv').querySelector('.wsf');
  if (el && el.value.trim()) copyText(el.value, b.closest('.kv').querySelector('.k').textContent); else toast('这一项还空着');
});
const wsStrip = p => JSON.stringify(Object.fromEntries(Object.entries(p || {}).filter(([k]) => !k.startsWith('_'))));
const wsShape = p => JSON.stringify(Object.entries(p || {}).filter(([k]) => !k.startsWith('_')).map(([k, v]) => [k, Array.isArray(v) ? v.length : -1]));
function wsFocused() { const a = document.activeElement; return a && $('#wsProfileBody').contains(a) && a.classList.contains('wsf') ? a : null; }
function wsWriteBack(p, skip) {   // 服务器的最新值写回格子（正在编辑的那个格子不动），黄框跟着重画
  $('#wsProfileBody').querySelectorAll('.wsf').forEach(el => {
    if (el === skip) return;
    const items = p[el.dataset.sec], i = +el.dataset.i;
    if (!items || !items[i]) return;
    const v = String((el.dataset.sub ? items[i][el.dataset.k] : items[i][1]) ?? '');
    if (el.value !== v) { el.value = v; if (el.tagName === 'TEXTAREA') autoGrow(el); }
    el.classList.toggle('empty', !v.trim());
  });
}
async function saveWsProfile() {
  let outside = false;
  try {
    const mine = collectWsProfile();
    const d = await api('/api/wangshen-profile', {method: 'PUT', body: {profile: mine, base: S.wsProfileBase || {}}});
    outside = wsStrip(d.profile) !== wsStrip(mine);   // 别处（助手 / 另一个窗口）同时改过
    if (outside && wsShape(d.profile) !== wsShape(mine)) {   // 行数变了：等你不在编辑时整页重画（先别换基准，免得格子对错位）
      S.wsNeedsRender = true;
    } else {
      if (outside) wsWriteBack(d.profile, wsFocused());   // 把别处的改动写回没在编辑的格子，下次保存就不会把它冲掉
      S.wsProfile = d.profile; S.wsProfileBase = JSON.parse(JSON.stringify(d.profile));
    }
    renderWsAlert(d.missing, d.notes || [], false);
    $('#wsProfileSaved').textContent = '已保存 ' + new Date().toTimeString().slice(0, 5);
  } catch (e) {
    $('#wsProfileSaved').textContent = '没保存：' + e.message;
    toast('底稿没保存：' + e.message, 6000);
  } finally {
    wsSaveTimer = null;
    if (S.wsNeedsRender && !wsFocused()) { S.wsNeedsRender = false; loadWsProfile(); }
  }
}

// 别处改了底稿（助手、另一个窗口、直接改文件）：停在「网申」页时每 10 秒看一眼，你没在打字就刷新显示（「还缺几项」也跟着更新）
setInterval(async () => {
  if (document.hidden || S.tab !== 'kit' || wsSaveTimer) return;
  try {
    const d = await api('/api/wangshen-profile');
    renderWsAlert(d.missing, d.notes || [], d.example);   // 「还缺几项」不管光标在哪都按服务器结果更新
    const focused = wsFocused();
    if (wsStrip(d.profile) === wsStrip(S.wsProfile) && !S.wsNeedsRender) return;
    if (!focused) { S.wsNeedsRender = false; return loadWsProfile(); }
    if (wsShape(d.profile) !== wsShape(S.wsProfile)) { S.wsNeedsRender = true; return; }   // 行数变了：等你离开格子再重画
    wsWriteBack(d.profile, focused);   // 只更新没在编辑的格子
    S.wsProfile = d.profile; S.wsProfileBase = JSON.parse(JSON.stringify(d.profile));
  } catch (e) {}
}, 10000);

// ━━━ 证件号：只存在这台 Mac 的钥匙串里，页面上只看得到打码的样子 ━━━
async function loadIdcard() {
  let d;
  try { d = await api('/api/idcard'); } catch (e) { $('#idcardBox').innerHTML = `<p class="text-red-600">${esc(e.message)}</p>`; return; }
  const how = '网申页上「等你·证件号」那条点「复制证件号」：号码直接进这台 Mac 的剪贴板（60 秒后自动清掉），你在网页那一栏 ⌘V，再点「我填好了」。助手不碰这一栏。';
  if (!(S.cfg && S.cfg.local)) {   // 手机（隧道）上：不给输入框，号码不走隧道
    $('#idcardBox').innerHTML = `<div class="flex items-center gap-2"><span class="material-symbols-outlined text-teal-600">badge</span><b>证件号</b>
      <span>${d.saved ? esc(d.masked) + '（已存在电脑的钥匙串里）' : '还没存'}</span></div><p class="text-[11px] text-slate-500 mt-1">证件号只能在电脑上存、在电脑上复制。</p>`;
    return;
  }
  $('#idcardBox').innerHTML = `<div class="flex items-center gap-2 flex-wrap"><span class="material-symbols-outlined text-teal-600">badge</span><b>证件号</b>
      ${d.saved ? `<span class="font-mono">${esc(d.masked)}</span><span class="text-xs text-slate-400">只存在这台 Mac 的钥匙串里</span>
        <button id="idcardChange" class="copy-btn ml-auto">换一个</button><button id="idcardForget" class="copy-btn">删掉</button>`
      : `<input id="idcardInput" type="password" autocomplete="off" class="field !w-64 !py-1.5" placeholder="18 位身份证号（只存钥匙串）"/><button id="idcardSave" class="px-3 py-1.5 bg-primary text-white rounded-lg text-xs font-bold">存进钥匙串</button>`}</div>
    <p class="text-[11px] text-slate-500 mt-1">${esc(how)}</p>`;
}
document.addEventListener('click', async e => {
  if (e.target.closest('#idcardSave')) {
    const inp = $('#idcardInput');
    try { const d = await api('/api/idcard', {method: 'PUT', body: {number: inp.value}}); inp.value = ''; toast('存好了：' + d.masked, 3000); loadIdcard(); }
    catch (er) { toast(er.message, 6000); }
  }
  if (e.target.closest('#idcardChange')) {
    $('#idcardBox').querySelector('#idcardChange').outerHTML = '<input id="idcardInput" type="password" autocomplete="off" class="field !w-64 !py-1.5 ml-auto" placeholder="新的 18 位号码"/><button id="idcardSave" class="px-3 py-1.5 bg-primary text-white rounded-lg text-xs font-bold">存进钥匙串</button>';
  }
  if (e.target.closest('#idcardForget')) {
    try { await api('/api/idcard', {method: 'DELETE'}); toast('删掉了', 2000); loadIdcard(); } catch (er) { toast(er.message, 6000); }
  }
});
async function copyIdcard(appId) {   // 网申页、看板上「复制证件号」：放进本机剪贴板（号码不经过网页），顺手把 Chrome 切到这家的网页
  try {
    const d = await api('/api/idcard/copy', {method: 'POST', body: {app_id: appId || ''}});
    toast(d.jumped ? `已复制，Chrome 已切到那个网页：光标在证件号那一栏，按 ⌘V（${d.clear_after} 秒后剪贴板自动清掉）。助手看到填好了会自己接着做`
      : `证件号已复制（${d.clear_after} 秒后自动清掉）：去那一栏按 ⌘V，助手看到填好了会自己接着做`, 8000);
  } catch (er) { toast(er.message, 8000); }
}
