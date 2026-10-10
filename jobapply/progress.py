"""进展口述（WP18）：本人随手说一句——「收到 A 公司的笔试，周日截止」「B 公司的 AI 面做完了」「C 证券一面面完了」「D 公司一志愿挂了」——
面板用一次结构化 AI 调用拆成事件、对上申请，本人说的直接生效（阶段往前推、时间线记原话、截止写进下一步），每次都能撤销。
对不上（找不到、或者几家都像）的不瞎挂，列出候选让本人点。网申平台很多通知只发短信或站内信，所以这里是主入口，邮件识别是补充。"""

import json
import re
import threading
import uuid
from datetime import datetime

from . import apps, config, llm, records

FIX_EVENTS = ("改阶段", "改下一步", "不用建议", "采纳建议", "志愿方式",   # 本人更正：来信认错了、阶段 / 下一步要改、建议要不要
              "其实没投", "删岗位", "只投了", "删申请",                 # 看板记错了：没交出去、多记了岗位、整家根本没投
              "加岗位", "改岗位名", "改投递时间", "改公司名", "恢复岗位", "恢复申请")   # 漏记的补上、名字时间改对、删错的放回来
EVENTS = ("笔试邀请", "测评邀请", "AI面邀请", "面试邀请", "笔试完成", "测评完成", "AI面完成", "面试完成", "offer", "拒绝", "放弃",
          *FIX_EVENTS, "其他")
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
        "value": {"type": "string", "description": "改阶段：改成的阶段（已投递 / 笔试/测评 / 已电联 / 面试 / offer / 未通过 / 无回复 / 放弃 / 准备中）；"
                                                   "改下一步：下一步写什么（清空就空串）；志愿方式：串行或平行；改岗位名：新名字；"
                                                   "改投递时间：YYYY-MM-DD HH:MM；改公司名：新公司名；加岗位：地点（没说就空串）；别的事件空串"},
    }, "required": ["app_id", "company", "position", "event", "round", "happened_at", "due", "quote", "value"]}}},
    "required": ["events"],
}
SYSTEM = """你是求职投递面板的记录员。本人会随口说最近的进展（收到了谁的笔试 / 测评 / AI 面 / 面试邀请，做完了什么，谁拒了，拿到 offer……）。
把这句话拆成一件件事，每件事对上下面清单里的一个申请（填它的 app_id）。规则：
- 只用清单里有的 app_id。公司对不上、或者清单里有好几家都像，就把 app_id 写成空串，别猜。
- 「测评」「在线测试」「性格测试」「笔试」按本人说的词选：笔试类用笔试邀请 / 笔试完成，测评类用测评邀请 / 测评完成。
- 「AI 面」「AI 面试」「视频面（机器）」用 AI面邀请 / AI面完成；真人面试用面试邀请 / 面试完成，round 写几面。
- 「没做」「还没做」是收到了但没完成：用邀请那一类（笔试邀请 / 测评邀请 / AI面邀请）。
- 时间按今天推算成具体日期（「昨天晚上」→ 昨天的日期，时间不清楚就只写日期；「周日截止」→ 最近的那个周日）。
- quote 只放这件事的原话，不改写。
本人也会更正面板记的东西（来信认错了、阶段或下一步不对、建议要不要），用这几类事件：
- 某家清单里「待确认」的建议不对、来信认错了：用「不用建议」；本人顺带说了真实情况（比如「那封不是拒信，是笔试，13 号截止」），再按真实情况另记一件（笔试邀请，due 写截止）。本人说建议对：用「采纳建议」。说了是哪个岗位就写进 position。
- 本人要直接把某个岗位改成某个阶段（包括往回改，比如「B 证券改回已投递」）：用「改阶段」，value 写阶段。
- 本人改下一步或截止（「把 X 的下一步改成二面，10 月 15 号」「X 的下一步不用了」）：用「改下一步」，value 写下一步（不用了就空串），due 写截止。
- 本人说某家的志愿是串行（按顺序一个个看）还是平行（同时看）：用「志愿方式」，value 写串行或平行。
本人也会说看板记错了，用这几类事件：
- 「X 其实没交 / 没投出去 / 我点错了已提交」：用「其实没投」（说了哪个岗位写进 position）。
- 「X 的 Y 那条记错了 / 删掉 X 的 Y / X 多了一条 Y」：用「删岗位」，position 写 Y（照本人说的或清单里的岗位名）。
- 「X 我只投了 Y（这一个）」：用「只投了」，position 写 Y。
- 「X 我根本没投 / 把 X 的全删了」：用「删申请」。清单里同名的有好几家、本人说的是「全删了 / 全都没投」：每家各记一件「删申请」（这时可以填 app_id）。
- 「X 我还投了 Y / X 漏了 Y 这个岗位」：用「加岗位」，position 写 Y，value 写地点（没说就空串）。
- 「X 的 Y 应该叫 Z / 岗位名写错了，是 Z」：用「改岗位名」，position 写 Y（清单里现在的名字），value 写 Z。
- 「X 是 10 号晚上 11 点交的 / 投递时间记错了」：用「改投递时间」，value 写 YYYY-MM-DD HH:MM（按今天推算），说了哪个岗位写进 position。
- 「X 的公司名应该是 Z」：用「改公司名」，value 写 Z。
- 「X 的 Y 删错了 / 把 Y 放回来」：用「恢复岗位」，position 写 Y（清单里「收起的岗位」那里的名字）。
- 「X 删错了 / 把 X 恢复」：用「恢复申请」（清单里标了「已删」的那家，填它的 app_id）。"""
_lock = threading.Lock()


