"""面板里的助手：在面板侧边跟它聊天。它能操作你的 Chrome（Claude in Chrome）代填网申，也能回答投递相关的问题。

一个对话开一个常驻的 `claude -p --input-format stream-json`（本机 Claude Code，会员额度，不扣 API 余额）：
它干活的时候你说的话直接写进去，它在下一步就能看到（不用等它做完）。闲着 15 分钟自动关掉，再说话用 --resume 接上。
只给它浏览器工具：--tools "" 关掉读写文件、执行命令这些内置工具，--strict-mcp-config 不加载 Gmail 等连接器，
--allowedTools 只放行 Claude in Chrome。所以它发不了邮件、改不了面板数据、碰不到电脑上的文件；
它看到的资料（网申底稿、简历内容、档案、投递记录摘要、网站笔记）都是面板每一轮放进系统提示里给它的。"""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlparse

from . import agent_runner, checks, config, records, uploads, wsprofile, wstasks
from .llm import _claude_bin

CHATS_DIR = config.DATA_DIR / "agent_chats"
SITES_PATH = config.DATA_DIR / "wangshen_sites.json"
RULES_PATH = config.BASE_DIR / "wangshen_rules.md"
NOTIFY_AFTER_SECONDS = 60           # 做了一分钟以上的，做完弹 Mac 通知
# 进程寿命跟着申请走（进程一关它开的网页就不归它管了，银行这类网站得重新登录）：
TURN_QUIET_WARN = 60 * 60           # 一轮 60 分钟没有新动静：提醒一下（不再 45 分钟强杀）
TURN_MAX = 3 * 3600                 # 一轮做满 3 小时：自动中断这一轮（不杀进程）
DONE_IDLE_CLOSE = 10 * 60           # 做完了的（已提交且读回完、不投了、问答对话）：闲 10 分钟关进程
IDLE_CLOSE_MAX = 12 * 3600          # 不管做没做完：12 小时没有动静就关
STOP_GRACE = 10                     # 点「停下」后等它停的秒数，超过才整个结束进程
_clock = time.time                  # 测试里换成假时钟
SITE_NOTE = re.compile(r"【网站笔记】\s*([^\s：:]+)\s*[：:]\s*(.+)")
# 网站连不上面板（有的银行网站只许跟自己服务器通信）时，助手把读到的原文按这个格式写在回复里，面板摘出来记进看板
READBACK_BLOCK = re.compile(r"【网申读回】([^\n]*)\n([\s\S]*?)【/网申读回】")
PREFIX = "mcp__claude-in-chrome__"

_lock = threading.RLock()
_runners = {}                        # chat_id → 常驻的 claude 进程（_Runner）
_starting = set()                    # 进程正在起来的对话
_pending = {}                        # 进程还没起来时又说的话：起来后按顺序递进去
_waiting = []                        # 排队的话 [对话 id, 话, 原因, 时间]：同时干活的满了、或者同一个网站有别的助手在填，空出来按先后开始（落盘到 _run/queue.json）
_after_turn = {}                     # 对话 id → [网申待办 id]：这一轮做完让它去网站读回（跟它说「交了」时）


class Busy(Exception):
    pass


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _path(chat_id):
    if not re.fullmatch(r"[0-9a-f]{8,32}", chat_id or ""):
        raise KeyError(chat_id)
    return CHATS_DIR / f"{chat_id}.json"


def _load(chat_id):
    p = _path(chat_id)
    if not p.exists():
        raise KeyError(chat_id)
    return json.loads(p.read_text(encoding="utf-8"))


