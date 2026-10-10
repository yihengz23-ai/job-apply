"""进展口述（WP18）：本人随手说一句——「收到 A 公司的笔试，周日截止」「B 公司的 AI 面做完了」「C 证券一面面完了」「D 公司一志愿挂了」——
面板用一次结构化 AI 调用拆成事件、对上申请，本人说的直接生效（阶段往前推、时间线记原话、截止写进下一步），每次都能撤销。
对不上（找不到、或者几家都像）的不瞎挂，列出候选让本人点。网申平台很多通知只发短信或站内信，所以这里是主入口，邮件识别是补充。"""

import json
import re
import threading
import uuid
from datetime import datetime

from . import apps, config, llm, records

EVENTS = ("笔试邀请", "测评邀请", "AI面邀请", "面试邀请", "笔试完成", "测评完成", "AI面完成", "面试完成", "offer", "拒绝", "放弃", "其他")
STAGE_OF = {"笔试邀请": "笔试", "测评邀请": "笔试", "笔试完成": "笔试", "测评完成": "笔试",
            "AI面邀请": "面试中", "AI面完成": "面试中", "面试邀请": "面试中", "面试完成": "面试中",
            "offer": "offer", "拒绝": "拒绝"}
TODO = {"笔试邀请": "做笔试", "测评邀请": "做测评", "AI面邀请": "做 AI 面", "面试邀请": "参加面试"}
SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {"type": "object", "properties": {
        "app_id": {"type": "string", "description": "对上的申请 id（只能用清单里有的；对不上写空串）"},
        "company": {"type": "string", "description": "本人说的公司名，照抄"},
        "position": {"type": "string", "description": "本人说到的岗位或志愿（没说就空串）"},
        "event": {"type": "string", "enum": list(EVENTS)},
        "round": {"type": "string", "description": "第几轮 / 哪一种（如「一面」「AI 面」「群面」），没说就空串"},
        "happened_at": {"type": "string", "description": "发生时间 YYYY-MM-DD HH:MM（按今天推算；不知道就空串）"},
        "due": {"type": "string", "description": "截止 / 预约时间 YYYY-MM-DD 或 YYYY-MM-DD HH:MM（没说就空串）"},
        "quote": {"type": "string", "description": "本人这一件事的原话"},
    }, "required": ["app_id", "company", "position", "event", "round", "happened_at", "due", "quote"]}}},
    "required": ["events"],
}
SYSTEM = """你是求职投递面板的记录员。本人会随口说最近的进展（收到了谁的笔试 / 测评 / AI 面 / 面试邀请，做完了什么，谁拒了，拿到 offer……）。
把这句话拆成一件件事，每件事对上下面清单里的一个申请（填它的 app_id）。规则：
- 只用清单里有的 app_id。公司对不上、或者清单里有好几家都像，就把 app_id 写成空串，别猜。
- 「测评」「在线测试」「性格测试」「笔试」按本人说的词选：笔试类用笔试邀请 / 笔试完成，测评类用测评邀请 / 测评完成。
- 「AI 面」「AI 面试」「视频面（机器）」用 AI面邀请 / AI面完成；真人面试用面试邀请 / 面试完成，round 写几面。
- 「没做」「还没做」是收到了但没完成：用邀请那一类（笔试邀请 / 测评邀请 / AI面邀请）。
- 时间按今天推算成具体日期（「昨天晚上」→ 昨天的日期，时间不清楚就只写日期；「周日截止」→ 最近的那个周日）。
- quote 只放这件事的原话，不改写。"""
_lock = threading.Lock()


def _log_path():
    return config.DATA_DIR / "progress_log.jsonl"


def _now():
    return datetime.now()


def _context(now):
    apps_list = [a for a in apps.list_apps() if a.get("campaign") == config.CURRENT_CAMPAIGN]
    by_app = {}
    for r in records.load():
        if r.get("app_id"):
            by_app.setdefault(r["app_id"], []).append(r)
    lines = []
    for a in apps_list:
        pos = sorted(by_app.get(a["id"], []), key=lambda r: (r.get("choice_no") or 99, r.get("job_title", "")))
        jobs = "；".join((f"志愿{r['choice_no']} " if r.get("choice_no") else "") + (r.get("job_title") or "")
                        + f"（{records.STAGE_LABEL.get(r.get('status'), r.get('status'))}）" for r in pos)
        lines.append(f"{a['id']}｜{a.get('company')}｜{jobs or '还没有岗位'}")
    week = "一二三四五六日"[now.weekday()]
    return f"今天是 {now:%Y-%m-%d %H:%M}（星期{week}）。\n申请清单（app_id｜公司｜岗位和阶段）：\n" + "\n".join(lines)


def parse(text, now=None):
    """本人的一句话 → 事件列表（结构化 AI 调用；app_id 不在清单里的当成空串）。"""
    now = now or _now()
    data, _meta = llm._call(system=SYSTEM, content=_context(now) + "\n\n本人说：" + text.strip(), schema=SCHEMA,
                            effort=config.CLAUDE_EFFORT_LIGHT)
    return clean_events((data or {}).get("events"))