def _log_path():
    return config.DATA_DIR / "progress_log.jsonl"


def _now():
    return datetime.now()


def _context(now):
    apps_list = [a for a in apps.list_apps() if a.get("campaign") == config.CURRENT_CAMPAIGN]
    by_app = {}
    hidden = {}
    for r in records.load():
        if r.get("app_id"):
            (hidden if r.get("deleted_at") else by_app).setdefault(r["app_id"], []).append(r)
    lines = []
    for a in apps_list:
        pos = sorted(by_app.get(a["id"], []), key=lambda r: (r.get("choice_no") or 99, r.get("job_title", "")))
        jobs = "；".join((f"志愿{r['choice_no']} " if r.get("choice_no") else "") + (r.get("job_title") or "")
                        + f"（{records.STAGE_LABEL.get(r.get('status'), r.get('status'))}）" for r in pos)
        extra = ""
        nxt = a.get("next_step") or {}
        if nxt.get("text") and not nxt.get("done"):
            extra += f"｜下一步：{nxt['text']}" + (f"（截止 {nxt['due']}）" if nxt.get("due") and nxt["due"] not in nxt["text"] else "")
        pend = [x.get("text", "") for x in a.get("suggestions") or [] if x.get("state") == "待定" and x.get("kind") not in ("底稿", "核对")]
        if pend:
            extra += "｜待确认：" + "；".join(pend)[:200]
        if hidden.get(a["id"]):
            extra += "｜收起的岗位：" + "；".join((r.get("job_title") or "（没写岗位名）") for r in hidden[a["id"]])[:200]
        lines.append(f"{a['id']}｜{a.get('company')}｜{jobs or '还没有岗位'}{extra}")
    for a in apps.list_apps(include_deleted=True):
        if a.get("deleted_at") and a.get("campaign") == config.CURRENT_CAMPAIGN:
            jobs = "；".join((r.get("job_title") or "（没写岗位名）") for r in hidden.get(a["id"], []))[:120]
            lines.append(f"{a['id']}｜{a.get('company')}｜（已删，{a['deleted_at']}，7 天内能恢复）{jobs}")
    week = "一二三四五六日"[now.weekday()]
    return (f"今天是 {now:%Y-%m-%d %H:%M}（星期{week}）。\n申请清单（app_id｜公司｜岗位和阶段｜下一步｜待确认的建议）：\n"
            + "\n".join(lines))