def _save(chat):
    CHATS_DIR.mkdir(exist_ok=True)
    chat["updated_at"] = _now()
    fd, tmp = tempfile.mkstemp(dir=CHATS_DIR, prefix=".chat-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(chat, f, ensure_ascii=False)
        os.replace(tmp, _path(chat["id"]))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _append(chat_id, role, text, **extra):
    with _lock:
        chat = _load(chat_id)
        chat["messages"].append({"role": role, "text": text, "at": _now(), **extra})
        _save(chat)
        return chat


def _set_running(chat_id, on):
    with _lock:
        try:
            chat = _load(chat_id)
        except KeyError:
            return
        if chat.get("running") != on:
            chat["running"] = on
            _save(chat)


# ── 对话 ─────────────────────────────────────────────────────

def new_chat(task_id="", title=""):
    chat = {"id": uuid.uuid4().hex[:12], "title": title, "created_at": _now(), "updated_at": _now(),
            "session_id": "", "running": False, "messages": [], "cost_usd": 0.0, "task_id": task_id or ""}
    with _lock:
        _save(chat)
    return chat


def list_chats(include_archived=False):
    """对话列表。收起来的（同一家网申多出来的旧对话、测试对话）默认不列，记录还在。"""
    out = []
    for p in CHATS_DIR.glob("*.json") if CHATS_DIR.exists() else []:
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if c.get("archived") and not include_archived:
            continue
        out.append({"id": c["id"], "title": c.get("title") or "新对话", "updated_at": c.get("updated_at", ""),
                    "running": c.get("running", False), "n": len(c.get("messages", [])), "task_id": c.get("task_id", "")})
    return sorted(out, key=lambda c: c["updated_at"], reverse=True)


def archive(chat_id):
    """收起一个对话（不删记录，只是不在列表里显示）。正在干活的不收。"""
    with _lock:
        if chat_id in _active():
            return False
        chat = _load(chat_id)
        chat["archived"] = True
        _save(chat)
    return True


def tidy_chats():
    """一家网申只留一个对话：同一家多出来的（以前每点一次「让助手填」就开一个）收起来，留待办上绑着的那个。"""
    bound = {t["id"]: t.get("chat_id") for t in wstasks.list_tasks()}
    n = 0
    for c in list_chats():
        tid = c.get("task_id")
        if tid and bound.get(tid) and bound[tid] != c["id"] and archive(c["id"]):
            n += 1
    return n


def get(chat_id, since=0):
    chat = _load(chat_id)
    msgs = chat.get("messages", [])
    return {**{k: v for k, v in chat.items() if k != "messages"}, "total": len(msgs), "messages": msgs[max(0, since):]}


def _active():
    """正在干活的对话（一轮还没做完，或者进程正在起来）。可以同时好几个：各开各的标签页（Chrome 里互不干扰，实测过）。"""
    with _lock:
        return {cid for cid, r in _runners.items() if r.turn} | set(_starting)


def running_chat():
    return next(iter(sorted(_active())), None)


def active_chats():
    return sorted(_active())


def waiting_chats():
    """排着队的对话 → 为什么在排。"""
    with _lock:
        out = {}
        for w in _waiting:
            out.setdefault(w[0], w[2])
        return out


def state_by_app():
    """申请（id 就是网申待办的 id）→ 它的助手现在怎样：working（这一轮在做）、queued_why（排着队，为什么排）、
    alive（进程还在不在）、chat_id、last（在做的话，最新一步）。看板算「轮到谁」用；同一家有几个对话时，在做 / 排队的那个优先。"""
    act, waiting = _active(), waiting_chats()
    with _lock:
        alive = {cid for cid, r in _runners.items() if not r.closed and r.runner.alive()}
    out = {}
    for c in list_chats():                 # 新的在前
        tid = c.get("task_id")
        if not tid:
            continue
        st = {"chat_id": c["id"], "working": c["id"] in act, "queued_why": waiting.get(c["id"], ""), "alive": c["id"] in alive}
        cur = out.get(tid)
        if cur is None or (not (cur["working"] or cur["queued_why"]) and (st["working"] or st["queued_why"])):
            out[tid] = st
    for st in out.values():
        if st["working"]:
            st["last"] = last_line(st["chat_id"])
    return out


def _site_of(chat_id):
    """这个对话在填哪个网站（看它绑定的网申待办的链接）。"""
    try:
        tid = _load(chat_id).get("task_id")
        url = wstasks.get(tid).get("url", "") if tid else ""
    except (KeyError, wstasks.NotFound):
        return ""
    return (urlparse(url).hostname or "").removeprefix("www.")


def _blocked(chat_id):
    """现在能不能开始干活：能就返回空串，不能就返回原因。同时干活的有上限；同一个网站同一时间只让一个助手填（同一个账号两边一起改会打架）。"""
    act = _active() - {chat_id}
    if len(act) >= config.AGENT_MAX_PARALLEL:
        return f"已经有 {len(act)} 个助手在同时干活（最多 {config.AGENT_MAX_PARALLEL} 个），空出一个就轮到它"
    site = _site_of(chat_id)
    if site and any(_site_of(c) == site for c in act):
        return f"另一个助手正在填同一个网站（{site}），那边做完就轮到它"
    r = _runners.get(chat_id)
    if not (r and not r.closed and r.runner.alive()):   # 要新起一个进程：活着的进程有上限（每个都占内存）
        live = [c for c, x in _runners.items() if c != chat_id and not x.closed and x.runner.alive()]
        if len(live) >= config.AGENT_MAX_LIVE and not any(_done(c) for c in live):
            return f"活着的助手已经有 {len(live)} 个、都还没做完，先在对话框里把用不着的那家停下收起，空出来就轮到它"
    return ""


def delete(chat_id):
    with _lock:
        if chat_id in _active():
            raise Busy("这个对话还在进行，先点「停止」")
        _waiting[:] = [w for w in _waiting if w[0] != chat_id]
        _save_queue()
        r = _runners.get(chat_id)
        if r:
            r.close_stdin()
        _path(chat_id).unlink(missing_ok=True)


class _Runner:
    """一个对话的 claude 进程（agent_runner：默认交给宿主托管，面板重启不断）。干活时写进去的话它下一步就能看到。"""

    def __init__(self, chat_id, runner):
        self.chat_id, self.runner = chat_id, runner
        self.turn = False            # 一轮正在做
        self.turn_started = 0.0
        self.last_event = _clock()   # 最近一次收到它的输出（判断「60 分钟没进展」、闲了多久）
        self.got_result = False
        self.closed = False          # 已经让它收尾了（做完手头的就退出）
        self.warned = False          # 这一轮「60 分钟没进展」提醒过了
        self.auto_stopped = False    # 这一轮做满 3 小时、自动中断过了
        self.saw_output = False      # 这个进程说过话（判断「会话接不上」用）
        self.deferred = None         # 可能是「会话接不上」的那条 result：等进程退出、看了错误输出再定

    def write(self, text):
        self.runner.write(text)

    def close_stdin(self):
        with _lock:
            if self.closed:
                return
            self.closed = True
        try:
            self.runner.close()
        except OSError:
            pass

    def begin_turn(self):
        with _lock:
            if self.turn:
                return False
            self.turn, self.turn_started, self.warned, self.auto_stopped = True, _clock(), False, False
            self.last_event = _clock()
        _set_running(self.chat_id, True)
        _task_started(self.chat_id)
        return True

    def end_turn(self):
        with _lock:
            if not self.turn:
                return
            self.turn = False
            took = _clock() - self.turn_started
            try:
                chat = _load(self.chat_id)
            except KeyError:
                return
            stopped = bool(chat.pop("stopped", False))
            chat["running"] = False
            if stopped:
                chat["messages"].append({"role": "system", "at": _now(),
                                         "text": "已停下：这一轮中断了，进程和它开的网页都还在，再说一句就接着做。"})
            _save(chat)
        _task_turn_done(self.chat_id, stopped=stopped)
        with _lock:
            todo = [] if stopped else _after_turn.pop(self.chat_id, [])
            _after_turn.pop(self.chat_id, None)
        for tid in todo:
            _later(_readback_safe, tid)
        _later(_drain)
        if took > NOTIFY_AFTER_SECONDS and not stopped:
            last = next((m["text"] for m in reversed(chat["messages"]) if m["role"] == "assistant"), "")
            _notify("面板助手做完了", re.sub(r"\s+", " ", last)[:60] or "去面板看看")


def send(chat_id, text):
    """说一句。它正在做的话直接递进去（下一步就能看到）；没在做就接着这个对话开一轮。
    同时干活的满了、或者同一个网站有别的助手在填：先排队，空出来自动开始（返回里带 queued = 原因）。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("说点什么")
    with _lock:
        chat = _load(chat_id)
        r = _runners.get(chat_id)
        working = bool(r and r.turn and not r.closed and r.runner.alive()) or chat_id in _starting
        if not working:
            why = "前面还有它自己排着的话" if any(w[0] == chat_id for w in _waiting) else _blocked(chat_id)
            if why:
                chat["messages"].append({"role": "user", "text": text, "at": _now(), "queued": True})
                chat["title"] = chat.get("title") or text.splitlines()[0][:24]
                chat["waiting"] = why
                _save(chat)
                _waiting.append([chat_id, text, why, _now()])
                _save_queue()
                return {**get(chat_id), "queued": why}
        chat["messages"].append({"role": "user", "text": text, "at": _now(), **({"midturn": True} if working else {})})
        _save(chat)
        _go(chat_id, text)
    return get(chat_id)


def _go(chat_id, text):
    """（锁内调用）让这个对话干起来：进程正在起来就先记着；进程活着就直接写进去；没有进程就起一个。"""
    chat = _load(chat_id)
    chat["running"] = True
    chat["title"] = chat.get("title") or text.splitlines()[0][:24]
    chat.pop("stopped", None)
    chat.pop("waiting", None)
    _save(chat)
    if chat_id in _starting:              # 进程正在起来：起来后递进去
        _pending.setdefault(chat_id, []).append(text)
        return
    r = _runners.get(chat_id)
    if r and not r.closed and r.runner.alive():
        try:
            r.begin_turn()
            r.write(text)
            return
        except (BrokenPipeError, OSError, ValueError):
            pass                           # 进程刚好退了：下面另起一个
    _make_room(keep=chat_id)
    _starting.add(chat_id)
    threading.Thread(target=_run_safe, args=(chat_id, text), daemon=True).start()


def _done(chat_id):
    """这个对话的事做完了没有：没绑网申的问答对话、不投了、已提交而且读回完了，算做完（进程可以关）。"""
    try:
        tid = _load(chat_id).get("task_id")
    except KeyError:
        return True
    if not tid:
        return True
    try:
        t = wstasks.get(tid)
    except wstasks.NotFound:
        return True
    if t.get("status") == "不投了":
        return True
    return t.get("status") == "已提交" and bool(t.get("readback")) and not t.get("readback_state")


def _make_room(keep):
    """（锁内调用）活着的进程到上限了：先关做完了的那几家（闲着的、最久没动静的先关）。都没做完就不关。"""
    live = [(cid, r) for cid, r in _runners.items() if cid != keep and not r.closed and r.runner.alive()]
    extra = len(live) + 1 - config.AGENT_MAX_LIVE
    if extra <= 0:
        return
    done = sorted(((c, r) for c, r in live if not r.turn and _done(c)), key=lambda cr: cr[1].last_event)
    for _cid, r in done[:extra]:
        r.close_stdin()


def _drain():
    """有空位了：排着的按先后开始（同一个网站有别的助手在填的先跳过，等那边做完）。"""
    with _lock:
        i = 0
        while i < len(_waiting):
            cid = _waiting[i][0]
            if _blocked(cid):
                i += 1
                continue
            texts = [w[1] for w in _waiting if w[0] == cid]
            _waiting[:] = [w for w in _waiting if w[0] != cid]
            _save_queue()
            try:
                chat = _load(cid)
            except KeyError:
                continue
            for m in chat["messages"]:
                m.pop("queued", None)
            _save(chat)
            _go(cid, "\n\n".join(texts))
            i = 0


def _later(fn, *args, delay=1.0):
    t = threading.Timer(delay, fn, args=args)
    t.daemon = True
    t.start()


def stop(chat_id):
    """停下：排着队的取消排队；在做的只中断这一轮（进程和它开的网页都留着，再说一句就接着做）。
    中断后 10 秒还没停下来，才整个结束进程。"""
    with _lock:
        r = _runners.get(chat_id)
        chat = _load(chat_id)
        queued = any(w[0] == chat_id for w in _waiting)
        if queued:                         # 还在排队的：取消排队
            _waiting[:] = [w for w in _waiting if w[0] != chat_id]
            _save_queue()
            chat.pop("waiting", None)
            for m in chat["messages"]:
                if m.pop("queued", None):
                    m["cancelled"] = True
            chat["messages"].append({"role": "system", "text": "排队取消了。", "at": _now()})
            _save(chat)
        if not chat.get("running") and not (r and r.turn) and chat_id not in _starting:
            return queued
        chat["stopped"] = True
        _save(chat)
    if r:
        r.runner.interrupt()
        _later(_force_stop, chat_id, r, delay=STOP_GRACE)
    return True


def _force_stop(chat_id, r):
    """中断了还停不下来：只能整个结束进程。"""
    if r.turn and _runners.get(chat_id) is r and r.runner.alive():
        _append(chat_id, "system", f"中断后 {STOP_GRACE} 秒还没停下来，只能整个停掉（它开的网页可能要重新登录）。", error=True)
        r.runner.kill()


# ── 看着各个进程：没进展提醒、太久自动中断、做完了就关 ─────────────────────

_watchdog = {"on": False}


def _ensure_watchdog():
    with _lock:
        if _watchdog["on"]:
            return
        _watchdog["on"] = True
    _later(_watchdog_run, delay=30)


def _watchdog_run():
    try:
        watchdog_tick()
    except Exception:
        pass
    finally:
        _later(_watchdog_run, delay=30)


def _label(chat_id):
    try:
        t = wstasks.get(_load(chat_id).get("task_id") or "")
        return f"{wstasks.color_of(t)[2]}·{wstasks.short_name(t)}"
    except (KeyError, wstasks.NotFound):
        return "这个对话"


def watchdog_tick(now=None):
    now = _clock() if now is None else now
    with _lock:
        items = list(_runners.items())
    for cid, r in items:
        if r.closed or not r.runner.alive():
            continue
        if r.turn:
            if now - r.last_event > TURN_QUIET_WARN and not r.warned:
                r.warned = True
                _append(cid, "system", "这一轮 60 分钟没有新进展了，去看一眼？", error=True)
                _notify("面板助手", f"{_label(cid)} 60 分钟没有新进展，看一眼？")
            if now - r.turn_started > TURN_MAX and not r.auto_stopped:
                r.auto_stopped = True
                _append(cid, "system", "这一轮做了 3 个小时，自动停下了（只中断这一轮，进程和网页都还在）。", error=True)
                with _lock:
                    try:
                        chat = _load(cid)
                        chat["stopped"] = True
                        _save(chat)
                    except KeyError:
                        pass
                r.runner.interrupt()
        else:
            idle = now - r.last_event
            if idle > IDLE_CLOSE_MAX or (idle > DONE_IDLE_CLOSE and _done(cid)):
                r.close_stdin()


# ── 排队落盘：面板重启后照样接着排 ────────────────────────────

def _run_root():
    return CHATS_DIR / "_run"


def _save_queue():
    """（锁内调用）"""
    p = _run_root() / "queue.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(".queue.json.tmp")
    tmp.write_text(json.dumps(_waiting, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _load_queue():
    try:
        items = json.loads((_run_root() / "queue.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [(list(w) + [""])[:4] for w in items if isinstance(w, list) and len(w) >= 3]


def recover():
    """面板启动时：
    - 宿主还活着的助手进程：重新接上，从上次读到的地方接着读（消息不重不丢）；
    - 宿主已经结束的：把没读完的输出读完，那一轮没做完的说清楚「进程已经结束」；
    - 其余上次没做完就关了面板的对话，标成已停止；
    - 排队从 _run/queue.json 恢复、接着排；不在里面的排队标记清掉并说明；
    - 网申页「助手在填」但没接上进程的，记一句为什么停（网申页显示「助手停了」）；读回中的标成没读回。"""
    attached = _reattach() if config.AGENT_DETACHED else set()
    known = set()
    with _lock:
        for w in _load_queue():
            try:
                _load(w[0])
            except KeyError:
                continue
            if not any(x[0] == w[0] and x[1] == w[1] for x in _waiting):
                _waiting.append(w)
            known.add(w[0])
        _save_queue()
    for c in list_chats(include_archived=True):
        if c["id"] in attached:
            continue
        with _lock:
            try:
                chat = _load(c["id"])
            except KeyError:
                continue
            changed = False
            if chat.get("running") and c["id"] not in _runners:
                chat["running"] = False
                chat["messages"].append({"role": "system", "text": "面板重启过，上一句没做完。要接着做就再说一句。", "at": _now()})
                changed = True
            if c["id"] not in known and (chat.get("waiting") or any(m.get("queued") for m in chat.get("messages") or [])):
                chat.pop("waiting", None)
                for m in chat["messages"]:
                    if m.pop("queued", None):
                        m["cancelled"] = True
                chat["messages"].append({"role": "system", "text": "面板重启前它在排队，点「让助手接着做」重新排。", "at": _now()})
                changed = True
            if changed:
                _save(chat)
    for t in wstasks.list_tasks():
        fields = {}
        if t.get("status") == "助手在填" and t.get("chat_id") not in attached and t.get("chat_id") not in known:
            fields["halted"] = "面板重启把它打断了"
        if t.get("readback_state") in ("读回中", "排队等读回") and t.get("readback_chat") not in attached \
                and t.get("readback_chat") not in known:
            fields["readback_state"] = "没读回（面板重启）"
        if fields:
            wstasks.update(t["id"], **fields)
    if known:
        _later(_drain)


def _reattach():
    """宿主托管的进程：活着的接上（后台线程接着读）；已经结束的把剩下的输出读完、收拾掉。返回接上的对话。"""
    root = _run_root()
    out = set()
    if not root.exists():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        cid = d.name
        hr = agent_runner.HostRunner.attach(d)
        try:
            chat = _load(cid)
        except KeyError:                    # 对话已经删了：进程也结束
            if hr.alive():
                hr.kill()
            else:
                agent_runner.HostRunner.retire(d)
            continue
        r = _Runner(cid, hr)
        r.turn = bool(chat.get("running"))
        r.turn_started = _clock()
        with _lock:
            _runners[cid] = r
        if hr.alive():
            out.add(cid)
            _ensure_watchdog()
            threading.Thread(target=_follow_safe, args=(cid, r), daemon=True).start()
        else:
            _follow_safe(cid, r, dead_note="助手进程已经结束，它开的网页不归它管了；接着填要新开网页，银行这类网站要重新登录。")
    return out


# ── 网申待办 ────────────────────────────────────────────────

def _task_started(chat_id):
    """从某条网申待办开的对话：开始干活时把待办标成「助手在填」（原来是待填、或者在等本人处理的都算）。"""
    try:
        tid = _load(chat_id).get("task_id")
        t = wstasks.get(tid) if tid else None
        if t and t.get("status") in ("待填", "等你处理"):
            wstasks.update(tid, status="助手在填", chat_id=chat_id, halted="")
        elif t and t.get("halted"):
            wstasks.update(tid, halted="")
        for t in wstasks.list_tasks():     # 排着队的读回轮到了
            if t.get("readback_state") == "排队等读回" and t.get("readback_chat") == chat_id:
                wstasks.update(t["id"], readback_state="读回中")
    except (KeyError, wstasks.NotFound):
        pass


def _task_turn_done(chat_id, stopped=False):
    """一轮做完了还停在「助手在填」（它没报状态就停了）：状态不改，记一句为什么停（网申页显示「助手停了」）。
    「要你做」只认助手在【网申记录】里明确写的那句，不拿它最后一句话凑。
    让它去读回、这一轮做完了还没读回来（没登录、找不到页面）：标成「没读回」，网申页上可以再点一次。"""
    try:
        chat = _load(chat_id)
        tid = chat.get("task_id")
        t = wstasks.get(tid) if tid else None
        if t and t.get("status") == "助手在填":
            wstasks.update(tid, halted="本人点了停下" if stopped else "这一轮停下了，没说要你做什么（看对话）")
        for t in wstasks.list_tasks():
            if t.get("readback_state") in ("读回中", "排队等读回") and t.get("readback_chat") == chat_id:
                wstasks.update(t["id"], readback_state="没读回（看对话）")
    except (KeyError, wstasks.NotFound):
        pass


def _brief(text, n=90):
    """一段话 → 一行短句（去掉【网申记录】【网站笔记】这些机读行和加粗符号）。"""
    lines = [l for l in (text or "").splitlines() if l.strip() and not l.strip().startswith("【")]
    s = re.sub(r"\s+", " ", " ".join(lines)).replace("**", "").strip()
    return s[:n] + ("…" if len(s) > n else "")


def last_line(chat_id):
    """这个对话最新一步在干什么（网申页「最新进展」用）：最后一句话，或者最后一步操作。"""
    try:
        msgs = _load(chat_id).get("messages") or []
    except KeyError:
        return ""
    recent = msgs[-12:]   # 最近说过的一句话比「在页面上跑一段脚本」这种操作名有用；最近没说话才用最后一步操作
    m = next((m for m in reversed(recent) if m["role"] == "assistant"), None) or \
        next((m for m in reversed(msgs) if m["role"] in ("assistant", "tool")), None)
    return _brief(m["text"], 60) if m else ""


# ── 读回：本人提交以后，让助手去网站把实际提交的内容、进度、账号读回来记进看板 ─────────

def readback_message(t):
    """本人提交以后：让助手把三样从网站上读回来记进看板——投了哪些岗位、每个岗位的 JD、实际提交的简历。"""
    key = wstasks.readback_key(t["id"])
    call = "await __wsfill.readback({task: '%s', key: '%s', kind: '%s'%s})"
    return (f"（面板）{t.get('company') or '这家'}｜{t.get('job') or '（岗位见网站）'} 本人已经提交了。"
            "请把这三样从网站上读回来记进投递看板，只看不改：\n"
            f"1. 投了哪些岗位：打开「我的投递 / 应聘记录」页（网申链接：{t.get('url') or '见之前的对话'}），"
            f"先加载填表脚本 await (0, eval)(await (await fetch('{config.PANEL_BASE}/wsfill.js')).text())，再执行：\n"
            f"   {call % (t['id'], key, 'status', ", status: '网站上显示的进度，照抄', account: '页面上显示的登录账号，照抄', positions: '岗位名（地点）；岗位名（地点）'")}\n"
            "2. 每个投了的岗位的 JD：逐个打开岗位详情页，各执行一次：\n"
            f"   {call % (t['id'], key, 'jd', ", position: '岗位名，和上面写的一样', location: '地点'")}\n"
            "3. 实际提交的简历：打开能看到提交内容的那一页（我的简历、申请详情、查看简历），执行：\n"
            f"   {call % (t['id'], key, 'resume', '')}\n"
            "填表脚本加载不了、或者 readback 说「连不上面板」（有的银行网站就这样）：用 get_page_text 读页面原文，在回复里按下面的格式**原样**写（不要改写、不要概括），面板会自动摘出来记进看板：\n"
            "【网申读回】类型：投递记录｜岗位：岗位名（地点）；岗位名（地点）｜进度：……｜账号：……\n（投递记录页原文）\n【/网申读回】\n"
            "【网申读回】类型：岗位JD｜岗位：岗位名｜地点：……\n（这个岗位的 JD 原文，每个岗位一段）\n【/网申读回】\n"
            "【网申读回】类型：简历\n（提交的简历原文）\n【/网申读回】\n"
            "最后回我两三句（投了几个岗位、网站上的进度、登录账号），再写一行【网申记录】……｜账号：……｜状态：已提交。"
            "没登录就请我在画了颜色框的网页里登录，然后留在这一轮里等，看到登录好了直接接着读；不要点任何按钮（更新简历、撤回、提交都不要点）。")


def start_readback(task_id):
    """让助手去读回这条待办。返回 (对话, 一句说明)；同时干活的满了就排队，空出来自动开始。"""
    t = wstasks.get(task_id)
    chat_id = t.get("chat_id") or ""
    if chat_id:
        try:
            _load(chat_id)
        except KeyError:
            chat_id = ""
    if not chat_id:
        chat_id = new_chat(task_id=task_id, title="读回：" + wstasks.short_name(t))["id"]
        wstasks.update(task_id, chat_id=chat_id)
    with _lock:   # 排队 / 开始 和 记状态一起做，免得刚排上就轮到、状态被写回「排队」
        out = send(chat_id, readback_message(t))
        queued = out.get("queued")
        wstasks.update(task_id, readback_state="排队等读回" if queued else "读回中", readback_chat=chat_id)
    if queued:
        return out, f"排队中：{queued}；轮到了会自动去网站读回提交的内容"
    return out, "助手去网站读回实际提交的内容了（右下角看进度，不想要可以点停止）"


def _readback_safe(task_id):
    try:
        start_readback(task_id)
    except (wstasks.NotFound, KeyError, ValueError):
        pass


# ── 跑 ──────────────────────────────────────────────────────

def _hook_settings():
    """file_upload 的钩子：只许传「网申上传」文件夹里的文件（jobapply/upload_guard.py，实测在 --setting-sources "" 下照样生效）。"""
    guard = config.BASE_DIR / "jobapply" / "upload_guard.py"
    cmd = " ".join(shlex.quote(str(x)) for x in (sys.executable, guard, uploads.DIR))
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": PREFIX + "file_upload",
                                                 "hooks": [{"type": "command", "command": cmd}]}]}}, ensure_ascii=False)


def _args(chat):
    allow, deny = uploads.read_rules()   # 读文件只许读「网申上传」文件夹（网站要传照片、简历时它自己传）
    args = [_claude_bin(), "-p", "--chrome", "--model", config.AGENT_MODEL, "--effort", config.AGENT_EFFORT,
            "--setting-sources", "", "--strict-mcp-config", "--tools", "Read", "--add-dir", str(uploads.DIR),
            "--allowedTools", "mcp__claude-in-chrome", *allow,
            # 不给它关标签页：组里最后一个标签页关掉整个标签组就没了，再开新组会被安全检查拦下（以前就这样卡住过）
            "--disallowedTools", "mcp__claude-in-chrome__tabs_close_mcp", *deny,
            "--settings", _hook_settings(),
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--append-system-prompt", system_prompt(chat)]
    if chat.get("session_id"):
        args += ["--resume", chat["session_id"]]
    return args


_SCRUB_ENV = ("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_PID", "CLAUDE_JOB_DIR", "CLAUDE_EFFORT", "AI_AGENT")


def _child_env():
    """给助手进程的环境：不带 API Key（只用会员额度）；面板若是从 Claude Code 里起的，不把那边的会话变量带过去；
    关掉 Claude Code 的自动记忆（实测：不关的话，本人全局记忆里跟求职无关的内容会进助手的上下文）。"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") and not k.startswith(_SCRUB_ENV)}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


