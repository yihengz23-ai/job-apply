"""网申待办（面板「网申」页的清单）：要网申的岗位都在这里，状态和投递看板跟着走。

来源有三种：投递页里没有邮箱、只能网申的岗位（自动转过来）；「邮箱+网申」的岗位（邮件照常在投递页发，网申这边多一条）；
在网申页直接贴的链接。助手填完 → 看板里出现一条「草稿」（网申）；本人提交后点「我已提交」或跟助手说一声 → 变「已投递」。
「邮箱+网申」的岗位一个岗位只留一条记录：网申提交后在邮件那条记录上补一笔「已同时网申」。
提交以后助手去网站把「实际提交的内容 + 网站上的进度 + 登录账号」读回来（__wsfill.readback），记在看板那条记录上，
看板里看到的就是网站上真交上去的版本（本人提交前自己改过的也在里面），不是面板生成的稿子。"""

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid

from . import checks, config, records

PATH = config.DATA_DIR / "wangshen_tasks.json"
KEY_PATH = config.DATA_DIR / ".wsreadback_key"   # 读回口令的种子（本机随机生成，不进公开版）
# 每一行都说清「现在轮到谁」：待填（还没开始）→ 助手在填 → 等你处理（助手停下来要你做事，todo 写着做什么）
# → 已填待提交（填好了等你提交）→ 已提交（助手去网站读回真实内容）；不投了。
STATUSES = ("待填", "助手在填", "等你处理", "已填待提交", "已提交", "不投了")
ACTIVE = ("助手在填", "等你处理", "已填待提交", "待填")
RESULT_KEYS = ("company_name", "company_type", "job_title", "job_location", "focus_industry", "position_type",
               "job_post_date", "deadline", "apply_url", "apply_channel", "jd_language")
# 助手回复里的机读行：【网申记录】公司：XX｜岗位：XX｜网址：https://…｜状态：已填待提交
MARKER = re.compile(r"【网申记录】([^\n]+)")
FILLED_WORDS = ("已填待提交", "已填好", "填好了", "待提交", "已暂存")
SUBMITTED_WORDS = ("已提交", "已投递", "提交了")
WAIT_WORDS = ("等你处理", "等你", "要你", "需要你", "等本人")
FINAL = ("已提交", "不投了")   # 终态：助手的【网申记录】改不了，只有本人在网申页点按钮能改
# 账号里助手写的占位话（页面上没显示账号时它会这么写）：不收，等真值。手机号、邮箱、网站用户名都收
ACCOUNT_PLACEHOLDER = re.compile(r"没显示|未显示|见网页|见页面|照抄|未知|不知道|看不到|待补|待定")
# 读回的几种页面：网站上的投递记录 / 进度页、实际提交的简历 / 申请表页、其他
READBACK_KINDS = {"status": "网站上的投递记录 / 进度", "resume": "实际提交的简历 / 申请表", "other": "其他页面"}
READBACK_MAX = 30000
# 每家网申一个颜色：面板「网申」页那一行、助手对话框、助手在网页四周画的框都用同一个，几个助手同时干活时一眼对得上
COLORS = [("🔵", "#2563eb", "蓝"), ("🟢", "#16a34a", "绿"), ("🟣", "#9333ea", "紫"), ("🟠", "#ea580c", "橙"),
          ("🔴", "#dc2626", "红"), ("🟡", "#ca8a04", "黄"), ("🟤", "#92400e", "棕"), ("⚫", "#334155", "黑")]

_lock = threading.RLock()


class NotFound(KeyError):
    pass


def _now():
    return records.now_str()


def _load():
    if not PATH.exists():
        return []
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"wangshen_tasks.json 损坏（{e}），为防止覆盖已停止写入") from e
    return data if isinstance(data, list) else []


