"""面板里的助手：在面板侧边跟它聊天。它能操作你的 Chrome（Claude in Chrome）代填网申，也能回答投递相关的问题。

每说一句起一个 `claude -p`（本机 Claude Code，会员额度，不扣 API 余额），用 --resume 接着同一个对话。
只给它浏览器工具：--tools "" 关掉读写文件、执行命令这些内置工具，--strict-mcp-config 不加载 Gmail 等连接器，
--allowedTools 只放行 Claude in Chrome。所以它发不了邮件、改不了面板数据、碰不到电脑上的文件；
它看到的资料（网申底稿、简历内容、档案、投递记录摘要、网站笔记）都是面板每一轮放进系统提示里给它的。"""

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime

from . import checks, config, records, wsprofile
from .llm import _claude_bin

CHATS_DIR = config.BASE_DIR / "agent_chats"
SITES_PATH = config.BASE_DIR / "wangshen_sites.json"
RULES_PATH = config.BASE_DIR / "wangshen_rules.md"
MAX_TURN_SECONDS = 45 * 60          # 一句话最多做 45 分钟（填一整张网申够了），超时自动停
NOTIFY_AFTER_SECONDS = 60           # 做了一分钟以上的，做完弹 Mac 通知
SITE_NOTE = re.compile(r"【网站笔记】\s*([^\s：:]+)\s*[：:]\s*(.+)")
PREFIX = "mcp__claude-in-chrome__"

_lock = threading.RLock()
_procs = {}                          # chat_id → 正在跑的 claude 进程


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


# ── 对话 ─────────────────────────────────────────────────────

def new_chat():
    chat = {"id": uuid.uuid4().hex[:12], "title": "", "created_at": _now(), "updated_at": _now(),
            "session_id": "", "running": False, "messages": [], "cost_usd": 0.0}
    with _lock:
        _save(chat)
    return chat


def list_chats():
    out = []
    for p in CHATS_DIR.glob("*.json") if CHATS_DIR.exists() else []:
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": c["id"], "title": c.get("title") or "新对话", "updated_at": c.get("updated_at", ""),
                    "running": c.get("running", False), "n": len(c.get("messages", []))})
    return sorted(out, key=lambda c: c["updated_at"], reverse=True)


def get(chat_id, since=0):
    chat = _load(chat_id)
    msgs = chat.get("messages", [])
    return {**{k: v for k, v in chat.items() if k != "messages"}, "total": len(msgs), "messages": msgs[max(0, since):]}


def delete(chat_id):
    with _lock:
        if chat_id in _procs:
            raise Busy("这个对话还在进行，先点「停止」")
        _path(chat_id).unlink(missing_ok=True)


def running_chat():
    with _lock:
        return next(iter(_procs), None)


