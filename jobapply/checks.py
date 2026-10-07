"""发信前的确定性检查（不靠 AI）：自动修正能安全修的，其余按严重程度报出来。

level:
  error  —— 不处理就不该发（剪贴板模式直接拦下；网页面板需要点「仍然发送」）
  warn   —— 建议看一眼
  info   —— 提示
"""

import re
from datetime import datetime, date
from zoneinfo import ZoneInfo

from . import config

BEIJING = ZoneInfo("Asia/Shanghai")


def is_night(now=None):
    """北京时间晚上（默认 21 点到第二天 7 点）：发出去的邮件 HR 早上会被压在一堆新邮件下面。"""
    start, end = config.NIGHT_HOURS
    h = (now or beijing_now()).hour
    return h >= start or h < end


def beijing_now():
    """HR 在国内：凌晨提醒、截止日期都按北京时间算（人在美国时也对）。"""
    return datetime.now(BEIJING).replace(tzinfo=None)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
PLACEHOLDER_RE = re.compile(r"\[[^\[\]\n]{1,30}\]")
# 方括号里整个就是「要你填的字段名」（哪怕 JD 模板里原样写着）→ 一定是没填的占位；
# 「[实习申请]」「[2027届实习]」「[Full-time Application]」这种是 JD 要求原样照抄的字面量，不算
FIELD_NAME = re.compile(r"(?:您的|你的|your\s*)?(?:姓名|名字|学校|院校|毕业院校|专业|学历|年级|届别|毕业时间|毕业年份|"
                        r"应聘岗位|申请岗位|岗位名称|岗位|职位|职位名称|到岗时间|可到岗时间|最早到岗时间|入职时间|实习时长|实习天数|"
                        r"每周天数|每周实习天数|实习期|期望薪资|期望日薪|薪资|城市|工作城市|工作地点|地点|电话|手机|手机号|微信|邮箱|"
                        r"方向|研究方向|来源|渠道|招聘信息来源|信息来源|身份证号|排名|"
                        r"(?:full\s*)?name|school|university|major|degree|position|role|job\s*title|start\s*date|"
                        r"date|salary|city|phone|email|wechat)", re.I)
# 「信息来源 / 渠道」这一项：本人决定邮件里一律不写（来源只进自己的投递记录）
_SOURCE_WORD = r"(?:招聘|获取|信息|岗位)*(?:来源|渠道|途径)+(?:平台)?|\b(?:source|channel)\b"
_OPEN, _CLOSE, _INNER = r"[\[【（(<《]", r"[\]】）)>》]", r"[^\[\]【】（）()<>《》\n]"
# JD 格式里的这一项（整组括号「（注明信息来源）」或「-信息来源」）：标题格式检查时不算它
SOURCE_FIELD = re.compile(r"\s*[-+_｜|/、]?\s*(?:" + _OPEN + _INNER + r"{0,12}?(?:" + _SOURCE_WORD + r")" + _INNER + r"{0,8}" + _CLOSE
                          + r"|(?:注明|填写|写明)?(?:" + _SOURCE_WORD + r"))", re.I)