def parse(text, now=None):
    """本人的一句话 → 事件列表（结构化 AI 调用；app_id 不在清单里的当成空串）。"""
    now = now or _now()
    data, _meta = llm._call(system=SYSTEM, content=_context(now) + "\n\n本人说：" + text.strip(), schema=SCHEMA,
                            effort=config.CLAUDE_EFFORT_LIGHT)
    return clean_events((data or {}).get("events"))


def clean_events(events):
    """AI 拆出来的、或者界面上点了候选之后送回来的事件：只留认识的事件类型和字段，app_id 不在清单里的当成空串。"""
    known = {a["id"] for a in apps.list_apps(include_deleted=True)}
    out = []
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("event") not in EVENTS:
            continue
        ev = {k: str(ev.get(k) or "").strip()[:500] for k in ("app_id", "company", "position", "event", "round", "happened_at", "due", "quote", "value")}
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


def _targets(app, ev, strict=False):
    """这件事落在哪几个岗位上：说了岗位就找那个；串行志愿落在当前志愿；否则这个申请里还没结束的岗位都算。
    strict（删、退回、改名这类）：说了岗位名却对不上，一个都不动——这家只有一个岗位也一样。"""
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
        if not hit:
            from . import wstasks
            hit = [r for r in pos if wstasks.same_position({"name": r.get("job_title") or "", "location": r.get("job_location") or ""},
                                                           {"name": ev["position"]})]
        if hit or len(pos) > 1 or strict:
            return hit    # 说了岗位名、这家又有好几个岗位却对不上：一个都不改
    live = [r for r in pos if r.get("status") not in records.TERMINAL]
    if app.get("volunteer_mode") == "串行" and live:
        return [min(live, key=lambda r: r.get("choice_no") or 99)]
    return live or pos


def _step_sig(nxt):
    """下一步的「样子」（撤销时核对：这一步后来被别的口述或来信改过，就不去盖它）。"""
    nxt = nxt or {}
    return [nxt.get("text", ""), nxt.get("due", ""), bool(nxt.get("done"))]


STAGE_SYNONYM = {"面试": "面试中", "测评": "笔试", "笔试/测评": "笔试", "挂了": "拒绝", "拒了": "拒绝", "未通过": "拒绝", "被拒": "拒绝",
                 "准备中": "草稿", "电联": "已电联", "投了": "已投递", "已提交": "已投递", "OFFER": "offer", "Offer": "offer"}


def _stage_key(word):
    w = re.sub(r"\s+", "", word or "")
    if w in records.STATUSES:
        return w
    return next((k for k, v in records.STAGE_LABEL.items() if v == w), "") or STAGE_SYNONYM.get(w, "")