def _run_safe(chat_id, text):
    r, retry, sent = None, False, [text]
    try:
        r, sent = _spawn(chat_id, text)
        retry = _follow(chat_id, r)
    except Exception as e:  # 起不来 / 读坏了：告诉用户，别让对话卡在「进行中」
        _append(chat_id, "system", f"出错了：{type(e).__name__}: {e}", error=True)
    finally:
        _finish(chat_id, r)
    if retry:
        _restart_fresh(chat_id, "\n\n".join(sent))


def _follow_safe(chat_id, r, dead_note=None):
    try:
        _follow(chat_id, r, dead_note=dead_note)
    except Exception as e:
        _append(chat_id, "system", f"出错了：{type(e).__name__}: {e}", error=True)
    finally:
        _finish(chat_id, r)


def _spawn(chat_id, text):
    """起进程、递第一句（和进程起来前又说的话）。返回 (_Runner, 递进去的话)。"""
    chat = _load(chat_id)
    workdir = CHATS_DIR / "_cwd"          # 空目录：不读任何项目的 CLAUDE.md
    workdir.mkdir(parents=True, exist_ok=True)
    runner = agent_runner.start(_run_root(), chat_id, _args(chat), cwd=workdir, env=_child_env())
    r = _Runner(chat_id, runner)
    with _lock:
        _runners[chat_id] = r
        _starting.discard(chat_id)
        if _load(chat_id).get("stopped"):   # 进程起来之前就点了停止
            runner.kill()
    _ensure_watchdog()
    r.begin_turn()
    sent = [text]
    try:
        r.write(text)
        with _lock:
            for extra in _pending.pop(chat_id, []):
                r.write(extra)
                sent.append(extra)
    except (BrokenPipeError, OSError, ValueError):
        pass                                # 起不来：读到结束时会说
    return r, sent


