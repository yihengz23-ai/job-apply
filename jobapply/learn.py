"""底稿学习（资料回流，WP11）：每家交完、把「实际提交的简历」从网站读回来以后，和网申底稿比一比——
本人在网站上亲手改过、补上的（换了一段自我评价、填了证明人、所在部门、学号……）学回底稿，下一家就照新的填；
拿不准的（可能只这家这样、像是助手按网站限制改的、对不上底稿里唯一一格的）不动底稿，出一条「记进底稿？」，本人点了才记；
像是填错了的（籍贯和现居住地对调……）不学，单独提醒本人去网站上改。
本人在助手对话里说的（助手写的【底稿】行）照原话记进底稿。
每次记进底稿的都留一笔（哪家 / 对话、什么时候、原来是什么），能撤销；撤销过、点过「不用」的，以后不再学这一条。
证件号、银行卡号这类永远不记（发给模型前先打码，记的时候再查一遍）。"""

import copy
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timedelta

from . import apps, config, llm, notify, wsprofile, wstasks

PAIR_SECTIONS = tuple(s for s in wsprofile.SECTIONS if s not in ("家庭成员", "项目经历"))   # 「栏目：内容」一行一格的段
WHO = ("本人改的", "本人补的", "助手按网站限制改的", "助手猜的或替本人选的", "网站格式不同", "像是填错了", "看不出")
SCOPE = ("以后都这样", "只这家这样", "看不出")
SENSITIVE = re.compile(r"证件|身份|护照|passport|id\s*no|银行|卡号|账号|账户|社保|医保|工资卡|已隐去", re.I)
VARIANT = re.compile(r"拼音|英文|英语|精简|简短|短版|长版|完整|全称|简称|english|pinyin", re.I)   # 括号里是这些的是另一个版本，不是备注
PLACEHOLDER = {"栏目", "栏目名", "某栏目", "XX", "xx", "X"}
CHOICE = re.compile(r"替你|替本人|推测|空着|留给你|字数|上限|精简|最接近|是你写的|删了|删掉|改短|我选了|按网站|没有.*选")
KEEP = 300
MAX_LINES = 10
_lock = threading.RLock()
_running = set()

SYSTEM = """你帮一位求职者维护「网申底稿」：网申表格会问、简历上没有的个人信息（证明人、所在部门、自我评价、学号、偏好……），助手代填网申时照它填。
这一家已经交了。给你三样：现在的底稿；助手填这家时说过的选择（它替本人选了什么、按网站限制改了什么、留给本人填什么）；交完以后从网站读回的、实际交上去的内容。
找出「实际交上去的」和底稿不一样、或者底稿里没有的地方，一条一条列出来，每条判断：
- who：本人改的（底稿里有，网站上是本人另写的一版，助手没说是它改的）；本人补的（底稿里没有，助手说留给本人或空着，网站上有了）；助手按网站限制改的（助手说了，比如字数上限删短了、选项里没有就选了最接近的）；助手猜的或替本人选的（助手说是推测、替你选的）；网站格式不同（同一个意思写法不同：日期格式、全称简称、加了单位）；像是填错了（和底稿、和常理对不上，像是填错或填反了：籍贯和现居住地对调、底稿写明了怎么填却交了别的，比如排名选了「其它」）；看不出。
- scope：以后都这样（本人的资料或长期偏好：证明人、所在部门、学号、新版自我评价……）；只这家这样（这家特有的：投哪个岗位、意向城市、期望薪资、到岗时间、志愿和调剂、这家专门问的问题）；看不出。
- section：写进底稿的哪一段（只能是给你列出的几段之一）；key：底稿里已有的栏目照抄栏目名，新的起一个一看就懂的名字（比如「A 公司证明人」）；old：底稿里原来的（没有就写空）；new：网站上实际交的原文（照抄，不要改写）；evidence：读回原文里能证明的那一小段；why：一句话（不要抄号码）。
底稿里同一栏有完整版和精简版两格的（比如「自我评价」和「自我评价（精简版，字数有限时用）」）：网站上交的是短的、或者那一栏有字数上限，key 写精简版那一格。
不要列：简历本身的内容（学历、实习经历描述、技能——简历另有一份逐字一致的）；家庭成员、项目经历；上传的文件（照片、简历、作品附件的文件名）；证件号、银行卡号、社保号、打码的内容；读回里没读到的栏目（没读到不等于不一样）；两边意思一样、只是标点空格不同的；网站上选的是底稿里写到的几种之一、或者同样成立的另一种写法（比如微信栏填了底稿里说也能搜到的手机号）；网站上的值和底稿里另一格的值一样的（助手用的就是那一格）。
网站上的比底稿更完整（比如籍贯多写到县级市），who 写看不出、scope 写以后都这样。拿不准就写「看不出」，不要硬判。没有要列的就给空列表。"""

