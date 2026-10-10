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
    from . import backup
    backup.tick()
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


def gone():
    """本人整家删掉的申请（软删除，7 天内能恢复）：它们的网申待办不再列出，也不再被助手的记录、同一个网址认领。"""
    from . import apps
    try:
        return {a["id"] for a in apps.list_apps(include_deleted=True) if a.get("deleted_at")}
    except Exception:
        return set()


def _find(items, url="", job="", queue_id=""):
    out = gone()
    items = [t for t in items if t.get("id") not in out]
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
    from . import apps
    apps.ensure_for_task(t)   # v3：每条网申待办对应一个申请（id 相同）
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


def update(task_id, by="面板", **fields):
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
    from . import apps
    apps.mirror_task(t, fields, by=by)   # v3：同步到同 id 的申请上
    return t


def delete(task_id):
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("id") == task_id), None)
        if not t:
            return False
        if t.get("status") != "已提交":   # 没提交的草稿记录一起删（多个岗位的也都删），看板里不留空记录
            for rid in {t.get("record_id"), *(t.get("position_records") or {}).values()} - {"", None}:
                _drop_draft(rid)
        items.remove(t)
        _save(items)
    try:                                  # 申请里网申那一半跟着去掉（还剩邮件那条就留着卡）
        from . import apps
        apps.detach_task(task_id)
    except Exception:
        pass
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
    try:                                      # 邮件那条记录并进网申待办的申请（同一家一张卡，渠道「邮件+网申」）
        from . import apps
        apps.ensure_for_task(t)
    except Exception:
        pass
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
                             apply_account=t.get("account", ""), app_id=t["id"],
                             resume_version="网申上传", source_type="网申（面板助手）" if t.get("chat_id") else "网申")
    return records.add(rec)


UNSUBMITTED = ("待填", "助手在填", "等你处理", "已填待提交")   # 退回「还没交」（「不投了」另算：交过的照旧算投过）


def is_web_record(r):
    """网申那边的岗位记录（不是 Gmail 发出去的那封信：「邮箱+网申」读回以后，第一个岗位用的就是邮件那条）。"""
    return not (r.get("to_email") or r.get("gmail_message_id") or r.get("send_mode") in ("发送", "草稿", "定时"))


def _unsubmit(t, *, by="本人", reason="网申待办退回了，其实没交"):
    """网申待办从「已提交」退回「还没交」：网申那边还停在「已投递」的岗位退回草稿、清掉投递时间（已经到笔试 / 面试的不动，
    Gmail 已经发出去的那封信不动）；这家一个投出去的都不剩了，「首次投出」也清掉。返回退回的记录 id。"""
    from . import apps
    out = []
    for rid in sorted({t.get("record_id"), *(t.get("position_records") or {}).values()} - {"", None}):
        r = records.get(rid)
        if r and r.get("status") == "已投递" and not r.get("deleted_at") and is_web_record(r):
            try:
                apps.advance(rid, "草稿", by=by, manual=True, reason=reason)
                records.update(rid, {"sent_at": "", "sent_ts": 0, "sent_at_locked": False}, by=by)
                out.append(rid)
            except (apps.NotFound, ValueError):
                pass
    if out:
        try:
            apps.clear_first_submitted(t["id"])
        except apps.NotFound:
            pass
    return out


def set_status(task_id, status, note=""):
    """改状态，看板跟着走：已填待提交 → 看板一条「草稿」；已提交 → 「已投递」（邮件那条记录上补记）；不投了 → 删掉草稿。"""
    if status not in STATUSES:
        raise ValueError(f"不认识的状态：{status}")
    with _lock:
        t = get(task_id)
        before = t.get("status")
        fields = {"status": status, "halted": ""}
        if before == "已提交" and status in UNSUBMITTED:
            fields["submitted_at"] = ""
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
    if before == "已提交" and status in UNSUBMITTED:
        _unsubmit(t)
    sync_readback(t)   # 提交前就读回过的（或者刚建出看板记录）：带到记录上
    from . import apps
    try:
        if status == "已提交":
            apps.mark_submitted(task_id, at=t.get("submitted_at") or None, basis=note or "网申页标了已提交", by="本人")
        elif status == "不投了":
            apps.give_up(task_id, note or t.get("note") or "待补原因", by="本人")
    except (apps.NotFound, ValueError):
        pass
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
    if re.match(r"\s*(?:在填|接着填|继续填)", word):   # 「在填（证件号你填好了，我接着填）」：先认开头，别被括号里的「填好了」「你」带偏
        return "助手在填"
    if any(w in word for w in SUBMITTED_WORDS) and "待" not in word:
        return "已提交"
    if any(w in word for w in FILLED_WORDS):
        return "已填待提交"
    if any(w in word for w in WAIT_WORDS):
        return "等你处理"
    if any(w in word for w in ("在填", "接着填", "继续填")):   # 等本人做的事（证件号、登录）做好了，助手接着填
        return "助手在填"
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