def _apply_fix(app, ev, entry):
    """本人的更正（改阶段 / 改下一步 / 不用或采纳建议 / 志愿方式）。返回给本人看的一句提醒（没有就空串）。"""
    kind, val, aid = ev["event"], (ev.get("value") or "").strip(), app["id"]
    note = ""
    if kind == "改阶段":
        to = _stage_key(val)
        targets = _targets(app, ev) if to else []
        if not to:
            note = f"没听出要改成哪个阶段（{val or '没说'}），阶段没改"
        elif not targets:
            note = "没认出说的是哪个岗位 / 志愿，阶段没改"
        for r in targets:
            cur = r.get("status") or "已投递"
            if cur != to:                    # 本人说的：往回改也可以（写进时间线）
                apps.advance(r["id"], to, by="本人", manual=True, reason="本人口述更正")
                entry["changes"].append({"kind": "stage", "record_id": r["id"], "from": cur, "to": to, "job": r.get("job_title", "")})
    elif kind == "改下一步":
        prev = apps.get(aid).get("next_step") or {}
        apps.set_next_step(aid, val, due=ev.get("due") or "", source="本人口述")
        entry["changes"].append({"kind": "next_step", "app_id": aid, "prev": prev, "new": _step_sig(apps.get(aid).get("next_step"))})
    elif kind in ("不用建议", "采纳建议"):
        pend = [x for x in apps.get(aid).get("suggestions") or [] if x.get("state") == "待定" and x.get("kind") not in ("底稿", "核对")]
        if ev.get("position"):
            want = re.sub(r"\s+", "", ev["position"])
            pend = [x for x in pend if want in re.sub(r"\s+", "", x.get("text", ""))] or pend
        if not pend:
            note = "这家没有待确认的建议"
        for x in pend:
            pay = x.get("payload") or {}
            before = (records.get(pay["record_id"]) or {}).get("status") if pay.get("record_id") else None
            hid = [rid for rid in pay.get("hide") or [] if not (records.get(rid) or {}).get("deleted_at")] \
                if x.get("kind") == "岗位数" and kind == "采纳建议" else []
            (apps.accept if kind == "采纳建议" else apps.dismiss)(aid, x["id"], by="本人")
            entry["changes"].append({"kind": "suggestion", "app_id": aid, "sid": x["id"], "state": "采纳" if kind == "采纳建议" else "不用",
                                     "record_id": pay.get("record_id"), "from": before, "to": pay.get("to")})
            for rid in hid:          # 采纳「岗位数」收起了多出来的岗位：撤销时放回
                entry["changes"].append({"kind": "pos_hidden", "record_id": rid, "app_id": aid})
    elif kind == "志愿方式":
        mode = "串行" if re.search(r"串|顺序|依次", val) else "平行" if re.search(r"平行|同时", val) else ""
        if not mode:
            note = "没听出是串行还是平行，没改"
        else:
            prev = apps.get(aid).get("volunteer_mode", "")
            apps.set_volunteer_mode(aid, mode, by="本人")
            entry["changes"].append({"kind": "mode", "app_id": aid, "prev": prev, "new": mode})
    elif kind == "其实没投":
        note = _unsubmit(app, ev, entry)
    elif kind in ("删岗位", "只投了"):
        pos = [r for r in apps.positions(aid) if not r.get("deleted_at")]
        hit = _targets(app, ev, strict=True) if ev.get("position") else []
        if not hit:
            note = "没认出说的是哪个岗位，没删（可以在这家的详情里删）"
        else:
            drop = hit if kind == "删岗位" else [r for r in pos if r not in hit]
            for r in drop:
                apps.hide_position(r["id"], by="本人", reason="本人说记错了")
                entry["changes"].append({"kind": "pos_hidden", "record_id": r["id"], "app_id": aid})
    elif kind == "删申请":
        apps.soft_delete(aid, by="本人")
        entry["changes"].append({"kind": "app_deleted", "app_id": aid})
        return note
    elif kind == "加岗位":
        note = _add_position(app, ev, entry)
    elif kind == "改岗位名":
        hit = _targets(app, ev, strict=True) if ev.get("position") else []
        if len(hit) != 1:
            note = "没认出要改哪个岗位的名字，没改"
        elif not val:
            note = "没听出新名字，没改"
        else:
            old = _rename(aid, hit[0], val)
            entry["changes"].append({"kind": "pos_renamed", "record_id": hit[0]["id"], "app_id": aid, "from": old["name"],
                                     "src": old["src"], "to": val[:80]})
    elif kind == "改投递时间":
        note = _set_sent_at(app, ev, entry)
    elif kind == "改公司名":
        if not val:
            note = "没听出新的公司名，没改"
        else:
            prev = app.get("company", "")
            apps.rename(aid, val, by="本人")
            _task_company(aid, val)
            entry["changes"].append({"kind": "company", "app_id": aid, "prev": prev, "new": val})
    elif kind == "恢复岗位":
        hidden = [r for r in apps.positions(aid) if r.get("deleted_at") and not r.get("deleted_with_app")]
        hit = _name_hits(hidden, ev["position"]) if ev.get("position") else hidden if len(hidden) == 1 else []
        if not hit:
            note = "没认出要放回哪个岗位，没放" if hidden else "这家没有收起的岗位"
        for r in hit:
            apps.unhide_position(r["id"], by="本人")
            entry["changes"].append({"kind": "pos_restored", "record_id": r["id"], "app_id": aid})
    elif kind == "恢复申请":
        if not app.get("deleted_at"):
            note = "这家没删，不用恢复"
        else:
            apps.restore(aid, by="本人")
            entry["changes"].append({"kind": "app_restored", "app_id": aid})
    apps.add_timeline(aid, "更正", f"{kind}{'：' + val if val else ''}：「{ev.get('quote', '')}」", "本人")
    entry["changes"].append({"kind": "timeline", "app_id": aid})
    return note