def clean_events(events):
    """AI 拆出来的、或者界面上点了候选之后送回来的事件：只留认识的事件类型和字段，app_id 不在清单里的当成空串。"""
    known = {a["id"] for a in apps.list_apps()}
    out = []
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("event") not in EVENTS:
            continue
        ev = {k: str(ev.get(k) or "").strip()[:500] for k in ("app_id", "company", "position", "event", "round", "happened_at", "due", "quote")}
        if ev["app_id"] not in known:
            ev["app_id"] = ""
        out.append(ev)
    return out


def candidates(company):
    """公司名对上的申请（本轮、没删的）。"""
    key = apps.canonical_company(company)
    if not key:
        return []
    out = []
    for a in apps.list_apps():
        if a.get("campaign") != config.CURRENT_CAMPAIGN:
            continue
        other = apps.canonical_company(a.get("company"))
        if other and (key in other or other in key):
            out.append(a)
    return out


def _targets(app, ev):
    """这件事落在哪几个岗位上：说了岗位就找那个；串行志愿落在当前志愿；否则这个申请里还没结束的岗位都算。"""
    pos = [r for r in apps.positions(app["id"]) if not r.get("deleted_at")]
    if ev.get("position"):
        want = re.sub(r"\s+", "", ev["position"])
        num = re.search(r"([一二三四五六七八九\d])\s*志愿|志愿\s*([一二三四五六七八九\d])", ev["position"])
        if num:
            n = num.group(1) or num.group(2)
            n = int(n) if n.isdigit() else "一二三四五六七八九".index(n) + 1
            hit = [r for r in pos if r.get("choice_no") == n]
            if not hit:   # 迁移来的岗位没记志愿号：用和看板一样的办法，从读回原文里认第几志愿
                texts = "\n".join(dict.fromkeys(r.get("ws_submitted") or "" for r in pos if "志愿" in (r.get("ws_submitted") or "")))
                guess = apps.choice_order(texts, pos)
                hit = [r for r in pos if guess.get(r["id"]) == n]
            return hit    # 认不出是哪个志愿：一个都不改（不落到这家所有岗位上）
        hit = [r for r in pos if want and (want in re.sub(r"\s+", "", r.get("job_title", "")) or re.sub(r"\s+", "", r.get("job_title", "")) in want)]
        if hit or len(pos) > 1:
            return hit    # 说了岗位名、这家又有好几个岗位却对不上：一个都不改
    live = [r for r in pos if r.get("status") not in records.TERMINAL]
    if app.get("volunteer_mode") == "串行" and live:
        return [min(live, key=lambda r: r.get("choice_no") or 99)]
    return live or pos


def _step_sig(nxt):
    """下一步的「样子」（撤销时核对：这一步后来被别的口述或来信改过，就不去盖它）。"""
    nxt = nxt or {}
    return [nxt.get("text", ""), nxt.get("due", ""), bool(nxt.get("done"))]


def _apply_one(app, ev, entry):
    """返回给本人看的一句提醒（没有就空串）。"""
    stage = STAGE_OF.get(ev["event"])
    label = ev["event"] + (f"（{ev['round']}）" if ev.get("round") else "")
    when = ev.get("happened_at") or ""
    targets = _targets(app, ev)
    note = "没认出说的是哪个岗位 / 志愿，阶段没改（时间线记下了），可以在这家的详情里改" if stage and not targets else ""
    for r in targets:
        cur = r.get("status") or "已投递"
        forward = stage and cur != stage and (stage in records.TERMINAL or records.STAGE_ORDER.get(stage, 0) > records.STAGE_ORDER.get(cur, 0)) \
            and cur not in records.TERMINAL
        if forward:
            apps.advance(r["id"], stage, by="本人", manual=True, reason="本人口述")
            entry["changes"].append({"kind": "stage", "record_id": r["id"], "from": cur, "to": stage, "job": r.get("job_title", "")})
    if ev["event"] == "放弃":
        before = apps.get(app["id"])
        impact = apps.give_up(app["id"], ev.get("quote") or "本人说不投了", by="本人")
        entry["changes"].append({"kind": "give_up", "app_id": app["id"], "records": impact.get("not_submitted", []),
                                 "prev": {"web_phase": before.get("web_phase", ""), "need": before.get("need"), "color": before.get("color")}})
    apps.add_timeline(app["id"], "进展", f"{label}" + (f"（{when}）" if when else "") + f"：「{ev.get('quote', '')}」", "本人")
    entry["changes"].append({"kind": "timeline", "app_id": app["id"]})
    if ev["event"].endswith("邀请") or ev.get("due") and not ev["event"].endswith("完成"):
        # 收到了（还没做）：下一步写成本人要做的事，有截止写截止；没说截止也算轮到本人（看板「今天」里列着）
        prev = apps.get(app["id"]).get("next_step") or {}
        todo = TODO.get(ev["event"], label) + (f"（{ev['round']}）" if ev.get("round") and ev["event"] == "面试邀请" else "")
        due = ev.get("due") or ""
        text = f"{todo}（{due}{'' if ev['event'] == '面试邀请' else ' 截止'}）" if due else f"{todo}（截止没说）"
        apps.set_next_step(app["id"], text, due=due, source="本人口述")
        entry["changes"].append({"kind": "next_step", "app_id": app["id"], "prev": prev, "new": _step_sig(apps.get(app["id"]).get("next_step"))})
    elif ev["event"].endswith("完成"):
        prev = apps.finish_next_step(app["id"], by="本人")
        if prev:
            entry["changes"].append({"kind": "next_step", "app_id": app["id"], "prev": prev, "new": _step_sig(apps.get(app["id"]).get("next_step"))})
    return note