def _follow(chat_id, r, dead_note=None):
    """读这个进程的输出，直到它结束。返回 True = 原来的会话接不上（--resume 找不到），要换新会话重来。"""
    for ev in r.runner.events():
        r.last_event = _clock()
        t = ev.get("type")
        if t == "assistant":
            r.saw_output = True
        if t in ("assistant", "user") and not r.turn:   # 做完一轮后又递进来的话：它接着开了一轮
            r.begin_turn()
        if t == "result" and ev.get("is_error") and not ev.get("num_turns") and not r.saw_output and _resuming(chat_id):
            r.deferred = ev                  # 可能是「会话接不上」：等进程退出、看错误输出再定
            continue
        if apply_event(chat_id, ev, runner=r):
            r.got_result = True
            r.end_turn()
    r.runner.wait()
    if r.deferred is not None:
        if "No conversation found" in r.runner.stderr_tail(2000):
            return True
        if apply_event(chat_id, r.deferred, runner=r):
            r.got_result = True
            r.end_turn()
    if not r.got_result and r.turn and not _load(chat_id).get("stopped"):
        err = r.runner.stderr_tail()
        _append(chat_id, "system", dead_note or f"助手没有正常结束（{err or '进程退出了'}）", error=True)
    return False


def _resuming(chat_id):
    try:
        return bool(_load(chat_id).get("session_id"))
    except KeyError:
        return False