def _unsubmit(app, ev, entry):
    """其实没交：网申的把网申待办退回「已填待提交」（网申那边的岗位跟着退回草稿）；说了哪个岗位就只退那一个。
    Gmail 已经发出去的信不动（要改就说「改成准备中」）。都退回了「首次投出」也清掉；撤销时都放回。"""
    from . import wstasks
    aid = app["id"]
    live = [r for r in apps.positions(aid) if not r.get("deleted_at")]
    targets = _targets(app, ev, strict=True) if ev.get("position") else live
    mail = [r for r in targets if not wstasks.is_web_record(r)]
    targets = [r for r in targets if wstasks.is_web_record(r)]
    before = {r["id"]: (r.get("status") or "已投递", r.get("sent_at", ""), r.get("sent_ts", 0)) for r in targets}
    first = apps.get(aid).get("first_submitted_at", "")
    try:
        t = wstasks.get(aid)
    except wstasks.NotFound:
        t = None
    if t and t.get("status") == "已提交" and not ev.get("position") and targets:
        wstasks.set_status(aid, "已填待提交")
        entry["changes"].append({"kind": "task_status", "task_id": aid, "from": "已提交", "to": "已填待提交",
                                 "submitted_at": t.get("submitted_at", "")})
    for r in targets:
        if (records.get(r["id"]) or {}).get("status") == "已投递":
            apps.advance(r["id"], "草稿", by="本人", manual=True, reason="本人说其实没交")
            records.update(r["id"], {"sent_at": "", "sent_ts": 0, "sent_at_locked": False}, by="本人")
        if before[r["id"]][0] == "已投递" and (records.get(r["id"]) or {}).get("status") == "草稿":
            entry["changes"].append({"kind": "unsubmit", "record_id": r["id"], "sent_at": before[r["id"]][1], "sent_ts": before[r["id"]][2]})
    if targets:
        apps.clear_first_submitted(aid)
    if first and apps.get(aid).get("first_submitted_at") != first:
        entry["changes"].append({"kind": "first_submitted", "app_id": aid, "prev": first, "new": ""})
    if not any(c["kind"] in ("unsubmit", "task_status") for c in entry["changes"]):
        return "Gmail 里那封已经发出去了，阶段没改（要改就说「改成准备中」）" if mail else "这家没有记成已投递的岗位，没改"
    return "Gmail 发出去的那封信没动" if mail else ""


def _name_hits(pos, want):
    """按岗位名找（包含、或者 same_position 认得是同一个）。"""
    from . import wstasks
    w = re.sub(r"\s+", "", want or "")
    hit = [r for r in pos if w and (w in re.sub(r"\s+", "", r.get("job_title", "")) or re.sub(r"\s+", "", r.get("job_title", "")) in w)]
    return hit or [r for r in pos if wstasks.same_position({"name": r.get("job_title") or "", "location": r.get("job_location") or ""},
                                                             {"name": want})]


def _task_company(aid, name):
    from . import wstasks
    try:
        wstasks.update(aid, company=name)
    except wstasks.NotFound:
        pass


