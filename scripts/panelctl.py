#!/usr/bin/env python3
"""求职投递面板的管理小工具（本机用，给 Claude 和启动器调用；本人不用记）。

  python scripts/panelctl.py status          面板、助手进程、发信、网申现在什么情况
  python scripts/panelctl.py start           面板没在跑才在后台启动（重复执行也安全，绝不重启正在跑的面板）
  python scripts/panelctl.py safe-restart    不会打断任何事的时候才重启面板，不然说明为什么不行
  python scripts/panelctl.py stop            同样先检查，安全才停
  python scripts/panelctl.py export-excel    从 records.json 重新导出桌面的 Excel 镜像
  python scripts/panelctl.py tunnel          手机访问（只有 ~/.jobapply_tunnel_on 存在时才开，默认关）
  python scripts/panelctl.py close-launcher-windows   关掉已经跑完的「投递面板.command」终端窗口

加 JOBAPPLY_ENV=test 就是管测试环境（5002 端口、data_test 数据）。
这个脚本不导入 Gmail 模块，也不发信。"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobapply import config  # noqa: E402

# 旧版 config（改造前）没有运行环境这几项：按正式环境算
ENV = getattr(config, "ENV", "prod")
PORT = getattr(config, "PORT", 5001)
BASE = getattr(config, "PANEL_BASE", f"http://localhost:{PORT}")
DATA = getattr(config, "DATA_DIR", config.BASE_DIR)
NAME = "jobapply_test" if ENV == "test" else "jobapply_panel"
LOG_DIR = Path.home() / "Library" / "Logs" / "jobapply"
LOG = LOG_DIR / ("panel_test.log" if ENV == "test" else "panel.log")
PID_FILE = Path(f"/tmp/{NAME}.pid")
TUNNEL_FLAG = Path.home() / ".jobapply_tunnel_on"
TUNNEL_PID = Path("/tmp/jobapply_tunnel.pid")
TUNNEL_LOG = LOG_DIR / "tunnel.log"
BJ = ZoneInfo("Asia/Shanghai")
WAIT_STATES = ("等你处理", "已填待提交")
SCRUB_ENV = ("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_PID", "CLAUDE_JOB_DIR", "CLAUDE_EFFORT", "AI_AGENT")


# ── 读状态（只读文件和进程表，不导入面板的模块）─────────────────────

def _get(path, timeout=3):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read()
    except Exception:
        return None, b""


def panel_responding():
    return _get("/")[0] == 200


def listen_pid(port=PORT):
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
    pids = [int(x) for x in out.split() if x.strip().isdigit()]
    return pids[0] if pids else None


def _ps():
    """[(pid, ppid, 启动时间, 命令行)]"""
    out = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,lstart=,command="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        try:
            started = datetime.strptime(" ".join(parts[2:7]), "%a %b %d %H:%M:%S %Y")
        except ValueError:
            started = None
        rows.append((int(parts[0]), int(parts[1]), started, parts[7]))
    return rows


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def chats():
    d = DATA / "agent_chats"
    return [c for c in (_load_json(p, None) for p in sorted(d.glob("*.json"))) if isinstance(c, dict)] if d.exists() else []


def tasks():
    t = _load_json(DATA / "wangshen_tasks.json", [])
    return t if isinstance(t, list) else []


def queue():
    q = _load_json(DATA / "queue.json", [])
    return q if isinstance(q, list) else []


def agent_procs(panel_pid):
    """助手 claude 进程：挂在面板下面的（老办法，重启面板会断）和宿主托管的（WP8a 之后，重启面板不受影响）。"""
    by_session = {c.get("session_id"): c for c in chats() if c.get("session_id")}
    by_id = {c.get("id"): c for c in chats()}
    task_by_id = {t.get("id"): t for t in tasks()}
    rows = _ps()
    run_root = str(DATA / "agent_chats" / "_run")
    hosts = {}
    for pid, _ppid, _st, cmd in rows:
        if "jobapply.agent_host" in cmd and run_root in cmd:
            hosts[pid] = cmd.rstrip("/").split("/")[-1]          # 运行目录名就是对话 id
    out, found = [], []
    for pid, ppid, started, cmd in rows:
        if " -p " not in f" {cmd} " or "--chrome" not in cmd or "claude" not in cmd.split(" -p ")[0]:
            continue
        if ppid in hosts:
            found.append((pid, started, "", hosts[ppid]))
        elif panel_pid and ppid == panel_pid:
            ids = re.findall(r"--resume ([0-9a-f-]{36})", cmd)   # 接回的会话编号（在命令行最后）
            found.append((pid, started, ids[-1] if ids else "", ""))
    claimed = {sid for _, _, sid, _ in found if sid}
    for pid, started, sid, host_chat in found:
        if host_chat:
            chat = by_id.get(host_chat) or {}
        else:
            chat = by_session.get(sid) or (_new_chat_of(started, claimed) if not sid else {})
        claimed.add(chat.get("session_id"))
        task = task_by_id.get(chat.get("task_id")) or {}
        out.append({"pid": pid, "started": started, "session": sid, "chat": chat.get("id", ""), "detached": bool(host_chat),
                    "running": bool(chat.get("running")),
                    "what": task.get("company") or chat.get("title") or ("（新对话）" if not sid else "（没对上对话）")})
    return out


def _new_chat_of(started, claimed):
    """新开的会话命令行里没有编号：找进程启动前后一两分钟里开的、还没对上进程的那个对话。"""
    if not started:
        return {}
    best, gap = {}, timedelta(minutes=2)
    for c in chats():
        if c.get("session_id") in claimed or c.get("archived"):
            continue
        first = next((m.get("at") for m in c.get("messages") or [] if m.get("at")), None)
        try:
            d = abs(datetime.strptime(first, "%Y-%m-%d %H:%M:%S") - started)
        except (TypeError, ValueError):
            continue
        if d < gap:
            best, gap = c, d
    return best


def schedule_info(now=None):
    now = now or datetime.now(BJ).replace(tzinfo=None)
    q = queue()
    sending = [i for i in q if i.get("status") == "发送中"]
    sched = sorted((i.get("send_at") or "") for i in q if i.get("status") == "已定时")
    nxt = None
    if sched:
        try:
            nxt = datetime.strptime(sched[0], "%Y-%m-%d %H:%M")
        except ValueError:
            nxt = now   # 读不懂的时间按「马上要发」算，宁可不动
    return sending, sched, nxt


def blockers(allow_waiting_tasks=False):
    """现在重启 / 停面板会打断什么。空列表 = 安全。
    还有挂在面板下面的助手进程（老办法）：重启会把它们和它们开的网页一起断掉，条件从严（没有这种进程、没人在等你、
    60 分钟内没有定时信）。全都交给宿主托管了：重启不碰助手，只看发信（没有发送中、10 分钟内没有定时信）。"""
    why = []
    pid = listen_pid()
    procs = agent_procs(pid) if pid else []
    attached = [p for p in procs if not p["detached"]]
    window = 10
    if attached:
        window = 60
        names = "、".join(f"{p['what']}（pid {p['pid']}{'，正在干活' if p['running'] else ''}）" for p in attached)
        why.append(f"还有 {len(attached)} 个助手进程挂在面板下面（重启会把它们和它们开的网页一起断掉）：{names}")
        waiting = [t for t in tasks() if t.get("status") in WAIT_STATES]
        if waiting and not allow_waiting_tasks:
            why.append("网申里有等本人操作的：" + "、".join(f"{t.get('company')}（{t.get('status')}）" for t in waiting)
                       + "——本人处理完或说一声再动（确认没人在操作可加 --allow-waiting-tasks）")
    sending, sched, nxt = schedule_info()
    if sending:
        why.append(f"有 {len(sending)} 封信「发送中」")
    now = datetime.now(BJ).replace(tzinfo=None)
    if nxt and nxt - now < timedelta(minutes=window):
        why.append(f"{window} 分钟内有定时信（{sched[0]} 北京时间，共 {len(sched)} 封排着）")
    return why


# ── 命令 ───────────────────────────────────────────────────────────

def cmd_status(_args):
    pid = listen_pid()
    print(f"环境：{'测试' if ENV == 'test' else '正式'}   地址：{BASE}   数据：{DATA}")
    if pid:
        info = next((r for r in _ps() if r[0] == pid), None)
        code, body = _get("/api/health")
        health = _load_bytes(body) if code == 200 else {}
        ver = health.get("version") or "旧版（没有版本接口）"
        parent = next((r[3] for r in _ps() if info and r[0] == info[1]), "")
        tied = "（挂在终端窗口里：关掉那个窗口面板就停了）" if "投递面板.command" in parent or "bash" in parent.split(" ")[0] else ""
        print(f"面板：在跑  pid {pid}  启动于 {info[2] if info else '?'}  版本 {ver}  "
              f"{'能响应' if panel_responding() else '没响应！'}{tied}")
    else:
        print("面板：没在跑")
    other = 5001 if PORT != 5001 else 5002
    if listen_pid(other):
        print(f"另外：{other} 端口上也有面板在跑（{'正式' if other == 5001 else '测试环境'}）")
    procs = agent_procs(pid) if pid else []
    print(f"助手进程：{len(procs)} 个")
    for p in procs:
        print(f"  - pid {p['pid']}  启动于 {p['started']}  {p['what']}  {'正在干活' if p['running'] else '空闲'}"
              f"  对话 {p['chat'] or '?'}  {'宿主托管（重启面板不受影响）' if p['detached'] else '挂在面板下面'}")
    sending, sched, nxt = schedule_info()
    print(f"发信：发送中 {len(sending)} 封；定时 {len(sched)} 封" + (f"，最早 {sched[0]}（北京时间）" if sched else ""))
    waiting = [t for t in tasks() if t.get("status") in WAIT_STATES or t.get("status") == "助手在填"]
    print(f"网申：{len(waiting)} 条要留意")
    for t in waiting:
        print(f"  - {t.get('company')}｜{(t.get('job') or '')[:24]}  {t.get('status')}  {t.get('updated_at', '')}"
              + (f"  要你做：{t['todo'][:40]}" if t.get("todo") else ""))
    if ENV == "test":
        print("手机访问：测试环境不开")
    else:
        tp = _pid_from(TUNNEL_PID)
        print("手机访问：" + ("开着" if tp and _alive(tp) or _cloudflared_pids() else "关着")
              + ("（开关文件在）" if TUNNEL_FLAG.exists() else "（默认关）"))
    why = blockers()
    print("现在重启面板：" + ("可以" if not why else "不行——\n  · " + "\n  · ".join(why)))
    return 0


def _load_bytes(b):
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return {}


def _pid_from(path):
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _cloudflared_pids():
    return [r[0] for r in _ps() if "cloudflared" in r[3] and f"localhost:{PORT}" in r[3]]


def _python():
    for p in (ROOT / ".venv" / "bin" / "python", Path.home() / "job_apply" / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return sys.executable


def cmd_start(_args):
    if panel_responding():
        print(f"面板已经在跑（{BASE}），不重启。")
        return 0
    if listen_pid():
        for _ in range(10):   # 端口有人占着但没响应：多等一会儿（可能正忙），绝不去杀它
            time.sleep(1)
            if panel_responding():
                print(f"面板已经在跑（{BASE}），不重启。")
                return 0
        print(f"{PORT} 端口被占着，但面板没响应。先别动它，用 panelctl status 看看是什么。")
        return 1
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # 从 Claude Code 里启动时，别把它的会话变量带给面板（面板起的助手会以为自己嵌在别的会话里）
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB_ENV)}
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动面板（{ENV}）=====\n")
        log.flush()
        proc = subprocess.Popen([_python(), str(ROOT / "app.py")], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)   # 自己一个会话：关终端窗口不影响它
    PID_FILE.write_text(str(proc.pid))
    for _ in range(60):
        if panel_responding():
            print(f"面板已启动：{BASE}（pid {proc.pid}，日志 {LOG}）")
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    print(f"面板没起来，看日志：{LOG}")
    return 1


def _stop_panel():
    pid = listen_pid()
    if not pid:
        return True
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        time.sleep(0.25)
        if not listen_pid():
            return True
    return False


def _check_or_refuse(args, what):
    """正式环境：有会被打断的事就拒绝（返回 False）。测试环境没什么要保护的：照样列出来，但不拦。"""
    why = blockers(allow_waiting_tasks="--allow-waiting-tasks" in args)
    if why and ENV == "test":
        print("（测试环境，不拦）" + "；".join(why))
        return True
    if why:
        print(f"现在不能{what}面板：\n  · " + "\n  · ".join(why))
        return False
    return True


def cmd_stop(args):
    if not _check_or_refuse(args, "停"):
        return 2
    if not _stop_panel():
        print("面板 10 秒内没停下来，没有强行结束它。用 panelctl status 看看。")
        return 1
    PID_FILE.unlink(missing_ok=True)
    print("面板已停。")
    return 0


def cmd_safe_restart(args):
    if not _check_or_refuse(args, "重启"):
        return 2
    if not _stop_panel():
        print("面板 10 秒内没停下来，没有强行结束它。用 panelctl status 看看。")
        return 1
    return cmd_start(args)


def cmd_export_excel(_args):
    from jobapply import records
    path = records.export_excel(records.load())
    print(f"已从 {config.RECORDS_PATH} 导出：{path}")
    return 0


def cmd_tunnel(_args):
    """手机访问：开关文件在才开；已经开着就不重复开。"""
    if ENV == "test":
        print("测试环境不开手机访问。")
        return 0
    if not TUNNEL_FLAG.exists():
        print(f"手机访问默认关（要开：建一个空文件 {TUNNEL_FLAG}）。")
        return 0
    tp = _pid_from(TUNNEL_PID)
    if (tp and _alive(tp)) or _cloudflared_pids():
        print("手机访问已经开着。")
        return 0
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_tunnel_worker"], cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    TUNNEL_PID.write_text(str(proc.pid))
    print("手机访问已开（链接稍后出现在面板左下角）。")
    return 0


def cmd_tunnel_worker(_args):
    exe = Path.home() / "cloudflared"
    if not exe.exists():
        return 1
    with open(TUNNEL_LOG, "a", encoding="utf-8") as log:
        proc = subprocess.Popen([str(exe), "tunnel", "--url", BASE], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        for line in proc.stdout:
            log.write(line)
            log.flush()
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                config.TUNNEL_URL_PATH.write_text(m.group(0))
    return proc.wait()


def cmd_close_launcher_windows(_args):
    """等启动器那个终端窗口跑完，再把所有已经跑完的「投递面板.command」窗口关掉（不碰还在跑的）。"""
    if "--now" not in _args:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "close-launcher-windows", "--now"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)   # 自己一个会话：不算在那个终端窗口里，窗口才关得掉
        return 0
    time.sleep(1.5)
    script = '''
tell application "Terminal"
  set ids to id of every window
  repeat with i from 1 to count of ids
    try
      set w to window id (item i of ids)
      if (name of w) contains "投递面板.command" and (count of tabs of w) > 0 then
        if (busy of tab 1 of w) is false then close w
      end if
    end try
  end repeat
end tell'''
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)
    return 0


COMMANDS = {"status": cmd_status, "start": cmd_start, "stop": cmd_stop, "safe-restart": cmd_safe_restart,
            "export-excel": cmd_export_excel, "tunnel": cmd_tunnel, "_tunnel_worker": cmd_tunnel_worker,
            "close-launcher-windows": cmd_close_launcher_windows}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]))