def _finish(chat_id, r):
    """进程结束后的收尾。按进程对象核对：同一个对话已经起了新进程的，不动新进程的表项和状态。"""
    with _lock:
        mine = r is None or _runners.get(chat_id) is r
        if not mine:
            return
        if r is not None:
            _runners.pop(chat_id, None)
        _starting.discard(chat_id)
        _pending.pop(chat_id, None)
        try:
            chat = _load(chat_id)
        except KeyError:
            chat = None
        if chat is not None:
            stopped = chat.pop("stopped", False)
            was_running = chat.get("running")
            chat["running"] = False
            if stopped and was_running:
                chat["messages"].append({"role": "system", "text": "已停止。", "at": _now()})
            _save(chat)
    if r is not None and isinstance(r.runner, agent_runner.HostRunner) and not r.runner.alive():
        try:
            agent_runner.HostRunner.retire(r.runner.dir)
        except OSError:
            pass
    if chat is not None and was_running:
        _task_turn_done(chat_id, stopped=stopped)
    _later(_drain)


def _restart_fresh(chat_id, text):
    """--resume 的会话找不到了（被 Claude 清理了等）：清掉会话号，开新会话，把前情带过去，再把刚才那句话递进去。"""
    with _lock:
        try:
            chat = _load(chat_id)
        except KeyError:
            return
        chat["session_id"] = ""
        chat["messages"].append({"role": "system", "at": _now(),
                                 "text": "原来的会话接不上了（Claude 那边找不到），换了一个新会话接着做，前情已经带过去。"})
        _save(chat)
        _go(chat_id, _resume_summary(chat_id) + "\n\n" + text)


