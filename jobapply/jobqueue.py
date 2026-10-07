"""批量队列：一次贴很多链接 / JD，后台并行抓取、分析、检查，做完等你审核。

状态：排队中 → 处理中 → 待审核（检查全过或只有提示）/ 需处理（有必须处理的问题）/ 失败
      审核后 → 发送中（已被认领，防止重复发送）→ 已发送 / 已存草稿 / 已记录（网申）
队列存在 queue.json，面板重启后没做完的会接着做。"""

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import checks, config, fetch, gmail_client, llm, pipeline

QUEUE_PATH = config.BASE_DIR / "queue.json"
WORKERS = int(os.environ.get("JOBAPPLY_QUEUE_WORKERS", "3"))
ACTIVE = ("排队中", "处理中")
DONE = ("已发送", "已存草稿", "已记录")
SENDING = "发送中"
REVIEWABLE = ("待审核", "需处理")
REGEN = "重写中"   # 按补充要求重写：在后台做，可以先去看下一封，写好存回这一条
SCHEDULED = "已定时"   # 晚上点了发送：到第二天 config.SEND_AT 由面板自己发出
OPT_KEYS = ("position_hint", "resume_hint", "report_hint", "extra")   # 「这批的设置」
SEND_GAP_SECONDS = 3   # 批量发送时每封之间停一下，别像群发
URL_RE = re.compile(r"^https?://\S+$")

_lock = threading.RLock()
_ready_lock = threading.Lock()
_pool = None
_notified_sig = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load():
    if not QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        QUEUE_PATH.rename(QUEUE_PATH.with_suffix(f".bad-{datetime.now():%Y%m%d%H%M%S}.json"))
        return []


def _save(items):
    fd, tmp = tempfile.mkstemp(dir=QUEUE_PATH.parent, prefix=".queue-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, QUEUE_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _get(item_id):
    with _lock:
        return next((it for it in _load() if it["id"] == item_id), None)


def _update(item_id, **fields):
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == item_id:
                it.update(fields, updated_at=_now())
                _save(items)
                return it
    return None


def _new_item(kind, value, **extra):
    it = {"id": uuid.uuid4().hex[:8], "created_at": _now(), "updated_at": _now(), "kind": kind,
          "input": value, "status": "排队中", "error": "", "page": {}, "jd_text": value if kind == "text" else "",
          "target_job": "", "opts": {}, "analysis": None, "edited": None, "wangshen": None, "record_id": ""}
    it.update(extra)
    return it


def _executor():
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="jobq")
    return _pool


def _submit(item_id):
    _executor().submit(_process_safe, item_id)


def _submit_regen(item_id):
    _executor().submit(_regen_safe, item_id)


# ── 对外接口 ───────────────────────────────────────────────