SCHEMA = llm._obj({"items": {"type": "array", "items": llm._obj({
    "section": {"type": "string", "enum": list(PAIR_SECTIONS)},
    "key": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
    "who": {"type": "string", "enum": list(WHO)}, "scope": {"type": "string", "enum": list(SCOPE)},
    "evidence": {"type": "string"}, "why": {"type": "string"}})}})


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── 状态：学习记录（能撤销）、本人不要的、每家学过的那份读回 ───────────────

def _path():
    return config.DATA_DIR / "profile_learn.json"


def _state():
    try:
        st = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    return {"entries": st.get("entries") or [], "rejected": st.get("rejected") or [], "sigs": st.get("sigs") or {}}


def _save_state(st):
    st["entries"] = st["entries"][-KEEP:]
    path = _path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def recent(n=20):
    return list(reversed(_state()["entries"]))[:n]


# ── 号码、栏目名 ─────────────────────────────────────────────

def _sensitive(*texts):
    """证件号、银行卡号这类：关键词，或者去掉空格、横线以后的 15 位以上长号码、截断 / 带星号的证件号。"""
    for t in texts:
        s = str(t or "")
        if SENSITIVE.search(s):
            return True
        joined = re.sub(r"(?<=\d)[\s\-.·]+(?=\d)", "", s)
        if re.search(r"\d{15,}", joined) or apps.MASK_ID_PART.search(joined):
            return True
    return False


def mask_for_model(text):
    """发给模型前打码：带空格、横线写的长号码也算（apps.mask 只认连着写的）。"""
    text = re.sub(r"\d[\d \-.·]{13,}\d",
                  lambda m: "[号码已隐去]" if len(re.sub(r"\D", "", m.group(0))) >= 15 else m.group(0), text or "")
    return apps.mask(text)


def _norm(key):
    return re.sub(r"[（(][^）)]*[）)]|\s", "", str(key or ""))


def _flat(value):
    return re.sub(r"[\s/／·\-－—、，,。；;：:]", "", str(value or ""))


def _paren(key):
    m = re.search(r"[（(]([^）)]*)[）)]", str(key or ""))
    return m.group(1) if m else ""


def _similar(a, b):
    return bool(a and b) and any(a[i:i + 2] in b for i in range(max(1, len(a) - 1)))


def _compatible(a, b):
    """两个栏目名括号里的字：是「拼音」「精简版」这类另一个版本的，要像才算同一格；只是备注（「两段同一人」）就不管。"""
    return _similar(a, b) if VARIANT.search(a) or VARIANT.search(b) else True