def _save(items):
    fd, tmp = tempfile.mkstemp(dir=PATH.parent, prefix=".wstasks-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def norm_url(u):
    """比较用：去掉首尾空白、末尾的 / 和 utm 之类的追踪参数（Moka 这种 # 后面是路由，要留着）。"""
    u = (u or "").strip()
    u = re.sub(r"([?&])(utm_[a-z]+|spm|from|share[a-z_]*)=[^&#]*", r"\1", u)
    u = re.sub(r"[?&]+(#|$)", r"\1", u)
    return u.rstrip("/").lower()


def _pick_color(items):
    """还没提交的几家里用得最少的颜色（先到先得，尽量不撞色）。"""
    used = [t.get("color") for t in items if t.get("status") in ACTIVE and isinstance(t.get("color"), int)]
    counts = [used.count(i) for i in range(len(COLORS))]
    return counts.index(min(counts))


def list_tasks():
    rank = {s: i for i, s in enumerate(ACTIVE)}
    with _lock:
        items = _load()
        missing = [t for t in items if not isinstance(t.get("color"), int)]
        for t in missing:   # 以前建的待办还没有颜色：补上
            t["color"] = _pick_color(items)
        vague = [t for t in items if t.get("note") == "助手上次没报填好，可以再让它接着填"]
        for t in vague:     # 旧版留下的含糊说法：改成「等你处理」那一套，不再显示
            t["note"] = ""
        if missing or vague:
            _save(items)
    return sorted(items, key=lambda t: (rank.get(t.get("status"), 9), "" if t.get("status") in ACTIVE else "~",
                                        t.get("updated_at", "")), reverse=False)


def counts(items=None):
    out = {}
    for t in items if items is not None else _load():
        out[t.get("status", "待填")] = out.get(t.get("status", "待填"), 0) + 1
    return out


def get(task_id):
    t = next((t for t in _load() if t.get("id") == task_id), None)
    if not t:
        raise NotFound(task_id)
    return t


def _find(items, url="", job="", queue_id=""):
    if queue_id:
        hit = next((t for t in items if t.get("queue_id") == queue_id), None)
        if hit:
            return hit
    key = norm_url(url)
    if not key:
        return None
    same = [t for t in items if norm_url(t.get("url")) == key and t.get("status") != "不投了"]
    if job:
        return next((t for t in same if not t.get("job") or t.get("job") == job), None)
    return same[0] if same else None


def add(url="", company="", job="", *, source="手动添加", **extra):
    """加一条待办；同一个网址（同一个岗位）已经有了就补齐缺的信息，不重复加。"""
    url = checks.safe_url(url) if url else ""
    with _lock:
        items = _load()
        hit = _find(items, url, job, extra.get("queue_id", ""))
        if hit:
            for k, v in (("company", company), ("job", job), ("url", url), *extra.items()):
                if v and not hit.get(k):
                    hit[k] = v
            hit["updated_at"] = _now()
            _save(items)
            return hit
        t = {"id": uuid.uuid4().hex[:10], "created_at": _now(), "updated_at": _now(), "status": "待填",
             "company": company.strip(), "job": job.strip(), "url": url, "source": source, "deadline": "",
             "note": "", "record_id": "", "email_record_id": "", "queue_id": "", "chat_id": "", "result": {}, "jd_text": "",
             "account": ""}   # 申请账号：在这家网申网站注册账号用的手机号 / 邮箱
        t.update({k: v for k, v in extra.items() if v is not None})
        t.setdefault("color", _pick_color(items))
        items.append(t)
        _save(items)
        return t


def from_analysis(result, *, page=None, jd_text="", queue_id="", email_too=False):
    """投递页分析完的岗位转过来：网址优先用 JD 里的网申链接，没有就用文章二维码里的链接 / 文章本身。"""
    page = page or {}
    url = result.get("apply_url") or next(iter(page.get("qr_urls") or []), "") or page.get("url", "")
    return add(url, result.get("company_name", ""), result.get("job_title", ""),
               source="投递页（邮件 + 网申）" if email_too else "投递页（只能网申）", queue_id=queue_id,
               deadline=result.get("deadline", ""), jd_text=jd_text or "", source_url=page.get("url", ""),
               source_label=page.get("source_label", ""),
               result={k: result.get(k, "") for k in RESULT_KEYS})


def update(task_id, **fields):
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("id") == task_id), None)
        if not t:
            raise NotFound(task_id)
        if "url" in fields and fields["url"]:
            fields["url"] = checks.safe_url(fields["url"])
        t.update(fields)
        t["updated_at"] = _now()
        _save(items)
    if "account" in fields:   # 申请账号同步到看板里那条记录
        for rid in {t.get("record_id"), t.get("email_record_id")} - {"", None}:
            if records.get(rid):
                records.update(rid, {"apply_account": fields["account"]})
    return t


def delete(task_id):
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("id") == task_id), None)
        if not t:
            return False
        if t.get("record_id") and t.get("status") != "已提交":   # 没提交的草稿记录一起删，看板里不留空记录
            _drop_draft(t["record_id"])
        items.remove(t)
        _save(items)
        return True


