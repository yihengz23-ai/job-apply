"""发信前的确定性检查（不靠 AI）：自动修正能安全修的，其余按严重程度报出来。

level:
  error  —— 不处理就不该发（剪贴板模式直接拦下；网页面板需要点「仍然发送」）
  warn   —— 建议看一眼
  info   —— 提示
"""

import re
from datetime import datetime, date

from . import config

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
PLACEHOLDER_RE = re.compile(r"\[[^\[\]\n]{1,30}\]")
PUBLIC_DOMAINS = {
    "gmail.com", "163.com", "126.com", "qq.com", "foxmail.com", "sina.com", "sina.cn",
    "sohu.com", "yeah.net", "139.com", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "yahoo.com", "aliyun.com", "88.com",
}

COURTESY_BANNED = ["深感荣幸", "充满热情", "百忙之中", "祝好", "此致", "我相信我的经历",
                   "深感兴趣", "非常荣幸", "不胜感激", "高度契合", "深度契合", "完美契合",
                   "完美匹配", "高度匹配", "宝贵的机会", "宝贵机会", "Dear Hiring Manager",
                   "尊敬的招聘", "HR您好", "招聘负责人您好"]
# 关于候选人本人的能力 / 资历说法：简历和档案里没有就报（JD 里提到不算数）
CLAIMS = ["CFA", "CPA", "FRM", "ACCA", "法律职业资格", "博士", "PhD", "获奖", "一等奖", "金奖",
          "奖学金", "发表", "论文", "专利", "主导", "独立负责", "牵头", "建模", "财务模型", "DCF", "LBO",
          "三张表", "精通"]
# 机构名 / 套话：JD、简历、档案里都没有才报
ENTITIES = ["哈佛", "斯坦福", "清华", "北大", "复旦", "高盛", "摩根", "黑石", "红杉", "赋能", "助力"]
HONORIFICS_ZH = ["总", "老师", "女士", "先生", "经理", "博士", "同学"]
FILENAME_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]')


def split_emails(value):
    """把 list / 'a;b, c' / 'mailto:a' 统一成去重的小写邮箱列表。"""
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    out = []
    for item in value:
        for m in EMAIL_RE.findall(str(item).replace("＠", "@")):
            e = m.strip().strip(".").lower()
            if e not in out:
                out.append(e)
    return out


def _issue(level, field, msg):
    return {"level": level, "field": field, "msg": msg}


def _is_en(result):
    return result.get("jd_language") == "英文"


def _strip_emails(text):
    return EMAIL_RE.sub(" ", text or "")


def sanitize_filename(name, default):
    name = (name or "").strip() or default
    name = FILENAME_BAD.sub("-", name)
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


# ── 自动修正（安全的、确定性的）────────────────────────────────

def _greeting_parts(body):
    lines = body.split("\n")
    first = lines[0].strip() if lines else ""
    return first, lines


def _greeting_name(first):
    """从称呼行里拆出「名字」部分；通用称呼返回 ''。"""
    s = first.rstrip("，,:： ")
    if s in ("您好", "你好", "Hello", "Hi", "Hi there", "Dear all", "Hello there"):
        return ""
    m = re.match(r"^(?:Hi|Hello|Dear)\s+(.+)$", s, re.I)
    if m:
        name = re.sub(r"^(Mr|Ms|Mrs|Miss|Dr)\.?\s+", "", m.group(1).strip(), flags=re.I)
        return name.strip()
    if s.endswith("您好") or s.endswith("你好"):
        name = s[:-2].strip()
        for h in HONORIFICS_ZH:
            if name.endswith(h) and len(name) > len(h):
                name = name[: -len(h)]
                break
        return name.strip()
    return ""