def _find(profile, section, key):
    """底稿里的这一格：返回 (段, 那一行, 怎么对上的)。怎么对上：exact 栏目名一字不差；loose 去掉括号对上唯一一格
    （「实习所在部门」对上「实习所在部门（几段都是）」；括号里是「拼音」「精简版」这类另一个版本的不算）；
    cross 给的段里没有、别的段里有；ambiguous 对上好几格；None 底稿里没有。给了段就先只在这一段里找。"""
    def look(secs):
        rows = [(s, it) for s in secs for it in profile.get(s) or [] if isinstance(it, list) and len(it) == 2]
        exact = [r for r in rows if r[1][0] == key]
        if len(exact) == 1:
            return exact[0], "exact"
        if exact:
            return None, "ambiguous"
        p = _paren(key)
        loose = [r for r in rows if _norm(r[1][0]) == _norm(key) and _compatible(p, _paren(r[1][0]))]
        if len(loose) > 1:                # 好几格：括号里的字像的那一格；没写括号的就找也没括号的那一格
            loose = [r for r in loose if (_similar(p, _paren(r[1][0])) if p else not _paren(r[1][0]))] or loose
        if len(loose) == 1:
            return loose[0], "loose"
        return None, ("ambiguous" if loose else None)

    if section in PAIR_SECTIONS:
        hit, how = look([section])
        if hit or how:
            return (*hit, how) if hit else (None, None, how)
        hit, how = look([s for s in PAIR_SECTIONS if s != section])
        return (*hit, "cross") if hit else (None, None, how)
    hit, how = look(list(PAIR_SECTIONS))
    return (*hit, how) if hit else (None, None, how)


# ── 写进底稿、撤销、本人不要的 ──────────────────────────────────

def apply(items, *, source, app_id="", company=""):
    """把几格写进底稿（一次保存），记一笔能撤销的学习记录。返回 (记录或 None, 没写的那几条)。
    每条可以带 expect：做决定时这一格的值（None = 那时没有这一格）——本人这期间改过（现在不一样了）就不写。
    对不上唯一一格的（好几格都像、在别的段）、同一批里第二次写同一格的，也不写；证件号这类、和现在一样的直接跳过。"""
    with _lock, wsprofile.LOCK:
        profile, example = wsprofile.load()
        if example:                    # 还没有自己的底稿
            return None, list(items)
        profile = copy.deepcopy(profile)
        done, skipped, touched = [], [], set()
        for it in items:
            key, new = str(it.get("key") or "").strip()[:60], str(it.get("new") or "").strip()[:3000]
            if not key or not new or key in PLACEHOLDER or _sensitive(key, new, it.get("why")):
                continue
            sec, row, how = _find(profile, it.get("section"), key)
            cur = row[1] if row else None
            if cur == new and how not in ("ambiguous", "cross"):
                continue                   # 已经是这个了：不算冲突
            if how in ("ambiguous", "cross") or (row and (sec, row[0]) in touched) or ("expect" in it and cur != it["expect"]):
                skipped.append(it)
                continue
            if row:
                key = row[0]
                row[1] = new
            else:
                sec = it.get("section") if it.get("section") in PAIR_SECTIONS else "其他"
                profile.setdefault(sec, []).append([key, new])
            touched.add((sec, key))
            done.append({"section": sec, "key": key, "old": cur, "new": new, "why": str(it.get("why") or "")[:200]})
        if not done:
            return None, skipped
        wsprofile.save(profile)
        entry = {"id": uuid.uuid4().hex[:10], "at": _now(), "source": source, "app_id": app_id, "company": company,
                 "items": done, "undone": False}
        st = _state()
        st["entries"].append(entry)
        _save_state(st)
    if app_id:
        for d in done:
            try:
                apps.add_timeline(app_id, "底稿", f"记进底稿：{d['key']}：{d['new'][:80]}"
                                  + (f"（原来：{d['old'][:40]}）" if d["old"] else ""), source)
            except apps.NotFound:
                break
    return entry, skipped


def _pair(key, value):
    return [_norm(key), hashlib.sha1(_flat(value).encode()).hexdigest()[:12]]


def _reject(st, key, value):
    if _pair(key, value) not in st["rejected"]:
        st["rejected"].append(_pair(key, value))


