// 演示模式：拦截 /api/* 请求，返回虚构数据（不连 AI、不发邮件）。
// 由 scripts/build_demo.py 注入到 demo/index.html。
(function () {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const now = new Date();
  const ts = (daysAgo, h = 10, m = 0) => {
    const d = new Date(now); d.setDate(d.getDate() - daysAgo); d.setHours(h, m, 0, 0);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const CUR = '2026秋·全职+留用实习', OLD = '2026春·实习';
  const body = (greet, job, line) => `${greet}\n\n我是张三，某大学硕士在读，2027年6月毕业，人在上海，两周内可到岗，每周5天，可实习6个月以上，申请贵司${job}岗位。\n\n${line}\n\n简历见附件，期待有机会进一步交流。\n\n张三\n`;
  const L1 = '我目前在A资本做半导体与AI基础设施方向的股权投资实习，从16个维度对比过四类新型存储并完成20页行业研究；此前在B资本有两段股权投资实习，覆盖航空航天和先进制造。';
  const L2 = '我目前在A资本实习，参与了某半导体材料项目（拟投5000万元）的立项和某设备项目（拟投4000万元）的投决；此前在B资本参与一家航空航天头部企业的成长期融资，做了商业尽调和可比公司估值。';
  let id = 0;
  const rec = (o) => Object.assign({
    id: 'demo' + (++id), campaign: CUR, status: '已投递', send_mode: '发送', source_type: '网页面板',
    job_source: '某招聘公众号', cc_email: '', notes: '', reply_status: '', reply_at: '', reply_from: '', reply_snippet: '',
    attach_report: false, resume_version: '双语', attachments: [{kind: '简历', filename: '张三-某大学-简历.pdf', version: '双语'}],
    jd_text: '（演示数据：JD 原文略）', issues_at_send: [], job_post_date: '', deadline: '', gmail_thread_id: '',
  }, o);
  const RECORDS = [
    rec({company_name: '某硬科技PE C', company_type: '人民币VC', job_title: '投资分析师（半导体方向）', position_type: '全职', job_location: '上海', focus_industry: '半导体', sent_at: ts(0, 9, 12), to_email: 'campus@example-c.com', subject: '2027校招-投资分析师-张三-某大学-硕士', email_body: body('您好，', '投资分析师', L2)}),
    rec({company_name: '某美元VC A', company_type: '美元VC', job_title: '投资实习生（可留用）', position_type: '留用实习', job_location: '上海', focus_industry: 'AI/硬科技', sent_at: ts(1, 21, 5), to_email: 'talent@example-a.com', subject: '【投资实习生+张三+某大学+2027届+每周5天共6月】', status: '已电联', reply_status: '有回复', reply_at: ts(0, 11, 20), reply_from: 'talent@example-a.com', reply_snippet: '同学你好，简历已收到，方便明天下午电话聊一下吗？', attach_report: true, email_body: body('您好，', '投资实习生', L1)}),
    rec({company_name: '某产业资本 D', company_type: '产业资本/CVC/战投', job_title: '投资部实习生（可转正）', position_type: '留用实习', job_location: '北京', focus_industry: '新能源/储能', sent_at: ts(2, 10, 40), to_email: 'invest_hr@example-d.com', subject: '实习生-张三-某大学-6个月', status: '面试中', reply_status: '有回复', reply_at: ts(1, 15, 2), reply_from: 'hr@example-d.com', reply_snippet: '【面试邀请】请于周四下午参加线上面试…', notes: '周四 14:00 线上一面', email_body: body('李女士您好，', '投资部实习生', '我在家族企业C集团担任董事长助理期间，协助比较SCR脱硝、余热回收、光伏等技改的CAPEX与回收期；目前在A资本做股权投资实习，参与项目立项与投决。')}),
    rec({company_name: '某券商直投 E', company_type: '券商/投行/FA', job_title: '股权投资分析师', position_type: '全职', job_location: '深圳', focus_industry: '先进制造', sent_at: ts(3, 14, 18), to_email: 'zhaopin@example-e.com', subject: '股权投资分析师申请 - 张三｜某大学（2027届）', email_body: body('您好，', '股权投资分析师', L2)}),
    rec({company_name: '某双币基金 G', company_type: '双币VC/PE', job_title: 'Investment Analyst Intern', position_type: '实习', job_location: '香港/上海', focus_industry: '半导体/AI', sent_at: ts(3, 16, 30), to_email: 'recruiting@example-g.com', subject: 'Intern Application – San Zhang – Example University', resume_version: '英文', attachments: [{kind: '简历', filename: 'San Zhang-ExampleU-Resume.pdf', version: '英文'}], email_body: 'Dear Ms. Chen,\n\nI\'m San Zhang, a master\'s student at Example University (graduating June 2027). I\'m based in Shanghai and can start within two weeks, five days a week.\n\nI\'m currently a private equity intern at A Capital covering semiconductors and AI infrastructure.\n\nMy resume is attached. I\'d welcome the chance to discuss the role.\n\nBest regards,\nSan Zhang\n'}),
    rec({company_name: '某国资基金 F', company_type: '国资/政府引导基金', job_title: '投资经理助理', position_type: '全职', job_location: '苏州', focus_industry: '硬科技', sent_at: ts(4, 11, 0), to_email: 'hr@example-f.com', subject: '投资经理助理申请 - 张三｜某大学（2027届）', reply_status: '退信', reply_at: ts(4, 11, 2), reply_from: 'mailer-daemon@googlemail.com', reply_snippet: '找不到地址：系统找不到电子邮件地址 hr@example-f.com…', email_body: body('您好，', '投资经理助理', L2)}),
    rec({company_name: '某并购基金 H', company_type: 'PE/并购基金', job_title: '投资研究实习生', position_type: '留用实习', job_location: '上海', focus_industry: '工业/消费', sent_at: ts(5, 20, 45), to_email: 'jobs@example-h.com', subject: '投研实习生申请 - 张三｜某大学', status: '草稿', send_mode: '草稿', email_body: body('您好，', '投资研究实习生', L1)}),
    rec({company_name: '某大厂战投 I', company_type: '企业/大厂', job_title: '战略投资实习生', position_type: '实习', job_location: '北京', focus_industry: 'AI', sent_at: ts(6, 13, 25), to_email: '', send_mode: '未发邮件', resume_version: '网申（按网站要求上传）', apply_url: 'https://example.com/jobs/123', subject: '', email_body: ''}),
    rec({company_name: '某人民币VC B', company_type: '人民币VC', job_title: '投资实习生', position_type: '实习', job_location: '上海', focus_industry: '半导体', sent_at: ts(9, 10, 10), to_email: 'hr@example-b.com', subject: '投资实习生申请 - 张三｜某大学', status: 'offer', reply_status: '有回复', reply_at: ts(6, 18, 0), reply_from: 'hr@example-b.com', reply_snippet: '恭喜你通过面试，offer 详见附件…', notes: '已收 offer，考虑中', email_body: body('您好，', '投资实习生', L1)}),
    rec({company_name: '某美元VC J', company_type: '美元VC', job_title: 'Growth 投资分析师', position_type: '全职', job_location: '北京', focus_industry: 'TMT', sent_at: ts(11, 9, 50), to_email: 'careers@example-j.com', subject: 'Growth 投资分析师申请 - 张三｜某大学（2027届）', status: '拒绝', email_body: body('您好，', 'Growth 投资分析师', L2)}),
    rec({company_name: '某医疗基金 K', company_type: 'PE/并购基金', job_title: '投资实习生', position_type: '实习', job_location: '上海', focus_industry: '医疗健康', sent_at: ts(12, 15, 0), to_email: 'intern@example-k.com', subject: '投资实习生申请 - 张三｜某大学', reply_status: '自动回复', reply_at: ts(12, 15, 1), reply_from: 'intern@example-k.com', reply_snippet: '您好，邮件已收到，我们会尽快处理。', email_body: body('您好，', '投资实习生', L2)}),
    rec({company_name: '某早期基金 L', company_type: '人民币VC', job_title: '投资经理（校招）', position_type: '全职', job_location: '杭州', focus_industry: 'AI/机器人', sent_at: ts(14, 10, 30), to_email: 'hr@example-l.com', subject: '投资经理（校招）申请 - 张三｜某大学（2027届）', email_body: body('您好，', '投资经理（校招）', L1)}),
    rec({company_name: '某美元VC A', company_type: '美元VC', job_title: '投资实习生', position_type: '实习', job_location: '上海', focus_industry: 'AI', campaign: OLD, sent_at: '2026-04-12 10:00', to_email: 'talent@example-a.com', subject: '实习申请 - 张三｜某大学', status: '无回复', email_body: '（春季实习投递）'}),
    rec({company_name: '某硬科技PE M', company_type: 'PE/并购基金', job_title: '投资实习生', position_type: '实习', job_location: '深圳', focus_industry: '先进制造', campaign: OLD, sent_at: '2026-04-15 15:00', to_email: 'hr@example-m.com', subject: '实习申请 - 张三｜某大学', status: 'offer', email_body: '（春季实习投递）'}),
  ];

  const DEMO_JD = `【某硬科技PE C 2027届校园招聘】投资分析师（半导体方向）
某硬科技PE C 是一家专注科技领域的股权投资机构，重点投资半导体、先进制造、新能源等领域。
岗位职责：
1. 负责半导体、先进制造等领域的行业研究，撰写行业研究报告；
2. 参与项目筛选、尽职调查、投资分析及投后管理；
3. 协助撰写立项报告、投资建议书等材料。
任职要求：
1. 2027届硕士及以上应届毕业生，理工科或经济金融背景；
2. 有PE/VC实习经验者优先。
工作地点：上海/深圳
投递方式：请将简历发送至 campus@example-c.com，邮件标题请注明"2027校招-投资分析师-姓名-学校-最高学历"，简历命名同邮件标题。可附过往研究报告。
截止时间：2026年10月31日`;

  const RESULT = {
    is_jd: true, not_jd_reason: '', company_name: '某硬科技PE C', company_type: '人民币VC', job_title: '投资分析师（半导体方向）',
    position_type: '全职', job_location: '上海/深圳', focus_industry: '半导体/先进制造', job_post_date: '', deadline: '2026-10-31',
    jd_language: '中文', apply_channel: '邮箱', to_emails: ['campus@example-c.com'], cc_emails: [], apply_url: '', contact_in_jd: '',
    jd_rules: {subject_format: '2027校招-投资分析师-姓名-学校-最高学历', resume_filename_format: '简历命名同邮件标题', report_filename_format: '',
      body_requirements: [], requested_materials: ['简历', '过往研究报告（可附）']},
    fit_warnings: ['任职要求「理工科或经济金融背景」：候选人硕士为某专业，是否满足需本人判断'], missing_info: [],
    resume_version: '双语', resume_filename: '2027校招-投资分析师-张三-某大学-硕士.pdf', resume_filename_en: '',
    attach_report: true, report_filename: '张三-公司研究报告.pdf', attach_resume: true,
    email_subject: '2027校招-投资分析师-张三-某大学-硕士',
    email_body: '您好，\n\n我是张三，某大学硕士，2027年6月毕业，应聘贵司投资分析师（半导体方向）岗位。\n\n我目前在A资本做半导体与AI基础设施方向的股权投资实习，从16个维度对比四类新型存储并完成20页行业研究，也参与了某半导体材料项目（拟投5000万元）的立项和某设备项目（拟投4000万元）的投决；此前在B资本有两段股权投资实习，覆盖航空航天和先进制造。\n\n简历见附件，另附一份我做过的公司研究样本，供参考。\n\n张三\n',
  };
  const RELATED = [{id: 'demo0', sent_at: '2026-04-15 15:00', company_name: '某硬科技PE M', job_title: '投资实习生', status: 'offer', campaign: OLD, position_type: '实习', match: '机构名相近'}];
  const baseIssues = () => [
    {level: 'warn', field: 'duplicate', msg: '以前投过：2026-04-15 某硬科技PE M｜投资实习生｜offer（机构名相近）'},
    {level: 'warn', field: 'fit', msg: 'JD 要求：' + RESULT.fit_warnings[0]},
    {level: 'info', field: 'post_date', msg: '截止日期 2026-10-31，还有 3 周。'},
  ];

  function check(r) {
    const issues = [];
    const text = (r.email_subject || '') + '\n' + (r.email_body || '') + '\n' + (r.resume_filename || '');
    const ph = text.match(/\[[^\[\]\n]{1,30}\]/g);
    if (ph) issues.push({level: 'error', field: 'subject', msg: '还有没填的占位：' + ph.join('、')});
    const to = String(r.to_emails || '').toLowerCase().match(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g) || [];
    if (!to.length) issues.push({level: 'error', field: 'to', msg: '没有收件邮箱。'});
    to.filter(e => !DEMO_JD.toLowerCase().includes(e)).forEach(e => issues.push({level: 'error', field: 'to', msg: `邮箱 ${e} 在 JD 原文里找不到，可能是 AI 编的或抄错了，请核对。`}));
    if (/！|!/.test(r.email_body || '')) issues.push({level: 'warn', field: 'body', msg: '正文有感叹号。'});
    if (/2026年12月|2026届/.test(text)) issues.push({level: 'error', field: 'body', msg: '毕业时间写错了，和简历上的不一致。'});
    return issues.concat(baseIssues());
  }

  function stats(recs) {
    const out = {total: recs.length, week_new: 0, followup: [], status_dist: {}, type_dist: {}, loc_dist: {}, industry_dist: {},
      position_dist: {}, daily: {}, daily_detail: {}, replied: 0, bounced: [], companies: new Set(recs.map(r => r.company_name)).size};
    const week = Date.now() - 7 * 864e5;
    recs.forEach(r => {
      const inc = (o, k) => { if (k) o[k] = (o[k] || 0) + 1; };
      inc(out.status_dist, r.status); inc(out.type_dist, r.company_type); inc(out.position_dist, r.position_type);
      inc(out.loc_dist, (r.job_location || '').split('/')[0]);
      (r.focus_industry || '').split('/').forEach(x => inc(out.industry_dist, x));
      if (r.reply_status === '有回复') out.replied++;
      if (r.reply_status === '退信') out.bounced.push(r.company_name + '（' + r.to_email + '）');
      const t = new Date(r.sent_at.replace(' ', 'T')).getTime();
      if (t >= week) out.week_new++;
      if (r.status === '已投递' && t < week && !['有回复', '退信'].includes(r.reply_status)) out.followup.push(r.company_name);
      const day = r.sent_at.slice(5, 10).replace('-', '/');
      inc(out.daily, day); (out.daily_detail[day] = out.daily_detail[day] || []).push(r.company_name);
    });
    return out;
  }

  const CFG = {model: 'claude-opus-5-5', effort: 'medium', sender: 'demo@example.com', campaign: CUR, campaigns: [OLD, CUR],
    statuses: ['草稿', '已投递', '已电联', '面试中', 'offer', '拒绝', '无回复'], position_types: ['全职', '留用实习', '实习', '不明确'],
    resume_versions: ['双语', '中文', '英文', '中英两份'], resume: {ok: true, pages: 2, zh_pages: [0], en_pages: [1], grad_problems: [], size_kb: 306},
    report_exists: true, report_default_name: '张三-公司研究报告.pdf', report_label: '研究样本（中文公司研究）',
    resume_default_zh: '张三-某大学-简历.pdf', resume_default_en: 'San Zhang-ExampleU-Resume.pdf', candidate: '张三', local: true};

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, opts = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const i = url.pathname.indexOf('/api/');
    if (i < 0) return realFetch(input, opts);
    const path = url.pathname.slice(i);
    const method = (opts.method || 'GET').toUpperCase();
    const body = opts.body ? JSON.parse(opts.body) : {};
    const json = (o, status = 200) => new Response(JSON.stringify(o), {status, headers: {'Content-Type': 'application/json'}});
    await sleep(250);
    const camp = url.searchParams.get('campaign');
    const filtered = !camp || camp === '全部' ? RECORDS : RECORDS.filter(r => r.campaign === camp);
    if (path === '/api/config') return json(CFG);
    if (path === '/api/gmail/status') return json({ok: true, email: 'demo@example.com'});
    if (path === '/api/tunnel-url') return json({url: ''});
    if (path === '/api/fetch-url') { await sleep(800); return json({title: '某硬科技PE C 2027届校园招聘', content: DEMO_JD, source: 'wechat', source_label: '某招聘公众号（公众号）', publish_date: '2026-10-05', url: url.href, ocr_used: false, jobs: [], content_length: DEMO_JD.length}); }
    if (path === '/api/analyze') {
      await sleep(2200);
      const r = JSON.parse(JSON.stringify(RESULT));
      if (body.extra) r.email_body = r.email_body.replace('此前在B资本', '（已按补充要求调整）此前在B资本');
      return json({ok: true, result: r, fixes: ['称呼「Jessica总您好」在 JD 原文里找不到依据（可能是从邮箱地址猜的），已改成「您好，」'],
        issues: check(r), related: RELATED, resume: CFG.resume, meta: {model: 'claude-opus-5-5', seconds: 14.2, cost_usd: 0.031, cache_read: 11490, input_tokens: 420, output_tokens: 1300}});
    }
    if (path === '/api/check') return json({issues: check(body.result || {}), related: RELATED, resume: CFG.resume});
    if (path === '/api/send') return json({ok: true, mode: body.mode === 'draft' ? '草稿' : '发送', attachments: [body.result.resume_filename, body.result.attach_report ? body.result.report_filename : ''].filter(Boolean), record_id: ''});
    if (path === '/api/record') return json({ok: true});
    if (path === '/api/records' && method === 'GET') return json([...filtered].sort((a, b) => b.sent_at.localeCompare(a.sent_at)).map(r => ({...r, _type: r.company_type})));
    if (path.startsWith('/api/records/')) return json({ok: true});
    if (path === '/api/stats') return json(stats(filtered));
    if (path === '/api/check-replies') { await sleep(900); return json({ok: true, logs: ['（演示）检查 12 条记录的回复…', '完成：3 条有回复 / 来信，1 条退信']}); }
    if (path === '/api/gmail-sync') { await sleep(900); return json({ok: true, logs: ['（演示）Gmail 已发送里没有遗漏的投递']}); }
    return json({error: '演示版不支持这个操作'}, 400);
  };

  document.addEventListener('DOMContentLoaded', () => {
    const bar = document.createElement('div');
    bar.className = 'fixed bottom-0 md:bottom-auto md:top-0 left-0 right-0 z-[80] bg-amber-400 text-amber-950 text-xs text-center py-1 font-bold';
    bar.textContent = '演示版：所有机构、邮箱、人物均为虚构，不连接 AI、不会发送任何邮件';
    document.body.appendChild(bar);
    document.getElementById('jdInput').value = DEMO_JD;
    document.getElementById('sourceInput').value = '某招聘公众号（公众号）';
    ['previewResume', 'downloadResume', 'exportBtn'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('click', e => { e.preventDefault(); e.stopImmediatePropagation(); if (window.toast) toast('演示版不提供文件下载'); }, true);
    });
  });
})();
