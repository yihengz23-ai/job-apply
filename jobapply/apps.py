"""申请（v3 数据模型）：一次投出的动作——一封邮件，或一个网站账号里的一次报名。岗位（看板上的一条记录）挂在申请下面。

存在 records.json 的 applications[]，和岗位同一个文件、同一把锁（records._locked），两边一起写成功或都不写。
每个信息只存一处（见 docs/overhaul/overhaul_spec_v3.md 4.2）：公司、网申小状态、账号、颜色、对话、网页、快照、
下一步、时间线、AI 草稿在申请上；岗位名、地点、志愿、JD、阶段在岗位上。

护栏（测试强制）：
- R1 系统只能往前推阶段；往回只能本人手动，并写进时间线（advance）。
- R2 首次投出时间只写一次（mark_submitted）。
- R6 同时可见的申请颜色不重复；申请结束就释放颜色（pick_color / release_color）。
- R8 放弃、删除都先返回影响清单（give_up / soft_delete 的 dry_run）。
「轮到谁」不存，view() 现算（4.6 的优先级表）。"""

import copy
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import config, records

CHANNELS = ("邮件", "网申", "邮件+网申")
WEB_PHASES = ("待开始", "在填", "等你", "待你提交", "已提交", "已放弃")
NEED_KINDS = ("登录", "验证码", "证件号", "承诺", "上传", "回答", "选岗位", "自己处理")
READBACK_STATES = ("", "待读回", "读回中", "等你登录", "不完整", "完成", "不读了")
TURNS = ("已结束", "轮到你", "停了", "面板在做", "等对方")
TURN_COLOR = {"已结束": "灰", "轮到你": "琥珀", "停了": "红", "面板在做": "青", "等对方": "灰蓝"}
# 邮件稿（queue.json 的状态）→ 界面上的小状态（4.9）
MAIL_SUB = {"排队中": "写信中", "处理中": "写信中", "待审核": "待你审", "需处理": "必须改", "重写中": "重写中",
            "已定时": "已定时", "已存草稿": "Gmail 草稿", "发送中": "发送中", "失败": "写信失败"}
MAIL_YOUR_TURN = ("待你审", "必须改", "结果不确定", "写信失败")
MAIL_PANEL_BUSY = ("写信中", "重写中", "已定时", "发送中")
SNAP_DIR_NAME = "ws_snapshots"
ALIASES_FILE = "company_aliases.json"

_listeners = {}
_listen_lock = threading.Lock()


class Conflict(ValueError):
    """本人看到的已经不是最新的了（比如下一步刚被来信换掉）：让本人刷新再点。"""


class NotFound(KeyError):
    pass


def _now():
    return records.now_str()


def new_id():
    return uuid.uuid4().hex[:10]


def stable_id(*parts):
    """迁移用：同样的输入总是得到同样的 id（同一份数据跑两次，结果逐字节一样）。"""
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


# ── 事件 ───────────────────────────────────────────────────

def on(event, fn):
    with _listen_lock:
        _listeners.setdefault(event, []).append(fn)


def emit(event, **kw):
    for fn in list(_listeners.get(event, [])):
        try:
            fn(**kw)
        except Exception:   # 监听者出错不影响主流程
            pass


# ── 读写（都在 records 的锁里）──────────────────────────────

def _apps(data):
    return data.setdefault("applications", [])


def _find(data, app_id):
    return next((a for a in _apps(data) if a.get("id") == app_id), None)


def _positions(data, app_id):
    return [r for r in data["records"] if r.get("app_id") == app_id]


def blank(app_id, channel, company, *, campaign=None, source=None, entry_url="", queue_id="", created_at=None):
    """申请的字段骨架（4.2）。"""
    at = created_at or _now()
    return {
        "id": app_id, "campaign": campaign or config.CURRENT_CAMPAIGN, "created_at": at, "updated_at": at,
        "company": (company or "").strip(), "channel": channel if channel in CHANNELS else "邮件", "queue_id": queue_id or "",
        "source": {"type": "", "label": "", "url": "", "post_date": "", **(source or {})},
        "entry_url": entry_url or "", "site": _site(entry_url),
        "account": {"login": "", "form_phone": "", "form_email": ""},
        "color": None, "chat_id": "",
        "tab": {"seq": 0, "current": 0, "opened_at": "", "last_seen": "", "state": "", "lost_reason": ""},
        "web_phase": "", "need": None, "questions": [], "candidates": [], "choices_made": [],
        "readback": {"state": "", "attempts": 0, "missing": [], "checklist": {}},
        "snapshots": [], "site_progress": {"text": "", "at": ""},
        "first_submitted_at": "", "first_submitted_basis": "",
        "next_step": {"text": "", "due": "", "source": "", "inferred": False, "confirmed": False},
        "suggestions": [], "diffs": [], "overrides": {},
        "reply": {"status": "", "at": "", "from": "", "snippet": "", "checked_at": ""},
        "timeline": [], "notes": "", "ai_drafts": [], "deleted_at": "", "legacy_extra": {},
        "volunteer_mode": "",    # 串行（按志愿顺序依次流转）/ 平行（几个岗位同时看）/ 单个 / 空（还不知道）
        "volunteer_note": "",    # 网站上关于志愿规则的原话
        "halted": "",            # 助手停下的原因（面板重启打断了 / 本人点了停下 / 这一轮没说明就停了）
    }