def already_there(section, key, value):
    """底稿里这一格已经是这个值了（只差空格分隔）。"""
    profile, _ = wsprofile.load()
    row = _find(profile, section, key)[1]
    return bool(row) and _flat(row[1]) == _flat(value)


def reject(key, value):
    """本人点了「不用」：以后读回再看到这一栏这个值，不学也不问。"""
    with _lock:
        st = _state()
        _reject(st, key, value)
        _save_state(st)


def undo(entry_id):
    """撤销一笔：倒着把每格改回原来的（新加的删掉）；后来又改过的格子不动。撤了的以后不再学。返回改回了几格。"""
    with _lock, wsprofile.LOCK:
        st = _state()
        e = next((x for x in st["entries"] if x.get("id") == entry_id), None)
        if not e:
            raise KeyError(entry_id)
        if e.get("undone"):
            return 0
        profile, _ = wsprofile.load()
        profile = copy.deepcopy(profile)
        n = 0
        for it in reversed(e["items"]):
            sec, row, how = _find(profile, it["section"], it["key"])
            if how != "exact" or row[1] != it["new"]:
                continue
            if it["old"] is None:
                profile[sec].remove(row)
            else:
                row[1] = it["old"]
            _reject(st, it["key"], it["new"])
            n += 1
        if n:
            wsprofile.save(profile)
            e.update(undone=True, undone_at=_now(), undone_cells=n)
        _save_state(st)
    if n and e.get("app_id"):
        try:
            apps.add_timeline(e["app_id"], "底稿", f"撤销了记进底稿的 {n} 格", "本人")
        except apps.NotFound:
            pass
    return n


def accept(app_id, s):
    """本人点了「记进底稿」：照建议写（建议出来以后这一格又被改过的不覆盖，告诉本人）。"""
    p = s.get("payload") or {}
    company = apps.get(app_id).get("company", "")
    it = {"section": p.get("section"), "key": p.get("key"), "new": p.get("value"), "why": "本人采纳了建议"}
    if "old_exists" in p:
        it["expect"] = p.get("old") if p.get("old_exists") else None
    entry, skipped = apply([it], source=f"采纳：{company}", app_id=app_id, company=company)
    if skipped:
        raise ValueError(f"底稿里「{p.get('key')}」后来改过（或者有好几格都像它），没覆盖；要改的话直接在底稿里改")
    return entry


# ── 对话里说的：助手写的【底稿】行 ─────────────────────────────

def _split_items(s):
    parts = [p.strip() for p in re.split(r"[；;]", s) if p.strip()]
    if len(parts) > 1 and all(re.match(r"[^：:，,。]{1,20}[：:]", p) for p in parts):
        return parts
    return [s]


def chat_items(text):
    """助手回复里的【底稿】：「【底稿】栏目：内容」一行一条（一行里用分号隔开的几条拆开）；
    「【底稿】」单独一行的，下面紧跟的「- 栏目：内容」各算一条。"""
    out, lines = [], (text or "").splitlines()
    for i, line in enumerate(lines):
        if "【底稿】" not in line:
            continue
        rest = re.sub(r"^[\s*`]+|[\s*`]+$", "", line.split("【底稿】", 1)[1])
        if rest:
            out.extend(_split_items(rest))
            continue
        for nxt in lines[i + 1:]:
            m = re.match(r"\s*[-•*·]\s+(.+)", nxt)
            if not m:
                break
            out.extend(_split_items(m.group(1)))
    return out