OPEN, CLOSE = "（(【[「《", "）)】]」》"
SUMMARY_JOB = re.compile(r"(等|共)\s*[\d一二三四五六七八九十两]+\s*个|[\d一二三四五六七八九十两]+\s*个\s*(岗位|志愿|职位)")   # 「某某计划等 4 个志愿」「（5个岗位：……）」是概括，不是岗位单
PLACEHOLDER_JOB = re.compile(r"^[（(][^（()）]*[)）]$|^(待读回|岗位见网站|岗位见网页|还没选|见网页|见网站|未选|无)$")   # 整个就是一对括号：「（待读回）」


def _top_split(raw, seps):
    """只在括号外面的分隔符处切开：「投资专员（甲公司股权；乙市）」是一个岗位，不是两个。"""
    parts, depth, cur = [], 0, ""
    for ch in str(raw or ""):
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE and depth:
            depth -= 1
        if depth == 0 and ch in seps:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


LEAD_TAG = re.compile(r"^(?:\s*[（(【\[][^（()）【】\[\]]*[)）】\]])+\s*")       # 「（2027届校招）」「【优先】」这类开头标签
VOL_TAG = re.compile(r"\s*[（(]\s*第\s*[一二三四五六七八九十\d]+\s*志愿\s*[)）]\s*")
TAIL = re.compile(r"\s*[（(]([^（()）]*)[)）]\s*$")
NO_PLACE = {"地点网站未显示", "网站未显示", "未显示", "未注明", "不限", "待定"}
_CJK = re.compile(r"[\u4e00-\u9fff]")


def base_name(name):
    """比岗位用的名字：去掉「（第2志愿）」、开头的标签、末尾括号里的地点 / 方向 / 公司、空白。"""
    s = LEAD_TAG.sub("", VOL_TAG.sub("", str(name or "")))
    return re.sub(r"\s+", "", TAIL.sub("", s))


def _quals(s):
    """限定词（地点、方向、公司）拆成一个个：「北京-朝阳区（某某中心）」→ 北京-朝阳区、某某中心；「甲股权；乙市」→ 甲股权、乙市。"""
    out = set()
    for q in re.split(r"[；;，,、/|]+", re.sub(r"[（(]([^（()）]*)[)）]", r"，\1", str(s or ""))):
        q = re.sub(r"\s+", "", q)
        if q and q not in NO_PLACE:
            out.add(q)
    return out


def _forms(p):
    """一个岗位的几种读法 [(名字, 名字里的限定词, 地点栏的限定词)]：名字末尾括号里的是「名字里的」（方向、公司、地点都可能）；
    「甲集团 产业投资岗」（公司 空格 岗位）再多一种读法：岗位名「产业投资岗」、名字里的限定词「甲集团」。"""
    s = LEAD_TAG.sub("", VOL_TAG.sub("", str(p.get("name") or ""))).strip()
    named, loc = set(), _quals(p.get("location"))
    m = TAIL.search(s)
    if m:
        named = _quals(m.group(1))
        s = s[:m.start()].strip()
    out = [(re.sub(r"\s+", "", s), named, loc)]
    m = re.fullmatch(r"(\S+)\s+(\S+)", s)
    if m and _CJK.search(m.group(1)) and _CJK.search(m.group(2)):
        out.append((m.group(2), named | _quals(m.group(1)), loc))
    return out


def _overlap(xs, ys):
    return any(x == y or x in y or y in x for x in xs for y in ys)


