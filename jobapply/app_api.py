"""第 2 批界面要的数据（路由层只管转发）：
- overview：看板一行 = 一次申请（同一家的几个岗位 / 志愿合成一张卡片，串行志愿算出「在看 / 排队 / 已流转」）；
- today：「今天」——轮到本人的事、停下的、快到期的；
- detail：单家详情——岗位与 JD、读回的原文、时间线、待确认的建议、邮件稿、对话；
- 建议采纳 / 不用、志愿方式。口述进展走 progress，「我弄完了」走原来的「让助手接着做」（申请 id 就是网申待办 id）。"""

from datetime import datetime, timedelta

from . import apps, config, jobqueue, progress, records, wstasks

TURN_RANK = {"轮到你": 0, "停了": 1, "面板在做": 2, "等对方": 3, "已结束": 4}
DONE_MAIL = ("已发送", "已记录", "转网申")


def _agent_states():
    try:
        from . import agent
        return agent.state_by_app()
    except Exception:
        return {}


def _context():
    data = records.load_all()
    by_app, rec_app = {}, {}
    for r in data["records"]:
        rec_app[r.get("id")] = r.get("app_id")
        if r.get("app_id") and not r.get("deleted_at"):
            by_app.setdefault(r["app_id"], []).append(r)
    mail = {}
    for it in sorted(jobqueue.list_items(), key=lambda x: x.get("updated_at") or x.get("created_at") or ""):
        aid = it.get("app_id") or rec_app.get(it.get("record_id"))
        if aid and it.get("status") not in DONE_MAIL:
            mail[aid] = it                     # 同一家多封：取最新的那封
    return data["applications"], by_app, mail, _agent_states()


def positions_view(app, pos):
    """岗位按志愿顺序排；串行志愿时：第一个还没结束的是「在看」，后面的「排队」，前面没过的「未通过 · 已流转」。"""
    mode = app.get("volunteer_mode") or ""
    texts = list(dict.fromkeys(r.get("ws_submitted") or "" for r in pos if "志愿" in (r.get("ws_submitted") or "")))
    guess = apps.choice_order("\n".join(texts), pos) if len(pos) > 1 else {}     # 没记志愿序号的：从读回原文里认
    no = {r.get("id"): r.get("choice_no") or guess.get(r.get("id")) or 0 for r in pos}
    ps = sorted(pos, key=lambda r: (no[r.get("id")] or 99, r.get("created_at") or "", r.get("job_title") or ""))
    out, current = [], False
    for r in ps:
        st = r.get("status") or "已投递"
        serial = ""
        if mode == "串行":
            if st in records.TERMINAL:
                serial = "未通过 · 已流转" if st == "拒绝" else records.STAGE_LABEL.get(st, st)
            elif not current:
                serial, current = "在看", True
            else:
                serial = "排队：前面的志愿有结果后才看"
        out.append({"id": r.get("id"), "job_title": r.get("job_title", ""), "choice_no": no[r.get("id")],
                    "choice_guessed": bool(no[r.get("id")] and not r.get("choice_no")),
                    "org": r.get("org", ""), "location": r.get("job_location", ""), "status": st,
                    "stage_label": records.STAGE_LABEL.get(st, st), "site_status": r.get("site_status", ""),
                    "serial_state": serial, "has_jd": bool((r.get("jd_text") or "").strip()), "sent_at": r.get("sent_at", ""),
                    "reply_status": r.get("reply_status", ""), "reply_kind": r.get("reply_kind", "")})
    return out


def card(app, pos, mail, ag, apps_list, now=None):
    v = apps.view(app, {"positions": pos, "mail": mail, "agent": ag or {}, "now": now})
    acct = app.get("account") or {}
    pv = positions_view(app, pos)
    cur = next((p for p in pv if p["serial_state"] == "在看"), None)
    if cur:                       # 串行：阶段看当前志愿（平行的取走得最远的那个，下面一行列各阶段几个）
        v["stage"], v["stage_label"] = cur["status"], cur["stage_label"]
    counts = {}
    for p in pv:
        counts[p["stage_label"]] = counts.get(p["stage_label"], 0) + 1
    last = (app.get("timeline") or [{}])[-1]
    first = app.get("first_submitted_at", "")
    if not first:                 # 迁移前的网申申请没记首次投出：取岗位里最早的投递时间（标「按记录」）
        sent = sorted(r.get("sent_at") for r in pos if r.get("sent_at") and (r.get("status") or "已投递") != "草稿")
        first = sent[0] if sent else ""
    return {"id": app["id"], "company": app.get("company", ""), "channel": app.get("channel", ""), "campaign": app.get("campaign", ""),
            "color": app.get("color"), "color_label": apps.color_label(app, apps_list), "web_phase": app.get("web_phase", ""),
            "color_hex": wstasks.COLORS[app["color"] % len(wstasks.COLORS)][1] if isinstance(app.get("color"), int) else "",
            "first_submitted_at": first, "first_submitted_guess": bool(first and not app.get("first_submitted_at")), "account": acct.get("login") or acct.get("form_phone") or acct.get("form_email") or "",
            "volunteer_mode": app.get("volunteer_mode", ""), "volunteer_note": app.get("volunteer_note", ""),
            "site_progress": (app.get("site_progress") or {}).get("text", ""), "chat_id": app.get("chat_id", ""),
            "need": app.get("need"), "mail_status": (mail or {}).get("status", ""), "positions": pv,
            "stage_counts": counts if len(pv) > 1 and len(counts) > 1 else {}, "last_text": last.get("text", ""),
            "search": " ".join(filter(None, [r.get("to_email", "") for r in pos] + [r.get("cc_email", "") for r in pos]
                                    + [(r.get("notes") or "")[:300] for r in pos] + [app.get("notes", "")[:300]])),
            "reply": app.get("reply") or {}, "entry_url": app.get("entry_url", ""),
            "suggestions": [s for s in app.get("suggestions") or [] if s.get("state") == "待定"], **v}