def from_chat(said, *, app_id="", company=""):
    """「栏目：内容」或「段 / 栏目：内容」，照本人原话记进底稿。返回 (记录或 None, 一句给对话看的话；空 = 不用说)。"""
    s = re.sub(r"^[\s*`>•\-]+|[\s*`]+$", "", said or "")
    m = re.match(r"([^：:]{1,80})[：:]\s*(.+)$", s)
    if not m:
        return None, f"这句没看懂，没记进底稿：{s[:60]}（格式：栏目：内容）"
    left, value = m.group(1).strip().strip("*`"), m.group(2).strip().strip("*`").strip()
    sec, key = "", left
    mm = re.match(r"([^/／]{1,8}?)\s*[/／]\s*(.+)$", left)
    if mm and mm.group(1).strip() in PAIR_SECTIONS:   # 「段 / 栏目」；「是否受过处分 / 被开除」这种栏目名本身带斜杠的照原样
        sec, key = mm.group(1).strip(), mm.group(2).strip()
    if key in PLACEHOLDER or not value:
        return None, ""
    if _sensitive(key, value):
        return None, f"「{key}」没记：证件号、银行卡号这类不进底稿"
    profile, example = wsprofile.load()
    if example:
        return None, "还没有自己的网申底稿：先在「网申」页下面填一份，再记这些"
    found_sec, row, how = _find(profile, sec, key)
    if how == "ambiguous":
        return None, f"底稿里有好几格都像「{key}」，没记：照底稿里的栏目名原样再写一遍"
    entry, _skipped = apply([{"section": found_sec or sec, "key": row[0] if row else key, "new": value, "why": "本人在对话里说的"}],
                            source="对话", app_id=app_id, company=company)
    if not entry:
        return None, f"底稿里「{key}」本来就是这个，没改"
    d = entry["items"][0]
    return entry, f"底稿记上了：{d['key']}：{d['new'][:60]}" + (f"（原来：{d['old'][:30]}）" if d["old"] else "")


# ── 读回以后学 ───────────────────────────────────────────────

def _wrapup(chat_id):
    """助手填这家时说过的选择：整段对话里提到替本人选了什么、留给本人什么、按字数删短了什么的那些句子。"""
    if not chat_id:
        return ""
    try:
        from . import agent
        msgs = [m.get("text") or "" for m in agent._load(chat_id).get("messages") or [] if m.get("role") == "assistant"]
    except Exception:
        return ""
    sents = [s.strip() for t in msgs for s in re.split(r"(?<=[。；！？\n])", t) if CHOICE.search(s)]
    return "\n".join(dict.fromkeys(x for x in sents if x))[-6000:]


def _clean(it):
    it = {k: str((it or {}).get(k) or "").strip() for k in ("section", "key", "old", "new", "who", "scope", "evidence", "why")}
    it["who"] = it["who"] if it["who"] in WHO else "看不出"
    it["scope"] = it["scope"] if it["scope"] in SCOPE else "看不出"
    return it


def _when(t):
    """这家交的时间（没记的用读回的时间），统一到秒。"""
    w = t.get("submitted_at") or ((t.get("readback") or {}).get("resume") or {}).get("at") or ""
    return w + ":00" if len(w) == 16 else w


def _stale(sec, row, when, then, known):
    """这一格在这家交了以后本人又改过（或者是之后才加的）：True；没改过：False；说不准：None。
    先看底稿记的每格修改时间，没记过的再看那时候的备份。"""
    changed = wsprofile.changed_at(sec, row[0])
    if changed:
        return changed[:16] > when[:16]   # 交的时间只记到分钟：同一分钟里的不算之后
    if not known:
        return None
    if then is None:                  # 那之后没再存过
        return False
    then_row = _find(then, sec, row[0])[1]
    return not then_row or then_row[1] != row[1]


def _old_enough(when, hours=24):
    try:
        return datetime.now() - datetime.strptime(when[:16], "%Y-%m-%d %H:%M") > timedelta(hours=hours)
    except ValueError:
        return False