CITIES = ("北京", "上海", "深圳", "广州", "杭州", "香港", "澳门", "苏州", "南京", "成都", "新加坡", "天津", "重庆", "武汉", "西安",
          "长春", "长沙", "厦门", "青岛", "宁波", "合肥", "济南", "郑州", "福州", "无锡", "珠海", "东莞", "海外", "全国", "线上", "远程")


def _is_place(s):
    """括号里写的是不是地点（每一段都是城市，或者「XX市 / XX区」这样的短地名）。"""
    parts = [x for x in re.split(r"[；;，,、/|\s]+", str(s or "")) if x]
    return bool(parts) and all(x.startswith(CITIES) or (len(x) <= 8 and x[-1] in "市省区县") for x in parts)


def _rank(p):
    """第几志愿：岗位上记的，或者名字 / 进度里写的「（第2志愿）」。0 = 不知道。"""
    if p.get("rank"):
        return int(p["rank"])
    for s in (p.get("name"), p.get("status")):
        m = VOLUNTEER.search(str(s or ""))
        if m:
            n = m.group(1) or m.group(2)
            return int(n) if n.isdigit() else _CN_NUM.get(n, 0)
    return 0


def same_position(a, b):
    """两条是不是同一个岗位（{name, location, rank}）：名字一样（去掉开头标签、末尾括号、志愿序号以后），志愿序号不冲突，并且
    - 两边名字里都带了括号（方向 / 公司 / 地点）：括号里的要对得上（「管理培训生（公司金融方向）」和「（投行与金融市场方向）」是两个，
      哪怕地点都写「总行」）；
    - 只有一边名字里带括号：它要对得上另一边写的任何一处，或者两边地点对得上（「管理培训生」地点「公司金融方向」和
      「管理培训生（公司金融方向）」是一个）；
    - 都没带：比地点（「北京」和「北京-朝阳区」算对上，「北京」和「上海」是两个）；有一边什么都没写，算同一个。
    同名的第 1 志愿和第 2 志愿是两个。"""
    ra, rb = _rank(a), _rank(b)
    if ra and rb and ra != rb:
        return False
    for na, ta, la in _forms(a):
        for nb, tb, lb in _forms(b):
            if not na or na != nb:
                continue
            if ta and tb:
                ok = _overlap(ta, tb)
            elif not (ta | la) or not (tb | lb):
                ok = True
            elif ta or tb:     # 只有一边名字里带括号：括号里的对上另一边写的任何一处，或者两边地点对得上
                ok = (_overlap(ta, tb | lb) if ta else _overlap(tb, ta | la)) or _overlap(la, lb)
            else:
                ok = _overlap(la, lb)
            if ok:
                return True
    return False


def split_jobs(raw):
    """【网申记录】里的「岗位」可能列了好几个（「A（第一志愿）、B（第二志愿）」「A；B」）：拆成 [{name, location, rank}]。
    只有一个岗位、也没写志愿的，返回空列表（照旧当成这家的岗位名）。"""
    raw = str(raw or "").strip()
    parts = [p for p in _top_split(raw, "；;、，,") if not PLACEHOLDER_JOB.match(p)]
    if len(parts) < 2 and "志愿" not in raw:
        return []
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        rank = 0
        m = VOLUNTEER.search(part)
        if m:
            n = m.group(1) or m.group(2)
            rank = int(n) if n.isdigit() else _CN_NUM.get(n, 0)
            part = VOLUNTEER.sub("", part).strip(" ：:-—")
        m = TAIL.search(part)   # 末尾括号里是城市才当地点（「总行管培生（北京）」）；方向、公司留在名字里（「管理培训生（公司金融方向）」）
        name, loc = (part[:m.start()].strip(), m.group(1).strip()) if m and _is_place(m.group(1)) else (part, "")
        if name:
            out.append({"name": name[:80], "location": loc[:40], "status": "", "rank": rank})
    return out if len(out) > 1 or (out and out[0]["rank"]) else []


LOCAL_URL = re.compile(r"\s*https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)", re.I)