def _rename(aid, rec, new, src="本人"):
    """改岗位名：网申的岗位单、JD 跟着改（本人定的名字读回不再改）。返回 {name: 旧名字, src: 原来是谁定的}。"""
    from . import wstasks
    try:
        wstasks.get(aid)
        return wstasks.rename_position(aid, rec["id"], new, by="本人", src=src)
    except wstasks.NotFound:
        records.update(rec["id"], {"job_title": new[:80]}, by="本人")
        return {"name": rec.get("job_title") or "", "src": ""}


def _add_position(app, ev, entry):
    """本人说「X 还投了 Y」：网申的加进岗位单（看板跟着建一条）；邮件的照这家已有的岗位建一条。"""
    from . import wstasks
    aid, name = app["id"], (ev.get("position") or "").strip()[:80]
    if not name:
        return "没听出要加哪个岗位，没加"
    live = [r for r in apps.positions(aid) if not r.get("deleted_at")]
    if any(wstasks.same_position({"name": r.get("job_title") or "", "location": r.get("job_location") or ""}, {"name": name}) for r in live):
        return f"这家已经有「{name}」了，没重复加"
    try:
        wstasks.get(aid)
        rid = wstasks.add_position(aid, name, ev.get("value") or "", by="本人")
        if not rid:
            entry["changes"].append({"kind": "task_pos_added", "task_id": aid, "name": name})
            return "记在网申待办的岗位单上了；看板上的岗位等这家填好时一起建"
    except wstasks.NotFound:
        base = live[0] if live else None
        if not base:
            return "这家还没有岗位记录，没法照着加"
        rid = records.add(records.new_record(
            company_name=base.get("company_name") or app.get("company", ""), company_type=base.get("company_type", ""),
            job_title=name, job_location=(ev.get("value") or "")[:40], status="草稿" if base.get("status") == "草稿" else "已投递",
            send_mode=base.get("send_mode", ""), apply_channel=base.get("apply_channel", ""), campaign=base.get("campaign", ""),
            position_type=base.get("position_type", ""), source_type=base.get("source_type", ""),
            sent_at=base.get("sent_at", ""), sent_ts=base.get("sent_ts", 0), app_id=aid))
    entry["changes"].append({"kind": "pos_added", "record_id": rid, "app_id": aid})
    return ""


def _set_sent_at(app, ev, entry):
    """本人改投递时间：说了哪个岗位就改那个，没说就改这家投出去的岗位；「首次投出」跟着改。都能撤销。"""
    aid, val = app["id"], (ev.get("value") or "").strip()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?", val)
    if not m:
        return "没听出投递时间（要写成 2026-10-10 23:28），没改"
    live = [r for r in apps.positions(aid) if not r.get("deleted_at")]
    targets = _targets(app, ev, strict=True) if ev.get("position") else [r for r in live if (r.get("status") or "已投递") != "草稿"]
    if not targets:
        return "没认出要改哪个岗位（还没投出去的岗位没有投递时间），没改"
    for r in targets:
        stamp = f"{m.group(1)} {(m.group(2) or (r.get('sent_at') or '')[11:16] or '00:00').zfill(5)}"
        try:
            ts = datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            return "没听出投递时间（要写成 2026-10-10 23:28），没改"
        entry["changes"].append({"kind": "sent_at", "record_id": r["id"], "new": stamp,
                                 "prev": [r.get("sent_at", ""), r.get("sent_ts", 0), bool(r.get("sent_at_locked"))]})
        records.update(r["id"], {"sent_at": stamp, "sent_ts": ts, "sent_at_locked": True}, by="本人")
    sent = sorted(x.get("sent_at") for x in (records.get(r["id"]) for r in live) if x and x.get("sent_at") and (x.get("status") or "已投递") != "草稿")
    if sent and apps.get(aid).get("first_submitted_at") != sent[0]:
        prev = apps.set_first_submitted(aid, sent[0], by="本人")
        entry["changes"].append({"kind": "first_submitted", "app_id": aid, "prev": prev, "new": sent[0]})
    return ""