def split_input(text):
    """每行都是链接 → 每个链接一条；从微信「复制链接」或分享出来的「标题 + 链接」也按链接处理；
    否则整段当作一份 JD。"""
    text = (text or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and all(URL_RE.match(l) for l in lines):
        return [("url", l) for l in dict.fromkeys(lines)]
    urls = fetch.share_links(text)
    if urls:
        return [("url", u) for u in urls]   # 只有链接 + 短标题，不是 JD 正文
    return [("text", text)] if len(text) >= 50 else []


def clean_opts(opts):
    """「这批的设置」：只留认得的值；没指定的不存（= 按默认 / 按 JD 判断）。"""
    o = opts if isinstance(opts, dict) else {}
    allowed = {"position_hint": [p for p in llm.POSITION_TYPES if p != "不明确"], "resume_hint": llm.RESUME_VERSIONS,
               "report_hint": llm.REPORT_HINTS}
    out = {k: o[k] for k, vals in allowed.items() if o.get(k) in vals}
    extra = o.get("extra")
    if isinstance(extra, str) and extra.strip():
        out["extra"] = extra.strip()[:500]
    return out


def enqueue(text, opts=None):
    opts = clean_opts(opts)
    parts = split_input(text)
    if not parts:
        raise ValueError("没识别到内容：贴链接（每行一个）或至少 50 字的 JD 文字。")
    with _lock:
        items = _load()
        known = {it["input"] for it in items if it["kind"] == "url" and it["status"] not in DONE}
        new = [_new_item(k, v, opts=dict(opts)) for k, v in parts if not (k == "url" and v in known)]
        items.extend(new)
        _save(items)
    for it in new:
        _submit(it["id"])
    return new


def list_items():
    with _lock:
        return _load()


def counts(items=None):
    items = list_items() if items is None else items
    out = {}
    for it in items:
        out[it["status"]] = out.get(it["status"], 0) + 1
    return out


def save_edits(item_id, edited=None, *, issues=None, wangshen=None, rev=None):
    """审核时的改动（都可选）：改过的邮件（收件人统一成列表，防止再打开时出错）、最新检查结果、改过的网申问答。
    只有待审核 / 需处理的能改；rev 对不上（条目已经重做过）的旧页面不能改。"""
    fields = {}
    if edited is not None:
        edited = dict(edited)
        for k in ("to_emails", "cc_emails"):
            edited[k] = checks.split_emails(edited.get(k))
        fields["edited"] = edited
    if isinstance(issues, list):
        fields["live_issues"] = [i for i in issues if isinstance(i, dict)]
    if isinstance(wangshen, dict):
        fields["wangshen"] = wangshen
    if not fields:
        return False
    with _lock:
        it = _get(item_id)
        if not it or (rev and it.get("rev") and rev != it["rev"]):
            return False
        if it["status"] not in REVIEWABLE:
            # 「邮箱+网申」邮件已发、网申还没补记：只允许改网申问答
            if not (set(fields) == {"wangshen"} and _awaiting_ws(it)):
                return False
        return _update(item_id, **fields) is not None


def _awaiting_ws(it):
    r = it.get("edited") or (it.get("analysis") or {}).get("result") or {}
    return it["status"] in ("已发送", "已存草稿", SCHEDULED) and r.get("apply_channel") == "邮箱+网申" and not it.get("ws_recorded")


def save_wangshen(item_id, kit):
    with _lock:
        it = _get(item_id)
        return bool(it) and _update(item_id, wangshen=kit) is not None


def mark_uncertain(item_id, error):
    """发送结果不明（可能已经发出）：不退回待审核，标成需处理 + send_uncertain（重做也不清掉），
    一键处理永远跳过它，面板发送前会先让你去 Gmail 确认。"""
    with _lock:
        it = _get(item_id)
        if it and it["status"] == SENDING:
            _update(item_id, status="需处理", error=error, send_uncertain=True)


def mark_ws_recorded(item_id):
    """「邮箱+网申」：邮件发完以后又补记了网申。"""
    return _update(item_id, ws_recorded=True) is not None


def claim(item_id, allowed=REVIEWABLE):
    """发送前认领：把条目标成「发送中」，同一条不会被两个请求 / 一键发送同时发。成功返回条目，否则 None。"""
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == item_id:
                if it["status"] not in allowed:
                    return None
                it.update(prev_status=it["status"], status=SENDING, updated_at=_now())
                _save(items)
                return it
    return None


def release(item_id, error=""):
    """发送没成功：退回发送前的状态。"""
    with _lock:
        it = _get(item_id)
        if it and it["status"] == SENDING:
            _update(item_id, status=it.get("prev_status") or "待审核", error=error)


def refresh_issues():
    """面板启动时：待审核 / 需处理的重新跑一遍发信前检查（不调 AI）。规则改过以后，旧条目上过期的提示自动消失。"""
    n = 0
    for it in list_items():
        if it["status"] not in REVIEWABLE or not it.get("analysis"):
            continue
        result, page = it.get("edited") or it["analysis"]["result"], it.get("page") or {}
        try:
            rev = pipeline.review(pipeline._normalize_edits(dict(result)), it.get("jd_text", ""),
                                  source_label=page.get("source_label", ""))
        except Exception:
            continue
        with _lock:
            cur = _get(it["id"])
            if cur and cur["status"] in REVIEWABLE and cur.get("rev") == it.get("rev"):
                _update(it["id"], live_issues=rev["issues"])
                n += 1
    return n


def skip(item_id):
    """「跳过，看下一个」：这条排到待看的最后（记下跳过的时间，面板按它排队）。"""
    with _lock:
        it = _get(item_id)
        return bool(it) and it["status"] in REVIEWABLE and _update(item_id, skipped_at=_now()) is not None


def retry(item_id):
    """重做：只接受失败 / 需处理 / 待审核的（排队中、处理中的再点一次会被处理两遍）。"""
    with _lock:
        cur = _get(item_id)
        if not cur or cur["status"] not in ("失败",) + REVIEWABLE:
            return False
        it = _update(item_id, status="排队中", error="", analysis=None, edited=None, wangshen=None, live_issues=None,
                     rev="")
    if it:
        _submit(item_id)
    return it is not None


def delete(item_id):
    with _lock:
        items = _load()
        kept = [it for it in items if it["id"] != item_id or it["status"] == SENDING]  # 正在发的不能删
        _save(kept)
        return len(kept) < len(items)


def clear_done():
    with _lock:
        items = _load()
        kept = [it for it in items if it["status"] not in DONE]
        _save(kept)
        return len(items) - len(kept)


def mark_done(item_id, status, record_id=""):
    return _update(item_id, status=status, record_id=record_id) is not None


def resume_pending():
    """面板启动时：上次没做完的接着做；发送到一半被打断的，标成需处理，请人去 Gmail 确认。"""
    with _lock:
        items = _load()
        todo = [it["id"] for it in items if it["status"] in ACTIVE]
        regens = [it["id"] for it in items if it["status"] == REGEN]   # 重写到一半关了面板：接着重写
        for it in items:
            if it["status"] == "处理中":
                it["status"] = "排队中"
            elif it["status"] == SENDING:
                it.update(status="需处理", send_uncertain=True,
                          error="上次发送时面板被关掉了：请先到 Gmail「已发送 / 草稿」确认这封有没有发出去，"
                                "没发出去再点发送，发出去了就直接删掉这条。")
        _save(items)
    for item_id in todo:
        _submit(item_id)
    for item_id in regens:
        _submit_regen(item_id)
    return len(todo) + len(regens)


def regen(item_id, *, jd_text="", target_job=None, opts=None):
    """按补充要求重写：交给后台（不用在页面上等，可以先看下一封），写好替换这一条的邮件。
    opts：岗位类型 / 简历版本 / 研究样本 / 补充要求（和「这批的设置」一样）。"""
    with _lock:
        it = _get(item_id)
        if not it or it["status"] not in REVIEWABLE:
            return None
        fields = dict(status=REGEN, regen_prev=it["status"], regen_req=clean_opts(opts), error="")
        if isinstance(jd_text, str) and len(jd_text.strip()) >= 50:
            fields["jd_text"] = jd_text   # 审核时改过 JD 原文：按改过的写
        if isinstance(target_job, str):
            fields["target_job"] = target_job.strip()
        it = _update(item_id, **fields)
    _submit_regen(item_id)
    return it


def _regen_fail(item_id, error):
    """重写没成功：原来那封留着不动，退回原来的状态并写上原因。"""
    with _lock:
        it = _get(item_id)
        if it and it["status"] == REGEN:
            _update(item_id, status=it.get("regen_prev") or "待审核", error=error)


def _regen_safe(item_id):
    try:
        _regen(item_id)
    except Exception as e:
        _regen_fail(item_id, f"重写失败：{type(e).__name__}: {e}")


def _regen(item_id):
    it = _get(item_id)
    if not it or it["status"] != REGEN:
        return
    page = it.get("page") or {}
    out = pipeline.analyze(it["jd_text"], source_label=page.get("source_label", ""), target_job=it.get("target_job", ""),
                           **{k: v for k, v in (it.get("regen_req") or {}).items() if k in OPT_KEYS})
    if not out.get("ok"):
        _regen_fail(item_id, "重写失败：" + (out.get("error") or "AI 没写出来"))
        return
    kit = it.get("wangshen")
    if pipeline.is_wangshen(out["result"]) and not (kit and not kit.get("error")):  # 改成网申了、还没问答：顺手写
        try:
            kit, _ = pipeline.wangshen(it["jd_text"], result=out["result"], source_label=page.get("source_label", ""),
                                       target_job=it.get("target_job", ""))
        except llm.LLMError as e:
            kit = {"error": str(e)}
    ws_only = not checks.split_emails(out["result"].get("to_emails"))
    has_error = not ws_only and any(i["level"] == "error" for i in out["issues"])
    with _lock:
        cur = _get(item_id)
        if not cur or cur["status"] != REGEN:   # 期间被删了：不管
            return
        _update(item_id, analysis=out, edited=None, live_issues=None, wangshen=kit, error="",
                status="需处理" if has_error else "待审核", rev=uuid.uuid4().hex[:8])


# ── 后台处理 ───────────────────────────────────────────────

def _process_safe(item_id):
    try:
        _process(item_id)
    except Exception as e:  # 单条失败不影响其他条
        _update(item_id, status="失败", error=f"{type(e).__name__}: {e}")
    finally:
        _maybe_notify()


def _process(item_id):
    it = _update(item_id, status="处理中", error="")
    if not it:
        return  # 已被删除
    page, jd, target = it.get("page") or {}, it.get("jd_text") or "", it.get("target_job") or ""
    if it["kind"] == "url" and not jd:
        try:
            p = fetch.fetch_url(it["input"])
        except fetch.FetchError as e:
            _update(item_id, status="失败", error=str(e))
            return
        page = {k: p.get(k, "") for k in ("title", "source_label", "publish_date", "url", "ocr_used", "qr_urls")}
        jd = p["content"]
        n_multi, n_emails = fetch.count_job_signals(jd)
        if n_multi >= 2 or n_emails >= 2:
            jobs = llm.detect_jobs(jd)
            if len(jobs) > 1:
                titles = [j["title"] for j in jobs]
                # 同名岗位（不同城市 / 不同机构）带上地点，AI 才分得清是哪一个
                names = [f"{j['title']}（{j['location']}）" if titles.count(j["title"]) > 1 and j.get("location")
                         else j["title"] for j in jobs]
                target = names[0]
                multi = len(names)   # 同一篇拆出来的：一键处理不自动发，要你挑投哪个
                siblings = [_new_item("text", f"{page.get('title') or it['input']}｜{n}",
                                      jd_text=jd, target_job=n, page=page, opts=dict(it.get("opts") or {}), multi_job=multi)
                            for n in names[1:]]
                _update(item_id, multi_job=multi)
                with _lock:
                    items = _load()
                    items.extend(siblings)
                    _save(items)
                for s in siblings:
                    _submit(s["id"])
        _update(item_id, page=page, jd_text=jd, target_job=target)
    opts = {k: v for k, v in (it.get("opts") or {}).items() if k in OPT_KEYS}
    out = pipeline.analyze(jd, source_label=page.get("source_label", ""), target_job=target, **opts)
    if not out.get("ok"):
        _update(item_id, status="失败", error=out.get("error", "不是招聘信息"))
        return
    kit = None
    if pipeline.is_wangshen(out["result"]):
        try:
            kit, _ = pipeline.wangshen(jd, result=out["result"], source_label=page.get("source_label", ""),
                                       target_job=target)
        except llm.LLMError as e:
            kit = {"error": str(e)}
    # 只网申（没有收件邮箱）的岗位不发邮件：邮件那边的问题不算「需处理」
    ws_only = not checks.split_emails(out["result"].get("to_emails"))
    has_error = not ws_only and any(i["level"] == "error" for i in out["issues"])
    _update(item_id, analysis=out, wangshen=kit, status="需处理" if has_error else "待审核", rev=uuid.uuid4().hex[:8])


def _notify(title, text):
    subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}" sound name "Glass"'],
                   capture_output=True)