def _resume_summary(chat_id):
    chat = _load(chat_id)
    lines = ["（面板）原来的会话接不上了（Claude 那边找不到），这是一个新会话。前情："]
    t = None
    if chat.get("task_id"):
        try:
            t = wstasks.get(chat["task_id"])
        except wstasks.NotFound:
            t = None
    if t:
        lines.append(f"- 这家：{t.get('company') or '（公司见网页）'}｜{t.get('job') or '（岗位见网页）'}｜网申状态：{t.get('status')}"
                     + (f"｜要本人做：{t['todo']}" if t.get("status") == "等你处理" and t.get("todo") else ""))
        if t.get("url"):
            lines.append(f"- 网申链接：{t['url']}")
    said = [m for m in chat.get("messages") or [] if m.get("role") in ("user", "assistant")
            and re.search(r"[\u4e00-\u9fff]", m.get("text") or "")]
    for m in said[-10:]:
        lines.append(f"- {'本人' if m['role'] == 'user' else '你'}：{_brief(m['text'], 200)}")
    lines.append("先用 tabs_context_mcp 看看原来那个画了颜色框的网页还在不在；不在就新开一个接着做。")
    return "\n".join(lines)


def _cost_delta(chat, ev, r):
    """total_cost_usd 是这个进程的累计值（实测）：只加新增的部分。按进程号认，面板重启接上后也不会重复加。"""
    total = float(ev.get("total_cost_usd") or 0)
    pid = getattr(getattr(r, "runner", None), "pid", None)
    seen = chat.get("cost_seen") or {}
    base = float(seen.get("total") or 0) if pid and seen.get("pid") == pid else 0.0
    if pid:
        chat["cost_seen"] = {"pid": pid, "total": max(base, total)}
    return max(0.0, total - base)