def _apply_one(app, ev, entry):
    """返回给本人看的一句提醒（没有就空串）。"""
    if ev["event"] in FIX_EVENTS:
        return _apply_fix(app, ev, entry)
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
            if app.get("deleted_at") and ev["event"] != "恢复申请":
                note = "这家已经删了（7 天内可以说「把它恢复」），没改"
            else:
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
        elif c["kind"] == "suggestion":   # 建议放回「待定」；采纳时改了阶段、阶段还没被别的改过，也改回去
            if c.get("state") == "采纳" and c.get("record_id") and c.get("to") and c.get("from"):
                r = records.get(c["record_id"])
                if r and r.get("status") == c["to"]:
                    apps.advance(c["record_id"], c["from"], by="本人", manual=True, reason="撤销口述")
            apps.reopen_suggestion(c["app_id"], c["sid"])
            n += 1
        elif c["kind"] == "unsubmit":
            r = records.get(c["record_id"])
            if r and r.get("status") == "草稿":
                apps.advance(c["record_id"], "已投递", by="本人", manual=True, reason="撤销口述")
                records.update(c["record_id"], {"sent_at": c.get("sent_at", ""), "sent_ts": c.get("sent_ts", 0), "sent_at_locked": True}, by="本人")
                n += 1
        elif c["kind"] == "task_status":
            from . import wstasks
            try:
                if wstasks.get(c["task_id"]).get("status") == c["to"]:   # 只把待办的状态放回去（岗位上面那几条已经单独放回）
                    wstasks.update(c["task_id"], status=c["from"], **({"submitted_at": c["submitted_at"]} if c.get("submitted_at") else {}))
                    n += 1
            except wstasks.NotFound:
                pass
        elif c["kind"] == "first_submitted":
            try:
                if apps.get(c["app_id"]).get("first_submitted_at", "") == c.get("new", ""):
                    apps.set_first_submitted(c["app_id"], c.get("prev", ""), by="本人")
                    n += 1
            except apps.NotFound:
                pass
        elif c["kind"] == "sent_at":
            r = records.get(c["record_id"])
            if r and r.get("sent_at") == c.get("new"):
                at, ts, locked = c["prev"]
                records.update(c["record_id"], {"sent_at": at, "sent_ts": ts, "sent_at_locked": locked}, by="本人")
                n += 1
        elif c["kind"] in ("pos_added", "pos_restored"):
            try:
                apps.hide_position(c["record_id"], by="本人", reason="撤销口述")
                n += 1
            except apps.NotFound:
                pass
        elif c["kind"] == "task_pos_added":
            from . import wstasks
            try:
                wstasks.drop_position(c["task_id"], c["name"])
                n += 1
            except wstasks.NotFound:
                pass
        elif c["kind"] == "pos_renamed":
            r = records.get(c["record_id"])
            if r and r.get("job_title") == c.get("to") and c.get("from"):
                _rename(c["app_id"], r, c["from"], src=c.get("src") or "")
                n += 1
        elif c["kind"] == "company":
            try:
                if apps.get(c["app_id"]).get("company") == c.get("new"):
                    apps.rename(c["app_id"], c.get("prev") or c["new"], by="本人")
                    _task_company(c["app_id"], c.get("prev") or c["new"])
                    n += 1
            except apps.NotFound:
                pass
        elif c["kind"] == "app_restored":
            try:
                apps.soft_delete(c["app_id"], by="本人")
                n += 1
            except apps.NotFound:
                pass
        elif c["kind"] == "pos_hidden":
            try:
                apps.unhide_position(c["record_id"])
                n += 1
            except apps.NotFound:
                pass
        elif c["kind"] == "app_deleted":
            try:
                apps.restore(c["app_id"])
                n += 1
            except apps.NotFound:
                pass
        elif c["kind"] == "mode":
            try:
                if apps.get(c["app_id"]).get("volunteer_mode") == c.get("new"):
                    apps.set_volunteer_mode(c["app_id"], c.get("prev", ""), by="本人")
                    n += 1
            except apps.NotFound:
                pass
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