def _maybe_notify():
    """一批全部做完时发一个 Mac 系统通知。"""
    global _notified_sig
    with _lock:
        items = _load()
        if any(it["status"] in ACTIVE for it in items):
            return
        c = counts(items)
        sig = tuple(sorted((it["id"], it["status"]) for it in items if it["status"] not in DONE))
        if not sig or sig == _notified_sig:
            return
        _notified_sig = sig
    text = f"待审核 {c.get('待审核', 0)} 个，需处理 {c.get('需处理', 0)} 个" + (f"，失败 {c['失败']} 个" if c.get("失败") else "")
    _notify("投递队列处理完了", text)


# ── 定时发送（Gmail 接口没有「定时发送」，到点由面板自己发：面板要开着、电脑不能睡着）──────

def next_send_time(now=None):
    """下一个 config.SEND_AT（北京时间），格式 YYYY-MM-DD HH:MM。晚上 11 点点的 → 第二天 10:00；凌晨 1 点点的 → 当天 10:00。"""
    now = now or checks.beijing_now()
    h, m = (int(x) for x in config.SEND_AT.split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at.strftime("%Y-%m-%d %H:%M")


def schedule(item_id, result, *, force=False, send_at=None, allowed=REVIEWABLE):
    """把审核确认过的这封存好，到点原样发出。force：确认框里你已经点过「我确认没问题」。成功返回条目。"""
    edited = dict(result)
    for k in ("to_emails", "cc_emails"):
        edited[k] = checks.split_emails(edited.get(k))
    with _lock:
        it = _get(item_id)
        if not it or it["status"] not in allowed:
            return None
        prev = it.get("prev_status") if it["status"] == SENDING else it["status"]
        return _update(item_id, status=SCHEDULED, edited=edited, send_at=send_at or next_send_time(),
                       send_force=force is True, sched_prev=prev or "待审核", error="")


def schedule_draft(item_id, send_at=None):
    """存过的草稿：到点从 Gmail 原样发出（只认有记录的「已存草稿」）。"""
    with _lock:
        it = _get(item_id)
        if not it or it["status"] != "已存草稿" or not it.get("record_id"):
            return None
        return _update(item_id, status=SCHEDULED, send_draft=True, send_at=send_at or next_send_time(),
                       sched_prev="已存草稿", error="")


def unschedule(item_id):
    """取消定时：退回原来的状态（待审核 / 需处理），可以再改再发。"""
    with _lock:
        it = _get(item_id)
        if not it or it["status"] != SCHEDULED:
            return False
        return _update(item_id, status=it.get("sched_prev") or "待审核", send_at="", send_force=False,
                       send_draft=False) is not None


def _fail_scheduled(item_id, error, **extra):
    """定时没发成：要你处理。草稿那种退回「已存草稿」（草稿还在 Gmail 里，别在面板里再发一封新的）。"""
    with _lock:
        it = _get(item_id)
        if it and it["status"] == SENDING:
            if it.get("send_draft"):
                _update(item_id, status="已存草稿", error=error + "（草稿还在 Gmail 草稿箱里，可以自己去 Gmail 发）", send_draft=False)
            else:
                _update(item_id, status="需处理", error=error, **extra)


def send_due(now=None, gap=SEND_GAP_SECONDS):
    """到点的定时邮件发出去。到点时正赶上晚上（比如电脑一整天没开，晚上才打开面板）就顺延到下一个发送时间。"""
    now = now or checks.beijing_now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    sent, failed = [], []
    for snap in [x for x in list_items() if x["status"] == SCHEDULED and (x.get("send_at") or "") <= stamp]:
        if checks.is_night(now):
            _update(snap["id"], send_at=next_send_time(now))
            continue
        it = claim(snap["id"], allowed=(SCHEDULED,))
        if not it:
            continue
        if (sent or failed) and gap:
            time.sleep(gap)
        result, page = it.get("edited") or it["analysis"]["result"], it.get("page") or {}
        try:
            if it.get("send_draft"):   # 存过的草稿：从 Gmail 原样发出
                out = pipeline.send_saved_draft(it["record_id"])
            else:
                out = pipeline.deliver(result, it["jd_text"], mode="send", force=it.get("send_force") is True,
                                       source_label=page.get("source_label", ""), source_url=page.get("url", ""),
                                       target_job=it.get("target_job", ""), publish_date=page.get("publish_date", ""),
                                       source_type="定时发送")
        except gmail_client.SendUncertain as e:  # 可能已经发出：不再自动发，请人去 Gmail 确认
            _fail_scheduled(it["id"], f"定时发送结果不确定（{e}），可能已经发出：先去 Gmail「已发送」确认", send_uncertain=True)
            failed.append(_label(it))
            continue
        except gmail_client.GmailAuthError:
            _fail_scheduled(it["id"], "定时发送没发出：Gmail 授权过期了。重新授权后打开这封再点发送")
            failed.append(_label(it))
            continue
        except pipeline.Blocked as e:
            _fail_scheduled(it["id"], f"定时发送没发出：发之前检查出必须处理的问题（{e}）")
            failed.append(_label(it))
            continue
        except Exception as e:
            _fail_scheduled(it["id"], f"定时发送没发出：{e}")
            failed.append(_label(it))
            continue
        mark_done(it["id"], "已发送", out.get("record_id", ""))
        sent.append(_label(it))
    if sent or failed:
        _notify("定时发送", f"已发出 {len(sent)} 封" + (f"，{len(failed)} 封没发出，打开面板看看" if failed else ""))
    return {"sent": sent, "failed": failed}


_caffeinate = None


def _keep_awake(on):
    """有排着的定时邮件时不让 Mac 自己睡着（合上盖子还是会睡）。"""
    global _caffeinate
    if on and (_caffeinate is None or _caffeinate.poll() is not None):
        _caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    elif not on and _caffeinate is not None and _caffeinate.poll() is None:
        _caffeinate.terminate()
        _caffeinate = None


_scheduler_started = False


def start_scheduler(interval=30):
    """面板启动时开一个后台线程：每 30 秒看一次有没有到点的定时邮件。"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        while True:
            try:
                send_due()
                _keep_awake(any(it["status"] == SCHEDULED for it in list_items()))
            except Exception as e:  # 出错也别让线程死掉，下一轮接着看
                print(f"定时发送出错：{type(e).__name__}: {e}")
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True, name="jobq-scheduler").start()


# ── 一键处理就绪的 ─────────────────────────────────────────

class Busy(Exception):
    pass


def process_ready(mode="send", gap=SEND_GAP_SECONDS):
    """检查全过（没有「必须处理」也没有「注意」）的条目，直接发送、存草稿或排到明早定时发（mode=schedule）；其余跳过、留给人工。
    同一时间只允许跑一个；每条先认领再发，发送之间间隔几秒。Gmail 授权失效时整批停下。"""
    if not _ready_lock.acquire(blocking=False):
        raise Busy("上一次「一键处理」还没跑完，等它结束再点。")
    try:
        return _process_ready(mode, gap)
    finally:
        _ready_lock.release()


def _process_ready(mode, gap):
    sent, skipped, first = [], [], True
    for snap in [x for x in list_items() if x["status"] == "待审核"]:
        if not _send_ok(snap, skipped):
            continue
        it = claim(snap["id"], allowed=("待审核",))  # 认领后拿到的是最新内容（处理过程中用户可能刚改过）
        if not it:
            skipped.append(f"{_label(snap)}：正在别处发送或已经处理过")
            continue
        if not _send_ok(it, skipped):
            release(it["id"])
            continue
        result, page = it.get("edited") or it["analysis"]["result"], it.get("page") or {}
        rev = pipeline.review(pipeline._normalize_edits(dict(result)), it["jd_text"],
                              source_label=page.get("source_label", ""))
        blocking = [i["msg"] for i in rev["issues"] if i["level"] in ("error", "warn")]
        if blocking:
            release(it["id"])
            skipped.append(f"{_label(it)}：{blocking[0]}")
            continue
        if mode == "schedule":  # 不发，原样存好排到明早
            schedule(it["id"], result, allowed=(SENDING,))
            sent.append(_label(it))
            continue
        if not first and gap and mode == "send":
            time.sleep(gap)
        first = False
        try:
            out = pipeline.deliver(result, it["jd_text"], mode=mode, source_label=page.get("source_label", ""),
                                   source_url=page.get("url", ""), target_job=it.get("target_job", ""),
                                   publish_date=page.get("publish_date", ""), source_type="批量队列")
        except gmail_client.GmailAuthError:
            release(it["id"])
            raise
        except gmail_client.SendUncertain as e:  # 可能已经发出：不退回待审核，免得下次再发一遍
            mark_uncertain(it["id"], str(e))
            skipped.append(f"{_label(it)}：{e}")
            continue
        except Exception as e:
            release(it["id"], error=f"发送失败：{e}")
            skipped.append(f"{_label(it)}：发送失败（{e}）")
            continue
        mark_done(it["id"], "已存草稿" if out["mode"] == "草稿" else "已发送", out.get("record_id", ""))
        sent.append(_label(it) + (f"（{out['record_error']}）" if out.get("record_error") else ""))
    return {"done": sent, "skipped": skipped}


def _label(it):
    r = it.get("edited") or (it.get("analysis") or {}).get("result") or {}
    return f"{r.get('company_name', '')}｜{r.get('job_title', '')}"


def _send_ok(it, skipped):
    """一键处理只发「纯邮件」岗位：网申、邮箱+网申、上次结果不确定的留给人工。"""
    if it.get("send_uncertain"):
        skipped.append(f"{_label(it)}：上次发送结果不确定，可能已经发出，需要你先去 Gmail 确认")
        return False
    result = it.get("edited") or it["analysis"]["result"]
    if not checks.split_emails(result.get("to_emails")):
        skipped.append(f"{_label(it)}：网申岗位，需要手动投")
        return False
    if result.get("apply_channel") == "邮箱+网申":
        skipped.append(f"{_label(it)}：除了发邮件还要网申，需要手动处理")
        return False
    if it.get("multi_job"):
        skipped.append(f"{_label(it)}：同一篇文章拆出了 {it['multi_job']} 个岗位，挑想投的在审核里发，其余删掉")
        return False
    return True
