// 「数据分析」页（#tab-analytics）。用到 board.js 里的 metric、bindTips。
// ━━━ 分析 ━━━
$('#aCampaign').addEventListener('change', loadAnalytics);
let _dailyDetail = {};
function bars(id, dist, color, limit = 8) {
  const entries = Object.entries(dist || {}).sort((a, b) => b[1] - a[1]).slice(0, limit);
  const max = Math.max(1, ...entries.map(e => e[1]));
  $(id).innerHTML = entries.map(([k, v]) => `<div class="mb-3"><div class="flex justify-between text-xs font-medium mb-1"><span>${esc(k)}</span><span class="text-slate-400">${v}</span></div><div class="h-1.5 bg-slate-100 rounded-full overflow-hidden"><div class="h-full rounded-full" style="width:${v / max * 100}%;background:${color}"></div></div></div>`).join('') || '<p class="text-slate-400 text-sm">暂无</p>';
}

async function loadAnalytics() {
  let s;
  try { s = await api('/api/stats?campaign=' + encodeURIComponent($('#aCampaign').value)); } catch (e) { return toast('加载失败：' + e.message); }
  const sd = s.status_dist || {};
  const progressed = ['已电联', '面试中', 'offer'].reduce((a, k) => a + (sd[k] || 0), 0);
  const pct = n => s.total ? Math.round(n / s.total * 100) + '%' : '—';
  $('#analyticsMetrics').innerHTML = metric('总投递', s.total) + metric('覆盖机构', s.companies, '#475569') + metric('有回复', s.replied, '#16a34a') +
    metric('推进率（电联及以后）', pct(progressed), '#005bbf') + metric('Offer', sd['offer'] || 0, '#16a34a') + metric('待跟进', s.followup.length, '#ba1a1a', s.followup);
  bindTips($('#analyticsMetrics'));
  const daily = s.daily || {}; _dailyDetail = s.daily_detail || {};
  const days = Object.keys(daily).sort();
  const maxV = Math.max(1, ...Object.values(daily));
  let cum = 0; const cumData = days.map(d => (cum += daily[d]));
  $('#trendChart').innerHTML = days.map((d, i) => `<div class="trend-col flex-1 h-full flex flex-col items-center justify-end gap-1 group cursor-pointer" data-day="${esc(d)}" data-n="${daily[d]}" data-c="${cumData[i]}">
      <div class="w-full bg-primary/25 group-hover:bg-primary/60 rounded-t min-h-[4px]" style="height:${daily[d] / maxV * 190}px"></div><span class="text-[10px] text-slate-400">${esc(d.slice(5).replace('-', '/'))}</span></div>`).join('') || '<p class="text-slate-400 text-sm m-auto">暂无数据</p>';
  document.querySelectorAll('.trend-col').forEach(el => {
    el.onmouseenter = ev => showTip(ev, el.dataset.day, el.dataset.n, el.dataset.c);
    el.onmouseleave = () => $('#trendTooltip').classList.add('hidden');
  });
  const cv = $('#cumulativeLine');
  const ctx = cv.getContext('2d');
  const rect = $('#trendChart').getBoundingClientRect();
  cv.width = rect.width; cv.height = rect.height; ctx.clearRect(0, 0, cv.width, cv.height);
  if (days.length > 1) {
    const h = cv.height - 24, w = cv.width / days.length, cmax = Math.max(...cumData);
    ctx.strokeStyle = '#f97316'; ctx.lineWidth = 2; ctx.setLineDash([6, 3]); ctx.beginPath();
    days.forEach((d, i) => { const x = w * i + w / 2, y = h - cumData[i] / cmax * h * 0.85; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke(); ctx.setLineDash([]);
  }
  const colors = {'草稿': '#94a3b8', '已投递': '#3b82f6', '笔试': '#06b6d4', '已电联': '#6366f1', '面试中': '#f97316', 'offer': '#22c55e', '拒绝': '#ef4444', '无回复': '#cbd5e1'};
  const total = Object.values(sd).reduce((a, b) => a + b, 0) || 1, C = 2 * Math.PI * 40;
  let off = 0, rings = '<circle cx="50" cy="50" r="40" fill="transparent" stroke="#e6eeff" stroke-width="12"/>';
  for (const [k, v] of Object.entries(sd)) { const dash = v / total * C; rings += `<circle cx="50" cy="50" r="40" fill="transparent" stroke="${colors[k] || '#94a3b8'}" stroke-width="12" stroke-dasharray="${dash} ${C}" stroke-dashoffset="${-off}"/>`; off += dash; }
  $('#statusRing').innerHTML = `<svg class="w-full h-full -rotate-90" viewBox="0 0 100 100">${rings}</svg><div class="absolute inset-0 flex flex-col items-center justify-center"><span class="text-3xl font-bold">${s.total}</span><span class="text-[10px] text-slate-400 font-bold">TOTAL</span></div>`;
  $('#statusLegend').innerHTML = Object.entries(sd).map(([k, v]) => `<div class="flex items-center gap-2"><div class="w-2 h-2 rounded-full" style="background:${colors[k] || '#94a3b8'}"></div><span class="text-xs">${esc(k)} (${v})</span></div>`).join('');
  bars('#posBars', s.position_dist, '#8b5cf6'); bars('#typeBars', s.type_dist, '#005bbf'); bars('#industryBars', s.industry_dist, '#6366f1');
  bars('#locList', s.loc_dist, '#0ea5e9');
}
function showTip(e, day, n, c) {
  const tip = $('#trendTooltip');
  tip.innerHTML = `<div class="font-bold mb-1">${esc(day)}（当日 ${esc(n)} / 累计 ${esc(c)}）</div><div class="text-slate-300">${esc((_dailyDetail[day] || []).join('、'))}</div>`;
  tip.classList.remove('hidden');
  const cr = $('#trendChart').parentElement.getBoundingClientRect();
  tip.style.left = Math.min(e.clientX - cr.left + 10, cr.width - 250) + 'px';
  tip.style.top = Math.max(e.clientY - cr.top - 80, 0) + 'px';
}
