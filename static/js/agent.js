// 右下角助手：对话框、对话列表、轮询新消息。
// 对话标题和颜色要用 ws.js 里的 WT、wtColor、hostOf（ws.js 在后面加载），所以定时轮询和打开面板时的那次查询放在 core.js 末尾的「启动」里。
// ━━━ 助手（面板里聊天；它能操作你的 Chrome 代填网申，不点提交、不发邮件）━━━
const AG = {id: null, n: 0, running: false, open: false, busy: false};
const agMd = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
  .replace(/(https?:\/\/[^\s<&]+)/g, '<a class="text-primary underline break-all" target="_blank" rel="noopener" href="$1">$1</a>').replace(/\n/g, '<br>');
function agAppend(msgs) {
  const box = $('#agentMsgs'), atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  box.querySelector('.ag-empty')?.remove();
  for (const m of msgs) {
    const el = document.createElement('div');
    if (m.role === 'user') {
      el.className = 'ag-user' + (m.queued ? ' opacity-60' : '') + (m.cancelled ? ' opacity-40 line-through' : ''); el.textContent = m.text;
      el.title = m.queued ? '排队中：轮到它会自动开始' : m.midturn ? '它干活时说的，下一步就会看到' : '';
    }
    else if (m.role === 'assistant') { el.className = 'ag-bot'; el.innerHTML = agMd(m.text); }
    else if (m.role === 'tool') { el.className = 'ag-tool' + (m.error ? ' !text-red-500' : ''); el.innerHTML = `<span class="material-symbols-outlined !text-[13px]">${m.error ? 'error' : 'chevron_right'}</span><span>${esc(m.text)}</span>`; }
    else { el.className = 'ag-sys' + (m.error ? ' !text-red-700 !bg-red-50' : ''); el.textContent = m.text; }
    box.appendChild(el);
  }
  if (atBottom || msgs.some(m => m.role === 'user')) box.scrollTop = box.scrollHeight;
}
function agEmptyHint() {
  $('#agentMsgs').innerHTML = '<div class="ag-empty text-xs text-slate-400 space-y-1.5"><p>可以这样说：</p><p>· 帮我填这个网申：https://…</p><p>· 我这周投了哪些？A公司投过没有？</p><p>· 刚才那张表接着填，身份证号我已经填好了。</p></div>';
}
const AGC = {active: 0, waiting: 0};   // 所有对话里：几个在干活、几个在排队（网申页轮询时顺带更新）
function agFab() {
  const n = Math.max(AGC.active, AG.running ? 1 : 0);
  $('#agentFabLabel').textContent = (n ? `助手 · ${n > 1 ? n + ' 个' : ''}正在做` : '助手') + (AGC.waiting ? ` · ${AGC.waiting} 个排队` : '');
  $('#agentFab').classList.toggle('!bg-teal-600', n > 0);
}
function agSetRunning(on, waiting = '') {
  AG.running = on; AG.waiting = waiting || '';
  $('#agentStatus').classList.toggle('hidden', !on && !AG.waiting);
  $('#agentStatusText').textContent = on || !AG.waiting ? '正在做；想改什么直接说，它下一步就能看到。做完会弹通知' : `排队中：${AG.waiting}`;
  $('#agentStatus .animate-spin').classList.toggle('hidden', !on && !!AG.waiting);
  $('#agentStop').textContent = !on && AG.waiting ? '取消排队' : '停止';
  $('#agentInput').placeholder = on ? '它正在做；想让它改什么直接说（⌘+回车 发送）' : AG.waiting ? '排着队；还想补充什么也可以先说（⌘+回车 发送）' : '比如：帮我填这个网申 https://…（⌘+回车 发送）';
  agFab();
}
function agChatLabel(c) {   // 网申对话：颜色圆点 + 公司（没有就网站名）+ 岗位，和「网申」页那一行对得上
  const t = c.task_id && (WT.items || []).find(x => x.id === c.task_id);
  if (!t) return (c.running ? '● ' : '') + c.title;
  return `${wtColor(t)[0]} ${t.company || hostOf(t.url) || c.title}${t.job ? '｜' + t.job : ''}` + (c.running ? ' · 在做' : '');
}
function agTint() {   // 对话框左边一条这家网申的颜色
  const c = (AG.list || []).find(x => x.id === AG.id), t = c && c.task_id && (WT.items || []).find(x => x.id === c.task_id);
  $('#agentDrawer').style.borderLeft = t ? `6px solid ${wtColor(t)[1]}` : '';
}
async function agLoadList() {
  const d = await api('/api/agent');
  AG.list = d.chats;
  $('#agentChats').innerHTML = '<option value="">新对话</option>' + d.chats.map(c => `<option value="${esc(c.id)}">${esc(agChatLabel(c))}</option>`).join('');
  $('#agentChats').value = AG.id || '';
  agTint();
  return d;
}
async function agOpenChat(id) {
  AG.id = id || null; AG.n = 0; agTint();
  if (!AG.id) { agEmptyHint(); agSetRunning(false); return; }
  $('#agentMsgs').innerHTML = '';
  await agPoll();
}
async function agPoll() {
  if (!AG.id || AG.busy) return;
  AG.busy = true;
  const id = AG.id;
  try {
    const d = await api(`/api/agent/${id}?since=${AG.n}`);
    if (id !== AG.id) return;   // 期间换了对话
    agAppend(d.messages); AG.n = d.total;
    if (AG.running && !d.running) agLoadList().catch(() => {});
    const wasWaiting = AG.waiting;
    agSetRunning(d.running, d.waiting);
    if (wasWaiting && !d.waiting) { AG.n = 0; $('#agentMsgs').innerHTML = ''; AG.busy = false; return agPoll(); }   // 轮到了：重画一遍（去掉「排队中」的样子）
  } catch (e) { if (e.status === 404) { AG.id = null; agEmptyHint(); agLoadList().catch(() => {}); } }
  finally { AG.busy = false; }
}
async function agShow(open) {
  AG.open = open;
  $('#agentDrawer').classList.toggle('open', open);
  if (!open) return;
  const d = await agLoadList();
  if (!AG.id && d.running) await agOpenChat(d.running);
  else if (!AG.id) agEmptyHint();
  else if (!$('#agentMsgs').children.length) await agOpenChat(AG.id);
  setTimeout(() => $('#agentInput').focus(), 250);
}
async function agSend(text, fresh = false) {
  text = (text || '').trim();
  if (!text) return;
  try {
    if (fresh || !AG.id) { const c = await api('/api/agent/new', {method: 'POST'}); AG.id = c.id; AG.n = 0; $('#agentMsgs').innerHTML = ''; }
    const d = await api(`/api/agent/${AG.id}/send`, {method: 'POST', body: {text}});
    agAppend(d.messages.slice(AG.n)); AG.n = d.total; agSetRunning(!d.queued, d.queued);
    if (d.queued) toast('排队中：' + d.queued, 5000);
    $('#agentInput').value = '';
    agLoadList().catch(() => {});
  } catch (e) { toast(e.message, 5000); }
}
async function agStart(text) { await agShow(true); await agSend(text, true); }   // 「让助手代填」：开一个新对话
$('#agentFab').onclick = () => agShow(!AG.open);
$('#agentClose').onclick = () => agShow(false);
$('#agentNew').onclick = () => { agOpenChat(null); $('#agentChats').value = ''; $('#agentInput').focus(); };
$('#agentChats').onchange = e => agOpenChat(e.target.value);
$('#agentSend').onclick = () => agSend($('#agentInput').value);
$('#agentInput').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); agSend($('#agentInput').value); } });
$('#agentStop').onclick = async () => { if (AG.id) { try { await api(`/api/agent/${AG.id}/stop`, {method: 'POST'}); } catch (e) { toast(e.message); } } };