def link_email_record(queue_id, record_id):
    """「邮箱+网申」的邮件发出 / 存了草稿：网申这条记下邮件那条记录，提交后补记在同一条上。"""
    if not queue_id or not record_id:
        return None
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("queue_id") == queue_id), None)
        if not t:
            return None
        t["email_record_id"] = record_id
        t["updated_at"] = _now()
        _save(items)
        return t


# ── 状态 → 看板记录 ─────────────────────────────────────────

def _record_fields(t):
    r = t.get("result") or {}
    return dict(company_name=t.get("company") or r.get("company_name", ""), company_type=r.get("company_type", ""),
                job_title=t.get("job") or r.get("job_title", ""), job_location=r.get("job_location", ""),
                focus_industry=r.get("focus_industry", ""), position_type=r.get("position_type", ""),
                job_post_date=r.get("job_post_date", ""), deadline=t.get("deadline") or r.get("deadline", ""),
                apply_url=t.get("url", ""), apply_channel="网申/链接", jd_text=t.get("jd_text", ""),
                source_url=t.get("source_url") or t.get("url", ""), job_source=t.get("source_label", ""),
                language=r.get("jd_language", ""))


def _drop_draft(record_id):
    rec = records.get(record_id)
    if rec and rec.get("status") == "草稿" and rec.get("send_mode") == "未发邮件":
        records.delete(record_id)


def _ensure_record(t, status):
    """网申这条在看板里的记录：没有就建（草稿 / 已投递）；有就只做「草稿 → 已投递」这一步（投递时间这时写一次）。
    已经投出的记录（已投递、笔试、面试中……）不动：状态不往回改，投递时间不重写。返回记录 id。"""
    rid = t.get("record_id")
    rec = records.get(rid) if rid else None
    if rec:
        if rec.get("status") == "草稿" and status != "草稿":
            fields = {"status": status}
            if status == "已投递":
                fields.update(sent_at=records.now_str(), sent_ts=time.time())
            records.update(rid, fields)
        return rid
    rec = records.new_record(**_record_fields(t), status=status, send_mode="未发邮件", attach_report=False,
                             apply_account=t.get("account", ""),
                             resume_version="网申上传", source_type="网申（面板助手）" if t.get("chat_id") else "网申")
    return records.add(rec)


def set_status(task_id, status, note=""):
    """改状态，看板跟着走：已填待提交 → 看板一条「草稿」；已提交 → 「已投递」（邮件那条记录上补记）；不投了 → 删掉草稿。"""
    if status not in STATUSES:
        raise ValueError(f"不认识的状态：{status}")
    with _lock:
        t = get(task_id)
        fields = {"status": status, "halted": ""}
        if status != "等你处理":
            fields["todo"] = ""
        if note:
            fields["note"] = note
        if status == "已填待提交" and not t.get("email_record_id"):
            fields["record_id"] = _ensure_record(t, "草稿")
        elif status == "已提交":
            if t.get("email_record_id") and records.get(t["email_record_id"]):
                from . import pipeline   # 避免循环引用
                pipeline.record_web_application({}, "", record_id=t["email_record_id"])
                if t.get("record_id"):
                    _drop_draft(t["record_id"])
                    fields["record_id"] = ""
            else:
                fields["record_id"] = _ensure_record(t, "已投递")
            if not t.get("submitted_at"):   # 提交时间只写一次（读回、再报一次已提交都不改）
                fields["submitted_at"] = _now()
        elif status == "不投了" and t.get("record_id"):
            _drop_draft(t["record_id"])
            fields["record_id"] = ""
        t = update(task_id, **fields)
    sync_readback(t)   # 提交前就读回过的（或者刚建出看板记录）：带到记录上
    return t


# ── 助手回复里的【网申记录】────────────────────────────────

def parse_markers(text):
    out = []
    for line in MARKER.findall(text or ""):
        d = {}
        for part in re.split(r"[｜|]", line):
            m = re.match(r"\s*([^：:]+?)\s*[：:]\s*(.*?)\s*$", part)
            if m:
                d[m.group(1)] = m.group(2)
        out.append(d)
    return out


def _marker_status(word):
    word = word or ""
    if any(w in word for w in SUBMITTED_WORDS) and "待" not in word:
        return "已提交"
    if any(w in word for w in FILLED_WORDS):
        return "已填待提交"
    if any(w in word for w in WAIT_WORDS):
        return "等你处理"
    return ""