def apply_markers(text, *, task_id="", chat_id=""):
    """助手报的【网申记录】：更新对应的网申待办（对话是从某条待办开的就认那条，否则按网址找，找不到新建）。
    已提交、不投了是终态：助手只能补账号、岗位、进度，改不了状态（读回时要登录，只记「读回等你登录」）。
    返回状态真的变了的待办（面板据此在对话里说一句）。"""
    done = []
    marks = []
    for d in parse_markers(text):
        url = d.get("网址") or d.get("链接") or ""
        url = url if re.match(r"\s*https?://", url, re.I) and not LOCAL_URL.match(url) else ""   # 「（待你告诉我）」、面板自己的地址都不算
        job = d.get("岗位", "").strip()
        job = "" if PLACEHOLDER_JOB.match(job) else job        # 「（待读回）」「（岗位见网站）」不是岗位名，也不拿来认是哪一家
        jobs = split_jobs(job)
        picked = jobs or ([{"name": job[:80], "location": "", "status": "", "rank": 0}] if job and not SUMMARY_JOB.search(job) else [])
        marks.append((d, url, job, jobs, picked))
    # 同一家在这条回复里可能分几行写（一组一行、一个岗位一行、再加一行汇总）：合起来才是它报的岗位单。
    # 只有填的过程中（不是报「已提交」的那条）、没有汇总写法的，才拿它去掉之前报过、这次没报的岗位
    plan = {}
    for d, url, job, jobs, picked in marks:
        g = plan.setdefault(task_id or norm_url(url), {"picked": [], "sure": True})
        g["picked"] += picked
        if (job and SUMMARY_JOB.search(job)) or _marker_status(d.get("状态", "")) == "已提交":
            g["sure"] = False
    for d, url, job, jobs, picked in marks:
        company = d.get("公司", "")
        status = _marker_status(d.get("状态", ""))
        g = plan[task_id or norm_url(url)]
        with _lock:
            t = None
            if task_id:
                try:
                    t = get(task_id)
                except NotFound:
                    t = None
            if task_id and (not t or t["id"] in gone()):
                continue          # 这个对话对应的那家已经被本人删了：不再新建（以前会一删一建，循环出一堆「已提交」）
            if not t:
                t = _find(_load(), url, "" if jobs else job)
            if not t and not url:
                continue          # 没有网址又对不上已有的：不凭一句话新建一家
            if not t:
                t = add(url, company, "" if jobs else job, source="助手记录", chat_id=chat_id)
            if status == "已提交" and not task_id and t.get("source") == "助手记录":
                status = "已填待提交"     # 助手自己记出来的一家（不是本人从网申页交给它的）：它说「已提交」不算数，等本人点「我已提交」
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
            w = {**t, "positions": [dict(x) for x in t.get("positions") or []],
                 "position_records": dict(t.get("position_records") or {})}
            dropped, hide = _drop_unpicked(w, g["picked"]) if g["picked"] and g["sure"] and before not in FINAL else ([], [])
            if jobs or dropped:
                _merge_positions(w, picked)
                fill["positions"], fill["position_records"] = w["positions"], w["position_records"]
                if w.get("record_id") != t.get("record_id"):
                    fill["record_id"] = w["record_id"]
                fill["job"] = "、".join(x["name"] for x in w["positions"])[:120] or t.get("job", "")
            if fill:
                t = update(t["id"], **fill)
            if hide:
                from . import apps
                for rid in hide:
                    try:
                        apps.hide_position(rid, by="面板", reason="助手报的岗位单里没有了（本人挑定了别的；说「把它放回来」能放回）")
                    except apps.NotFound:
                        pass
            if before not in FINAL and status and status != before:
                t = set_status(t["id"], status)
            elif jobs or progress:
                sync_readback(t)
        mode_said = (d.get("志愿方式") or "").strip()
        if mode_said:
            _record_volunteer_mode(t["id"], mode_said)
        if t.get("status") != before:
            done.append(t)
    return done