def from_readback(task_id, *, backfill=None, fresh=None):
    """这家的「实际提交的简历」读回来了：和底稿比，本人改的 / 补的记进底稿，拿不准的出建议，像是填错的提醒。同一份读回只学一次。
    backfill（默认：交了超过一天的算）：补学以前交过的——只补底稿里没有的格子，已有的出建议；fresh：这一轮补学已经写过的格子。"""
    t = wstasks.get(task_id)
    text = ((t.get("readback") or {}).get("resume") or {}).get("text") or ""
    if not text.strip():
        return None
    sig = hashlib.sha1(text.encode()).hexdigest()[:16]
    with _lock:
        if _state()["sigs"].get(task_id) == sig or task_id in _running:
            return None
        _running.add(task_id)
    try:
        profile, example = wsprofile.load()
        if example:                   # 还没有自己的底稿：先不学（不记签名，有了底稿以后再学）
            return None
        when = _when(t)
        backfill = _old_enough(when) if backfill is None else backfill
        fresh = set() if fresh is None else fresh
        known, then = wsprofile.as_of(when)
        company = t.get("company") or ""
        content = (f"## 这家：{company}\n\n## 现在的网申底稿（能写的段：{'、'.join(PAIR_SECTIONS)}）\n{wsprofile.as_text(profile)}\n\n"
                   f"## 助手填这家时说过的选择\n{mask_for_model(_wrapup(t.get('chat_id'))) or '（没找到）'}\n\n"
                   f"## 交完以后从网站读回的、实际交上去的内容\n{mask_for_model(text)[:20000]}")
        data, _meta = llm._call(system=SYSTEM, content=content, schema=SCHEMA, effort=config.CLAUDE_EFFORT)
        st = _state()
        done_here = {(_norm(i["key"]), _flat(i["new"])) for e in st["entries"] if e.get("app_id") == task_id for i in e["items"]}
        others = [(it[0], it[1]) for sec in PAIR_SECTIONS for it in profile.get(sec) or [] if isinstance(it, list) and len(it) == 2]
        auto, ask, wrong = [], [], []
        for x in map(_clean, (data or {}).get("items") or []):
            if not x["key"] or not x["new"] or _sensitive(x["key"], x["new"], x["why"]) or _pair(x["key"], x["new"]) in st["rejected"] \
                    or (_norm(x["key"]), _flat(x["new"])) in done_here:
                continue
            sec, row, how = _find(profile, x["section"], x["key"])
            if not row and x["old"]:       # 模型说底稿里原来有值、栏目名却对不上（几段合在一格的「实习所在部门」）：按原来的值找那一格
                same = [(s, it) for s in PAIR_SECTIONS for it in profile.get(s) or []
                        if isinstance(it, list) and len(it) == 2 and _flat(it[1]) == _flat(x["old"])]
                sec, row, how = (*same[0], "byvalue") if len(same) == 1 else (None, None, "ambiguous")
            if len(_flat(x["new"])) >= 6 and any(_flat(v) == _flat(x["new"]) and (not row or k != row[0]) for k, v in others):
                continue                  # 和底稿里另一格一样：助手用的就是那一格
            stale = False if not row or (sec, row[0]) in fresh else _stale(sec, row, when, then, known)
            if stale:
                continue                  # 交了以后本人在底稿里又改过：网站上那是旧写法
            x["old_exists"], x["old"] = bool(row), row[1] if row else ""
            if x["who"] == "像是填错了":
                wrong.append(x)
            elif x["who"] in ("本人改的", "本人补的") and x["scope"] == "以后都这样":
                shrink = bool(row) and len(row[1]) >= 100 and len(x["new"]) < 0.6 * len(row[1])   # 长段换成短得多的：多半是字数上限
                if not row and how is None and not x["old"]:   # 底稿里确实没有：补上
                    auto.append({**x, "expect": None})
                elif how == "exact" and not backfill and stale is False and not shrink:
                    auto.append({**x, "expect": row[1]})
                else:
                    ask.append(x)
            elif x["scope"] != "只这家这样" and x["who"] in ("本人改的", "本人补的", "看不出"):
                ask.append(x)
        entry, skipped = apply(auto, source=f"读回：{company}", app_id=task_id, company=company) if auto else (None, [])
        ask += skipped                    # 想的时候本人刚改过、或者同一格出现两次：改成问
        for d in (entry or {}).get("items") or []:
            fresh.add((d["section"], d["key"]))
        for kind, xs in (("底稿", ask), ("核对", wrong)):
            for x in xs:
                line = (f"{company}网站上「{x['key']}」交的是「{x['new'][:80]}」，底稿里" + (f"是「{x['old'][:40]}」" if x["old_exists"] else "没有")
                        + ("：记进底稿？" if kind == "底稿" else "，像是填错了。已经交了：网站上还能改的话去改一下"))
                try:
                    apps.suggest(task_id, kind, line, {"section": x["section"], "key": x["key"], "value": x["new"],
                                                       "old": x["old"], "old_exists": x["old_exists"], "why": x["why"]})
                except apps.NotFound:
                    break
        with _lock:
            st = _state()
            st["sigs"][task_id] = sig      # 学完才记：模型没调通、面板中途重启的，下次启动还会再学
            _save_state(st)
        if entry:
            notify.send("底稿学到了", f"{company}：" + "、".join(d["key"] for d in entry["items"]), app_id=task_id, kind="learn")
        return {"applied": entry, "asked": len(ask), "wrong": len(wrong)}
    finally:
        with _lock:
            _running.discard(task_id)