def is_placeholder_account(value):
    """「（页面上没显示）」「见网页」「无」这类占位话，或者整句只有括号。里面有号码、邮箱的不算。"""
    v = str(value or "").strip()
    if not v or v in ("无", "没有", "暂无", "空", "-", "—", "N/A", "n/a"):
        return True
    if re.fullmatch(r"[（(][^（()）]*[)）]", v):
        return True
    return bool(ACCOUNT_PLACEHOLDER.search(v)) and not re.search(r"\d{4}|@", v)


def clean_account(value, current=""):
    """助手报的账号：占位话不收。原来是空的或占位话、或者新值是同一个号码更完整的写法（打码的位置补上了数字），
    才换成新的；原来已经有真值就不动（本人在网申页改账号不走这里）。返回要写的值，不用改就返回空串。"""
    v = re.sub(r"\s+", " ", str(value or "")).strip().replace("＊", "*")[:120]
    if is_placeholder_account(v):
        return ""
    cur = re.sub(r"\s+", " ", str(current or "")).strip().replace("＊", "*")
    if not cur or is_placeholder_account(cur):
        return v if v != cur else ""
    a, b = cur.replace(" ", ""), v.replace(" ", "")
    if a != b and len(a) == len(b) and "*" in a and b.count("*") < a.count("*") \
            and all(x == y or x == "*" for x, y in zip(a, b)):
        return v
    return ""


VOLUNTEER = re.compile(r"[（(]\s*第\s*([一二三四五六七八九十\d]+)\s*志愿\s*[)）]|第\s*([一二三四五六七八九十\d]+)\s*志愿[:：]?")
_CN_NUM = {c: i for i, c in enumerate("一二三四五六七八九十", 1)}


def split_jobs(raw):
    """【网申记录】里的「岗位」可能列了好几个（「A（第一志愿）、B（第二志愿）」「A；B」）：拆成 [{name, location, rank}]。
    只有一个岗位、也没写志愿的，返回空列表（照旧当成这家的岗位名）。"""
    raw = str(raw or "").strip()
    if not raw or not (re.search(r"[；;、，,]", raw) or "志愿" in raw):
        return []
    out = []
    for part in re.split(r"[；;、，,]+", raw):
        part = part.strip()
        if not part:
            continue
        rank = 0
        m = VOLUNTEER.search(part)
        if m:
            n = m.group(1) or m.group(2)
            rank = int(n) if n.isdigit() else _CN_NUM.get(n, 0)
            part = VOLUNTEER.sub("", part).strip(" ：:-—")
        m = re.match(r"(.+?)\s*[（(]([^（()）]+)[)）]\s*$", part)
        name, loc = (m.group(1).strip(), m.group(2).strip()) if m else (part, "")
        if name:
            out.append({"name": name[:80], "location": loc[:40], "status": "", "rank": rank})
    return out if len(out) > 1 or (out and out[0]["rank"]) else []


def apply_markers(text, *, task_id="", chat_id=""):
    """助手报的【网申记录】：更新对应的网申待办（对话是从某条待办开的就认那条，否则按网址找，找不到新建）。
    已提交、不投了是终态：助手只能补账号、岗位、进度，改不了状态（读回时要登录，只记「读回等你登录」）。
    返回状态真的变了的待办（面板据此在对话里说一句）。"""
    done = []
    for d in parse_markers(text):
        url = d.get("网址") or d.get("链接") or ""
        company, job = d.get("公司", ""), d.get("岗位", "")
        status = _marker_status(d.get("状态", ""))
        jobs = split_jobs(job)
        with _lock:
            t = None
            if task_id:
                try:
                    t = get(task_id)
                except NotFound:
                    t = None
            if not t:
                t = _find(_load(), url, "" if jobs else job)
            if not t:
                t = add(url, company, "" if jobs else job, source="助手记录", chat_id=chat_id)
            before = t.get("status")
            fill = {k: v for k, v in (("company", company), ("job", "" if jobs else job)) if v and not t.get(k)}
            acct = clean_account(d.get("账号", ""), t.get("account", ""))
            if acct:
                fill["account"] = acct
            progress = (d.get("进度") or "").strip()
            if progress:
                fill.update(site_status=progress[:200], site_status_at=_now())
            if url and not t.get("url"):
                fill["url"] = url
            if chat_id and not t.get("chat_id"):
                fill["chat_id"] = chat_id
            todo = (d.get("要你做") or d.get("要你") or d.get("待办") or "").strip()
            if before in FINAL:
                if before == "已提交" and status == "等你处理":
                    fill["readback_state"] = "读回等你登录"
            elif status == "等你处理":
                fill["todo"] = todo or "助手没写要你做什么，看对话"
            if jobs:
                if not t.get("job"):
                    fill["job"] = "、".join(j["name"] for j in jobs)[:120]
                cur = dict(t)
                _merge_positions(cur, jobs)
                fill["positions"] = cur["positions"]
            if fill:
                t = update(t["id"], **fill)
            if before not in FINAL and status and status != before:
                t = set_status(t["id"], status)
            elif jobs or progress:
                sync_readback(t)
        if t.get("status") != before:
            done.append(t)
    return done