def _record_volunteer_mode(task_id, said):
    """助手报的「志愿方式：串行（原话：……）」：记到申请上。本人自己改过（不是助手 / 读回记的）就不覆盖。"""
    from . import apps
    mode = "串行" if re.search(r"串行|依次|按顺序", said) else "平行" if re.search(r"平行|同时", said) else ""
    if not mode:
        return
    quote = (re.search(r"原话[：:]\s*(.+?)[）)]?\s*$", said) or [None, ""])[1]
    try:
        a = apps.get(task_id)
        by_user = any(e.get("kind") == "志愿" and e.get("by") == "本人" for e in a.get("timeline") or [])
        if not by_user and a.get("volunteer_mode") != mode:
            apps.set_volunteer_mode(task_id, mode, (quote or said)[:160], by="助手")
    except (apps.NotFound, ValueError):
        pass


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
    items = raw if isinstance(raw, list) else _top_split(raw, "；;\n")
    for it in items:
        if isinstance(it, dict):
            name = str(it.get("name") or it.get("岗位") or "").strip()
            loc = str(it.get("location") or it.get("地点") or "").strip()
            st = str(it.get("status") or it.get("进度") or "").strip()
        else:
            m = re.match(r"\s*(.+?)\s*[（(]([^（()）]+)[)）]\s*$", str(it))
            name, loc, st = (m.group(1).strip(), m.group(2).strip(), "") if m else (str(it).strip(), "", "")
        if name and not PLACEHOLDER_JOB.match(name):
            out.append({"name": name[:80], "location": loc[:40], "status": st[:60]})
    return out


SRC_RANK = {"": 0, "助手": 0, "JD": 1, "网站": 2, "本人": 3}   # 岗位名以谁说的为准：本人 > 网站「我的投递」> 岗位 JD 页 > 助手


def _merge_positions(t, new, *, src=""):
    """新报的岗位并进这家的岗位单：是同一个岗位（same_position，名字一模一样的优先）就补上新的有值的字段（地点、进度、志愿序号），
    不是就加一条。新来的说法更可信（src 排名更高）就把岗位名换成它的（看板记录、JD 跟着换）；名字定下来以后，
    同样可信或者更不可信的说法不再改名，免得几次读回写法略有不同、标题来回变。"""
    pos = t.setdefault("positions", [])
    rank = SRC_RANK.get(src, 0)
    for p in new:
        p = {**p, "src": src} if src else dict(p)
        same = [x for x in pos if same_position(x, p)]
        cur = next((x for x in same if x["name"] == p["name"]), None) or (same[0] if same else None)
        if not cur:
            pos.append(p)
            continue
        if rank > SRC_RANK.get(cur.get("src") or "", 0):
            if cur["name"] != p["name"] and not any(x["name"] == p["name"] for x in pos):
                for key in ("position_records", "jds"):
                    d = t.get(key) or {}
                    if cur["name"] in d and p["name"] not in d:
                        d[p["name"]] = d.pop(cur["name"])
                cur["name"] = p["name"]
            cur["src"] = src
        cur.update({k: v for k, v in p.items() if v and k not in ("name", "src")})


def _ever_submitted(rec):
    return rec.get("status") not in ("草稿", None, "") or any(h.get("to") == "已投递" for h in rec.get("history") or [])


def _drop_unpicked(t, picked):
    """还没交的时候，助手报的岗位单就是本人现在挑定的：先前报过、这次没报的岗位从岗位单上拿掉，看板上那条收起（软删，能放回）。
    只动从没交过的网申草稿：网站上读回的、本人亲口加的、交过的、Gmail 发出去的都不动。主记录没有别的可换就不收，
    留给这次报的岗位接着用。t 是待办的工作副本（在它的 positions / position_records / record_id 上改）。
    返回 (拿掉的岗位名, 要收起的记录 id)。"""
    pr = t.setdefault("position_records", {})
    keep, drop = [], []
    for p in t.get("positions") or []:
        rid = pr.get(p.get("name"))
        rec = records.get(rid) if rid else None
        if p.get("src") in ("网站", "本人") or any(same_position(p, x) for x in picked) or \
                (rec and (not is_web_record(rec) or _ever_submitted(rec))):
            keep.append(p)
        else:
            drop.append((p.get("name"), rid))
    hide = []
    for name, rid in drop:
        pr.pop(name, None)
        if not rid:
            continue
        if rid == t.get("record_id"):
            others = [x for x in pr.values() if x and records.get(x)]
            if not others:
                continue
            t["record_id"] = others[0]
        hide.append(rid)
    t["positions"] = keep
    return [name for name, _ in drop], hide


def _jd_known(t):
    """这家投了哪些岗位还不知道（助手没报岗位、报的是占位或「等 N 个」）。"""
    job = str(t.get("job") or "").strip()
    return bool(job) and not PLACEHOLDER_JOB.match(job) and not SUMMARY_JOB.search(job)