def _site(url):
    from urllib.parse import urlparse
    try:
        return (urlparse(url or "").hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _touch(app, at=None):
    app["updated_at"] = at or _now()


def _timeline(app, kind, text, by, at=None):
    app.setdefault("timeline", []).append({"at": at or _now(), "kind": kind, "text": text, "by": by})


def load():
    """(申请列表, 岗位列表)"""
    data = records.load_all()
    return data["applications"], data["records"]


def get(app_id):
    a = _find(records.load_all(), app_id)
    if not a:
        raise NotFound(app_id)
    return a


def list_apps(filters=None, *, include_deleted=False):
    f = filters or {}
    out = []
    for a in records.load_all()["applications"]:
        if a.get("deleted_at") and not include_deleted:
            continue
        if f.get("campaign") and f["campaign"] != "全部" and a.get("campaign") != f["campaign"]:
            continue
        if f.get("channel") and f["channel"] not in a.get("channel", ""):
            continue
        out.append(a)
    return out


def positions(app_id):
    return _positions(records.load_all(), app_id)


# ── 建 / 关联 ───────────────────────────────────────────────

def create(channel, company, *, source=None, entry_url="", app_id=None, queue_id="", campaign=None, by="面板"):
    def _do(data):
        aid = app_id or new_id()
        if _find(data, aid):
            return _find(data, aid)
        a = blank(aid, channel, company, campaign=campaign, source=source, entry_url=entry_url, queue_id=queue_id)
        if channel in ("网申", "邮件+网申"):
            a["web_phase"] = "待开始"
        _timeline(a, "建立", f"建了这个申请（{a['channel']}）", by)
        _apps(data).append(a)
        return a
    a = records.mutate_all(_do)
    emit("app_created", app=a)
    return a


def _attach(data, rec, app_id):
    rec["app_id"] = app_id
    a = _find(data, app_id)
    if a and a.get("company") and rec.get("company_name") != a["company"] and not rec.get("company_name"):
        rec["company_name"] = a["company"]


def ensure_in(data, rec):
    """（锁内调用）给一条记录找 / 建它的申请：有 app_id 且申请在就用它；有 app_id 但申请没了，按这个 id 补建；
    没有 app_id：网申渠道建网申申请，其余建邮件申请。返回 app_id。records.add 的钩子用。"""
    aid = rec.get("app_id")
    if aid and _find(data, aid):
        return aid
    ch = rec.get("apply_channel") or ""
    # 「邮箱+网申」带了邮箱的那条是邮件；之后 wstasks.link_email_record 会把它并进网申待办那张卡
    web = ("网申" in ch and not (ch.startswith("邮箱") and rec.get("to_email"))) or rec.get("send_mode") == "未发邮件" and not rec.get("to_email")
    if not aid:
        aid = new_id()
    a = blank(aid, "网申" if web else "邮件", rec.get("company_name", ""), campaign=rec.get("campaign") or None,
              entry_url=rec.get("apply_url", ""), created_at=rec.get("created_at") or rec.get("sent_at") or None)
    if rec.get("status") not in ("草稿", "", None):
        a["first_submitted_at"] = rec.get("sent_at", "") if not web else ""
        if web:
            a["web_phase"] = "已提交"
    elif web:
        a["web_phase"] = "待开始"
    _timeline(a, "建立", "新记录自动建的申请", "面板", at=a["created_at"])
    _apps(data).append(a)
    rec["app_id"] = aid
    return aid


def ensure_for_record(record_id):
    def _do(data):
        rec = next((r for r in data["records"] if r.get("id") == record_id), None)
        if not rec:
            raise NotFound(record_id)
        return ensure_in(data, rec)
    return records.mutate_all(_do)


TASK_PHASE = {"待填": "待开始", "助手在填": "在填", "等你处理": "等你", "已填待提交": "待你提交", "已提交": "已提交", "不投了": "已放弃"}


def task_to_app_fields(t):
    """网申待办（v2）→ 申请上对应的字段（links 迁移和 wstasks.add 的钩子共用）。"""
    acct = t.get("account") or ""
    return {
        "company": t.get("company") or "", "entry_url": t.get("url") or "", "site": _site(t.get("url")),
        "color": t.get("color") if isinstance(t.get("color"), int) else None, "chat_id": t.get("chat_id") or "",
        "web_phase": TASK_PHASE.get(t.get("status"), "待开始"), "queue_id": t.get("queue_id") or "",
        "account": {"login": "" if _is_placeholder(acct) else acct, "form_phone": "", "form_email": ""},
        "channel": "邮件+网申" if t.get("email_record_id") else "网申",
        "source": {"type": t.get("source") or "", "label": t.get("source_label") or "", "url": t.get("source_url") or "",
                   "post_date": ""},
    }


def _is_placeholder(v):
    from . import wstasks
    return wstasks.is_placeholder_account(v) if v else False


def ensure_for_task(t):
    """一条网申待办对应的申请（id 就是待办的 id，对话里存的 task_id 不用改）。没有就建；
    待办关联的记录（主记录、邮件那条、各岗位）都挂上来。wstasks.add 的钩子、links 迁移用。返回申请 id。"""
    tid = t["id"]

    def _do(data):
        a = _find(data, tid)
        if not a:
            f = task_to_app_fields(t)
            a = blank(tid, f["channel"], f["company"], entry_url=f["entry_url"], queue_id=f["queue_id"],
                      source=f["source"], created_at=t.get("created_at") or None)
            for k in ("color", "chat_id", "web_phase", "account", "site"):
                a[k] = f[k]
            _timeline(a, "建立", "网申待办建的申请", "面板", at=a["created_at"])
            _apps(data).append(a)
        rids = {t.get("record_id"), t.get("email_record_id"), *(t.get("position_records") or {}).values()} - {"", None}
        for r in data["records"]:
            if r.get("id") in rids or r.get("ws_task_id") == tid:
                old = r.get("app_id")
                r["app_id"] = tid
                if old and old != tid:     # 原来自动建的邮件申请：没有别的岗位了就并进来（不留空申请）
                    other = _find(data, old)
                    if other and not _positions(data, old):
                        _apps(data).remove(other)
                        _timeline(a, "合并", f"并入了自动建的申请 {old}", "面板")
        if t.get("email_record_id") and a.get("channel") == "网申":
            a["channel"] = "邮件+网申"
        return tid
    return records.mutate_all(_do)


# ── 阶段（R1）、首次投出（R2）───────────────────────────────

def advance(record_id, to_status, *, by, manual=False, reason=""):
    """改一个岗位的阶段。系统（manual=False）只能往前推；往回只能本人手动，并写进时间线。返回改后的记录。"""
    if to_status not in records.STATUSES:
        raise ValueError(f"不认识的阶段：{to_status}")

    def _do(data):
        r = next((x for x in data["records"] if x.get("id") == record_id), None)
        if not r:
            raise NotFound(record_id)
        cur = r.get("status") or "已投递"
        if cur == to_status:
            return r
        back = _is_backward(cur, to_status)
        if back and not manual:
            raise ValueError(f"系统不能把「{records.STAGE_LABEL.get(cur, cur)}」往回改成「{records.STAGE_LABEL.get(to_status, to_status)}」")
        at = _now()
        if cur == "草稿" and to_status == "已投递" and not r.get("sent_at_locked"):
            r["sent_at"], r["sent_ts"], r["sent_at_locked"] = at, datetime.now().timestamp(), True
        r.setdefault("history", []).append({"at": at, "from": cur, "to": to_status, "by": by, **({"reason": reason} if reason else {})})
        r["status"], r["status_updated_at"] = to_status, at
        a = _find(data, r.get("app_id"))
        if a:
            label = records.STAGE_LABEL
            _timeline(a, "阶段", f"{r.get('job_title') or '岗位'}：{label.get(cur, cur)} → {label.get(to_status, to_status)}"
                      + ("（往回改）" if back else "") + (f"，{reason}" if reason else ""), by, at=at)
            _touch(a, at)
        return r
    r = records.mutate_all(_do)
    emit("stage_changed", record=r, by=by)
    return r


def _is_backward(cur, new):
    o = records.STAGE_ORDER
    if cur in records.TERMINAL:      # 已经结束的（未通过 / 无回复 / 放弃）再改，算往回
        return True
    if new in records.TERMINAL:
        return False
    return o.get(new, 0) < o.get(cur, 0)


def mark_submitted(app_id, *, at=None, basis, by):
    """投出去了：首次投出时间只写一次（R2）；还是草稿的岗位变成已投递（已经走得更远的不动）；网申小状态变已提交。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        stamp = at or _now()
        if not a.get("first_submitted_at"):
            a["first_submitted_at"], a["first_submitted_basis"] = stamp, basis
            _timeline(a, "投出", f"投出去了（{basis}）", by, at=stamp)
        if a.get("channel") in ("网申", "邮件+网申") and a.get("web_phase") not in ("已放弃",):
            a["web_phase"] = "已提交"
            a["need"] = None
        for r in _positions(data, app_id):
            if r.get("status") == "草稿":
                r.setdefault("history", []).append({"at": stamp, "from": "草稿", "to": "已投递", "by": by})
                r["status"], r["status_updated_at"] = "已投递", stamp
                if not r.get("sent_at_locked"):
                    r["sent_at"], r["sent_ts"], r["sent_at_locked"] = a["first_submitted_at"], datetime.now().timestamp(), True
        _touch(a)
        return a
    a = records.mutate_all(_do)
    emit("submitted", app=a, by=by)
    return a


# ── 网申小状态、要你做 ─────────────────────────────────────

def set_web_phase(app_id, phase, *, by):
    if phase not in WEB_PHASES:
        raise ValueError(f"不认识的网申状态：{phase}")

    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        if a.get("web_phase") in ("已提交", "已放弃") and by == "助手":   # R3：终态只有本人能改
            return a
        if a.get("web_phase") != phase:
            _timeline(a, "网申", f"{a.get('web_phase') or '—'} → {phase}", by)
            a["web_phase"] = phase
            if phase != "等你":
                a["need"] = None
            _touch(a)
        return a
    return records.mutate_all(_do)


def set_need(app_id, kind, text, where="", auto=False, by="助手"):
    """轮到本人做一件事（kind 是 NEED_KINDS 之一），网申小状态变「等你」。"""
    if kind not in NEED_KINDS:
        kind = "自己处理"

    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        if a.get("web_phase") in ("已提交", "已放弃"):
            return a
        a["need"] = {"kind": kind, "text": (text or "").strip()[:300], "where": where, "auto": bool(auto), "at": _now(), "by": by}
        if a.get("web_phase") != "等你":
            _timeline(a, "等你", f"{kind}：{a['need']['text']}", by)
        a["web_phase"] = "等你"
        _touch(a)
        return a
    a = records.mutate_all(_do)
    emit("need_set", app=a)
    return a


def clear_need(app_id, by):
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        if a.get("need"):
            a["need"] = None
            if a.get("web_phase") == "等你":
                a["web_phase"] = "在填"
            _timeline(a, "网申", "要你做的事做完了", by)
            _touch(a)
        return a
    return records.mutate_all(_do)


# ── 放弃 / 删除 / 恢复 / 合并 / 改名（R8：先给影响清单）─────────────

def _impact(data, app_id):
    pos = _positions(data, app_id)
    return {"positions": [{"id": r["id"], "job_title": r.get("job_title", ""), "status": r.get("status", "")} for r in pos],
            "not_submitted": [r["id"] for r in pos if r.get("status") == "草稿"],
            "submitted": [r["id"] for r in pos if r.get("status") not in ("草稿", None)],
            "queue_id": (_find(data, app_id) or {}).get("queue_id", ""),
            "chat_id": (_find(data, app_id) or {}).get("chat_id", "")}


def give_up(app_id, reason, *, by, dry_run=False):
    """不投了（必须写原因）：还没投出的岗位改成放弃，已投出的不动；网申小状态已放弃；释放颜色。返回影响清单。"""
    reason = (reason or "").strip()
    if not reason and not dry_run:
        raise ValueError("不投了要写一句原因")

    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        impact = _impact(data, app_id)
        if dry_run:
            return impact
        at = _now()
        for r in _positions(data, app_id):
            if r.get("status") == "草稿":
                r.setdefault("history", []).append({"at": at, "from": "草稿", "to": "放弃", "by": by, "reason": reason})
                r["status"], r["status_updated_at"] = "放弃", at
        if a.get("channel") in ("网申", "邮件+网申"):
            a["web_phase"] = "已放弃"
        a["need"] = None
        a["color"] = None
        _timeline(a, "放弃", f"不投了：{reason}", by, at=at)
        _touch(a, at)
        return impact
    return records.mutate_all(_do)


def soft_delete(app_id, *, by, dry_run=False):
    """删除是软删除：申请标 deleted_at，岗位、对话、邮件稿一起收起，7 天内可恢复。返回影响清单。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        impact = _impact(data, app_id)
        if dry_run:
            return impact
        at = _now()
        a["deleted_at"] = at
        a["color"] = None
        for r in _positions(data, app_id):
            r["deleted_at"] = at
        _timeline(a, "删除", "删除了（7 天内可恢复）", by, at=at)
        return impact
    return records.mutate_all(_do)


def restore(app_id, *, by="本人"):
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        a["deleted_at"] = ""
        for r in _positions(data, app_id):
            r.pop("deleted_at", None)
        _timeline(a, "恢复", "恢复了", by)
        _touch(a)
        return a
    return records.mutate_all(_do)


def purge_deleted(days=7, now=None):
    """删了满 7 天的申请和它的岗位真正删掉（备份里还有）。返回删掉的申请 id。"""
    now = now or datetime.now()

    def _do(data):
        gone = []
        for a in list(_apps(data)):
            try:
                at = datetime.strptime(a.get("deleted_at") or "", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if now - at >= timedelta(days=days):
                gone.append(a["id"])
                _apps(data).remove(a)
        data["records"][:] = [r for r in data["records"] if r.get("app_id") not in gone]
        return gone
    return records.mutate_all(_do)


def merge(src, dst, *, by):
    """把 src 的岗位并到 dst（同一网站同一账号的两家），src 收起。"""
    def _do(data):
        a, b = _find(data, src), _find(data, dst)
        if not a or not b:
            raise NotFound(src if not a else dst)
        n = 0
        for r in _positions(data, src):
            r["app_id"] = dst
            r["company_name"] = b.get("company") or r.get("company_name", "")
            n += 1
        a["deleted_at"] = _now()
        a["color"] = None
        _timeline(b, "合并", f"并入了「{a.get('company')}」的 {n} 个岗位", by)
        _timeline(a, "合并", f"并到了「{b.get('company')}」", by)
        _touch(b)
        return b
    return records.mutate_all(_do)


def rename(app_id, company, *, by="本人"):
    company = (company or "").strip()
    if not company:
        raise ValueError("公司名不能为空")

    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        old = a.get("company", "")
        a["company"] = company
        for r in _positions(data, app_id):     # 岗位上的 company_name 是同步副本（vault 和查重在用）
            r["company_name"] = company
        _timeline(a, "改名", f"{old} → {company}", by)
        _touch(a)
        return a
    return records.mutate_all(_do)


def set_account(app_id, login=None, form_phone=None, form_email=None, *, by="本人"):
    """申请账号拆成登录名、表上手机、表上邮箱三格；占位话不收。岗位上的 apply_account 过渡期同步成登录名。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        acct = a.setdefault("account", {"login": "", "form_phone": "", "form_email": ""})
        changed = False
        for k, v in (("login", login), ("form_phone", form_phone), ("form_email", form_email)):
            if v is None:
                continue
            v = str(v).strip()
            if v and _is_placeholder(v):
                continue
            if acct.get(k) != v:
                acct[k] = v
                changed = True
        if changed:
            for r in _positions(data, app_id):
                r["apply_account"] = acct.get("login") or acct.get("form_phone") or acct.get("form_email") or ""
            _timeline(a, "账号", "申请账号更新了", by)
            _touch(a)
        return a
    return records.mutate_all(_do)


# ── 下一步、建议 ────────────────────────────────────────────

def set_next_step(app_id, text, due="", source="", inferred=False, *, confirmed=None):
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        a["next_step"] = {"text": (text or "").strip(), "due": due or "", "source": source, "inferred": bool(inferred),
                          "confirmed": (not inferred) if confirmed is None else bool(confirmed), "done": False}
        _timeline(a, "下一步", f"{text}" + (f"（截止 {due}）" if due else ""), source or "面板")
        _touch(a)
        return a
    return records.mutate_all(_do)


def finish_next_step(app_id, *, by="本人", expect=None):
    """下一步做完了（本人点了「做完了」，或者口述「X 做完了」）：标 done，时间线记一笔。没有没做完的下一步就什么都不做，返回 None。
    expect：本人点按钮时看到的那一步；和现在的对不上（期间被来信或另一个窗口换了）就抛 Conflict，不把新的那步标掉。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        nxt = a.get("next_step") or {}
        if expect is not None and nxt.get("text") != expect:
            raise Conflict("下一步刚变了：刷新一下再点")
        if not nxt.get("text") or nxt.get("done"):
            return None
        prev = dict(nxt)
        nxt["done"] = True
        nxt["done_at"] = _now()
        _timeline(a, "下一步", f"做完了：{nxt['text']}", by)
        _touch(a)
        return prev
    return records.mutate_all(_do)


def reopen_next_step(app_id, *, by="本人"):
    """「做完了」点错了：下一步改回没做完。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        nxt = a.get("next_step") or {}
        if nxt.get("text") and nxt.get("done"):
            nxt["done"] = False
            nxt.pop("done_at", None)
            _timeline(a, "下一步", f"改回没做完：{nxt['text']}", by)
            _touch(a)
        return a
    return records.mutate_all(_do)


def suggest(app_id, kind, text, payload=None):
    """系统给一条建议（比如「改成笔试/测评？」），本人点了才生效。同样的建议不重复给。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        for s in a.setdefault("suggestions", []):
            if s.get("kind") == kind and s.get("text") == text and s.get("state") == "待定":
                return s
        s = {"id": new_id(), "kind": kind, "text": text, "payload": payload or {}, "state": "待定", "at": _now()}
        a["suggestions"].append(s)
        _touch(a)
        return s
    return records.mutate_all(_do)


def _resolve(app_id, sid, state, by):
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        s = next((x for x in a.get("suggestions", []) if x.get("id") == sid), None)
        if not s:
            raise NotFound(sid)
        if s.get("state") != "待定":          # 已经点过了（两个窗口、连点两下）：不重复改
            return {**s, "already": True}
        s["state"], s["resolved_at"], s["by"] = state, _now(), by
        _timeline(a, "建议", f"{'采纳' if state == '采纳' else '不用'}：{s.get('text')}", by)
        _touch(a)
        return s
    return records.mutate_all(_do)


def accept(app_id, sid, *, by="本人"):
    s = _resolve(app_id, sid, "采纳", by)
    if s.get("already"):
        return s
    if s.get("kind") == "阶段" and (s.get("payload") or {}).get("record_id"):
        advance(s["payload"]["record_id"], s["payload"]["to"], by=by, manual=True, reason="采纳了建议")
    return s


def dismiss(app_id, sid, *, by="本人"):
    return _resolve(app_id, sid, "不用", by)


def set_reply(app_id, *, status="", at="", sender="", snippet="", subject=""):
    """这个申请最近一封来信（看板查回复时同步过来；退信时「轮到你」）。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        a["reply"] = {"status": status, "at": at, "from": sender, "snippet": snippet[:200], "subject": subject[:200],
                      "checked_at": _now(), "handled": False}
        _touch(a)
        return a
    return records.mutate_all(_do)


def detach_task(task_id, *, by="本人"):
    """网申页删了这条待办：申请里网申那一半跟着去掉。还剩别的岗位（比如「邮件+网申」的邮件那条）就留着卡，只清网申的状态、
    要你做、颜色；什么都不剩就软删除（看板上本来也不列空卡）。"""
    def _do(data):
        a = _find(data, task_id)
        if not a or a.get("deleted_at"):
            return None
        left = _positions(data, task_id)
        if not left:
            a["deleted_at"] = _now()
            _timeline(a, "删除", "网申待办删了，这家没有别的岗位", by)
            return a
        if a.get("web_phase") != "已提交":
            a["web_phase"] = ""
            if a.get("channel") == "邮件+网申":
                a["channel"] = "邮件"
        rb = a.setdefault("readback", {})
        if rb.get("state") in ("待读回", "读回中", "等你登录", "不完整"):
            rb["state"] = "不读了"
        a["need"], a["color"], a["halted"] = None, None, ""
        _timeline(a, "网申", "网申待办删了（邮件 / 已交的岗位还在）", by)
        _touch(a)
        return a
    return records.mutate_all(_do)


def handle_reply(app_id, *, by="本人"):
    """本人看过这封来信 / 退信了：不再算轮到你。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        rep = a.setdefault("reply", {})
        if rep.get("status") and not rep.get("handled"):
            rep["handled"] = True
            _timeline(a, "来信", f"看过了：{rep.get('status')}" + (f"（{rep.get('subject')}）" if rep.get("subject") else ""), by)
            _touch(a)
        return a
    return records.mutate_all(_do)


def add_timeline(app_id, kind, text, by):
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        _timeline(a, kind, text, by)
        _touch(a)
        return a
    return records.mutate_all(_do)


# ── 快照：从网站读回的原文，只追加不改，按内容去重 ──────────────────

MASK_ID = re.compile(r"(?<![0-9A-Za-z])\d{15,19}[0-9Xx]?(?![0-9A-Za-z])")
# 截断 / 带星号的证件号：6 位地区码 + 出生年（19xx / 20xx）开头，后面跟数字或 *（如 1234561990****1233、123456199001）
MASK_ID_PART = re.compile(r"(?<![0-9A-Za-z*])\d{6}(?:19|20)\d{2}[\dXx*]{0,10}(?![0-9A-Za-z*])")
# 「证件号码 / 身份证号 / 护照号」后面跟的那串（带空格、星号也算）整串隐去
MASK_ID_LINE = re.compile(r"((?:证件号码?|身份证(?:号码?)?|护照号码?)\s*[:：]?\s*)[0-9Xx*][0-9Xx* ]{3,24}")


def mask(text):
    """存快照、导出 Excel 前统一打码：完整的证件号 / 银行卡号（15 到 19 位）、截断或带星号的证件号、「证件号码」后面那一串。"""
    t = MASK_ID_LINE.sub(lambda m: m.group(1) + "[证件号已隐去]", text or "")
    t = MASK_ID.sub("[证件号已隐去]", t)
    return MASK_ID_PART.sub("[证件号已隐去]", t)


def snap_dir():
    return config.DATA_DIR / SNAP_DIR_NAME


def add_snapshot(app_id, kind, text, url="", source="网页脚本", at=None):
    """存一份读回的原文：ws_snapshots/<申请id>/<时间>-<类型>.txt。同一申请里内容一样（sha 相同）的不重复存。返回快照索引。"""
    body = mask(str(text or "")).strip()
    if not body:
        raise ValueError("读回的内容是空的")
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    stamp = at or _now()

    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        for s in a.setdefault("snapshots", []):
            if s.get("sha") == sha:
                return s
        name = f"{re.sub(r'[^0-9]', '', stamp)[:14]}-{re.sub(r'[^0-9A-Za-z一-龥]', '', kind)[:12] or '页面'}-{sha[:6]}.txt"
        rel = Path(SNAP_DIR_NAME) / app_id / name
        path = config.DATA_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_name("." + name + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, path)
        s = {"id": "s" + sha[:8], "kind": kind, "at": stamp, "url": url or "", "chars": len(body), "source": source,
             "path": str(rel), "sha": sha}
        a["snapshots"].append(s)
        _timeline(a, "读回", f"读回了「{kind}」（{len(body)} 字，{source}）", "助手" if source != "本人" else "本人", at=stamp)
        _touch(a, stamp)
        return s
    s = records.mutate_all(_do)
    emit("readback_saved", app_id=app_id, snapshot=s)
    return s


def snapshot_text(snap):
    try:
        return (config.DATA_DIR / snap["path"]).read_text(encoding="utf-8")
    except (OSError, KeyError):
        return ""


# ── 颜色（R6）、网页编号 ─────────────────────────────────────

def _visible(a):
    """「同时可见」：网申还没结束、或者读回还没完、或者正轮到本人。"""
    if a.get("deleted_at"):
        return False
    phase = a.get("web_phase")
    if phase in ("待开始", "在填", "等你", "待你提交"):
        return True
    if phase == "已提交" and a.get("readback", {}).get("state") not in ("完成", "不读了", ""):
        return True
    return False


def pick_color(app_id):
    """给这家挑一个颜色：在同时可见的申请之间不重复；8 种都用了就挑用得最少的（界面上写成「蓝2」）。已经有颜色就不换。"""
    from . import wstasks

    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        used = [x.get("color") for x in _apps(data) if x is not a and _visible(x) and isinstance(x.get("color"), int)]
        if isinstance(a.get("color"), int) and a["color"] not in used:
            return a["color"]
        counts = [used.count(i) for i in range(len(wstasks.COLORS))]
        a["color"] = counts.index(min(counts))
        _touch(a)
        return a["color"]
    return records.mutate_all(_do)


def release_color(app_id):
    def _do(data):
        a = _find(data, app_id)
        if a and a.get("color") is not None:
            a["color"] = None
            _touch(a)
        return a
    return records.mutate_all(_do)


def color_label(app, apps_list=None):
    """「蓝」；同时可见的有两家撞了色（超过 8 家）就写「蓝2」。没颜色（已结束）返回「灰」。"""
    from . import wstasks
    c = app.get("color")
    if not isinstance(c, int):
        return "灰"
    name = wstasks.COLORS[c % len(wstasks.COLORS)][2]
    same = [x for x in (apps_list or []) if x.get("color") == c and _visible(x) and not x.get("deleted_at")]
    same.sort(key=lambda x: x.get("created_at", ""))
    n = next((i for i, x in enumerate(same, 1) if x.get("id") == app.get("id")), 1)
    return name if n <= 1 else f"{name}{n}"


def next_page(app_id):
    """助手为这家新开了一个网页：页码加 1，返回新页码。"""
    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        tab = a.setdefault("tab", {"seq": 0, "current": 0})
        tab["seq"] = int(tab.get("seq") or 0) + 1
        tab.update(current=tab["seq"], opened_at=_now(), last_seen=_now(), state="在线", lost_reason="")
        _touch(a)
        return tab["seq"]
    return records.mutate_all(_do)


# ── 公司别名（归一、查重用）────────────────────────────────────

def aliases():
    try:
        data = json.loads((config.BASE_DIR / ALIASES_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in (data.get("aliases") or {}).items() if isinstance(k, str) and isinstance(v, str)}


def canonical_company(name):
    """公司名归一：先查别名表（本人确认过才会写进去），再去掉「有限公司」「资本」这类后缀。"""
    name = (name or "").strip()
    return records.norm_company(aliases().get(name, name))


# ── 轮到谁（4.6，现算不存）──────────────────────────────────

def stage_of(positions_list):
    """申请的阶段：所有岗位里走得最远的那个（都结束了就取结束的那种）。没有岗位算准备中。"""
    live = [p.get("status") or "已投递" for p in positions_list if not p.get("deleted_at")]
    if not live:
        return "草稿"
    active = [s for s in live if s not in records.TERMINAL]
    if active:
        return max(active, key=lambda s: records.STAGE_ORDER.get(s, 0))
    for s in ("拒绝", "无回复", "放弃"):
        if s in live:
            return s
    return live[0]


def _due_within(due, hours, now):
    try:
        d = datetime.strptime(due[:16], "%Y-%m-%d %H:%M") if len(due) > 10 else datetime.strptime(due[:10], "%Y-%m-%d") + timedelta(hours=23, minutes=59)
    except (TypeError, ValueError):
        return False
    return d - now <= timedelta(hours=hours)


def view(app, ctx=None):
    """一个申请在界面上怎么显示：{stage, stage_label, sub, turn, turn_color, todo, button, why, next, last_activity, readback}。
    ctx：positions（这个申请的岗位）、mail（它的邮件稿 queue 条目）、agent（{working, queued_why, alive, stopped_reason}）、now。"""
    ctx = ctx or {}
    now = ctx.get("now") or datetime.now()
    pos = ctx.get("positions")
    if pos is None:
        pos = positions(app["id"])
    mail = ctx.get("mail")
    ag = ctx.get("agent") or {}
    stage = stage_of(pos)
    phase = app.get("web_phase") or ""
    rb = app.get("readback") or {}
    need = app.get("need") or {}
    sub = []
    mail_sub = ""
    if mail and stage == "草稿":
        mail_sub = "结果不确定" if mail.get("send_uncertain") else MAIL_SUB.get(mail.get("status"), "")
        if mail_sub == "已定时" and mail.get("send_at"):
            mail_sub = f"已定时（{mail['send_at'][5:]}）"
        if mail_sub:
            sub.append(mail_sub)
    if phase:
        sub.append(phase + (f"·{need.get('kind')}" if phase == "等你" and need.get("kind") else ""))
    if phase == "已提交" and rb.get("state"):
        sub.append("读回：" + rb["state"])
    out = {"stage": stage, "stage_label": records.STAGE_LABEL.get(stage, stage), "sub": sub, "todo": "", "button": None,
           "why": "", "next": app.get("next_step") or {}, "readback": rb,
           "last_activity": (app.get("timeline") or [{}])[-1].get("at", app.get("updated_at", ""))}

    def done(turn, todo="", button=None, why=""):
        out.update(turn=turn, turn_color=TURN_COLOR[turn], todo=todo, button=button, why=why)
        return out

    # 1 已结束
    finished = pos and all((p.get("status") in ("offer", *records.TERMINAL)) for p in pos if not p.get("deleted_at"))
    pending_suggest = [s for s in app.get("suggestions") or [] if s.get("state") == "待定"]
    pending_diffs = [d for d in app.get("diffs") or [] if d.get("state", "待定") == "待定"]
    if app.get("deleted_at"):
        return done("已结束", why="已删除")
    if phase == "已放弃" and not pending_suggest and not pending_diffs:
        if stage == "草稿":
            out.update(stage="放弃", stage_label=records.STAGE_LABEL.get("放弃", "放弃"))
        return done("已结束", why="不投了")
    if finished and not pending_suggest and not pending_diffs:
        return done("已结束")
    # 2 轮到你
    ms = mail_sub.split("（")[0]
    if ms in MAIL_YOUR_TURN:
        return done("轮到你", {"待你审": "审一下这封信", "必须改": "这封信有必须改的问题", "结果不确定": "去 Gmail 看看这封发出去没有",
                             "写信失败": "写信失败了，重写一次"}[ms], {"label": "去看信", "action": "open_mail"})
    if ms == "已定时" and mail.get("send_at"):
        try:
            if now - datetime.strptime(mail["send_at"], "%Y-%m-%d %H:%M") > timedelta(hours=3):
                return done("轮到你", "定时信过点 3 小时还没发出去", {"label": "去看信", "action": "open_mail"})
        except ValueError:
            pass
    if (app.get("reply") or {}).get("status") == "退信" and not (app.get("reply") or {}).get("handled"):
        return done("轮到你", "这封信被退回来了", {"label": "看退信", "action": "open_reply"})
    if phase == "等你":
        label = {"登录": "我登录好了", "证件号": "我填好了"}.get(need.get("kind"), "我弄完了")
        return done("轮到你", need.get("text") or "助手在等你", {"label": label, "action": "need_done"})
    if phase == "待你提交":
        return done("轮到你", "填好了：检查一下，在网站上提交", {"label": "我已在网站上提交", "action": "submitted"})
    if [q for q in app.get("questions") or [] if not q.get("answer")]:
        return done("轮到你", "助手有问题要问你", {"label": "回答", "action": "answer"})
    if [c for c in app.get("candidates") or [] if c.get("state", "待定") == "待定"]:
        return done("轮到你", "有候选岗位等你勾选", {"label": "选岗位", "action": "choose"})
    if phase == "已提交" and rb.get("state") == "等你登录":
        return done("轮到你", "读回要先登录这家网站", {"label": "我登录好了", "action": "login_done"})
    if phase == "已提交" and rb.get("state") == "不完整" and int(rb.get("attempts") or 0) >= 2:
        return done("轮到你", "读回还缺：" + "、".join(rb.get("missing") or []), {"label": "补读", "action": "readback"})
    if pending_suggest or pending_diffs:
        return done("轮到你", (pending_suggest or pending_diffs)[0].get("text", "有一条要你确认"), {"label": "看看", "action": "review"})
    nxt = app.get("next_step") or {}
    if nxt.get("due") and not nxt.get("done") and _due_within(nxt["due"], 72, now):
        text = nxt.get("text") or "下一步"
        return done("轮到你", text if nxt["due"] in text else f"{text}（截止 {nxt['due']}）", {"label": "做完了", "action": "step_done"})
    if nxt.get("text") and not nxt.get("due") and not nxt.get("done") and nxt.get("source") == "本人口述":
        return done("轮到你", nxt["text"], {"label": "做完了", "action": "step_done"})     # 本人说过要做、没说截止：也算轮到你
    if phase == "待开始":
        return done("轮到你", "还没开始填", {"label": "让助手填", "action": "run"})
    # 3 停了
    working, queued = bool(ag.get("working")), bool(ag.get("queued_why"))
    if (phase == "在填" or rb.get("state") == "读回中") and not working and not queued:
        why = ag.get("stopped_reason") or app.get("halted") or ("助手进程已结束" if ag.get("alive") is False else "没说明")
        if phase == "已提交":       # 已经交了、停在读回上：按钮是再去读回（只看不改），不是「接着填」
            return done("停了", "读回停了：" + why, {"label": "让助手再去读回", "action": "readback"}, why)
        return done("停了", "助手停了：" + why, {"label": "让助手接着做", "action": "continue"}, why)
    if ctx.get("job_failed"):
        return done("停了", ctx["job_failed"], {"label": "重试", "action": "retry"}, "出错")
    # 4 面板在做
    if ms in MAIL_PANEL_BUSY or working or queued or rb.get("state") == "读回中":
        return done("面板在做", ag.get("queued_why") or mail_sub or ("助手在填" if working else "读回中"))
    # 5 等对方
    return done("等对方")


# ── 过渡期适配层：v2 网申待办（wangshen_tasks.json）的改动同步到申请上 ─────────────

NEED_WORDS = (("登录", ("登录", "扫码", "登陆")), ("验证码", ("验证码",)), ("证件号", ("证件号", "身份证", "护照")),
              ("上传", ("上传", "照片", "附件")), ("选岗位", ("选岗位", "志愿", "选哪个岗位")), ("承诺", ("承诺", "声明", "勾选同意")),
              ("回答", ("告诉我", "问你", "要你回答", "你决定", "要你定")))
# 只认说得很明白的原话：「调整志愿顺序」这类只是能改顺序，不说明是串行；一句话里两种都有（「同一机构按顺序，不同机构间平行」）就不猜
VOLUNTEER_SERIAL = ("依次流转", "按志愿顺序依次", "按顺序依次", "按顺序流转", "前一志愿未通过", "第一志愿未通过")
VOLUNTEER_PARALLEL = ("平行志愿", "同时投递多个", "可同时投递", "同时进行筛选")


def need_kind(text):
    for kind, words in NEED_WORDS:
        if any(w in (text or "") for w in words):
            return kind
    return "自己处理"


def _readback_state(t, a):
    """v2 待办的读回状态 → 申请的读回状态（读回清单：投递记录、实际提交的简历、每个岗位的 JD）。"""
    st = t.get("readback_state") or ""
    rb = a.setdefault("readback", {"state": "", "attempts": 0, "missing": [], "checklist": {}})
    if st in ("读回中", "排队等读回"):
        rb["state"] = "读回中"
    elif st == "读回等你登录":
        rb["state"] = "等你登录"
    elif st.startswith("没读回"):
        rb["state"] = "不完整"
        rb["attempts"] = int(rb.get("attempts") or 0) + 1
        rb["missing"] = rb.get("missing") or ["投递记录", "实际提交的简历"]
    elif t.get("readback") or t.get("jds"):
        got = t.get("readback") or {}
        missing = [label for kind, label in (("status", "投递记录"), ("resume", "实际提交的简历")) if not (got.get(kind) or {}).get("text")]
        jds = t.get("jds") or {}
        missing += [f"{p['name']} 的 JD" for p in t.get("positions") or [] if p.get("name") not in jds]
        rb["checklist"] = {"投递记录": "status" in got, "实际提交的简历": "resume" in got, "岗位 JD": len(jds)}
        rb["missing"] = missing
        rb["state"] = "不完整" if missing else "完成"


def mirror_task(t, fields=None, by="面板"):
    """网申待办改了：同步到同 id 的申请上（申请不在就先按待办建）。出错不影响待办本身。"""
    tid = (t or {}).get("id")
    if not tid:
        return None
    f = set(fields or ())
    try:
        get(tid)
    except NotFound:
        ensure_for_task(t)
        f |= {"status", "todo", "account", "site_status", "readback_state", "positions", "chat_id", "color", "halted"}

    def _do(data):
        a = _find(data, tid)
        if not a:
            return None
        at = _now()
        if "status" in f:
            phase = TASK_PHASE.get(t.get("status"))
            locked = a.get("web_phase") in ("已提交", "已放弃") and by == "助手"
            if phase and a.get("web_phase") != phase and not locked:
                _timeline(a, "网申", f"{a.get('web_phase') or '—'} → {phase}", by, at=at)
                a["web_phase"] = phase
            if a.get("web_phase") != "等你":
                a["need"] = None
        if t.get("status") == "等你处理" and f & {"status", "todo"} and a.get("web_phase") == "等你":
            text = (t.get("todo") or "").strip() or "助手没写要你做什么，看对话"
            if not a.get("need") or a["need"].get("text") != text:
                a["need"] = {"kind": need_kind(text), "text": text[:300], "where": "", "auto": False, "at": at, "by": "助手"}
        if f & {"halted", "status"}:
            a["halted"] = t.get("halted") or ""
        if f & {"readback_state", "readback", "jds", "positions", "status"}:
            _readback_state(t, a)
        if f & {"site_status", "readback"} and t.get("site_status"):
            a["site_progress"] = {"text": t["site_status"], "at": t.get("site_status_at") or at}
        if "account" in f and t.get("account") and not _is_placeholder(t["account"]):
            a.setdefault("account", {"login": "", "form_phone": "", "form_email": ""})["login"] = t["account"]
        if "chat_id" in f and t.get("chat_id"):
            a["chat_id"] = t["chat_id"]
        if "color" in f and isinstance(t.get("color"), int) and a.get("color") is None and a.get("web_phase") not in ("已提交", "已放弃"):
            a["color"] = t["color"]
        if "company" in f and t.get("company") and not a.get("company"):
            a["company"] = t["company"]
        if "url" in f and t.get("url"):
            a["entry_url"], a["site"] = t["url"], _site(t["url"])
        if f & {"positions", "position_records"}:      # 志愿序号写到对应岗位上
            pr = t.get("position_records") or {}
            ranks = {p["name"]: p.get("rank") for p in t.get("positions") or [] if p.get("rank")}
            for r in data["records"]:
                for name, rid in pr.items():
                    if r.get("id") == rid:
                        r["app_id"] = tid
                        if ranks.get(name):
                            r["choice_no"] = int(ranks[name])
            if len(t.get("positions") or []) > 1 and not a.get("volunteer_mode") and any(ranks.values()):
                a["volunteer_mode"] = ""      # 有志愿序号但网站没说串行还是平行：先空着，界面上按志愿顺序显示
        _touch(a, at)
        return a
    try:
        return records.mutate_all(_do)
    except Exception:
        return None


def guess_volunteer_mode(text):
    """网站原话里的志愿规则：返回 (串行 / 平行 / "", 那一句原话)。"""
    for line in re.split(r"[\n。；;]", text or ""):
        serial, parallel = any(w in line for w in VOLUNTEER_SERIAL), any(w in line for w in VOLUNTEER_PARALLEL)
        if serial != parallel:
            return ("串行" if serial else "平行"), line.strip()[:160]
    return "", ""


VOLUNTEER_HINT = re.compile(r"[^\n。；;]{0,60}志愿[^\n。；;]{0,60}(顺序|流转|平行|同时|最多|调剂)[^\n。；;]{0,60}|[^\n。；;]{0,60}(最多|可同时)[^\n。；;]{0,20}(投递|申请|选择)[^\n。；;]{0,40}")


def volunteer_hints(text, n=4):
    """网站原话里讲志愿规则的句子（给本人判断是串行还是平行用，不自动定）。"""
    out = []
    for m in VOLUNTEER_HINT.finditer(text or ""):
        line = re.sub(r"\s+", " ", m.group(0)).strip()
        if line and line not in out:
            out.append(line[:160])
        if len(out) >= n:
            break
    return out


def set_volunteer_mode(app_id, mode, note="", *, by="本人"):
    if mode not in ("串行", "平行", "单个", ""):
        raise ValueError(f"不认识的志愿方式：{mode}")

    def _do(data):
        a = _find(data, app_id)
        if not a:
            raise NotFound(app_id)
        if a.get("volunteer_mode") != mode:
            _timeline(a, "志愿", f"志愿方式：{a.get('volunteer_mode') or '未知'} → {mode or '未知'}", by)
            a["volunteer_mode"] = mode
        if note:
            a["volunteer_note"] = note
        _touch(a)
        return a
    return records.mutate_all(_do)


CN_NUM = {c: i for i, c in enumerate("一二三四五六七八九十", 1)}
CHOICE_MARK = re.compile(r"第\s*([1-9]|[一二三四五六七八九十])\s*志愿|志愿\s?([一二三四五六七八九十])")


def _norm_title(s):
    return re.sub(r"[\s（）()【】\[\]·\-—_/|｜:：,，.。]+", "", s or "").lower()


def choice_order(text, positions_list):
    """读回原文 / 岗位上写着的志愿顺序 → {记录 id: 第几志愿}。认「第1志愿 岗位名」「第一志愿：岗位名」「志愿一 / 职位名称 / 岗位名」，
    还有岗位名、网站状态里自带的「（第4志愿）」。岗位名对不上、或者一段话对上好几个岗位的不算（宁可不标，不标错）。"""
    out = {}
    for p in positions_list:
        m = CHOICE_MARK.search((p.get("job_title") or "") + " " + (p.get("site_status") or ""))
        n = m and (int(m.group(1)) if (m.group(1) or "").isdigit() else CN_NUM.get(m.group(1) or m.group(2), 0))
        if n:
            out[p["id"]] = n
    marks = list(CHOICE_MARK.finditer(text or ""))
    for i, m in enumerate(marks):
        g = m.group(1) or m.group(2)
        n = int(g) if g.isdigit() else CN_NUM.get(g, 0)
        if not n or n in out.values():
            continue
        seg = _norm_title(text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else m.end() + 300][:300])
        hits = [p for p in positions_list if p["id"] not in out and _norm_title(p.get("job_title")) and _norm_title(p.get("job_title")) in seg]
        if len(hits) > 1:      # 一个名字是另一个的前缀（「投资岗」和「投资岗（上海）」）：取最长的那个，还分不出来就不算
            longest = max(len(_norm_title(p["job_title"])) for p in hits)
            hits = [p for p in hits if len(_norm_title(p["job_title"])) == longest]
        if len(hits) == 1:
            out[hits[0]["id"]] = n
    return out