# ── 读回：网站上实际提交的内容 → 看板 ──────────────────────────

def _secret():
    try:
        seed = KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        seed = ""
    if len(seed) < 32:
        seed = secrets.token_hex(32)
        KEY_PATH.write_text(seed, encoding="utf-8")
        os.chmod(KEY_PATH, 0o600)
    return seed.encode()


def color_of(t):
    """(圆点, 色值, 颜色名)"""
    c = t.get("color")
    return COLORS[c % len(COLORS)] if isinstance(c, int) else COLORS[0]


def short_name(t):
    """网页上标签、对话标题用的短名字：公司名，没有就用网站域名。"""
    from urllib.parse import urlparse
    return (t.get("company") or (urlparse(t.get("url") or "").hostname or "").removeprefix("www.") or "网申")[:16]


def readback_key(task_id):
    """读回口令：只写在面板发给助手的那句话里（网页上拿不到），面板凭它认「这是助手从网站读回来的」。"""
    return hmac.new(_secret(), str(task_id).encode(), hashlib.sha256).hexdigest()[:20]


def check_readback_key(task_id, key):
    return bool(key) and hmac.compare_digest(readback_key(task_id).encode(), str(key).encode())


def hide_ids(text):
    """证件号打码：18 位身份证、15 位旧证（读回的原文会进看板）。"""
    text = re.sub(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])", "[证件号已隐去]", text or "")
    return re.sub(r"(?<![0-9A-Za-z])\d{15}(?![0-9A-Za-z])", "[证件号已隐去]", text)


def _parse_positions(raw):
    """投了哪些岗位：可以是 [{name, location, status}]，也可以是「名称（地点）；名称（地点）」这样一串字。"""
    out = []
    items = raw if isinstance(raw, list) else re.split(r"[；;\n]+", str(raw or ""))
    for it in items:
        if isinstance(it, dict):
            name = str(it.get("name") or it.get("岗位") or "").strip()
            loc = str(it.get("location") or it.get("地点") or "").strip()
            st = str(it.get("status") or it.get("进度") or "").strip()
        else:
            m = re.match(r"\s*(.+?)\s*[（(]([^（()）]+)[)）]\s*$", str(it))
            name, loc, st = (m.group(1).strip(), m.group(2).strip(), "") if m else (str(it).strip(), "", "")
        if name:
            out.append({"name": name[:80], "location": loc[:40], "status": st[:60]})
    return out


def _merge_positions(t, new):
    """按「岗位名＋地点」合并（地点没写的按岗位名认）；新的有值的字段（地点、进度、志愿序号）补上去。"""
    pos = t.setdefault("positions", [])
    for p in new:
        cur = next((x for x in pos if x["name"] == p["name"]
                    and (not p.get("location") or not x.get("location") or x["location"] == p["location"])), None)
        if cur:
            cur.update({k: v for k, v in p.items() if v and k != "name"})
        else:
            pos.append(dict(p))