def _cand(a):
    """候选按钮上的字：同一家两次申请时分得清（「某社区 · 投资分析（笔试/测评）」）。"""
    pos = [r for r in apps.positions(a["id"]) if not r.get("deleted_at")]
    jobs = "、".join((r.get("job_title") or "")[:14] for r in pos[:2]) + ("…" if len(pos) > 2 else "")
    stage = apps.stage_of(pos)
    label = a.get("company", "") + (f" · {jobs}" if jobs else "") + f"（{records.STAGE_LABEL.get(stage, stage)}）"
    return {"app_id": a["id"], "company": a.get("company", ""), "label": label}


def apply(text, *, now=None, events=None):
    """处理本人说的一句话。返回 {id, applied: [事件+申请], ask: [对不上的事件+候选]}。events 给了就不调 AI（前端点了候选之后用）。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("说点什么，比如「收到 A 公司的笔试，周日截止」")
    now = now or _now()
    evs = clean_events(events) if events is not None else parse(text, now)
    entry = {"id": uuid.uuid4().hex[:10], "at": now.strftime("%Y-%m-%d %H:%M:%S"), "text": text, "changes": [], "applied": [], "ask": []}
    with _lock:
        for ev in evs:
            app = None
            if ev.get("app_id"):
                try:
                    app = apps.get(ev["app_id"])
                except apps.NotFound:
                    app = None
            if app is None:
                cands = candidates(ev.get("company", ""))
                if len(cands) == 1:
                    app = cands[0]
                else:
                    entry["ask"].append({**ev, "candidates": [_cand(a) for a in cands[:8]]})
                    continue
            note = _apply_one(app, ev, entry)
            entry["applied"].append({**ev, "app_id": app["id"], "company_name": app.get("company", ""), "note": note})
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recent(n=20):
    try:
        lines = _log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen, out = set(), []
    for line in reversed(lines):          # 同一次口述撤销后会补一行：只留最新的那一行
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("id") in seen:
            continue
        seen.add(e.get("id"))
        out.append(e)
        if len(out) >= n:
            break
    return out


def undo(entry_id):
    """撤销某一次口述带来的改动：阶段改回去、下一步恢复原样，时间线记一笔「撤销」。返回撤销了几处。"""
    entry = next((e for e in recent(500) if e.get("id") == entry_id), None)
    if not entry:
        raise KeyError(entry_id)
    if entry.get("undone"):
        return 0
    n = 0
    for c in reversed(entry.get("changes") or []):
        if c["kind"] == "stage":
            r = records.get(c["record_id"])
            if r and r.get("status") == c["to"]:
                apps.advance(c["record_id"], c["from"], by="本人", manual=True, reason="撤销口述")
                n += 1
        elif c["kind"] == "next_step":
            def _restore(data, c=c):
                a = next((x for x in data["applications"] if x["id"] == c["app_id"]), None)
                if a is None or ("new" in c and _step_sig(a.get("next_step")) != c["new"]):
                    return False          # 后来又被别的口述 / 来信改过：留着新的，不盖
                a["next_step"] = c["prev"] or {"text": "", "due": "", "source": "", "inferred": False, "confirmed": False}
                return True
            if records.mutate_all(_restore):
                n += 1
        elif c["kind"] == "give_up":
            for rid in c.get("records") or []:
                r = records.get(rid)
                if r and r.get("status") == "放弃":
                    apps.advance(rid, "草稿", by="本人", manual=True, reason="撤销口述")
            prev = c.get("prev") or {"web_phase": "待开始"}

            def _back(data, c=c, prev=prev):   # 放回不投之前的样子（网申小状态、要你做、颜色）；邮件申请本来就没有网申小状态
                a = next((x for x in data["applications"] if x["id"] == c["app_id"]), None)
                if a is not None and a.get("web_phase") in ("已放弃", ""):
                    a["web_phase"], a["need"] = prev.get("web_phase", ""), prev.get("need")
                    if a.get("color") is None:
                        a["color"] = prev.get("color")
            records.mutate_all(_back)
            n += 1
    for c in entry.get("changes") or []:
        if c["kind"] == "timeline":
            apps.add_timeline(c["app_id"], "撤销", f"撤销了口述：「{entry.get('text', '')}」", "本人")
            n += 1                     # 只记了时间线的口述：撤销也算一处（界面上不会误报「已经撤销过了」）
            break
    with _lock:   # 标记这一次已经撤销（日志只追加：补一行）
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({**entry, "undone": True, "undone_at": _now().strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")
    return n