def autofix(result, jd_text):
    """就地修正 result，返回修正说明列表。"""
    fixes = []
    en = _is_en(result)
    # 邮箱统一格式
    result["to_emails"] = split_emails(result.get("to_emails"))
    result["cc_emails"] = [e for e in split_emails(result.get("cc_emails")) if e not in result["to_emails"]]

    body = (result.get("email_body") or "").replace("\r\n", "\n").strip()
    # 称呼必须有 JD 依据（防止从邮箱地址猜名字）
    first, lines = _greeting_parts(body)
    name = _greeting_name(first)
    if name:
        jd_plain = _strip_emails(jd_text).lower()
        contact = (result.get("contact_in_jd") or "").strip()
        contact_ok = bool(contact) and contact.lower() in jd_plain
        name_ok = contact_ok and (name.lower() in contact.lower() or name[0] == contact[0])
        if not name_ok:
            generic = "Hello," if en else "您好，"
            lines[0] = generic
            body = "\n".join(lines)
            result["contact_in_jd"] = ""
            fixes.append(f"称呼「{first}」在 JD 原文里找不到依据（可能是从邮箱地址猜的），已改成「{generic}」")
    # 统一成简历上的写法（candidate_settings.json 的 spelling_fixes）
    for wrong, right in config.SPELLING_FIXES.items():
        if wrong in body:
            body = body.replace(wrong, right)
            fixes.append(f"「{wrong}」已改成简历上的写法「{right}」")
    result["email_body"] = body.strip() + "\n"

    subject = re.sub(r"\s+", " ", (result.get("email_subject") or "").strip())
    result["email_subject"] = subject

    default_resume = config.RESUME_DEFAULT_EN if result.get("resume_version") == "英文" else config.RESUME_DEFAULT_ZH
    for key, default in (("resume_filename", default_resume),
                         ("resume_filename_en", config.RESUME_DEFAULT_EN),
                         ("report_filename", config.REPORT_DEFAULT_NAME)):
        old = result.get(key) or ""
        if key == "resume_filename_en" and result.get("resume_version") != "中英两份":
            result[key] = ""
            continue
        if key == "report_filename" and not result.get("attach_report"):
            result[key] = ""
            continue
        new = sanitize_filename(old, default)
        if old and new != old:
            fixes.append(f"文件名「{old}」已规范为「{new}」")
        result[key] = new
    return fixes


# ── 检查 ───────────────────────────────────────────────────

def _numbers_allowed(jd_text, extra_texts):
    pool = jd_text + "\n" + "\n".join(extra_texts)
    nums = set(re.findall(r"\d+(?:\.\d+)?", pool))
    now = datetime.now()
    nums |= {str(now.year), str(now.month), str(now.day), str(now.year + 1), "1", "2"}
    return nums