def send(chat_id, text):
    """说一句：后台起 claude 接着这个对话做。浏览器只有一个，所以同一时间只跑一个对话。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("说点什么")
    with _lock:
        chat = _load(chat_id)
        other = running_chat()
        if other == chat_id or chat.get("running"):
            raise Busy("它还在做上一句，做完再说，或者先点「停止」")
        if other:
            raise Busy("另一个对话还在操作浏览器，先等它做完或点停止")
        chat["messages"].append({"role": "user", "text": text, "at": _now()})
        chat["running"] = True
        chat["title"] = chat.get("title") or text.splitlines()[0][:24]
        _save(chat)
        _procs[chat_id] = None            # 先占住，免得两句话同时起进程
    threading.Thread(target=_run_safe, args=(chat_id, text), daemon=True).start()
    return get(chat_id)


def stop(chat_id):
    with _lock:
        proc = _procs.get(chat_id)
        chat = _load(chat_id)
        if not chat.get("running") and proc is None:
            return False
        chat["stopped"] = True
        _save(chat)
    if proc:
        proc.terminate()
    return True


def recover():
    """面板启动时：上次没做完就关了面板的对话，标成已停止。"""
    for c in list_chats():
        if c["running"] and c["id"] not in _procs:
            with _lock:
                chat = _load(c["id"])
                chat["running"] = False
                chat["messages"].append({"role": "system", "text": "面板重启过，上一句没做完。要接着做就再说一句。", "at": _now()})
                _save(chat)


# ── 跑一轮 ──────────────────────────────────────────────────

def _args(chat):
    args = [_claude_bin(), "-p", "--chrome", "--model", config.AGENT_MODEL, "--effort", config.AGENT_EFFORT,
            "--setting-sources", "", "--strict-mcp-config", "--tools", "",
            "--allowedTools", "mcp__claude-in-chrome",
            "--output-format", "stream-json", "--verbose",
            "--append-system-prompt", system_prompt()]
    if chat.get("session_id"):
        args += ["--resume", chat["session_id"]]
    return args


def _run_safe(chat_id, text):
    t0 = time.time()
    try:
        _run(chat_id, text)
    except Exception as e:  # 起不来 / 读坏了：告诉用户，别让对话卡在「进行中」
        _append(chat_id, "system", f"出错了：{type(e).__name__}: {e}", error=True)
    finally:
        with _lock:
            _procs.pop(chat_id, None)
            try:
                chat = _load(chat_id)
            except KeyError:
                return
            stopped = chat.pop("stopped", False)
            chat["running"] = False
            if stopped:
                chat["messages"].append({"role": "system", "text": "已停止。", "at": _now()})
            _save(chat)
        if time.time() - t0 > NOTIFY_AFTER_SECONDS and not stopped:
            last = next((m["text"] for m in reversed(chat["messages"]) if m["role"] == "assistant"), "")
            _notify("面板助手做完了", re.sub(r"\s+", " ", last)[:60] or "去面板看看")


def _run(chat_id, text):
    chat = _load(chat_id)
    workdir = CHATS_DIR / "_cwd"          # 空目录：不读任何项目的 CLAUDE.md
    workdir.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    proc = subprocess.Popen(_args(chat), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", cwd=workdir, env=env)
    with _lock:
        _procs[chat_id] = proc
        if _load(chat_id).get("stopped"):   # 进程起来之前就点了停止
            proc.terminate()
    timer = threading.Timer(MAX_TURN_SECONDS, proc.terminate)
    timer.start()
    got_result = False
    try:
        proc.stdin.write(text)
        proc.stdin.close()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            got_result |= apply_event(chat_id, ev)
        proc.wait()
    finally:
        timer.cancel()
    if not got_result and not _load(chat_id).get("stopped"):
        err = (proc.stderr.read() or "").strip()[-300:]
        _append(chat_id, "system", f"助手没有正常结束（{err or f'退出码 {proc.returncode}'}）", error=True)


def apply_event(chat_id, ev):
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
                _append(chat_id, "assistant", block["text"].strip())
                save_site_notes(block["text"])
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
            chat["cost_usd"] = round(chat.get("cost_usd", 0) + (ev.get("total_cost_usd") or 0), 4)
            if ev.get("is_error") and not chat.get("stopped"):
                chat["messages"].append({"role": "system", "text": f"出错了：{str(ev.get('result') or ev.get('subtype'))[:300]}",
                                         "at": _now(), "error": True})
            _save(chat)
        return True
    return False


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


def system_prompt():
    profile, _ = wsprofile.load()
    kit = wsprofile.load_kit()
    rules = RULES_PATH.read_text(encoding="utf-8") if RULES_PATH.exists() else ""
    cand = config.PROFILE_PATH.read_text(encoding="utf-8") if config.PROFILE_PATH.exists() else ""
    notes = load_site_notes()
    notes_text = "\n".join(f"- {d}：" + "；".join(ns) for d, ns in notes.items()) or "（还没有）"
    now = checks.beijing_now()
    return f"""# 你在哪里
你是{config.CANDIDATE_NAME}求职投递面板里的助手，本人正在面板侧边的聊天框里跟你说话。今天是北京时间 {now:%Y-%m-%d %H:%M}。
你能用 Claude in Chrome 的工具操作本人自己的 Chrome（各网申网站的登录状态都在里面）。你的主要工作：
1. 代填网申表格（按下面的规则和资料）；
2. 回答跟投递有关的问题（投过哪些、某家投过没有、资料里写的是什么……）。
你没有读写电脑文件、执行命令、发邮件的工具：发邮件、记录投递、改资料由面板上的按钮完成；需要改底稿时请本人去「我的资料（网申）」页改。

# 说话方式
- 一律用中文，包括做事过程中的简短说明。简洁，先说结论。本人不是工程师，不说技术术语。
- 需要本人做的事（扫码登录、输入证件号、点牛客图标、最后提交）说清楚：在哪个标签页、点哪里。
- 操作浏览器时少说多做；一段做完再简短汇报。

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

# 最近的投递记录（{config.CURRENT_CAMPAIGN}，新的在前）
{_records_text()}
"""


def _notify(title, text):
    text = text.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}" sound name "Glass"'],
                   capture_output=True)
