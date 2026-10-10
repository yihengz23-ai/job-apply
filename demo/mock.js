// 演示模式：拦截 /api/* 请求，返回虚构数据（不连 AI、不发邮件）。
// 由 scripts/build_demo.py 注入到 demo/index.html。
(function () {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const realFetch = window.fetch.bind(window);
  const now = new Date();
  const ts = (daysAgo, h = 10, m = 0) => {
    const d = new Date(now); d.setDate(d.getDate() - daysAgo); d.setHours(h, m, 0, 0);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const CUR = '2026秋·全职+留用实习', OLD = '2026春·实习';
  const body = (greet, job, line) => `${greet}\n\n我是张三，某大学硕士在读，2027年6月毕业，人在上海，两周内可到岗，每周5天，可实习6个月以上，申请贵司${job}岗位。\n\n${line}\n\n简历见附件，期待有机会进一步交流。\n\n张三\n`;
  const L1 = '我目前在A资本做半导体方向的投资实习，参与了行业研究和两个项目的立项、投决材料准备；此前在B资本参与过一个先进制造方向的成长期项目。';
  const L2 = '我目前在A资本实习，参与了两个项目的立项和投决材料准备；此前在B资本参与一个成长期项目，做了专家访谈、商业尽调和可比公司估值。';
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
    rec({company_name: '某产业资本 D', company_type: '产业资本/CVC/战投', job_title: '投资部实习生（可转正）', position_type: '留用实习', job_location: '北京', focus_industry: '新能源/储能', sent_at: ts(2, 10, 40), to_email: 'invest_hr@example-d.com', subject: '实习生-张三-某大学-6个月', status: '面试中', reply_status: '有回复', reply_at: ts(1, 15, 2), reply_from: 'hr@example-d.com', reply_snippet: '【面试邀请】请于周四下午参加线上面试…', notes: '周四 14:00 线上一面', email_body: body('李女士您好，', '投资部实习生', '我目前在A资本做投资实习，参与了两个项目的立项和投决材料准备，做过技术、市场和客户研究，也关注储能和新能源方向的产业链。')}),
    rec({company_name: '某券商直投 E', company_type: '券商/投行/FA', job_title: '股权投资分析师', position_type: '全职', job_location: '深圳', focus_industry: '先进制造', sent_at: ts(3, 14, 18), to_email: 'zhaopin@example-e.com', subject: '股权投资分析师申请 - 张三｜某大学（2027届）', email_body: body('您好，', '股权投资分析师', L2)}),
    rec({company_name: '某双币基金 G', company_type: '双币VC/PE', job_title: 'Investment Analyst Intern', position_type: '实习', job_location: '香港/上海', focus_industry: '半导体/AI', sent_at: ts(3, 16, 30), to_email: 'recruiting@example-g.com', subject: 'Intern Application – San Zhang – Example University', resume_version: '英文', attachments: [{kind: '简历', filename: 'San Zhang-ExampleU-Resume.pdf', version: '英文'}], email_body: 'Dear Ms. Chen,\n\nI\'m San Zhang, a master\'s student at Example University (graduating June 2027). I\'m based in Shanghai and can start within two weeks, five days a week.\n\nI\'m currently a private equity intern at A Capital covering semiconductors and AI infrastructure.\n\nMy resume is attached. I\'d welcome the chance to discuss the role.\n\nBest regards,\nSan Zhang\n'}),
    rec({company_name: '某国资基金 F', company_type: '国资/政府引导基金', job_title: '投资经理助理', position_type: '全职', job_location: '苏州', focus_industry: '硬科技', sent_at: ts(4, 11, 0), to_email: 'hr@example-f.com', subject: '投资经理助理申请 - 张三｜某大学（2027届）', reply_status: '退信', reply_at: ts(4, 11, 2), reply_from: 'mailer-daemon@googlemail.com', reply_snippet: '找不到地址：系统找不到电子邮件地址 hr@example-f.com…', email_body: body('您好，', '投资经理助理', L2)}),
    rec({company_name: '某并购基金 H', company_type: 'PE/并购基金', job_title: '投资研究实习生', position_type: '留用实习', job_location: '上海', focus_industry: '工业/消费', sent_at: ts(5, 20, 45), to_email: 'jobs@example-h.com', subject: '投研实习生申请 - 张三｜某大学', status: '草稿', send_mode: '草稿', email_body: body('您好，', '投资研究实习生', L1)}),
    rec({company_name: '某大厂战投 I', company_type: '企业/大厂', job_title: '战略投资实习生', position_type: '实习', job_location: '北京', focus_industry: 'AI', sent_at: ts(6, 13, 25), to_email: '', send_mode: '未发邮件', resume_version: '网申上传（中文）', apply_url: 'https://example.com/jobs/123', subject: '', email_body: '',
      platform: '飞书', reply_status: '自动回复', reply_at: ts(6, 13, 30), reply_from: 'noreply@example.com', reply_snippet: '【某大厂战投 I】感谢您的投递，我们已收到您的申请…',
      apply_account: '138****0000', site_status: '简历筛选中', site_status_at: ts(5, 9, 0),
      ws_submitted: '【投递记录（从网站读回）】\n某大厂战投 I｜战略投资实习生｜北京｜简历筛选中\n\n【实际提交的简历（从网站读回，证件号已隐去）】\n姓名：张三　手机：138****0000\n教育：某大学　硕士　2025.09 - 2027.06\n实习：A资本　投资实习生　2026 - 至今\n　参与半导体方向的行业研究，完成一份行业研究报告；参与两个项目的立项和投决材料准备\n自我评价：（本人提交前改过的那一版，看板里记的就是网站上真交上去的这一版）'}),
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
    email_body: '您好，\n\n我是张三，某大学硕士，2027年6月毕业，应聘贵司投资分析师（半导体方向）岗位。\n\n我目前在A资本做半导体方向的投资实习，参与了行业研究和两个项目的立项、投决材料准备；此前在B资本参与过一个先进制造方向的成长期项目，做了专家访谈和可比公司估值。\n\n简历见附件，另附一份我做过的公司研究样本，供参考。\n\n张三\n',
  };
  // 网申（演示）：「我的资料」页的固定栏目 + AI 写的网申问答
  const KIT = {"_说明": "网申表格常用字段示例（虚构人物「张三」，经历也是虚构的）。复制为 application_kit.json 后换成你自己的信息：经历描述直接贴简历原文，tests/test_kit.py 会核对这些内容都能在简历 PDF 里找到。", "基本信息": [["姓名", "张三"], ["英文名", "San Zhang"], ["手机", "138xxxxxxxx"], ["微信", "138xxxxxxxx"], ["邮箱", "your.email@gmail.com"], ["现居城市", "上海"]], "教育经历": [{"学校": "某大学（Example University）", "学院": "某学院", "专业": "某专业（硕士）", "学历": "硕士", "起止时间": "2025.09 - 2027.06", "补充": ""}, {"学校": "某大学", "学院": "某学院", "专业": "某专业（学士）", "学历": "本科", "起止时间": "2021.09 - 2025.06", "补充": ""}], "实习经历": [{"公司": "A资本", "职位": "投资实习生", "部门/方向": "半导体与硬科技", "地点": "上海", "起止时间": "2026 - 至今", "精简描述": "参与半导体方向的行业研究，完成一份行业研究报告；参与两个项目的立项和投决材料准备；参与项目初筛。", "完整描述": ["行业研究：参与半导体方向的行业研究，整理产业链和竞争格局，完成一份行业研究报告", "项目工作：参与两个项目的立项和投决材料准备，负责技术、市场、竞争与客户研究", "项目筛选：参与项目初筛，整理专家访谈纪要"]}, {"公司": "B资本", "职位": "投资实习生", "部门/方向": "先进制造", "地点": "上海", "起止时间": "2025 年暑期", "精简描述": "参与一个成长期项目：专家访谈、市场空间测算、商业尽调和可比公司估值。", "完整描述": ["参与一个成长期项目：专家访谈、市场空间测算、商业尽调和可比公司估值"]}], "求职意向": [["毕业时间", "2027年6月（2027届）"], ["实习到岗时间", "两周内可到岗"], ["每周实习天数", "5天"], ["实习时长", "6个月以上"], ["全职入职时间", "2027年7月（毕业前可先以实习形式到岗）"], ["期望城市", "上海"], ["期望薪资", "[待填]"]], "技能与其他": [["语言", "英语（可作为工作语言）"], ["工具", "Office、Wind、Python"], ["AI 工具", "会用常见 AI 工具做数据整理与自动化"]]};
  const WS_KIT = {platform: '飞书', apply_steps: ['打开飞书招聘链接，选择「投资实习生」岗位', '登录 / 注册飞书招聘账号', '上传中文简历（单页），核对自动解析出的教育和实习经历', '在问答题里粘贴下方答案，核对后提交'],
    self_intro_short: '我是张三，某大学硕士在读，2027年6月毕业。人在上海，两周内可到岗，每周5天，可实习6个月以上。目前在A资本做半导体与AI基础设施方向的股权投资实习，此前在B资本做过两段股权投资实习。',
    self_intro: '我是张三，某大学硕士在读，2027年6月毕业。人在上海，两周内可到岗，每周5天，可以连续实习6个月以上。目前在A资本做半导体方向的投资实习：参与行业研究并完成一份行业研究报告，参与两个项目的立项和投决材料准备。此前在B资本参与过一个先进制造方向的成长期项目。',
    why_this_role: '贵司专注硬科技早期投资，重点看半导体和先进制造，正好是我实习做过的方向。岗位要做的行业研究、项目筛选和尽调，也是我现在日常在做的事。我可以每周到岗5天、连续实习6个月以上，也希望有机会留用。',
    fit_points: '1. 赛道对口：在A资本覆盖半导体方向，完成过一份行业研究报告。\n2. 项目流程：参与过项目立项和投决，做过专家访谈、商业尽调和可比公司估值。\n3. 时间：两周内到岗，每周5天，6个月以上。',
    custom_answers: [{question: '你最看好的一个硬科技细分赛道是什么？为什么？（300字以内）', answer: '我最看好新型存储。AI 推理对内存带宽和容量的需求增长很快……（演示：真实版本由 AI 按你的简历和这个岗位写）'}],
    notes: ['截止时间：2026-10-31，建议尽早提交', '飞书网申需要先注册账号；上传简历后要核对自动解析的结果', '表单如果问期望薪资，需要你自己填写 [待填]']};
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
      const day = r.sent_at.slice(0, 10);  // 带年份（界面上只显示月/日）
      inc(out.daily, day); (out.daily_detail[day] = out.daily_detail[day] || []).push(r.company_name);
    });
    return out;
  }

  const CFG = {model: 'claude-opus-5-5', effort: 'medium', sender: 'demo@example.com', campaign: CUR, campaigns: [OLD, CUR],
    statuses: ['草稿', '已投递', '已电联', '面试中', 'offer', '拒绝', '无回复'], position_types: ['全职', '留用实习', '实习', '不明确'],
    resume_versions: ['双语', '中文', '英文', '中英两份'], resume: {ok: true, pages: 2, zh_pages: [0], en_pages: [1], grad_problems: [], size_kb: 306},
    report_exists: true, report_default_name: '张三-公司研究报告.pdf', report_label: '研究样本（中文公司研究）',
    resume_default_zh: '张三-某大学-简历.pdf', resume_default_en: 'San Zhang-ExampleU-Resume.pdf', candidate: '张三', local: true, backend: 'claude_code'};

  // 批量队列（演示）：新加的条目 6 秒后变成「待审核」
  const qItem = (status, r, extra = {}) => ({id: 'q' + (++id), created_at: ts(0, 9, id), updated_at: ts(0, 9, id), kind: 'url',
    input: 'https://mp.weixin.qq.com/s/demo' + id, status, error: '', page: {source_label: '某招聘公众号（公众号）', title: r ? r.company_name + ' 招聘' : '某机构招聘', url: ''},
    jd_text: DEMO_JD, target_job: '', analysis: r ? {result: r, issues: check(r), fixes: [], related: [], meta: {model: 'claude-opus-5-5', seconds: 21.3, backend: '会员额度'}} : null,
    edited: null, record_id: '', ...extra});
  const QUEUE = [];
  setTimeout(() => {
    const a = JSON.parse(JSON.stringify(RESULT));
    const b = {...JSON.parse(JSON.stringify(RESULT)), company_name: '某美元VC N', job_title: '投资实习生（可留用）', position_type: '留用实习', email_subject: '【投资实习生+张三+某大学+2027届+每周5天共6月】', to_emails: ['talent@example-n.com']};
    const w = {...JSON.parse(JSON.stringify(RESULT)), company_name: '某硬科技基金 W', job_title: '投资实习生（2027届，可留用）', position_type: '留用实习', apply_channel: '网申/链接',
      to_emails: [], apply_url: 'https://example.com/campus/apply/888', jd_rules: {subject_format: '', resume_filename_format: '', report_filename_format: '', body_requirements: [], requested_materials: ['简历（网申上传）']}};
    QUEUE.push(qItem('待审核', a), qItem('需处理', b), qItem('待审核', w, {wangshen: WS_KIT}), qItem('处理中', null), qItem('失败', null, {error: '微信不让自动抓取这篇文章（环境异常）。请在微信里打开文章，复制正文粘贴进来。'}));
  }, 0);
  const qCounts = () => QUEUE.reduce((o, it) => (o[it.status] = (o[it.status] || 0) + 1, o), {});

  // ── 网申页（演示）：一家一行、一个颜色；助手代填 → 停下等你 → 填好记看板 → 你提交后它去网站读回实际提交的内容 ──
  const COLORS = [['🔵', '#2563eb', '蓝'], ['🟢', '#16a34a', '绿'], ['🟣', '#9333ea', '紫'], ['🟠', '#ea580c', '橙'],
    ['🔴', '#dc2626', '红'], ['🟡', '#ca8a04', '黄'], ['🟤', '#92400e', '棕'], ['⚫', '#334155', '黑']];
  const task = o => Object.assign({id: 't' + (++id), created_at: ts(1, 20), updated_at: ts(0, 9), status: '待填', company: '', job: '', url: '',
    source: '投递页（只能网申）', deadline: '', note: '', record_id: '', email_record_id: '', queue_id: '', chat_id: '', result: {}, jd_text: '',
    account: '', color: 0, todo: '', halted: '', readback: {}, positions: [], site_status: '', readback_state: ''}, o);
  const WS = [
    task({company: '某硬科技基金 W', job: '投资实习生（2027届，可留用）', url: 'https://example.com/campus/apply/888', color: 1, deadline: ts(-6).slice(0, 10)}),
    task({company: '某银行理财子 X', job: '校招（总行管培生、研究岗两个志愿）', url: 'https://example.com/bank/campus', color: 2, status: '等你处理',
      todo: '在画了紫框的网页里扫码登录（登录好了我会自己接着填，不用你说）', chat_id: 'cX', source: '网申页贴的链接'}),
    task({company: '某新能源集团 Y', job: '战略投资管培生', url: 'https://example.com/hr/y', color: 3, status: '已填待提交', chat_id: 'cY', account: '138****0000'}),
    task({company: '某互联网公司 Z', job: '科技投资（志愿一）、财务管培生（志愿二）', url: 'https://example.com/z/campus', color: 4, status: '已提交',
      chat_id: 'cZ', account: 'san.zhang@example.com', site_status: '志愿一 笔试/测评；志愿二 排队（网站写「志愿将按顺序依次流转」）',
      site_status_at: ts(0, 8, 30), readback: {status: {at: ts(0, 8, 30), text: '…'}, resume: {at: ts(0, 8, 31), text: '…'}},
      positions: [{name: '科技投资', location: '北京/上海', status: '笔试/测评'}, {name: '财务管培生', location: '上海', status: '排队'}]}),
    task({company: '某汽车集团 V', job: '无（不投）', url: 'https://example.com/v/jobs', status: '不投了', note: '看完 384 个岗位，没有投资类岗位'}),
  ];
  const CHATS = {};
  const msg = (role, text, extra = {}) => ({role, text, at: ts(0, 9), ...extra});
  const chat = (cid, title, task_id, messages) => (CHATS[cid] = {id: cid, title, task_id, session_id: 'demo-' + cid, running: false,
    cost_usd: 0, created_at: ts(0, 9), updated_at: ts(0, 9), messages});
  chat('cX', '某银行理财子 X', WS[1].id, [
    msg('user', '（面板）本人在网申页点了「让助手填」：请填这家网申：某银行理财子 X｜校招'),
    msg('tool', '打开 https://example.com/bank/campus'),
    msg('assistant', '这家要先登录才能填表。我在这个网页四周画了紫色框——请你在紫框网页里扫码登录，我留在这一轮里等，看到登录好了就接着填。\n【网申记录】公司：某银行理财子 X｜状态：等你处理｜要你做：在画了紫框的网页里扫码登录'),
    msg('system', '「网申」页：某银行理财子 X｜校招 → 等你处理')]);
  chat('cY', '某新能源集团 Y', WS[2].id, [
    msg('user', '（面板）本人在网申页点了「让助手填」：请填这家网申：某新能源集团 Y｜战略投资管培生'),
    msg('tool', '连续 9 步：填写：姓名；填写：手机；选择：学历；填写：学校；选择：毕业时间；填写：实习经历 1；填写：实习经历 2；上传：证件照；上传：单页中文简历'),
    msg('assistant', '能填的都填好了、也暂存了。证件号那一栏留给你（橙框网页「基本信息」第 4 栏）。检查没问题就在网站上提交，然后在网申页点「我已提交」。\n【网申记录】公司：某新能源集团 Y｜岗位：战略投资管培生｜账号：138****0000｜状态：已填待提交'),
    msg('system', '「网申」页：某新能源集团 Y｜战略投资管培生 → 已填待提交（看板里记了一条草稿）')]);
  chat('cZ', '某互联网公司 Z', WS[3].id, [
    msg('user', '（面板）某互联网公司 Z 本人已经提交了。请把这三样从网站上读回来记进投递看板，只看不改：投了哪些岗位、每个岗位的 JD、实际提交的简历。'),
    msg('system', '已记进投递看板：投递记录（124 字）'), msg('system', '已记进投递看板：岗位 JD「科技投资」（1206 字）'),
    msg('system', '已记进投递看板：实际提交的简历（1830 字）'),
    msg('assistant', '读回好了：投了 2 个志愿（科技投资、财务管培生），网站写「志愿将按顺序依次流转」，志愿一现在在笔试/测评。登录账号 san.zhang@example.com。')]);
  const timers = {};
  function play(cid, steps, done) {          // 按剧本一条条往对话里加，模拟助手在浏览器里一步步干活
    const c = CHATS[cid];
    c.running = true;
    let k = 0;
    const next = () => {
      if (!c.running) return;
      if (k >= steps.length) { c.running = false; c.updated_at = ts(0, 9); if (done) done(); return; }
      const [role, text] = steps[k++];
      c.messages.push(msg(role, text));
      timers[cid] = setTimeout(next, role === 'tool' ? 1100 : 1700);
    };
    timers[cid] = setTimeout(next, 600);
  }
  const FILL = t => [
    ['tool', '看看标签页'], ['tool', '打开 ' + t.url],
    ['assistant', `这是飞书招聘的申请表（${t.company}）。我先加载填表脚本，在这个网页四周画上${COLORS[t.color][2]}色框——你对着面板就知道这一页是我在填。\n【网申记录】公司：${t.company}｜岗位：${t.job}｜网址：${t.url}`],
    ['tool', `在页面上跑填表脚本：画框（${COLORS[t.color][2]}·${t.company}）`],
    ['tool', '连续 7 步：填写：姓名；填写：手机；填写：邮箱；选择：最高学历；填写：学校；选择：入学时间；选择：毕业时间'],
    ['tool', '上传文件：证件照（295×413）；单页中文简历'],
    ['tool', '连续 4 步：填写：实习经历 1；填写：实习经历 2；填写：自我评价；勾选：信息真实承诺'],
    ['tool', '截图看一眼'],
    ['assistant', `能填的都填好了、也暂存了：\n- 基本信息、教育、两段实习、自我评价：按网申底稿和简历原文填的（不改写、不编）；\n- 照片和单页中文简历我已经传上去了；\n- **证件号那一栏留给你**：${COLORS[t.color][2]}框网页「基本信息」第 3 栏。\n替你做的选择：期望城市选了上海（底稿里的第一优先）。\n检查没问题就在网站上点提交，然后在网申页点「我已提交」。\n【网申记录】公司：${t.company}｜岗位：${t.job}｜账号：138****0000｜状态：已填待提交`],
    ['system', `「网申」页：${t.company}｜${t.job} → 已填待提交（看板里记了一条草稿）`]];
  const CONTINUE = t => [
    ['tool', '看看标签页'], ['tool', '截图看一眼'],
    ['assistant', `看了一眼${COLORS[t.color][2]}框网页：已经登录好了，我接着填。`],
    ['tool', '连续 8 步：选择：志愿一 总行管培生；选择：志愿二 研究岗；填写：教育经历；填写：实习经历；填写：家庭成员；上传：证件照；上传：简历；勾选：是否服从调剂（按底稿：否）'],
    ['assistant', `填好了、也暂存了。证件号那一栏留给你。\n【网申记录】公司：${t.company}｜岗位：总行管培生（第一志愿）、研究岗（第二志愿）｜状态：已填待提交`],
    ['system', `「网申」页：${t.company} → 已填待提交（看板里记了一条草稿）`]];
  const READBACK = t => [
    ['user', `（面板）${t.company}｜${t.job} 本人已经提交了。请把这三样从网站上读回来记进投递看板，只看不改：投了哪些岗位、每个岗位的 JD、实际提交的简历。`],
    ['tool', '打开「我的投递」页'], ['tool', '在页面上跑填表脚本：读回投递记录'], ['system', '已记进投递看板：投递记录（86 字）'],
    ['tool', '打开岗位详情页'], ['system', `已记进投递看板：岗位 JD「${t.job.split('（')[0]}」（412 字）`],
    ['tool', '打开「查看简历」'], ['system', '已记进投递看板：实际提交的简历（1830 字）'],
    ['assistant', `读回好了：网站上显示「简历筛选中」，登录账号 138****0000。看板里这条记的就是网站上真交上去的内容，不是面板生成的稿子。\n【网申记录】公司：${t.company}｜状态：已提交`]];
  function boardRecord(t, status) {            // 填好 → 看板一条草稿；提交 → 已投递，带上读回的原文
    let r = RECORDS.find(x => x.id === 'ws-' + t.id);
    if (!r) {
      r = rec({id: 'ws-' + t.id, company_name: t.company, company_type: '人民币VC', job_title: t.job, position_type: '留用实习',
        job_location: '上海', focus_industry: '硬科技', sent_at: ts(0, 9, 30), to_email: '', send_mode: '未发邮件', resume_version: '网申上传',
        apply_url: t.url, apply_channel: '网申/链接', apply_account: t.account || '138****0000', subject: '', email_body: ''});
      RECORDS.unshift(r);
    }
    r.status = status;
    if (status === '已投递') Object.assign(r, {site_status: '简历筛选中', site_status_at: ts(0, 9, 40),
      ws_submitted: `【投递记录（从网站读回）】\n${t.company}｜${t.job}｜简历筛选中\n\n【实际提交的简历（从网站读回，证件号已隐去）】\n姓名：张三　手机：138****0000\n教育：某大学　硕士　2025.09 - 2027.06\n实习：A资本　投资实习生　2026 - 至今`});
  }
  const chatOut = (c, since = 0) => ({...c, total: c.messages.length, messages: c.messages.slice(since)});
  const lastLine = c => { const m = [...c.messages].reverse().find(x => x.role === 'assistant' || x.role === 'tool'); return m ? m.text.replace(/\n[\s\S]*/, '').slice(0, 60) : ''; };
  let PROFILE = null;
  const loadProfile = async () => PROFILE || (PROFILE = await realFetch('../wangshen_profile.example.json').then(r => r.json()).catch(() => ({})));

  // ── 看板上半部分（演示）：今天、按申请成组（同一家几个志愿一张卡，串行显示在看 / 排队）、单家详情、口述进展（可撤销）──
  const STAGE_LABEL = {'草稿': '准备中', '已投递': '已投递', '笔试': '笔试/测评', '已电联': '已电联', '面试中': '面试', 'offer': 'offer', '拒绝': '未通过', '无回复': '无回复', '放弃': '放弃'};
  const ORDER = ['草稿', '已投递', '笔试', '已电联', '面试中', 'offer'];
  const ENDED = ['拒绝', '无回复', '放弃'];
  const AX = {};          // 申请上的状态（下一步、建议、时间线、志愿方式、退信看过没有），按申请 id 存
  const ax = aid => AX[aid] || (AX[aid] = {next: {text: '', due: '', source: '', done: false}, sugg: [], timeline: [], mode: '', handled: false});
  const PROG = [];
  const tl = (aid, kind, text, by, at = ts(0, 9)) => ax(aid).timeline.push({at, kind, text, by});
  const VOL = {   // 某互联网公司 Z：两个志愿，串行（网站原话：志愿将按顺序依次流转）
    pos: [{id: 'zp1', job_title: '科技投资', location: '北京/上海', status: '笔试', site_status: '笔试/测评'}, {id: 'zp2', job_title: '财务管培生', location: '上海', status: '已投递', site_status: '排队'}]};
  const BANK = [{id: 'xp1', job_title: '总行管培生', location: '上海', status: '草稿', site_status: ''}, {id: 'xp2', job_title: '研究岗', location: '上海', status: '草稿', site_status: ''}];
  (() => {   // 演示开场的状态
    const d = RECORDS.find(r => r.company_name === '某产业资本 D'), a = RECORDS.find(r => r.company_name === '某美元VC A' && r.campaign === CUR);
    ax('a-' + d.id).next = {text: `参加面试（一面·线上，${ts(-1, 14, 0)}）`, due: ts(-1, 14, 0), source: '本人口述', done: false};
    tl('a-' + d.id, '进展', '面试邀请（一面）：「D 约了周四下午两点线上一面」', '本人', ts(1, 15, 10));
    ax('a-' + a.id).sugg.push({id: 'sA', kind: '阶段', text: '来信像是面试邀请：把「投资实习生（可留用）」改成「面试」？', state: '待定', payload: {record_id: a.id, to: '面试中'}});
    tl('a-' + a.id, '来信', '面试邀请：同学你好，简历已收到，方便明天下午电话聊一下吗？', '面板', ts(0, 11, 20));
    const z = WS[3];
    Object.assign(ax(z.id), {mode: '串行', note: '投递后志愿将按顺序依次流转'});
    ax(z.id).next = {text: '做测评（志愿一 科技投资）', due: ts(-2, 23, 59), source: '邮件', inferred: true, done: false};
    tl(z.id, '来信', '测评邀请：请于 3 天内完成在线测评', '面板', ts(0, 8, 0));
    Object.assign(ax(WS[1].id), {mode: '平行', note: '本次招聘可同时投递多个职位，互不影响'});
  })();
  const stageOf = pos => { const live = pos.filter(p => !ENDED.includes(p.status)); if (!pos.length) return '草稿';
    return live.length ? live.reduce((m, p) => ORDER.indexOf(p.status) > ORDER.indexOf(m) ? p.status : m, live[0].status) : pos[0].status; };
  function serial(mode, pos) {
    let cur = false;
    return pos.map((p, i) => {
      let s = '';
      if (mode === '串行') { if (ENDED.includes(p.status)) s = p.status === '拒绝' ? '未通过 · 已流转' : STAGE_LABEL[p.status]; else if (!cur) { s = '在看'; cur = true; } else s = '排队：前面的志愿有结果后才看'; }
      return {choice_no: i + 1, choice_guessed: false, org: '', has_jd: true, sent_at: p.sent_at || '', reply_status: '', reply_kind: '', ...p, stage_label: STAGE_LABEL[p.status], serial_state: s};
    });
  }
  function apCard(kind, src) {
    const isWs = kind === 'ws', aid = isWs ? src.id : 'a-' + src.id, A = ax(aid);
    let pos;
    if (isWs) {
      const r = RECORDS.find(x => x.id === 'ws-' + src.id);
      pos = src === WS[3] ? VOL.pos : src === WS[1] && src.status !== '已提交' ? BANK
        : [{id: r ? r.id : src.id + '-p', job_title: src.job, location: '', status: r ? r.status : (src.status === '不投了' ? '放弃' : '草稿'), site_status: src.site_status || ''}];
    } else pos = [{id: src.id, job_title: src.job_title, location: src.job_location, status: src.status, site_status: src.site_status || '', sent_at: src.sent_at}];
    const mode = A.mode, pv = serial(mode, pos), cur = pv.find(p => p.serial_state === '在看');
    let stage = cur ? cur.status : stageOf(pos);
    const phase = isWs ? {'待填': '待开始', '助手在填': '在填', '等你处理': '等你', '已填待提交': '待你提交', '已提交': '已提交', '不投了': '已放弃'}[src.status] : '';
    const c = {id: aid, company: isWs ? src.company : src.company_name, channel: isWs ? '网申' : '邮件', campaign: isWs ? CUR : src.campaign,
      color: isWs ? src.color : null, color_label: isWs ? COLORS[src.color][2] : '灰', color_hex: isWs && src.status !== '不投了' ? COLORS[src.color][1] : '',
      web_phase: phase, first_submitted_at: isWs ? (src.status === '已提交' ? ts(1, 21, 29) : '') : (src.send_mode === '发送' ? src.sent_at : ''), first_submitted_guess: false,
      account: isWs ? src.account : '', volunteer_mode: mode, volunteer_note: A.note || '', site_progress: isWs ? src.site_status : (src.site_status || ''),
      chat_id: isWs ? src.chat_id : '', need: src.status === '等你处理' ? {kind: /登录|扫码/.test(src.todo) ? '登录' : /证件/.test(src.todo) ? '证件号' : '回答', text: src.todo} : null,
      mail_status: '', positions: pv, reply: isWs ? {} : {status: src.reply_status, at: src.reply_at, from: src.reply_from, snippet: src.reply_snippet, handled: A.handled},
      entry_url: isWs ? src.url : '', suggestions: A.sugg.filter(s => s.state === '待定'), next: A.next, stage, stage_label: STAGE_LABEL[stage] || stage,
      sub: [phase].filter(Boolean), stage_counts: {}, last_activity: (A.timeline[A.timeline.length - 1] || {}).at || (isWs ? src.updated_at : src.sent_at),
      last_text: (A.timeline[A.timeline.length - 1] || {}).text || (isWs ? src.status : '投出'), todo: '', button: null, why: ''};
    if (pv.length > 1 && mode !== '串行') pv.forEach(p => { c.stage_counts[p.stage_label] = (c.stage_counts[p.stage_label] || 0) + 1; });
    if (Object.keys(c.stage_counts).length < 2) c.stage_counts = {};
    const turn = (t, todo = '', button = null) => Object.assign(c, {turn: t, todo, button});
    const n = c.next, chatBusy = isWs && CHATS[src.chat_id] && CHATS[src.chat_id].running;
    if (src.status === '不投了' || (!isWs && (ENDED.includes(src.status) || src.status === 'offer') && !c.suggestions.length)) return turn('已结束');
    if (!isWs && src.reply_status === '退信' && !A.handled) return turn('轮到你', '这封信被退回来了：换个邮箱重投', {label: '看退信', action: 'open_reply'});
    if (!isWs && src.send_mode === '草稿') return turn('轮到你', '存在 Gmail 草稿里还没发', {label: '去看信', action: 'open_mail'});
    if (chatBusy) return turn('面板在做', '助手在填：' + lastLine(CHATS[src.chat_id]));
    if (phase === '等你') return turn('轮到你', src.todo, {label: c.need.kind === '登录' ? '我登录好了' : c.need.kind === '证件号' ? '我填好了' : '我弄完了', action: 'need_done'});
    if (phase === '待你提交') return turn('轮到你', '填好了：检查一下，在网站上提交', {label: '我已在网站上提交', action: 'submitted'});
    if (c.suggestions.length) return turn('轮到你', c.suggestions[0].text, {label: '看看', action: 'review'});
    if (n.text && !n.done && (n.source === '本人口述' || n.due)) return turn('轮到你', n.text + (n.due && !n.text.includes(n.due) ? `（截止 ${n.due}）` : ''), {label: '做完了', action: 'step_done'});
    if (phase === '待开始') return turn('轮到你', '还没开始填', {label: '让助手填', action: 'run'});
    if (phase === '在填') return turn('停了', '助手停了：这一轮停下了', {label: '让助手接着做', action: 'continue'});
    return turn('等对方');
  }
  const RANK = {'轮到你': 0, '停了': 1, '面板在做': 2, '等对方': 3, '已结束': 4};
  const appsAll = () => [...WS.map(t => apCard('ws', t)), ...RECORDS.filter(r => !r.id.startsWith('ws-') && r.send_mode !== '未发邮件').map(r => apCard('rec', r)),
    ...RECORDS.filter(r => r.send_mode === '未发邮件' && !r.id.startsWith('ws-')).map(r => apCard('rec', r))];
  const appsOf = camp => appsAll().filter(c => !camp || camp === '全部' || c.campaign === camp)
    .sort((a, b) => (RANK[a.turn] - RANK[b.turn]) || String(b.last_activity).localeCompare(String(a.last_activity)));
  function progApply(text) {     // 演示版：按关键词拆，不调 AI（真实面板用一次结构化 AI 调用）
    const before = JSON.parse(JSON.stringify({AX, VOL, BANK, RECORDS: RECORDS.map(r => ({id: r.id, status: r.status}))}));
    const entry = {id: 'p' + (++id), at: ts(0, new Date().getHours(), new Date().getMinutes()), text, changes: [], applied: [], ask: [], before};
    const cards = appsOf(CUR);
    for (const part of text.split(/[；;。\n]/).map(s => s.trim()).filter(Boolean)) {
      const flat = part.replace(/\s/g, '');
      const hits = cards.filter(c => flat.includes(c.company.replace(/\s/g, '')) || flat.includes(c.company.replace(/^某/, '').replace(/\s/g, '')));
      const ev = /挂|拒/.test(part) ? '拒绝' : /offer/i.test(part) ? 'offer' : /做完|完成|面完|交了/.test(part) ? (/AI ?面/.test(part) ? 'AI面完成' : /面/.test(part) ? '面试完成' : /测评/.test(part) ? '测评完成' : '笔试完成')
        : /AI ?面/.test(part) ? 'AI面邀请' : /面试|一面|二面|约/.test(part) ? '面试邀请' : /测评/.test(part) ? '测评邀请' : /笔试/.test(part) ? '笔试邀请' : '';
      if (!ev) continue;
      if (hits.length !== 1) { entry.ask.push({company: part.slice(0, 12), event: ev, position: '', round: '', happened_at: '', due: '', quote: part, candidates: hits.slice(0, 6).map(c => ({app_id: c.id, company: c.company}))}); continue; }
      const c = hits[0], A = ax(c.id), stage = {'拒绝': '拒绝', 'offer': 'offer'}[ev] || (/面/.test(ev) ? '面试中' : '笔试');
      const recs = c.id === WS[3].id ? VOL.pos : RECORDS.filter(r => 'a-' + r.id === c.id || r.id === 'ws-' + c.id);
      recs.filter(r => !ENDED.includes(r.status)).slice(0, c.volunteer_mode === '串行' ? 1 : 99).forEach(r => { if (stage === '拒绝' || ORDER.indexOf(stage) > ORDER.indexOf(r.status)) r.status = stage; });
      const due = /周日/.test(part) ? ts(-((7 - now.getDay()) % 7 || 7)).slice(0, 10) : '';
      if (/邀请$/.test(ev)) A.next = {text: ({'笔试邀请': '做笔试', '测评邀请': '做测评', 'AI面邀请': '做 AI 面', '面试邀请': '参加面试'}[ev]) + (due ? `（${due} 截止）` : '（截止没说）'), due, source: '本人口述', done: false};
      else if (/完成$/.test(ev) && A.next.text) A.next = {...A.next, done: true};
      tl(c.id, '进展', `${ev}：「${part}」`, '本人', entry.at);
      entry.applied.push({app_id: c.id, company: c.company, company_name: c.company, event: ev, round: '', due, quote: part});
    }
    if (entry.applied.length) PROG.unshift(entry);
    return entry;
  }

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
    if (path === '/api/send' && body.queue_id) { const it = QUEUE.find(x => x.id === body.queue_id); if (it) Object.assign(it, {status: body.mode === 'draft' ? '已存草稿' : '已发送', updated_at: 's' + Date.now()}); }
    if (path === '/api/send') return json({ok: true, mode: body.mode === 'draft' ? '草稿' : '发送', attachments: [body.result.resume_filename, body.result.attach_report ? body.result.report_filename : ''].filter(Boolean), record_id: 'demo-sent'});
    if (path === '/api/record') {
      if (body.record_id) return json({ok: true, record_id: body.record_id, merged: true});
      const it = QUEUE.find(x => x.id === body.queue_id); if (it) Object.assign(it, {status: '已记录', updated_at: 'w' + Date.now()});
      return json({ok: true, record_id: 'demo-ws'});
    }
    if (path === '/api/kit') return json(KIT);
    if (path === '/api/wangshen') { await sleep(1500); return json({kit: JSON.parse(JSON.stringify(WS_KIT)), meta: {backend: '会员额度'}}); }
    if (path === '/api/records' && method === 'GET') return json([...filtered].sort((a, b) => b.sent_at.localeCompare(a.sent_at)).map(r => ({...r, _type: r.company_type})));
    if (path.startsWith('/api/records/')) return json({ok: true});
    if (path === '/api/stats') return json(stats(filtered));
    if (path === '/api/queue' && method === 'GET') return json({items: QUEUE, counts: qCounts(), workers: 3});
    if (path === '/api/queue' && method === 'POST') {
      const lines = String(body.text || '').split('\n').filter(l => l.trim());
      const n = lines.every(l => /^https?:\/\//.test(l.trim())) ? lines.length : 1;
      for (let k = 0; k < n; k++) {
        const it = qItem('处理中', null); QUEUE.push(it);
        setTimeout(() => { const r = JSON.parse(JSON.stringify(RESULT)); r.company_name = '某新机构 ' + it.id.toUpperCase();
          Object.assign(it, {status: '待审核', analysis: {result: r, issues: check(r), fixes: [], related: [], meta: {model: 'claude-opus-5-5', seconds: 19.8, backend: '会员额度'}}, updated_at: ts(0, 10, 0) + it.id}); }, 6000 + k * 1500);
      }
      return json({ok: true, added: n});
    }
    if (path.startsWith('/api/queue/') && path.endsWith('/retry')) { const it = QUEUE.find(x => path.includes(x.id)); if (it) Object.assign(it, {status: '处理中', error: '', updated_at: 'r' + Date.now()}); return json({ok: true}); }
    if (path === '/api/queue/clear-done') { for (let k = QUEUE.length - 1; k >= 0; k--) if (['已发送', '已存草稿', '已记录'].includes(QUEUE[k].status)) QUEUE.splice(k, 1); return json({ok: true}); }
    if (path === '/api/queue/process-ready') return json({done: [], skipped: QUEUE.filter(x => x.status === '待审核').map(x => x.analysis.result.company_name + '：演示版不会真的发送')});
    if (path.startsWith('/api/queue/') && method === 'DELETE') { const k = QUEUE.findIndex(x => path.endsWith(x.id)); if (k >= 0) QUEUE.splice(k, 1); return json({ok: true}); }
    if (path === '/api/schedule') {   // 演示：定时发送（不会真的发）
      const it = QUEUE.find(x => x.id === body.queue_id);
      if (it) Object.assign(it, {status: '已定时', send_at: '明天 10:00', updated_at: 's' + Date.now()});
      return json({ok: !!it, send_at: '2099-01-01 10:00'});
    }
    if (path === '/api/queue/schedule-drafts') {
      const ds = QUEUE.filter(x => x.status === '已存草稿');
      ds.forEach(x => Object.assign(x, {status: '已定时', send_at: '明天 10:00', updated_at: 'd' + Date.now()}));
      return json({ok: true, scheduled: ds.length, send_at: '2099-01-01 10:00'});
    }
    if (path.endsWith('/regen')) {   // 演示：重写 4 秒后「写好」
      const it = QUEUE.find(x => path.includes(x.id));
      if (it) { Object.assign(it, {status: '重写中', updated_at: 'g' + Date.now()});
        setTimeout(() => Object.assign(it, {status: '待审核', rev: 'r' + Date.now(), updated_at: 'h' + Date.now()}), 4000); }
      return json({ok: !!it});
    }
    if (path.startsWith('/api/queue/')) return json({ok: true});
    if (path === '/api/check-replies') { await sleep(900); return json({ok: true, logs: ['（演示）检查 12 条记录的回复…', '完成：3 条有回复 / 来信，1 条退信']}); }
    if (path === '/api/gmail-sync') { await sleep(900); return json({ok: true, logs: ['（演示）Gmail 已发送里没有遗漏的投递']}); }
    if (path === '/api/health') return json({app: 'jobapply', env: 'demo', version: 'demo'});
    if (path === '/api/wangshen-profile' && method === 'GET') return json({profile: await loadProfile(), example: false, missing: [], notes: []});
    if (path === '/api/wangshen-profile' && method === 'PUT') { PROFILE = body.profile || PROFILE; return json({ok: true, profile: PROFILE, missing: [], notes: []}); }
    if (path === '/api/wstasks' && method === 'GET') {
      const tasks = WS.map(t => { const c = CHATS[t.chat_id]; const busy = c && c.running;
        return {...t, agent_state: busy ? '在干活' : '', agent_wait: '', last: busy ? lastLine(c) : ''}; });
      const counts = tasks.reduce((o, t) => (o[t.status] = (o[t.status] || 0) + 1, o), {});
      return json({tasks, counts, active: Object.values(CHATS).filter(c => c.running).length, waiting: 0, max_parallel: 3, colors: COLORS});
    }
    if (path === '/api/wstasks' && method === 'POST') {
      const t = task({company: body.company || '', job: body.job || '', url: body.url || '', source: '网申页贴的链接', color: WS.length % 8});
      WS.unshift(t); return json({task: t});
    }
    const wt = path.match(/^\/api\/wstasks\/([^/]+)(\/(agent|readback))?$/);
    if (wt) {
      const t = WS.find(x => x.id === wt[1]);
      if (!t) return json({error: '这条待办不存在了'}, 404);
      if (method === 'DELETE') { WS.splice(WS.indexOf(t), 1); return json({ok: true}); }
      if (wt[3] === 'agent') {
        if (t.chat_id && CHATS[t.chat_id]) {
          const c = CHATS[t.chat_id];
          c.messages.push(msg('user', `（面板）本人在网申页点了「让助手接着做」。${t.todo ? '你上次说要本人做的是：' + t.todo + '。先看一眼网页确认这件事做好了没有。' : ''}接着填这家：${t.company}｜${t.job}。`));
          Object.assign(t, {status: '助手在填', todo: ''});
          play(c.id, CONTINUE(t), () => { Object.assign(t, {status: '已填待提交', job: '总行管培生（第一志愿）、研究岗（第二志愿）'}); boardRecord(t, '草稿'); });
          return json(chatOut(c));
        }
        const c = chat('c' + (++id), t.company, t.id, [msg('user', `（面板）本人在网申页点了「让助手填」：请填这家网申：${t.company}｜${t.job}\n网申链接：${t.url}`)]);
        Object.assign(t, {chat_id: c.id, status: '助手在填'});
        play(c.id, FILL(t), () => { Object.assign(t, {status: '已填待提交', account: '138****0000'}); boardRecord(t, '草稿'); });
        return json(chatOut(c));
      }
      if (wt[3] === 'readback' || (method === 'PUT' && body.status === '已提交' && t.status !== '已提交')) {
        Object.assign(t, {status: '已提交', readback_state: '读回中', todo: ''});
        boardRecord(t, '已投递');
        let c = CHATS[t.chat_id] || chat(t.chat_id = 'c' + (++id), t.company, t.id, []);
        play(c.id, READBACK(t), () => Object.assign(t, {readback_state: '', site_status: '简历筛选中', site_status_at: ts(0, 9, 40),
          readback: {status: {at: ts(0, 9, 40), text: '…'}, resume: {at: ts(0, 9, 41), text: '…'}}}));
        const message = '助手去网站读回实际提交的内容了（右下角看进度）';
        return json(wt[3] === 'readback' ? {chat: chatOut(c), message, task: t} : {task: t, readback: message});
      }
      if (method === 'PUT') {
        ['company', 'job', 'url', 'note', 'deadline', 'account'].forEach(k => { if (k in body) t[k] = body[k]; });
        if (body.status) Object.assign(t, {status: body.status, todo: body.status === '等你处理' ? t.todo : ''});
        return json({task: t});
      }
    }
    if (path === '/api/agent' && method === 'GET') {
      const list = Object.values(CHATS).map(c => ({id: c.id, title: c.title, updated_at: c.updated_at, running: c.running, n: c.messages.length, task_id: c.task_id}))
        .sort((a, b) => (b.running - a.running) || b.updated_at.localeCompare(a.updated_at));
      const run = list.filter(c => c.running).map(c => c.id);
      return json({chats: list, running: run[0] || null, active: run, waiting: {}, max_parallel: 3});
    }
    if (path === '/api/agent/new') return json(chatOut(chat('c' + (++id), '', '', [])));
    const ag = path.match(/^\/api\/agent\/([^/]+)(\/(send|stop))?$/);
    if (ag && CHATS[ag[1]]) {
      const c = CHATS[ag[1]];
      if (ag[3] === 'stop') { c.running = false; clearTimeout(timers[c.id]); c.messages.push(msg('system', '已停下：这一轮中断了，进程和它开的网页都还在。')); return json({ok: true}); }
      if (ag[3] === 'send') {
        c.title = c.title || String(body.text || '').slice(0, 24);
        c.messages.push(msg('user', String(body.text || '')));
        play(c.id, [['assistant', '（演示版）收到。真实面板里，助手会按这句话接着在网页上操作，干活时你随时可以插话。']]);
        return json(chatOut(c));
      }
      if (method === 'DELETE') { delete CHATS[c.id]; return json({ok: true}); }
      return json(chatOut(c, Number(url.searchParams.get('since') || 0)));
    }
    if (path === '/api/apps/overview') return json({apps: appsOf(url.searchParams.get('campaign') || CUR)});
    if (path === '/api/apps/today') {
      const cards = appsOf(CUR);
      return json({your_turn: cards.filter(c => c.turn === '轮到你'), stopped: cards.filter(c => c.turn === '停了'),
        soon: cards.filter(c => c.turn !== '轮到你' && c.turn !== '已结束' && c.next.due && !c.next.done),
        busy: cards.filter(c => c.turn === '面板在做').length, waiting: cards.filter(c => c.turn === '等对方').length,
        mail_review: QUEUE.filter(x => ['待审核', '需处理', '失败'].includes(x.status)).length,
        recent_progress: PROG.slice(0, 5).map(({before, ...e}) => e)});
    }
    const apm = path.match(/^\/api\/apps\/([^/]+)(?:\/([a-z-]+)(?:\/([^/]+))?)?$/);
    if (apm) {
      const aid = decodeURIComponent(apm[1]), act = apm[2], A = ax(aid), c = appsAll().find(x => x.id === aid);
      if (!c) return json({error: '这次申请不存在了'}, 404);
      if (!act) {
        const t = WS.find(x => x.id === aid), r0 = RECORDS.find(x => 'a-' + x.id === aid || x.id === 'ws-' + aid);
        const snaps = t === WS[3] ? [{kind: '投递记录', at: ts(0, 8, 30), text: '我的投递\n志愿一 科技投资（北京/上海）　笔试/测评\n志愿二 财务管培生（上海）　排队\n说明：投递后志愿将按顺序依次流转。'}]
          : r0 && r0.ws_submitted ? [{kind: '投递记录', at: r0.site_status_at || ts(0, 9), text: r0.ws_submitted}] : [];
        return json({card: c, positions: c.positions.map(p => ({...p, jd_text: (RECORDS.find(r => r.id === p.id) || {}).jd_text || '（演示数据：JD 原文略）',
          jd_url: '', history: [], notes: '', to_email: '', subject: '', email_body: '', ws_submitted: ''})),
          snapshots: snaps, timeline: [...A.timeline].reverse(), next_step: A.next, mail: null, notes: '', ai_drafts: [], volunteer_hints: []});
      }
      if (act === 'step-done' && A.next.text && !A.next.done) { A.next = {...A.next, done: true}; tl(aid, '下一步', '做完了：' + A.next.text, '本人'); }
      if (act === 'step-undo' && A.next.done) { A.next = {...A.next, done: false}; tl(aid, '下一步', '改回没做完：' + A.next.text, '本人'); }
      if (act === 'volunteer-mode') { A.mode = body.mode || ''; tl(aid, '志愿', '志愿方式 → ' + (A.mode || '未知'), '本人'); }
      if (act === 'reply-handled') { A.handled = true; tl(aid, '来信', '看过了：退信', '本人'); }
      if (act === 'suggestions') {
        const s = A.sugg.find(x => x.id === apm[3]);
        if (s && s.state === '待定') {
          s.state = body.action === 'accept' ? '采纳' : '不用';
          const r = s.state === '采纳' && RECORDS.find(x => x.id === s.payload.record_id);
          if (r) r.status = s.payload.to;
          tl(aid, '建议', (s.state === '采纳' ? '采纳：' : '不用：') + s.text, '本人');
        }
      }
      return json({ok: true, result: true});
    }
    if (path === '/api/progress' && method === 'POST') {
      const text = String(body.text || '').trim();
      if (!text) return json({error: '说点什么，比如「收到某硬科技PE C的笔试，周日截止」'}, 400);
      await sleep(600);
      const e = Array.isArray(body.events) && body.events[0]
        ? progApply((appsAll().find(x => x.id === body.events[0].app_id) || {}).company + body.events[0].event) : progApply(text);
      const {before, ...out} = e;
      return json({...out, text});
    }
    if (path === '/api/progress/recent') return json({items: PROG.map(({before, ...e}) => e)});
    const pu = path.match(/^\/api\/progress\/([^/]+)\/undo$/);
    if (pu) {
      const e = PROG.find(x => x.id === pu[1]);
      if (!e) return json({error: '没找到这条口述记录'}, 404);
      if (e.undone) return json({ok: true, undone: 0});
      Object.keys(AX).forEach(k => delete AX[k]);
      Object.assign(AX, JSON.parse(JSON.stringify(e.before.AX)));
      VOL.pos.splice(0, VOL.pos.length, ...e.before.VOL.pos);
      BANK.splice(0, BANK.length, ...e.before.BANK);
      e.before.RECORDS.forEach(x => { const r = RECORDS.find(y => y.id === x.id); if (r) r.status = x.status; });
      e.undone = true;
      if (e.applied[0]) tl(e.applied[0].app_id, '撤销', `撤销了口述：「${e.text}」`, '本人');
      return json({ok: true, undone: e.applied.length});
    }
    if (path === '/api/idcard' && method === 'GET') return json({saved: false, masked: ''});
    if (path.startsWith('/api/idcard')) return json({error: '演示版不存证件号（真实面板里只存在本机的钥匙串里，点「复制证件号」时直接进本机剪贴板）'}, 400);
    return json({error: '演示版不支持这个操作'}, 400);
  };

  document.addEventListener('DOMContentLoaded', () => {
    const bar = document.createElement('div');
    bar.className = 'fixed bottom-[70px] md:bottom-auto md:top-0 left-0 right-0 z-[80] bg-amber-400 text-amber-950 text-xs text-center py-1 font-bold';   // 手机上放在底部导航上面，不盖住导航的字
    bar.textContent = '演示版：所有机构、邮箱、人物均为虚构，不连接 AI、不会发送任何邮件';
    document.body.appendChild(bar);
    const st = document.createElement('style');   // 电脑上演示条在顶上：右边两个抽屉从它下面开始，标题和关闭按钮不被盖住
    st.textContent = '@media (min-width: 768px) { #appDrawer, #agentDrawer { top: 24px; } }';
    document.head.appendChild(st);
    // 导览：第一次打开时弹出（之后右下角「导览」按钮随时再看）
    const go = tab => { const el = document.querySelector(`.nav-link[data-tab="${tab}"]`) || document.querySelector(`[data-tab="${tab}"]`); if (el) el.click(); };
    const steps = [
      ['投递', 'new', '贴进链接或 JD，后台写好一封自动打开一封。点队列里的「某硬科技PE C」：左边是 AI 读出来的 JD 硬性要求，中间是写好的信，右边是发信前检查——红色的问题不改不让发，强制发送也发不出去。'],
      ['网申', 'kit', '只能网申的岗位放在这里，一家一行、一个颜色。点「某硬科技基金 W」那行的「让助手填」，右下角看助手一步步在浏览器里填表、传照片，证件号留给本人；填好后点「我已提交」，看它去网站读回实际提交的内容。'],
      ['投递看板', 'board', '顶上是「今天」：轮到你的事（看信、做测评、退信、登录）每条一个按钮；口述框里随口说一句「收到某硬科技PE C的笔试，周日截止」，面板拆成事件、记进那家、可撤销。下面一行一次申请：同一家投的几个志愿合成一张卡（某互联网公司 Z 是串行志愿：志愿一在看、志愿二排队），点开看这家的详情、时间线、从网站读回的原文。'],
      ['数据分析', 'analytics', '投递节奏、机构类型、地点、阶段分布。'],
    ];
    const card = document.createElement('div');
    card.id = 'demoTour';
    card.className = 'fixed inset-0 z-[90] bg-slate-900/50 flex items-center justify-center p-4';
    card.innerHTML = `<div class="bg-white rounded-2xl shadow-2xl max-w-xl w-full p-6 text-sm text-slate-700 max-h-[90vh] overflow-y-auto">
      <h2 class="text-lg font-bold text-slate-900 mb-1">求职投递面板 · 演示版</h2>
      <p class="mb-3">一个人用的求职投递系统：贴 JD → AI 起草邮件 → <b>代码逐项核对</b>（邮箱必须在 JD 原文里、占位没填不让发……）→ 发送或定时发送；
      只能网申的岗位交给面板里的<b>助手在浏览器里代填</b>，本人提交后它把网站上<b>实际提交的内容读回来</b>记进看板。</p>
      <p class="font-bold text-slate-900 mb-2">建议按这个顺序看：</p>
      <ol class="space-y-2 mb-4">${steps.map(([name, tab, text], i) => `<li class="flex gap-3"><button data-go="${tab}" class="shrink-0 h-7 px-2 rounded-lg bg-primary text-white text-xs font-bold">${i + 1} ${name}</button><span>${text}</span></li>`).join('')}</ol>
      <p class="text-xs text-slate-500 mb-4">所有机构、人物、邮箱、账号都是虚构的；演示版不连 AI、不发邮件、不打开任何网站。源码和设计说明见 GitHub README。</p>
      <div class="flex justify-end gap-2"><button data-go="kit" class="px-4 py-2 rounded-xl border-2 border-primary text-primary font-bold text-xs">先看网申助手</button>
      <button data-go="new" class="px-4 py-2 rounded-xl bg-primary text-white font-bold text-xs">开始看</button></div></div>`;
    const close = () => card.remove();
    card.addEventListener('click', e => {
      const b = e.target.closest('[data-go]');
      if (b) { close(); go(b.dataset.go); }
      else if (e.target === card) close();
    });
    const pill = document.createElement('button');
    pill.className = 'fixed left-4 bottom-20 md:left-[17rem] md:bottom-4 z-[85] px-3 py-1.5 rounded-full bg-slate-900 text-white text-xs font-bold shadow-lg';
    pill.textContent = '导览';
    pill.onclick = () => document.body.appendChild(card);
    document.body.appendChild(pill);
    let seen = false;
    try { seen = localStorage.getItem('demoTourSeen') === '1'; localStorage.setItem('demoTourSeen', '1'); } catch (e) {}
    if (!seen) document.body.appendChild(card);
    ['previewResume', 'downloadResume', 'exportBtn', 'wsResumeDl'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('click', e => { e.preventDefault(); e.stopImmediatePropagation(); if (window.toast) toast('演示版不提供文件下载'); }, true);
    });
  });
})();