def _safe(task_id, backfill=None, fresh=None):
    try:
        return from_readback(task_id, backfill=backfill, fresh=fresh)
    except Exception as e:   # 学不成不影响读回本身；没记签名，下次启动再学
        print(f"底稿学习（{task_id}）没做成：{type(e).__name__}: {e}")
        return None


def later(task_id):
    """读回存好以后在后台学（不拖慢读回）。"""
    threading.Thread(target=_safe, args=(task_id,), daemon=True).start()


def backfill():
    """面板启动后：交过、读回过、还没学过（或者上次没学成）的几家，按交的先后学一遍。返回学了几家。"""
    sigs = _state()["sigs"]
    todo = []
    for t in wstasks.list_tasks():
        text = ((t.get("readback") or {}).get("resume") or {}).get("text") or ""
        if text.strip() and sigs.get(t["id"]) != hashlib.sha1(text.encode()).hexdigest()[:16]:
            todo.append(t)
    todo.sort(key=_when)
    fresh, n = set(), 0
    for t in todo:
        if _safe(t["id"], fresh=fresh):
            n += 1
    return n


def pending():
    """各家还没点的「记进底稿？」和「像是填错了」（底稿页上一起列）。同一栏同一个值几家都有的合成一条。"""
    groups = {}
    for a in apps.list_apps():
        for s in a.get("suggestions") or []:
            if s.get("kind") not in ("底稿", "核对") or s.get("state") != "待定":
                continue
            p = s.get("payload") or {}
            if s["kind"] == "底稿" and already_there(p.get("section"), p.get("key"), p.get("value")):
                continue                     # 底稿里已经是这个了（别家先学进去了）：不用再问
            key = (s["kind"], _norm(p.get("key")), _flat(p.get("value"))) if s["kind"] == "底稿" else (s["kind"], a["id"], s["id"])
            g = groups.setdefault(key, {"kind": s["kind"], "text": s.get("text", ""), "at": s.get("at", ""), "refs": [],
                                        **{k: p.get(k, "") for k in ("key", "value", "old", "why")}})
            g["refs"].append({"app_id": a["id"], "sid": s["id"], "company": a.get("company", "")})
            g["at"] = max(g["at"], s.get("at", ""))
    out = []
    for g in groups.values():
        if g["kind"] == "底稿" and len(g["refs"]) > 1:   # 几家都这样：不写某一家的名字
            names = "、".join(dict.fromkeys(r["company"] for r in g["refs"]))
            g["text"] = (f"「{g['key']}」{names}交的都是「{g['value'][:80]}」，底稿里" + (f"是「{g['old'][:40]}」" if g["old"] else "没有")
                         + "：记进底稿？")
        out.append(g)
    return sorted(out, key=lambda x: x["at"], reverse=True)