def _attach_jd(t, name, location):
    """读了某个岗位的 JD：岗位单里有这个岗位就把 JD 页上的写法、地点补上；没有就只把 JD 存着，不因为看了一眼就多记一个岗位
    （本人挑岗位时助手会看好几个，只有真投的才进看板）。只有交了以后、投了哪些还不知道、网站岗位单也还没读到时，读的 JD 才算投了的岗位。"""
    p = {"name": name, "location": location, "status": ""}
    pos = t.get("positions") or []
    if any(same_position(x, p) for x in pos) or (
            t.get("status") == "已提交" and not _jd_known(t) and not any(x.get("src") == "网站" for x in pos)):
        _merge_positions(t, [p], src="JD")


def jd_for(t, p):
    """这个岗位的 JD（岗位 JD 页上的名字跟岗位单上的写法可能略有不同：同一个岗位就算）。"""
    jds = t.get("jds") or {}
    if p.get("name") in jds:
        return jds[p["name"]]
    return next((v for k, v in jds.items() if same_position({"name": k, "location": (v or {}).get("location", "")}, p)), None)


def save_readback(task_id, kind, text, *, url="", title="", status="", account="", position="", location="", positions=None,
                  source="网页脚本"):
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
            _attach_jd(t, name, str(location or "")[:40])
        else:
            t.setdefault("readback", {})[kind] = {"at": _now(), "url": checks.safe_url(url) if url else "",
                                                  "title": str(title or "")[:120], "text": text}
        if positions:
            _merge_positions(t, _parse_positions(positions), src="网站")
        if str(status or "").strip():
            t["site_status"], t["site_status_at"] = str(status).strip()[:200], _now()
        acct = clean_account(account, t.get("account", ""))
        if acct:
            t["account"] = acct
        t["readback_state"] = ""
        t["updated_at"] = _now()
        _save(items)
    rid = sync_readback(t)
    from . import apps
    try:   # v3：读回原文另存成快照（只追加、按内容去重），志愿规则顺手认出来
        label = {"status": "投递记录", "resume": "简历", "jd": f"岗位JD-{position or title}"}.get(kind, "页面")
        apps.mirror_task(t, {"readback", "readback_state", "positions", "position_records", "site_status", "account", "jds"})
        apps.add_snapshot(task_id, label, text, url=url, source=source)
        mode, line = apps.guess_volunteer_mode(text)
        if mode and not apps.get(task_id).get("volunteer_mode"):
            apps.set_volunteer_mode(task_id, mode, line, by="读回")
    except (apps.NotFound, ValueError):
        pass
    if kind == "status" and positions:   # 网站「我的投递」上列的是实情：看板岗位比它多，提醒本人核对
        _check_count(t, _parse_positions(positions))
    if kind == "resume":   # 交上去的简历读回来了：后台和底稿比一比，本人在网站上改过、补过的学回底稿
        from . import learn
        learn.later(task_id)
    return t, rid


def _extra_records(pos, site):
    """看板上这家的岗位里，对不上网站「我的投递」任何一条的（先认名字一模一样的，再认同一个岗位的不同写法）。"""
    left = list(pos)
    for p in site:
        hit = next((r for r in left if (r.get("job_title") or "") == p["name"]), None) or next(
            (r for r in left if same_position({"name": r.get("job_title") or "", "rank": r.get("choice_no") or 0,
                                               "location": "，".join(filter(None, (r.get("job_location"), r.get("org"))))}, p)), None)
        if hit:
            left.remove(hit)
    return left


def _check_count(t, site):
    """网站上投了 n 个、看板记了更多：出一条「岗位数」建议，本人点「采纳」就把多出来的从看板上去掉。
    同一批多出来的岗位提醒过一次（不管点没点）就不再提。"""
    from . import apps
    if not site:
        return
    try:
        a = apps.get(t["id"])
    except apps.NotFound:
        return
    pos = [r for r in apps.positions(t["id"]) if not r.get("deleted_at")]
    extra = _extra_records(pos, site)
    if len(pos) <= len(site) or not extra:
        return
    hide = sorted(r["id"] for r in extra)
    if any(s.get("kind") == "岗位数" and sorted((s.get("payload") or {}).get("hide") or []) == hide for s in a.get("suggestions") or []):
        return
    apps.suggest(t["id"], "岗位数", f"{t.get('company') or '这家'}网站「我的投递」上是 {len(site)} 个岗位，看板记了 {len(pos)} 个，"
                 f"多出来的像是：{'、'.join((r.get('job_title') or '（没写岗位名）')[:30] for r in extra[:5])}。点「采纳」把这几条从看板上去掉",
                 {"hide": hide, "site": len(site), "board": len(pos)})


