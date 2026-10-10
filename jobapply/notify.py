"""通知的唯一出口：Mac 系统通知 + 页面通知（前端轮询取）。
- 标题带颜色和公司（给了申请 id 就自动加，比如「蓝·某银行」）；
- 同一申请同一类事 10 分钟内不重复；
- 夜里（本机 00:30–07:30）不弹 Mac 通知，3 小时内到期的除外；页面通知照记；
- 测试环境不弹 Mac 通知。"""

import itertools
import subprocess
import threading
from collections import deque
from datetime import datetime, timedelta

from . import config

DEDUP_MINUTES = 10
QUIET = ((0, 30), (7, 30))      # 本机时间
_recent = {}                     # (申请 id, 类) → 上次发的时间
_lock = threading.Lock()
_seq = itertools.count(1)
EVENTS = deque(maxlen=200)       # 页面通知：{id, at, app_id, kind, title, text}


def _quiet(now):
    (h1, m1), (h2, m2) = QUIET
    t = (now.hour, now.minute)
    return (h1, m1) <= t < (h2, m2)


def _due_soon(due, now, hours=3):
    if not due:
        return False
    if isinstance(due, str):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                due = datetime.strptime(due[:16], fmt)
                break
            except ValueError:
                continue
        else:
            return False
    return due - now <= timedelta(hours=hours)


def _label(app_id):
    if not app_id:
        return ""
    try:
        from . import apps
        a = apps.get(app_id)
        return f"{apps.color_label(a, apps.list_apps())}·{a.get('company') or '这家'}"
    except Exception:
        return ""


def _mac(title, text):
    if config.IS_TEST_ENV:
        return
    t = (text or "").replace('"', "'").replace("\\", "")
    tt = (title or "").replace('"', "'").replace("\\", "")
    subprocess.run(["osascript", "-e", f'display notification "{t}" with title "{tt}" sound name "Glass"'], capture_output=True)


def send(title, text, *, app_id="", kind="", due=None, now=None):
    """发一条通知。返回 True = 弹了 Mac 通知；去重或夜里静默返回 False（页面通知照记，去重的除外）。"""
    now = now or datetime.now()
    key = (app_id or "", kind or title)
    with _lock:
        last = _recent.get(key)
        if app_id and kind and last and now - last < timedelta(minutes=DEDUP_MINUTES):
            return False
        _recent[key] = now
    label = _label(app_id)
    full = f"{label}｜{title}" if label else title
    EVENTS.append({"id": next(_seq), "at": now.strftime("%Y-%m-%d %H:%M:%S"), "app_id": app_id, "kind": kind,
                   "title": full, "text": text})
    if _quiet(now) and not _due_soon(due, now):
        return False
    _mac(full, (text or "")[:120])
    return True


def events_since(seq=0):
    return [e for e in list(EVENTS) if e["id"] > seq]