def overview(campaign=None, now=None):
    """看板：一张卡片一次申请。排序：轮到你 → 停了 → 面板在做 → 等对方 → 已结束；同一档里最近有动静的在前。"""
    apps_list, by_app, mail, ag = _context()
    campaign = config.CURRENT_CAMPAIGN if campaign is None else campaign
    tasks = {t.get("id") for t in wstasks.list_tasks()}
    out = []
    for a in apps_list:
        if a.get("deleted_at") or (campaign not in ("", "全部") and a.get("campaign") != campaign):
            continue
        if not by_app.get(a["id"]) and a["id"] not in tasks and a["id"] not in mail:
            continue    # 空卡：岗位、网申待办、没发出的信都没了（在老页面删了待办或记录）——不列，免得挂着点了 404 的按钮
        out.append(card(a, by_app.get(a["id"], []), mail.get(a["id"]), ag.get(a["id"]), apps_list, now))
    out.sort(key=lambda c: c.get("last_activity") or "", reverse=True)
    out.sort(key=lambda c: TURN_RANK.get(c["turn"], 9))
    return out


def today(now=None):
    """「今天」：轮到本人的（每条一个按钮）、停下的、7 天内到期的。"""
    now = now or datetime.now()
    cards = overview(now=now)
    soon = []
    for c in cards:
        due = (c.get("next") or {}).get("due") or ""
        if not due or (c.get("next") or {}).get("done") or c["turn"] in ("轮到你", "已结束"):
            continue
        try:
            d = datetime.strptime(due[:16], "%Y-%m-%d %H:%M") if len(due) > 10 else datetime.strptime(due[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if d - now <= timedelta(days=7):
            soon.append(c)
    items = jobqueue.list_items()      # 还没发出去的信不在看板上（还没有申请），在「投递」页审：这里只报个数
    mail_review = sum(1 for it in items if it.get("status") in (*jobqueue.REVIEWABLE, "失败") or it.get("send_uncertain"))
    return {"your_turn": [c for c in cards if c["turn"] == "轮到你"], "stopped": [c for c in cards if c["turn"] == "停了"],
            "soon": soon, "busy": sum(1 for c in cards if c["turn"] == "面板在做"),
            "waiting": sum(1 for c in cards if c["turn"] == "等对方"), "mail_review": mail_review,
            "recent_progress": progress.recent(5)}


def detail(app_id):
    """单家详情：概要（卡片）→ 岗位与 JD → 读回原文 → 时间线 → 待确认的建议 → 邮件稿、对话。"""
    apps_list, by_app, mail, ag = _context()
    a = next((x for x in apps_list if x.get("id") == app_id), None)
    if not a:
        raise apps.NotFound(app_id)
    pos = by_app.get(app_id, [])
    full = {r["id"]: r for r in pos}
    positions = []
    for p in positions_view(a, pos):
        r = full.get(p["id"], {})
        hist = [{**h, "text": h.get("text") or f"{records.STAGE_LABEL.get(h.get('from'), h.get('from') or '—')} → "
                 f"{records.STAGE_LABEL.get(h.get('to'), h.get('to') or '—')}" + (f"（{h['by']}）" if h.get("by") else "")}
                for h in r.get("history") or []]
        positions.append({**p, "jd_text": r.get("jd_text", ""), "jd_url": r.get("jd_url", ""), "history": hist,
                          "notes": r.get("notes", ""), "to_email": r.get("to_email", ""), "subject": r.get("subject", ""),
                          "email_body": r.get("email_body", ""), "ws_submitted": r.get("ws_submitted", "")})
    snaps = [{**s, "text": apps.snapshot_text(s)[:20000]} for s in (a.get("snapshots") or [])]
    hint_text = "\n".join([s["text"] for s in snaps] + list(dict.fromkeys(r.get("ws_submitted") or "" for r in pos)))
    return {"card": card(a, pos, mail.get(app_id), ag.get(app_id), apps_list), "positions": positions, "snapshots": snaps,
            "timeline": list(reversed(a.get("timeline") or [])), "next_step": a.get("next_step") or {},
            "mail": mail.get(app_id), "notes": a.get("notes", ""), "ai_drafts": a.get("ai_drafts") or [],
            "volunteer_hints": apps.volunteer_hints(hint_text) if len(pos) > 1 else []}


# ── 操作 ────────────────────────────────────────────────────

def accept_suggestion(app_id, sid):
    return apps.accept(app_id, sid, by="本人")


def dismiss_suggestion(app_id, sid):
    return apps.dismiss(app_id, sid, by="本人")


def set_volunteer_mode(app_id, mode):
    return apps.set_volunteer_mode(app_id, mode, by="本人")


def step_undo(app_id):
    """「做完了」点错了：改回没做完。"""
    return apps.reopen_next_step(app_id, by="本人")


def step_done(app_id, expect=None):
    """本人点了「做完了」：下一步标做完（expect 是本人看到的那一步，对不上就 Conflict）。"""
    apps.finish_next_step(app_id, by="本人", expect=expect)
    return apps.get(app_id)