# 生成内容里没填的占位：只认方括号「[招聘信息来源]」（「募资经理（渠道）」这种岗位名不能动）
SOURCE_SLOT = re.compile(r"\s*[-+_｜|/、]?\s*\[\s*(?:" + _SOURCE_WORD + r")\s*\]", re.I)
SOURCE_LINE = re.compile(r"^[ \t]*(?:招聘|获取)?(?:信息)?(?:来源|渠道)[ \t]*[:：].*\n?", re.M)
# 照抄进邮件的「（信息来源）」这一项（单独的「（渠道）」不算，可能是岗位名）
SOURCE_LEFTOVER = re.compile(_OPEN + r"\s*(?:注明|填写)?\s*(?:(?:招聘|获取)?信息来源|(?:获取|信息|招聘)渠道|获取途径|来源渠道)\s*" + _CLOSE)
# 正文说附了研究样本（「另附一份……研究样本」「……报告见附件」「attached a writing sample」）；
# 「如需研究样本可随时提供」「available upon request」这种不算
REPORT_MENTION = re.compile(
    r"研究样本|writing sample|research sample"
    r"|(?:另附|附上|随附|一并附|附件[里中]?还?有|附件(?:是|为|包括))[^，,。！？!?\n]{0,24}"
    r"(?:研究(?:样本|报告|材料|成果)|(?:行业|公司)研究(?![员岗方生所院助])|报告|样本|概览|memo|deck)"
    r"|(?:研究(?:样本|报告|材料|成果)|(?:行业|公司)研究(?![员岗方生所院助])|报告|样本|概览)[^，,。！？!?\n]{0,8}(?:见附件|在附件|附后|已附)"
    r"|(?:attach|enclos)\w*[^.!?;\n]{0,40}\b(?:sample|report|memo|deck|overview|excerpt)s?\b"
    r"|\b(?:sample|report|memo|deck|overview|excerpt)s?\b[^.!?;\n]{0,60}\b(?:is|are) (?:attached|enclosed)", re.I)
REPORT_COND = re.compile(r"如需|如有需要|如果需要|需要的话|可随时提供|可以提供|可提供|可另行提供|upon request|on request|if needed|"
                         r"if helpful|happy to (?:share|provide)|can (?:share|provide)|available", re.I)


# 正文说附了简历、研究样本以外的东西（文章、作品、截图、成绩单……）：附件里得真有
ATTACH_OTHER = re.compile(
    r"(?:另附|附上|随附|一并附|附件[里中]?还?有|附件(?:是|为|包括))[^，,。！？!?\n]{0,24}(文章|作品集?|截图|成绩单|论文|证书|deck|ppt|portfolio|transcript)"
    r"|(文章|作品集?|截图|成绩单|论文|证书)[^，,。！？!?\n]{0,10}(?:见附件|在附件|附后|已附)", re.I)
# 没写 https:// 的链接（「xxx.github.io/路径」），有的邮箱里点不开
BARE_LINK = re.compile(r"(?<![\w/@.:\-])((?:[a-z0-9-]+\.)+(?:io|com|cn|ai|net|org|vc|co|me|app)/[^\s，。；、）)】]*)", re.I)
# 写了投递提速的数字：HR 会联想到这封信也是批量自动发的
SPEEDUP = re.compile(r"15\s*[–\-~～到至]\s*20\s*分钟|约?\s*10\s*秒")


def mentions_report(body):
    sentences = [s for s in re.split(r"(?<=[。！？!?；;\n])|(?<=\.)\s", body or "") if s.strip()]
    return any(REPORT_MENTION.search(s) for s in sentences if not REPORT_COND.search(s))


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
# fetch 抓公众号时，图片里识别出来的文字放在这个标记后面
OCR_MARKER = "【以下是文章图片里的文字（系统自动识别）】"
# 图片里认出来的邮箱，抓取时已经自动双重核对过（Claude 和 macOS Vision 各认一遍，对不上再盯着图认一次）
OCR_VERIFIED = "【图片里的邮箱已自动核对（两种识别方法一致）】"
OCR_UNSURE = "【图片里的邮箱没能自动核对上】"


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


# 变形写法还原成 @ / .；「at」「#」后面必须是一个完整域名（后面不再跟 @ 或字母），免得把「lead at jane.doe@x.com」拆坏
_DOMAIN_AHEAD = r"(?=[\w-]+(?:\.[\w-]+)+(?![\w@-]|\.[\w-]))"
_OBFUSCATIONS = [
    (re.compile(r"\s*[\[\(（【{]\s*(?:dot|点)\s*[\]\)）】}]\s*", re.I), "."),   # 先还原 [dot]，域名才完整
    (re.compile(r"\s*[\[\(（【{]\s*(?:at|艾特)\s*[\]\)）】}]\s*", re.I), "@"),
    (re.compile(r"(?<=[\w.])\s*#\s*" + _DOMAIN_AHEAD, re.I), "@"),
    (re.compile(r"(?<=\w)\s+at\s+" + _DOMAIN_AHEAD, re.I), "@"),
]