def apply_event(chat_id, ev, runner=None):
    """处理 claude 的一条 stream-json 输出：文字 → 回复；工具调用 → 进度；result → 一轮结束。返回是不是 result。"""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
        with _lock:
            chat = _load(chat_id)
            chat["session_id"] = ev["session_id"]
            _save(chat)
    elif t == "assistant":
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                chat = _append(chat_id, "assistant", block["text"].strip())
                save_site_notes(block["text"])
                _apply_readback_blocks(chat_id, chat, block["text"])   # 先收原文，再看【网申记录】（这样报「已提交」时已经读回过了）
                try:
                    for task in wstasks.apply_markers(block["text"], task_id=chat.get("task_id", ""), chat_id=chat_id):
                        _append(chat_id, "system", f"「网申」页：{task.get('company') or ''}｜{task.get('job') or ''} → {task['status']}"
                                + ("（看板里记了一条草稿）" if task["status"] == "已填待提交" else
                                   "（看板里记成已投递）" if task["status"] == "已提交" else ""))
                        if task["status"] == "已提交" and not task.get("readback") and not task.get("readback_state"):
                            with _lock:   # 跟它说「交了」：这一轮做完让它去网站读回提交的内容
                                if task["id"] not in _after_turn.setdefault(chat_id, []):
                                    _after_turn[chat_id].append(task["id"])
                except Exception as e:   # 记不上不影响它干活
                    _append(chat_id, "system", f"网申记录没记上：{e}", error=True)
            elif block.get("type") == "tool_use":
                _append(chat_id, "tool", tool_summary(block.get("name", ""), block.get("input") or {}))
    elif t == "user":
        for block in (ev.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                body = block.get("content")
                body = body if isinstance(body, str) else " ".join(c.get("text", "") for c in body or [] if isinstance(c, dict))
                _append(chat_id, "tool", "出错：" + re.sub(r"\s+", " ", body)[:160], error=True)
    elif t == "result":
        with _lock:
            chat = _load(chat_id)
            chat["session_id"] = ev.get("session_id") or chat.get("session_id", "")
            chat["cost_usd"] = round(chat.get("cost_usd", 0) + _cost_delta(chat, ev, runner), 4)
            if ev.get("is_error") and not chat.get("stopped"):
                chat["messages"].append({"role": "system", "text": f"出错了：{str(ev.get('result') or ev.get('subtype'))[:300]}",
                                         "at": _now(), "error": True})
            _save(chat)
        return True
    return False


def _apply_readback_blocks(chat_id, chat, text):
    """回复里的【网申读回】类型：投递记录 / 简历 / 岗位JD｜岗位：…｜地点：…  ……原文……【/网申读回】 → 记进看板。"""
    blocks = READBACK_BLOCK.findall(text or "")
    if not blocks:
        return
    tid = chat.get("task_id")
    if not tid:
        _append(chat_id, "system", "读回的原文没记上：这个对话没跟「网申」页的哪一家绑在一起", error=True)
        return
    for head, body in blocks:
        d = {}
        for part in re.split(r"[｜|]", head):
            m = re.match(r"\s*([^：:]+?)\s*[：:]\s*(.*?)\s*$", part)
            if m:
                d[m.group(1)] = m.group(2)
        typ = d.get("类型", "")
        kind = ("jd" if re.search(r"JD|岗位说明|职位描述|职位详情", typ, re.I) else "resume" if re.search(r"简历|申请表", typ)
                else "status" if re.search(r"投递|进度|记录", typ) else "other")
        try:
            wstasks.save_readback(tid, kind, body, url=d.get("网址", ""), status=d.get("进度", ""), account=d.get("账号", ""),
                                  position=d.get("岗位", "") if kind == "jd" else "", location=d.get("地点", ""),
                                  positions=d.get("岗位") if kind == "status" else None, source="助手照抄")
            what = {"jd": f"岗位 JD「{d.get('岗位', '')}」", "resume": "实际提交的简历", "status": "投递记录"}.get(kind, "页面原文")
            _append(chat_id, "system", f"已记进投递看板：{what}（{len(body.strip())} 字）")
        except (wstasks.NotFound, ValueError) as e:
            _append(chat_id, "system", f"读回的原文没记上：{e}", error=True)


_ACTIONS = {"screenshot": "截图看一眼", "left_click": "点击", "double_click": "双击", "triple_click": "三击", "type": "输入",
            "key": "按键", "scroll": "滚动", "wait": "等一下", "zoom": "放大看", "hover": "悬停", "scroll_to": "滚到",
            "right_click": "右键", "left_click_drag": "拖动"}


def tool_summary(name, inp):
    """工具调用 → 一行人能看懂的进度。"""
    n = name.removeprefix(PREFIX)
    summ = str(inp.get("action_summary") or "").strip()
    if n == "computer":
        act = _ACTIONS.get(inp.get("action"), inp.get("action", ""))
        return f"{act}{'：' + summ if summ else ''}"
    if n == "navigate":
        return "打开 " + str(inp.get("url", ""))[:120]
    if n == "form_input":
        return "填写：" + (summ or str(inp.get("value", ""))[:40])
    if n == "find":
        return "找：" + str(inp.get("query", ""))[:60]
    if n == "browser_batch":
        steps = [tool_summary(a.get("name", ""), a.get("input") or {}) for a in inp.get("actions") or []]
        return f"连续 {len(steps)} 步：" + "；".join(steps[:4]) + ("……" if len(steps) > 4 else "")
    return {"tabs_create_mcp": "新开一个标签页", "tabs_context_mcp": "看看标签页", "tabs_close_mcp": "关掉标签页",
            "read_page": "读页面结构", "get_page_text": "读页面文字", "javascript_tool": "在页面上跑一段脚本",
            "file_upload": "上传文件", "upload_image": "上传图片", "list_connected_browsers": "看看连着哪个浏览器",
            "read_console_messages": "看页面报错", "read_network_requests": "看网络请求"}.get(n, n)


# ── 网站笔记 ────────────────────────────────────────────────

def load_site_notes():
    try:
        return json.loads(SITES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_site_notes(text):
    """回复里的「【网站笔记】域名：说明」记下来（去重），下次填同一个网站时放进提示里。"""
    found = [(d.lower().strip("/").removeprefix("www."), n.strip()) for d, n in SITE_NOTE.findall(text or "")]
    if not found:
        return 0
    with _lock:
        notes = load_site_notes()
        added = 0
        for domain, note in found:
            lst = notes.setdefault(domain, [])
            if note and note not in lst:
                lst.append(note)
                added += 1
        if added:
            SITES_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return added


# ── 系统提示 ────────────────────────────────────────────────

def _records_text(limit=60):
    try:
        rs = records.load()
    except Exception:
        return "（读不到投递记录）"
    rs = [r for r in rs if r.get("campaign") == config.CURRENT_CAMPAIGN] or rs
    if not rs:
        return "（还没有记录）"
    by = {}
    for r in rs:
        by[r.get("status") or "未知"] = by.get(r.get("status") or "未知", 0) + 1
    head = f"本轮一共 {len(rs)} 条记录（" + "、".join(f"{k} {v}" for k, v in by.items()) + f"）；下面列最新 {min(limit, len(rs))} 条："
    rs = sorted(rs, key=lambda r: r.get("sent_at", ""), reverse=True)[:limit]
    return head + "\n" + "\n".join(f"- {(r.get('sent_at') or '')[:10]}｜{r.get('company_name', '')}｜{r.get('job_title', '')}"
                                    f"｜{r.get('status', '')}｜{r.get('apply_channel') or '邮件'}" for r in rs)


def _tasks_text(limit=15):
    try:
        ts = [t for t in wstasks.list_tasks() if t.get("status") in wstasks.ACTIVE][:limit]
    except Exception:
        return "（读不到）"
    if not ts:
        return "（没有待办）"
    return "\n".join(f"- {t.get('company') or '?'}｜{t.get('job') or '?'}｜{t.get('status')}｜{t.get('url') or '没有网址'}" for t in ts)


def _site_notes_text(chat=None):
    """网站笔记：给了对话就只附对话里出现过的网站（同一平台不同公司的子域名也算，比如 a.zhiye.com 和 b.zhiye.com），
    其他网站只列个目录，免得网站一多提示越来越长。"""
    notes = load_site_notes()
    if not notes:
        return "（还没有）"
    if chat is None:
        rel = notes
    else:
        seen = " ".join(str(m.get("text") or "") for m in chat.get("messages") or []).lower()
        def hit(d):
            base = d.split(".", 1)[1] if d.count(".") >= 2 else d
            return d in seen or base in seen
        rel = {d: ns for d, ns in notes.items() if hit(d)}
    lines = [f"- {d}：" + "；".join(ns) for d, ns in rel.items()]
    rest = [f"{d}（{len(ns)} 条）" for d, ns in notes.items() if d not in rel]
    if rest:
        lines.append("其他网站也记过笔记，对话里出现这些网址时会自动附上：" + "、".join(rest))
    return "\n".join(lines)


def _my_task_text(chat):
    """这个对话是从哪条网申待办开的：告诉它这家的颜色，让它在网页上画同色的框（本人对着面板一眼分得清哪个网页是哪个对话在填）。"""
    try:
        t = wstasks.get((chat or {}).get("task_id") or "")
    except wstasks.NotFound:
        return ""
    dot, hexc, name = wstasks.color_of(t)
    label = wstasks.short_name(t)
    return f"""
# 这个对话负责的网申
{t.get('company') or '（公司见网页）'}｜{t.get('job') or '（岗位见网页）'}｜{t.get('url') or ''}
面板里这家用{name}色 {dot} 标记。在这家的网申网页上加载填表脚本后，先执行一次：
await __wsfill.mark({{label: '{label}', color: '{hexc}'}})
网页四周会出现一圈{name}色的框和「面板助手 · {label}」小标签（换页后重新加载脚本会自己带上），本人对着面板就知道这个网页是哪个对话在填。
- 这家从头到尾只用这一个网页；要本人登录，就请本人在这个画了{name}色框的网页里登录。
- **不要让本人拖标签页、调标签组**。原来的网页找不到了（标签组没了），就自己新开一个网页打开这家网站，画上框接着做；要登录就请本人在新网页里登录。
"""


def _uploads_text():
    try:
        return uploads.prepare()
    except Exception as e:   # 准备不出来不影响填表：照片、附件就留给本人传
        return f"（文件夹没准备好：{e}；要上传的都留给本人）"


def system_prompt(chat=None):
    profile, _ = wsprofile.load()
    kit = wsprofile.load_kit()
    rules = RULES_PATH.read_text(encoding="utf-8") if RULES_PATH.exists() else ""
    rules = rules.replace("http://localhost:5001", config.PANEL_BASE)   # 规则里写的是正式面板的地址；测试环境换成 5002
    cand = config.PROFILE_PATH.read_text(encoding="utf-8") if config.PROFILE_PATH.exists() else ""
    notes_text = _site_notes_text(chat)
    now = checks.beijing_now()
    return f"""# 你在哪里
你是{config.CANDIDATE_NAME}求职投递面板里的助手，本人正在面板侧边的聊天框里跟你说话。今天是北京时间 {now:%Y-%m-%d %H:%M}。
你能用 Claude in Chrome 的工具操作本人自己的 Chrome（各网申网站的登录状态都在里面）。你的主要工作：
1. 代填网申表格（按下面的规则和资料）；
2. 回答跟投递有关的问题（投过哪些、某家投过没有、资料里写的是什么……）。
你没有执行命令、发邮件、改文件的工具；读文件只能读下面「可以上传的文件」那个文件夹（网站要传照片、简历时用 file_upload 传），别的文件读不了也不要试。发邮件、改资料由面板完成；需要改底稿时请本人去面板「网申」页下面的网申底稿改。
面板靠你回复里单独一行的【网申记录】知道这家现在轮到谁（「网申」页每一行、投递看板都跟着变），格式：
【网申记录】公司：XX｜岗位：XX｜网址：网申页面的网址｜账号：登录这家网站用的手机号或邮箱（页面上看得到才写，打码的照抄，比如 138****0000）｜状态：……
- 一打开这家网申、看清是哪家公司哪个岗位，就先写一行（状态先不写），面板那一行马上就有名字。
- 每次停下来（这一轮做完）都要写一行，状态三选一：
  - 「已填待提交」：能填的都填了、能存的都存了，等本人检查提交；
  - 「等你处理」：卡在只有本人能做的事上，再加一项「要你做：……」，一句话写清（比如「要你做：告诉我高中信息」）。要本人登录的，照规则请本人在画了颜色框的网页里登录、留在这一轮里等，不用先报「等你处理」结束这一轮；
  - 本人说已经提交了：写「已提交」。

# 说话方式
- 一律用中文，包括做事过程中的简短说明。简洁，先说结论。本人不是工程师，不说技术术语。
- 能自己判断的直接做，不要问。照片、简历这些附件自己传（用 file_upload，文件见下面「可以上传的文件」）。要本人登录、扫码的，照规则在这一轮里等。
- 真正只有本人能做的事（证件号这一栏、最后提交、资料里没有而只有本人知道的信息）先跳过、把别的都做完，最后一次性说清楚：在哪个颜色框的网页、哪一栏。
- 证件号（身份证号、护照号）一律不填：哪怕资料里有、哪怕本人说过可以，也留给本人自己输。也不要往这一栏粘贴任何东西（剪贴板里可能就是证件号）。
  要本人填证件号时，【网申记录】的「要你做」里写明「证件号」三个字和哪个颜色框的网页、哪一栏：面板会给本人一个「复制证件号」按钮，本人粘贴后点「我填好了」叫你接着做。
- 操作浏览器时少说多做；一段做完再简短汇报。
- 本人可能在你干活时插话（新消息会跟在某一步的工具结果后面出现）：先按新消息调整（比如换个写法、跳过某段、先填别的），再接着做，不用等做完才理。

# 代填网申的规则
{rules}

# 本人的资料（以这些为准；写着「（空）」就是没有，不要编）
## 网申底稿（简历以外的个人信息）
{wsprofile.as_text(profile)}

## 简历内容（经历描述用这里的原文）
{wsprofile.kit_text(kit)}

## 候选人档案（事实层）
{cand}

# 网站笔记（以前填这些网站时记下的坑）
{notes_text}

# 可以上传的文件（桌面「自动投递/网申上传」文件夹；按网站写的尺寸 / 大小要求挑，用 file_upload 传）
{_uploads_text()}

# 网申待办（面板「网申」页的清单）
{_tasks_text()}
{_my_task_text(chat)}
# 最近的投递记录（{config.CURRENT_CAMPAIGN}，新的在前）
{_records_text()}
"""


def _notify(title, text):
    from . import notify
    notify.send(title, text)