def compose_readback(t):
    parts = []
    for kind, head in READBACK_KINDS.items():
        p = (t.get("readback") or {}).get(kind)
        if p and p.get("text"):
            parts.append(f"【{head}（{p.get('at', '')} 从网站读回）】" + (f"\n{p['url']}" if p.get("url") else "") + "\n" + p["text"])
    return "\n\n".join(parts)


def add_position(task_id, name, location="", *, by="本人"):
    """本人说「X 还投了 Y」：岗位单上加一条（本人说的名字定下来，读回不再改），这家已经有看板记录的跟着建一条（阶段跟主记录，
    主记录已经走到笔试 / 面试的，新的这条记成已投递）。返回新建的记录 id（还没有看板记录就空串）。"""
    name, location = str(name or "").strip()[:80], str(location or "").strip()[:40]
    if not name:
        raise ValueError("岗位名是空的")
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("id") == task_id), None)
        if not t:
            raise NotFound(task_id)
        pr = t.setdefault("position_records", {})
        bid = next((x for x in (t.get("record_id"), t.get("email_record_id")) if x and records.get(x)), "")
        base = records.get(bid) if bid else None
        if base and base.get("job_title") and bid not in pr.values():   # 原来那条（网申的、或者邮件那条）先放进岗位单，免得新岗位占了它的记录   # 原来只有一个岗位、没有岗位单：先把它放进去，免得新岗位占了它的记录
            if not any(x.get("name") == base["job_title"] for x in t.get("positions") or []):
                t.setdefault("positions", []).insert(0, {"name": base["job_title"], "location": base.get("job_location", ""),
                                                         "status": "", "rank": base.get("choice_no") or 0})
            pr[base["job_title"]] = base["id"]
        before = set(pr.values())
        _merge_positions(t, [{"name": name, "location": location, "status": "", "rank": 0}], src="本人")
        t["updated_at"] = _now()
        _save(items)
    sync_readback(t)
    new = [rid for rid in (get(task_id).get("position_records") or {}).values() if rid not in before]
    for rid in new:
        r = records.get(rid) or {}
        if r.get("status") not in ("草稿", "已投递"):
            records.update(rid, {"status": "已投递"}, by=by)
    return new[0] if new else ""


def drop_position(task_id, name):
    """撤销「加岗位」（还没建看板记录的那种）：从岗位单上拿掉。"""
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("id") == task_id), None)
        if not t:
            raise NotFound(task_id)
        if not (t.get("position_records") or {}).get(name):
            t["positions"] = [x for x in t.get("positions") or [] if x.get("name") != name]
            t["updated_at"] = _now()
            _save(items)


def rename_position(task_id, record_id, new, *, by="本人", src="本人"):
    """本人说「X 的 Y 应该叫 Z」：岗位单、JD、看板记录一起改名，名字定下来（以后读回不再改）。
    返回 {name: 原来的名字, src: 原来是谁定的}（撤销时带着 src 改回去，读回照旧能改它）。"""
    new = str(new or "").strip()[:80]
    if not new:
        raise ValueError("新名字是空的")
    rec = records.get(record_id)
    if not rec:
        raise NotFound(record_id)
    with _lock:
        items = _load()
        t = next((x for x in items if x.get("id") == task_id), None)
        prev_src = ""
        if t:
            pr = t.setdefault("position_records", {})
            old = next((n for n, rid in pr.items() if rid == record_id), None)
            prev_src = next((x.get("src") or "" for x in t.get("positions") or [] if x.get("name") == old), "")
            if old and old != new and new not in pr:
                pr[new] = pr.pop(old)
                jds = t.get("jds") or {}
                if old in jds and new not in jds:
                    jds[new] = jds.pop(old)
                for x in t.get("positions") or []:
                    if x.get("name") == old:
                        x["name"], x["src"] = new, src
            elif not old and new not in pr:      # 只有一个岗位、没有岗位单：建一条岗位单（本人定的名字），读回就不会再改它
                t.setdefault("positions", []).append({"name": new, "location": rec.get("job_location", ""), "status": "",
                                                      "rank": rec.get("choice_no") or 0, "src": src})
                pr[new] = record_id
            if (t.get("job") or "") == (rec.get("job_title") or "") or not t.get("job"):
                t["job"] = new
            t["updated_at"] = _now()
            _save(items)
    records.update(record_id, {"job_title": new}, by=by)
    return {"name": rec.get("job_title") or "", "src": prev_src}