def jd_emails(jd_text):
    """JD 里原样写出的邮箱 → (精确集合, 还原 [at]/# 等变形写法后的集合)。"""
    exact = set(split_emails(jd_text or ""))
    t = (jd_text or "").replace("＠", "@")
    for pat, rep in _OBFUSCATIONS:
        t = pat.sub(rep, t)
    return exact, set(split_emails(t))


def _ocr_checked(jd_text, marker):
    """JD 里「图片邮箱核对结果」那一行列出的邮箱。"""
    line = next((l for l in (jd_text or "").splitlines() if l.startswith(marker)), "")
    return set(split_emails(line))


def _issue(level, field, msg):
    return {"level": level, "field": field, "msg": msg}


def _is_en(result):
    return result.get("jd_language") == "英文"


def _strip_emails(text):
    return EMAIL_RE.sub(" ", text or "")


# 链接到空白、中文、全角标点为止（「https://a.com/x，截止10月底」只取链接）
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'\u3000-\u303f\u4e00-\u9fff\uff00-\uffef)】]+", re.I)


def safe_url(u):
    """只放行 http(s) 链接；「www.xx.com/…」补上 https://；文字里夹着链接就取第一个；其他一律清空。"""
    u = (u or "").strip()
    if not u:
        return ""
    m = _URL_IN_TEXT.match(u) or _URL_IN_TEXT.search(u)
    if m:
        return m.group(0).rstrip(".,;:!?")
    if "@" not in u and re.fullmatch(r"(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/[^\s\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]*)?", u, re.I):
        return "https://" + u
    return ""


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


_PUNCT = r"[，,：:！!。]"
_THEN_HELLO = rf"(?:{_PUNCT}+\s*(?:(?:您好|你好){_PUNCT}*\s*)?|(?:您好|你好){_PUNCT}*\s*|$)"
_GREET_ZH = re.compile(
    rf"^(?:(?:尊敬的)?[^，,：:！!。\s]{{0,12}}?(?:您好|你好)(?:{_PUNCT}+\s*|\s+|$)"            # 王总您好， / 您好！
    rf"|(?:大家|各位(?:老师)?|老师|[^，,：:！!。\s我]{{1,3}}?(?:总|老师|女士|先生|经理))好(?:{_PUNCT}+\s*|$)"  # 李老师好！
    rf"|尊敬的[^，,：:！!。\s]{{1,12}}?{_THEN_HELLO}"                                          # 尊敬的王总，您好：
    rf"|[^，,：:！!。\s我]{{1,3}}?(?:总|老师|女士|先生|经理){_THEN_HELLO})")                       # 王经理，您好！
_GREET_EN = re.compile(r"^(?:dear|hi|hello)\b(?:\s+(?!I\b|I'm\b|I am\b)[A-Za-z.'’]+){0,4}\s*(?:[,，:：!]+\s*|$)", re.I)


def _split_greeting(line):
    """第一行开头的称呼 → (称呼, 同一行后面的正文)；第一行不是以称呼开头（比如直接「我是…」）→ None。"""
    s = line.strip()
    m = _GREET_ZH.match(s) or _GREET_EN.match(s)
    return (s[:m.end()].strip(), s[m.end():].strip()) if m else None