def _parse_date(s):
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def run(result, jd_text, *, related=(), resume_status=None, profile_text="",
        rules_text="", source_label="", now=None):
    """返回问题列表（不修改 result）。"""
    issues = []
    now = now or datetime.now()
    en = _is_en(result)
    jd_lower = (jd_text or "").lower().replace("＠", "@")
    to, cc = split_emails(result.get("to_emails")), split_emails(result.get("cc_emails"))
    subject = result.get("email_subject") or ""
    body = result.get("email_body") or ""
    channel = result.get("apply_channel") or ""
    rules = result.get("jd_rules") or {}

    # 收件人
    if not to:
        if channel == "网申/链接":
            issues.append(_issue("info", "to", "这个岗位要求网申 / 链接投递：打开链接投完后点「记录网申」即可，不用发邮件。"))
        else:
            issues.append(_issue("error", "to", "没有收件邮箱。JD 里如果确实有投递邮箱，请手动填上。"))
    for e in to + cc:
        if e in jd_lower:
            continue
        local, _, domain = e.partition("@")
        if local and domain and local in jd_lower and domain in jd_lower:
            issues.append(_issue("warn", "to", f"邮箱 {e} 在 JD 里是变形写法（如 [at]、#），请核对拼写。"))
        else:
            issues.append(_issue("error", "to", f"邮箱 {e} 在 JD 原文里找不到，可能是 AI 编的或抄错了，请核对。"))
    if config.SENDER_EMAIL.lower() in to + cc:
        issues.append(_issue("error", "to", "收件人里有你自己的邮箱。"))

    # 占位符
    for field, text in (("subject", subject), ("body", body),
                        ("resume_filename", result.get("resume_filename") or ""),
                        ("report_filename", result.get("report_filename") or "")):
        ph = PLACEHOLDER_RE.findall(text)
        if ph:
            issues.append(_issue("error", field, f"还有没填的占位：{'、'.join(ph)}"))

    # 标题
    name = config.CANDIDATE_NAME_EN if en else config.CANDIDATE_NAME
    fmt = (rules.get("subject_format") or "").strip()
    if not subject:
        issues.append(_issue("error", "subject", "邮件标题是空的。"))
    elif name not in subject and config.CANDIDATE_NAME not in subject:
        if not fmt:
            issues.append(_issue("warn", "subject", "标题里没有你的名字。"))
        elif re.search(r"姓名|名字|name", fmt, re.I):
            issues.append(_issue("error", "subject", "JD 要求标题里写姓名，但标题里没有。"))
    if fmt:
        for sep in "【】-+_｜|/（）()":
            if sep in fmt and sep not in subject:
                issues.append(_issue("warn", "subject", f"JD 格式里有「{sep}」，生成的标题里没有，请对照格式检查。"))
                break

    # 正文
    if len(body.strip()) < 40:
        issues.append(_issue("error", "body", "正文太短或为空。"))
    for kw in config.NOT_ON_RESUME:
        if kw in body or kw in subject:
            issues.append(_issue("error", "body", f"出现了现行简历上没有的内容「{kw}」。"))
    for kw in config.WRONG_GRADUATION:
        if kw in body or kw in subject or kw in (result.get("resume_filename") or ""):
            issues.append(_issue("error", "body", f"毕业时间写错了（「{kw}」），和简历上的不一致。"))
    for kw in COURTESY_BANNED:
        if kw in body:
            issues.append(_issue("warn", "body", f"有套话「{kw}」，建议删掉。"))
    if "！" in body or "!" in body:
        issues.append(_issue("warn", "body", "正文有感叹号。"))
    resume_text = (resume_status or {}).get("text", "")
    own = (profile_text + resume_text).lower()
    for kw in CLAIMS:
        if kw.lower() in body.lower() and kw.lower() not in own:
            issues.append(_issue("warn", "body", f"正文说到「{kw}」，简历和档案里没有，确认不是夸大或编的。"))
    known = ((jd_text or "") + profile_text + resume_text).lower()
    for kw in ENTITIES:
        if kw.lower() in body.lower() and kw.lower() not in known:
            issues.append(_issue("warn", "body", f"正文出现「{kw}」，简历和 JD 里都没有，确认不是编的。"))
    allowed = _numbers_allowed(jd_text or "", [profile_text, resume_text, rules_text, source_label, subject])
    odd = [n for n in re.findall(r"\d+(?:\.\d+)?", body) if n not in allowed]
    if odd:
        issues.append(_issue("warn", "body", f"正文里的数字 {'、'.join(sorted(set(odd)))} 在简历和 JD 里都找不到，确认没写错。"))
    sentences = len(re.findall(r"[。？?；]", body)) if not en else len(re.findall(r"[.?!](\s|$)", body))
    if sentences > 8:
        issues.append(_issue("warn", "body", f"正文偏长（约 {sentences} 句），HR 一般只看前三行。"))
    tail = body.strip().splitlines()[-1].strip() if body.strip() else ""
    if not en and tail != config.CANDIDATE_NAME:
        issues.append(_issue("warn", "body", f"正文最后一行不是署名「{config.CANDIDATE_NAME}」。"))
    if en and config.CANDIDATE_NAME_EN not in tail:
        issues.append(_issue("warn", "body", f"英文邮件最后一行不是署名 {config.CANDIDATE_NAME_EN}。"))

    # 附件
    if resume_status is not None and result.get("attach_resume", True):
        if not resume_status.get("ok"):
            issues.append(_issue("error", "resume", resume_status.get("error", "简历文件有问题。")))
        elif resume_status.get("grad_problems"):
            issues.append(_issue("error", "resume", "简历 PDF 和档案对不上（毕业时间）："
                                 + "；".join(resume_status["grad_problems"])))
    if result.get("attach_report") and not config.REPORT_PATH.exists():
        issues.append(_issue("error", "report", f"找不到研究样本文件：{config.REPORT_PATH}"))
    materials = " ".join(rules.get("requested_materials") or [])
    if re.search(r"成绩单|transcript", materials, re.I):
        issues.append(_issue("warn", "attach", "JD 要求成绩单，系统里没有，需要自己另附。"))
    if re.search(r"英文.{0,6}(报告|样本|writing|memo|研究)|english.{0,10}(sample|report|memo)", materials, re.I):
        issues.append(_issue("warn", "attach", "JD 要英文研究样本，现有研究样本是中文的。"))

    # 机构 / 时间
    company = result.get("company_name") or ""
    if any(k in company.lower() for k in config.CURRENT_EMPLOYER_KEYWORDS):
        issues.append(_issue("warn", "company", f"这是你现在实习的机构（{company}），确认要投吗？"))
    for r in related:
        issues.append(_issue("warn", "duplicate",
                             f"以前投过：{r.get('sent_at', '')[:10]} {r.get('company_name', '')}｜{r.get('job_title', '')}"
                             f"｜{r.get('status', '')}（{r.get('match', '')}）"))
    dl = _parse_date(result.get("deadline") or "")
    if dl and dl < now.date():
        issues.append(_issue("warn", "deadline", f"截止日期 {dl} 已经过了。"))
    post = _parse_date(result.get("job_post_date") or "")
    if post and (now.date() - post).days > 45:
        issues.append(_issue("info", "post_date", f"岗位发布于 {post}，已经 {(now.date() - post).days} 天，可能已招满。"))
    if now.hour < 7:
        issues.append(_issue("info", "time", "现在是凌晨，可以先「存草稿」，早上在 Gmail 里发出或定时发送。"))

    for w in result.get("fit_warnings") or []:
        issues.append(_issue("warn", "fit", f"JD 要求：{w}"))
    for m in result.get("missing_info") or []:
        issues.append(_issue("info", "missing", f"档案里缺：{m}"))
    return issues


def has_errors(issues):
    return any(i["level"] == "error" for i in issues)