def save_readback(task_id, kind, text, *, url="", title="", status="", account="", position="", location="", positions=None):
    """助手从网站读回来的一页（__wsfill.readback 发来的，或者它回复里的【网申读回】）：存在待办上，有看板记录就同步过去。
    kind：status＝投递记录 / 进度页（可带 positions＝投了哪些岗位）；resume＝实际提交的简历；jd＝某个岗位的 JD（带 position、location）。
    返回 (待办, 记录 id)。"""
    kind = kind if kind in READBACK_KINDS or kind == "jd" else "other"
    text = hide_ids(str(text or "").strip())[:READBACK_MAX]
    if not text:
        raise ValueError("读回的内容是空的")
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("id") == task_id), None)
        if not t:
            raise NotFound(task_id)
        if kind == "jd":
            name = str(position or title or "").strip()[:80]
            if not name:
                raise ValueError("岗位 JD 要带上岗位名称（position）")
            t.setdefault("jds", {})[name] = {"at": _now(), "url": checks.safe_url(url) if url else "",
                                             "location": str(location or "")[:40], "text": text}
            _merge_positions(t, [{"name": name, "location": str(location or "")[:40], "status": ""}])
        else:
            t.setdefault("readback", {})[kind] = {"at": _now(), "url": checks.safe_url(url) if url else "",
                                                  "title": str(title or "")[:120], "text": text}
        if positions:
            _merge_positions(t, _parse_positions(positions))
        if str(status or "").strip():
            t["site_status"], t["site_status_at"] = str(status).strip()[:200], _now()
        acct = clean_account(account, t.get("account", ""))
        if acct:
            t["account"] = acct
        t["readback_state"] = ""
        t["updated_at"] = _now()
        _save(items)
    return t, sync_readback(t)


def compose_readback(t):
    parts = []
    for kind, head in READBACK_KINDS.items():
        p = (t.get("readback") or {}).get(kind)
        if p and p.get("text"):
            parts.append(f"【{head}（{p.get('at', '')} 从网站读回）】" + (f"\n{p['url']}" if p.get("url") else "") + "\n" + p["text"])
    return "\n\n".join(parts)


def sync_readback(t):
    """读回的内容、网站上的进度、登录账号写进看板。投了几个岗位就几条记录（第一个岗位用原来那条，其余新建），
    每条带自己的 JD，共用同一份「实际提交的简历 / 投递记录」原文。还没有记录的先存在待办上，建记录时再带过去。"""
    if not t or not (t.get("readback") or t.get("site_status") or t.get("positions")):
        return ""
    rid = next((r for r in (t.get("record_id"), t.get("email_record_id")) if r and records.get(r)), "")
    if not rid:
        return ""
    text = compose_readback(t)
    base = records.get(rid)

    def fields_for(rec, st=""):
        f = {}
        if text and text != rec.get("ws_submitted"):
            f["ws_submitted"] = text
        st = st or t.get("site_status") or ""
        if st:
            f.update(site_status=st, site_status_at=t.get("site_status_at", "") or _now())
        if t.get("account") and not rec.get("apply_account"):
            f["apply_account"] = t["account"]
        if t.get("url") and not rec.get("apply_url"):
            f["apply_url"] = t["url"]
        return f

    positions = t.get("positions") or []
    if not positions:
        f = fields_for(base)
        if f:
            records.update(rid, f)
        return rid
    pr = dict(t.get("position_records") or {})
    for i, p in enumerate(positions):
        r_id = pr.get(p["name"])
        if not (r_id and records.get(r_id)):
            if rid not in pr.values():
                r_id = rid                       # 第一个岗位用原来那条（「岗位未选」那条改成这个岗位）
            else:
                rec = records.new_record(
                    company_name=base.get("company_name") or t.get("company", ""), company_type=base.get("company_type", ""),
                    job_title=p["name"], job_location=p.get("location", ""), status=base.get("status", "已投递"),
                    send_mode="未发邮件", apply_channel="网申/链接", apply_url=t.get("url", ""), platform=base.get("platform", ""),
                    apply_account=t.get("account", ""), resume_version="网申上传", attach_report=False,
                    source_type=base.get("source_type", "网申"), sent_at=base.get("sent_at", ""), sent_ts=base.get("sent_ts", 0),
                    campaign=base.get("campaign", ""), focus_industry=base.get("focus_industry", ""), ws_task_id=t["id"])
                r_id = records.add(rec)
            pr[p["name"]] = r_id
        rec = records.get(r_id)
        f = fields_for(rec, p.get("status", ""))
        if rec.get("job_title") != p["name"]:
            f["job_title"] = p["name"]
        if p.get("location") and rec.get("job_location") != p["location"]:
            f["job_location"] = p["location"]
        jd = (t.get("jds") or {}).get(p["name"])
        if jd and jd.get("text") and jd["text"] != rec.get("jd_text"):
            f["jd_text"] = jd["text"]
        if r_id != rid and rec.get("status") == "草稿" and base.get("status") == "已投递":
            f.update(status="已投递", sent_at=base.get("sent_at", ""), sent_ts=base.get("sent_ts", 0))   # 跟着主记录一起投出，投递时间一样
        if f:
            records.update(r_id, f)
    if pr != (t.get("position_records") or {}):
        update(t["id"], position_records=pr)
    return rid