def _name_ok(name, contact, jd_plain):
    """称呼里的名字要有 JD 依据：和 JD 里的联系人对得上，或者名字本身（2 个字以上）出现在 JD 正文里。"""
    n = name.lower()
    cjk = bool(re.search(r"[\u4e00-\u9fff]", name))
    # 英文名按整词、至少 3 个字母比（「Ma」不能因为 JD 里有 market 就算有依据）
    word = (lambda x, text: x in text) if cjk else (lambda x, text: len(x) >= 3 and re.search(rf"\b{re.escape(x)}\b", text))
    if len(name) >= 2 and word(n, jd_plain):
        return True
    c = (contact or "").strip().lower()
    if not c or c not in jd_plain:
        return False
    if cjk:                                          # 中文：同姓即可（王总 ↔ 王女士）
        return n in c or c in n or name[0] == contact.strip()[0]
    return bool(word(n, c) or word(c, n))            # 英文名必须对得上（Jack ≠ Jessica）


GENERIC_GREETINGS = {"", "各位", "各位老师", "老师", "老师们", "hr", "各位hr", "大家", "招聘官", "招聘团队", "招聘负责人", "招聘组",
                     "there", "all", "team", "hiring team", "hiring manager", "recruiter", "recruiting team",
                     "talent team", "sir or madam", "sir/madam"}


def _greeting_name(first):
    """从称呼行里拆出「名字」部分；通用称呼返回 ''。
    认得：X您好 / X你好 / X好 / 尊敬的X / Hi X / Dear X / X，您好（标点、感叹号都去掉）。"""
    s = re.sub(r"[，,:：！!。.\s]+$", "", first.strip())
    m = re.match(r"^(?:hi|hello|dear)\b[\s,]*(.*)$", s, re.I)
    if m:
        name = re.sub(r"^(mr|ms|mrs|miss|dr)\.?\s+", "", m.group(1).strip(), flags=re.I)
        return "" if name.lower() in GENERIC_GREETINGS else name
    s = re.sub(r"^尊敬的", "", s)
    s = re.sub(r"[，,\s]*(?:您好|你好|好)$", "", s).strip("，, ")
    if s.lower() in GENERIC_GREETINGS:
        return ""
    for h in HONORIFICS_ZH:
        if s.endswith(h) and len(s) > len(h):
            s = s[: -len(h)]
            break
    return "" if s.lower() in GENERIC_GREETINGS else s.strip()


def autofix(result, jd_text):
    """就地修正 result，返回修正说明列表。"""
    fixes = []
    en = _is_en(result)
    # 邮箱统一格式
    result["to_emails"] = split_emails(result.get("to_emails"))
    result["cc_emails"] = [e for e in split_emails(result.get("cc_emails")) if e not in result["to_emails"]]

    body = (result.get("email_body") or "").replace("\r\n", "\n").strip()
    # 称呼必须有 JD 依据（防止从邮箱地址猜名字）；只换称呼本身，同一行后面的正文原样保留
    first, lines = _greeting_parts(body)
    split = _split_greeting(first)
    name = _greeting_name(split[0]) if split else ""
    if name and not _name_ok(name, result.get("contact_in_jd"), _strip_emails(jd_text).lower()):
        greet, rest = split
        generic = "Hello," if en else "您好，"
        lines[0] = generic + ((" " if en else "") + rest if rest else "")
        body = "\n".join(lines)
        result["contact_in_jd"] = ""
        fixes.append(f"称呼「{greet}」在 JD 原文里找不到依据（可能是从邮箱地址猜的），已改成「{generic}」")
    # 「信息来源」不写进邮件：AI 万一留了占位 / 单独一行「信息来源：…」，去掉
    new_body = SOURCE_SLOT.sub("", SOURCE_LINE.sub("", body))
    if new_body != body:
        body = new_body
        fixes.append("正文里的「信息来源」已去掉（来源只记在你自己的投递记录里，不写进邮件）")
    # 链接补上 https://（纯文本邮件里，没有 https:// 的地址有的邮箱点不开）
    linked = BARE_LINK.sub(r"https://\1", body)
    if linked != body:
        body = linked
        fixes.append("链接前面补上了 https://")
    # 统一成简历上的写法（candidate_settings.json 的 spelling_fixes）
    for wrong, right in config.SPELLING_FIXES.items():
        if wrong in body:
            body = body.replace(wrong, right)
            fixes.append(f"「{wrong}」已改成简历上的写法「{right}」")
    result["email_body"] = body.strip() + "\n"

    subject = re.sub(r"\s+", " ", (result.get("email_subject") or "").strip())
    if SOURCE_SLOT.search(subject):
        no_src = SOURCE_SLOT.sub("", subject).strip(" -+_｜|/、")
        if no_src:
            fixes.append(f"标题里的「信息来源」已去掉（来源只记在你自己的投递记录里）：{no_src}")
            subject = no_src
    result["email_subject"] = subject
    result["apply_url"] = safe_url(result.get("apply_url"))

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