def _held_back(rec):
    """本人亲口把这个岗位退回过草稿（「X 的 Y 其实没交」）：读回时不再跟着主记录改回已投递。"""
    return any(h.get("to") == "草稿" and h.get("by") == "本人" for h in rec.get("history") or [])


def sync_readback(t):
    """读回的内容、网站上的进度、登录账号写进看板。投了几个岗位就几条记录（第一个岗位用原来那条，其余新建），
    每条带自己的 JD，共用同一份「实际提交的简历 / 投递记录」原文。还没有记录的先存在待办上，建记录时再带过去。"""
    if not t or not (t.get("readback") or t.get("site_status") or t.get("positions") or t.get("jds")):
        return ""
    ids = [r for r in (t.get("record_id"), t.get("email_record_id")) if r and records.get(r)]
    rid = next((r for r in ids if not records.get(r).get("deleted_at")), "") or next(iter(ids), "")   # 主记录被收起了就用还在的那条
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
        jds = t.get("jds") or {}
        title = base.get("job_title") or ""
        if not _jd_known(t) and (not title or PLACEHOLDER_JOB.match(title)) and len(jds) == 1:
            name, jd = next(iter(jds.items()))     # 不知道投的哪个岗位、只读过一个岗位的 JD：就是它
            f.update(job_title=name, **({"job_location": jd["location"]} if jd.get("location") else {}))
        else:
            jd = None if base.get("jd_text") else jd_for(t, {"name": title or t.get("job") or "", "location": base.get("job_location") or ""})
        if jd and jd.get("text") and not base.get("jd_text"):
            f["jd_text"] = jd["text"]
        if f:
            records.update(rid, f)
        return rid
    pr = dict(t.get("position_records") or {})
    for i, p in enumerate(positions):
        r_id = pr.get(p["name"])
        if not (r_id and records.get(r_id)):
            if rid not in pr.values() and (is_web_record(base) or not base.get("job_title") or
                                           same_position({"name": base.get("job_title") or "", "location": base.get("job_location") or ""}, p)):
                r_id = rid                       # 第一个岗位用原来那条（「岗位未选」那条改成这个岗位；邮件那条只给同一个岗位用）
            else:
                rec = records.new_record(
                    company_name=base.get("company_name") or t.get("company", ""), company_type=base.get("company_type", ""),
                    job_title=p["name"], job_location=p.get("location", ""), status=base.get("status", "已投递"),
                    send_mode="未发邮件", apply_channel="网申/链接", apply_url=t.get("url", ""), platform=base.get("platform", ""),
                    apply_account=t.get("account", ""), resume_version="网申上传", attach_report=False,
                    source_type=base.get("source_type", "网申"), sent_at=base.get("sent_at", ""), sent_ts=base.get("sent_ts", 0),
                    campaign=base.get("campaign", ""), focus_industry=base.get("focus_industry", ""), ws_task_id=t["id"],
                    app_id=base.get("app_id") or t["id"])
                r_id = records.add(rec)
            pr[p["name"]] = r_id
        rec = records.get(r_id)
        f = fields_for(rec, p.get("status", ""))
        if rec.get("job_title") != p["name"]:
            f["job_title"] = p["name"]
        if p.get("location") and rec.get("job_location") != p["location"]:
            f["job_location"] = p["location"]
        jd = jd_for(t, p)
        if jd and jd.get("text") and jd["text"] != rec.get("jd_text"):
            f["jd_text"] = jd["text"]
        if r_id != rid and rec.get("status") == "草稿" and base.get("status") == "已投递" and not _held_back(rec):
            f.update(status="已投递", sent_at=base.get("sent_at", ""), sent_ts=base.get("sent_ts", 0))   # 跟着主记录一起投出，投递时间一样
        if f:
            records.update(r_id, f)
    if pr != (t.get("position_records") or {}):
        update(t["id"], position_records=pr)
    return rid