def _grad_numbers():
    """简历上的毕业时间（如 2027.06）的各种写法里会出现的数字：2027、27、06、6。"""
    out = set()
    for v in config.RESUME_MUST_CONTAIN.values():
        for y, m in re.findall(r"(20\d\d)\D{0,3}(\d{1,2})?", v):
            out |= {y, y[2:]}
            if m:
                out |= {m, str(int(m)), f"{int(m):02d}"}
    return out


def _numbers_allowed(jd_text, extra_texts):
    pool = jd_text + "\n" + "\n".join(extra_texts)
    nums = set(re.findall(r"\d+(?:\.\d+)?", pool)) | _grad_numbers()
    now = beijing_now()
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
    now = now or beijing_now()
    en = _is_en(result)
    to, cc = split_emails(result.get("to_emails")), split_emails(result.get("cc_emails"))
    subject = result.get("email_subject") or ""
    body = result.get("email_body") or ""
    channel = result.get("apply_channel") or ""
    rules = result.get("jd_rules") or {}

    # 收件人
    if not to:
        if channel == "网申/链接":
            issues.append(_issue("info", "to", "这个岗位要网申，不用发邮件：按「网申资料包」投完，点「我已网申，记一笔」。"))
        else:
            issues.append(_issue("error", "to", "没有收件邮箱。JD 里如果确实有投递邮箱，请手动填上。"))
    jd_text = jd_text or ""
    exact, deob = jd_emails(jd_text)
    known = exact | deob
    unsure = _ocr_checked(jd_text, OCR_UNSURE)   # 图片里的邮箱抓取时已自动双重核对，只有始终对不上的才拦
    for e in to + cc:
        if e not in known:
            issues.append(_issue("error", "to", f"邮箱 {e} 在 JD 原文里找不到，可能是 AI 编的或抄错了，请核对。"))
            continue
        if e not in exact:
            issues.append(_issue("warn", "to", f"邮箱 {e} 在 JD 里是变形写法（如 [at]、#），请核对拼写。"))
        if e in unsure:   # 极少：几种认法始终对不上。为免把简历发给陌生人，不放行
            issues.append(_issue("error", "to", f"图片里的邮箱 {e} 几种认法对不上，没法确定，为免把简历发错人，这封没放行。"))
    if config.SENDER_EMAIL.lower() in to + cc:
        issues.append(_issue("error", "to", "收件人里有你自己的邮箱。"))
    others = sorted(exact - set(to) - set(cc))  # 只列原文写明的邮箱（「look at abc.com」还原出来的假邮箱不算）
    if to and others:
        issues.append(_issue("info", "to", f"JD 里还有别的邮箱（{'、'.join(others[:4])}），确认选的是这个岗位对应的那个。"))

    # 占位符（JD 自己要求的方括号写法，如「[实习申请]姓名-学校」，不算占位）
    literal = (jd_text or "") + (rules.get("subject_format") or "") + (rules.get("resume_filename_format") or "") \
        + (rules.get("report_filename_format") or "")
    for field, text in (("subject", subject), ("body", body),
                        ("resume_filename", result.get("resume_filename") or ""),
                        ("report_filename", result.get("report_filename") or "")):
        ph = [p for p in PLACEHOLDER_RE.findall(text) if p not in literal or FIELD_NAME.fullmatch(p[1:-1].strip())]
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
    # 招聘信息来源：本人决定邮件里一律不写（只进自己的投递记录）
    src = re.sub(r"[（(]?公众号[)）]?$", "", (source_label or result.get("source_name") or "").strip()).strip()
    if len(src) >= 2 and src not in ("微信", "微信公众号") and src.lower() not in (result.get("company_name") or "").lower():
        where = [n for n, txt in (("标题", subject), ("正文", body)) if src.lower() in txt.lower()]
        if where:
            issues.append(_issue("error", "subject" if where[0] == "标题" else "body",
                                 f"{'和'.join(where)}里写了招聘信息来源「{src}」：邮件里不写来源，删掉。"))
    if SOURCE_LEFTOVER.search(subject) or SOURCE_LEFTOVER.search(body):
        issues.append(_issue("error", "subject", "邮件里还留着「信息来源」这一项：来源不写，删掉。"))
    fmt_cmp = SOURCE_FIELD.sub("", fmt)   # 「信息来源」那一项本来就不写，它两边的分隔符不算
    if fmt_cmp:
        for sep in "【】-+_｜|/（）()":
            if sep in fmt_cmp and sep not in subject:
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
    allowed = _numbers_allowed(jd_text or "", [profile_text, resume_text, source_label])
    plain = _URL_IN_TEXT.sub(" ", BARE_LINK.sub(" ", EMAIL_RE.sub(" ", subject + "\n" + body)))   # 链接、邮箱里的数字不算
    odd = [n for n in re.findall(r"\d+(?:\.\d+)?", plain) if n not in allowed]
    if odd:
        issues.append(_issue("warn", "body", f"标题 / 正文里的数字 {'、'.join(sorted(set(odd)))} 在简历和 JD 里都找不到，确认没写错。"))
    sentences = len(re.findall(r"[。？?；]", body)) if not en else len(re.findall(r"[.?!](\s|$)", body))
    longest = max((len(p) for p in re.split(r"\n\s*\n", body) if p.strip()), default=0)
    if not en and longest > 170:
        issues.append(_issue("warn", "body", f"有一段写了约 {longest} 字，经历段 120 字左右、1–2 个重点就够了。"))
    if SPEEDUP.search(body):
        issues.append(_issue("warn", "body", "正文写了投递提速的数字，HR 会联想到这封信也是批量自动发的，建议删掉。"))
    m = ATTACH_OTHER.search(body)
    if m and not result.get("extra_attachments"):
        issues.append(_issue("error", "attach", f"正文说附了「{m.group(1) or m.group(2)}」，附件里没有：点「添加附件」加上，或删掉那句。"))
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
    mentions = mentions_report(body)
    if mentions and not result.get("attach_report"):
        issues.append(_issue("error", "report", "正文说附了研究样本，但附件里没勾「研究样本」：要么勾上，要么删掉正文那句。"))
    elif result.get("attach_report") and not mentions:
        issues.append(_issue("warn", "report", "附了研究样本，正文没提一句：可以加一句「另附一份过往行业研究样本，供参考。」，"
                                               "或者点「按补充要求重写」。"))
    materials = " ".join(rules.get("requested_materials") or [])
    if not result.get("attach_report") and re.search(r"研究报告|研究样本|研究成果|writing sample|research sample|\bdeck\b|\bmemo\b",
                                                     materials, re.I):
        issues.append(_issue("warn", "report", "JD 提到要研究报告 / writing sample，这封没附研究样本：确认是故意不附。"))
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
    if is_night(now) and to:
        issues.append(_issue("info", "time", f"现在是北京时间晚上：点「发送」时可以选「明早 {config.SEND_AT} 自动发」。"))

    for w in result.get("fit_warnings") or []:
        issues.append(_issue("warn", "fit", f"JD 要求：{w}"))
    for m in result.get("missing_info") or []:
        issues.append(_issue("info", "missing", f"档案里缺：{m}"))
    return issues


def has_errors(issues):
    return any(i["level"] == "error" for i in issues)
